"""Stateless, authenticated CC observer; the Bridge remains task authority."""
from dataclasses import dataclass
from collections.abc import Mapping
import hmac
import time
from .contracts import Identity, Binding, identifier, normalize_request, digest, envelope, checked_task, completion
from .errors import ChannelError
from .http_server import bearer, single_header


@dataclass(frozen=True)
class Service:
    source: str
    bridge: object
    bindings: dict[str, Binding]
    accepting_new: bool = False
    owner: tuple[str, str] | None = None


class Facade:
    def __init__(self, services, *, wait_seconds=0.2, poll_seconds=0.01, mode="disabled"):
        if mode not in {"offline_prototype", "p1b_integration", "workbuddy_local_trial"}:
            raise ValueError("Explicit test mode required; production is disabled")
        if not services or not 0 <= wait_seconds <= 30 or not 0.001 <= poll_seconds <= 1:
            raise ValueError("Invalid facade configuration")
        for token, service in services.items():
            if not isinstance(token, str) or len(token) < 16 or not isinstance(service, Service):
                raise ValueError("Explicit service credentials required")
            self.validated_bindings(service, mode)
        if mode == "workbuddy_local_trial" and len(services) != 1:
            raise ValueError("Local trial requires exactly one service/owner")
        self.services = dict(services)
        self.mode = mode
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds

    @staticmethod
    def validated_bindings(service, mode="p1b_integration"):
        if not isinstance(service, Service) or not isinstance(service.bindings, Mapping) or type(service.accepting_new) is not bool:
            raise ValueError("Invalid service configuration")
        identifier(service.source)
        trial = mode == "workbuddy_local_trial"
        if trial:
            if type(service.owner) is not tuple or len(service.owner) != 2:
                raise ValueError("Local trial requires one explicit owner")
            for value in service.owner:
                identifier(value)
        values = dict(service.bindings)
        for alias, binding in values.items():
            if (not isinstance(alias, str) or not isinstance(binding, Binding)
                    or alias != binding.model_alias
                    or not alias.startswith("workbuddy-" if trial else "synthetic-")
                    or (binding.native_model.startswith("fake-") if trial else not binding.native_model.startswith("fake-"))):
                raise ValueError("Binding keys and model aliases must match the explicit execution mode")
        return values

    def authenticate(self, headers):
        supplied = bearer(headers)
        for token, service in self.services.items():
            if hmac.compare_digest(supplied, token):
                return service
        raise ChannelError("UNAUTHORIZED", 401)

    def handle(self, method, path, headers, payload):
        service = self.authenticate(headers)
        try:
            bindings = self.validated_bindings(service, self.mode)
        except (ValueError, ChannelError):
            raise ChannelError("INVALID_SERVICE_CONFIGURATION", 503) from None
        if method == "GET" and path == "/v1/models":
            models = sorted(bindings) if service.accepting_new else []
            owner = "workbuddy-local-trial" if self.mode == "workbuddy_local_trial" else ("p1a-synthetic" if self.mode == "offline_prototype" else "p1b-synthetic")
            return 200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": owner} for m in models]}, {}
        if method != "POST" or path not in {"/v1/chat/completions", "/v1/native-harness/events"}:
            raise ChannelError("NOT_FOUND", 404)
        if self.mode in {"p1b_integration", "workbuddy_local_trial"}:
            if (single_header(headers, "X-Native-Source") != service.source
                    or single_header(headers, "X-Native-Ledger") != service.bridge.ledger_instance):
                raise ChannelError("FROZEN_ROUTE_MISMATCH", 409, accepted=False)
        identity = Identity(service.source, single_header(headers, "X-Native-Tenant"),
                            single_header(headers, "X-Native-Principal"), single_header(headers, "X-Native-Operation"))
        if self.mode == "workbuddy_local_trial" and (identity.tenant, identity.principal) != service.owner:
            raise ChannelError("FORBIDDEN", 403, accepted=False)
        admission = single_header(headers, "X-Native-Admission-Allowed")
        frozen_binding = single_header(headers, "X-Native-Binding-Digest")
        if admission not in {"true", "false"}:
            raise ChannelError("INVALID_HEADER", 400)
        events = path == "/v1/native-harness/events"
        cursor = 0
        if events:
            from .event_contract import parse_cursor
            cursor = parse_cursor(single_header(headers, "X-Native-Event-Cursor"))
            # A nonzero cursor can only refer to a previously admitted task.
            if cursor:
                admission = "false"
        request = normalize_request(payload)
        request_digest = digest(request)
        task = service.bridge.lookup(identity.origin_key)
        replay = task is not None
        if task is None:
            if self.mode == "workbuddy_local_trial" and (len(request["messages"]) != 1 or request["messages"][0]["role"] != "user"):
                raise ChannelError("LOCAL_TRIAL_SINGLE_USER_MESSAGE_REQUIRED", 400, accepted=False)
            if not service.accepting_new or admission != "true":
                raise ChannelError("NEW_ADMISSION_DISABLED", 503, accepted=False)
            binding = bindings.get(request["model"])
            if binding is None or binding.model_alias != request["model"] or digest(binding.wire()) != frozen_binding:
                raise ChannelError("MODEL_NOT_AVAILABLE", 400, accepted=False)
            task = service.bridge.submit(envelope(identity, request, binding, service.bridge.ledger_instance))
        if events:
            from .event_contract import project_events
            checked_task(task, identity, request_digest, service.bridge.ledger_instance)
            if digest(task["request"]["binding"]) != frozen_binding:
                raise ChannelError("BRIDGE_BINDING_MISMATCH")
            page = service.bridge.events(task["remote_task_id"], cursor)
            result = project_events(page, task, identity, request_digest,
                                    frozen_binding, service.bridge, cursor)
            return 200, result, {"X-Native-Replay": str(replay).lower(), "X-Native-Accepted": "true"}
        deadline = time.monotonic() + self.wait_seconds
        while True:
            checked_task(task, identity, request_digest, service.bridge.ledger_instance)
            if digest(task["request"]["binding"]) != frozen_binding:
                raise ChannelError("BRIDGE_BINDING_MISMATCH")
            if task.get("result") or task.get("result_expired"):
                return 200, completion(task), {"X-Native-Replay": str(replay).lower(), "X-Native-Accepted": "true"}
            if time.monotonic() >= deadline:
                code = ("APPROVAL_PENDING" if task.get("liveness") == "waiting_approval" else
                        "EXECUTION_UNCERTAIN" if task.get("liveness") == "unknown" else "TASK_WAIT_TIMEOUT")
                raise ChannelError(code, 504, accepted=True)
            time.sleep(min(self.poll_seconds, max(0, deadline - time.monotonic())))
            task = service.bridge.task(task["remote_task_id"])
