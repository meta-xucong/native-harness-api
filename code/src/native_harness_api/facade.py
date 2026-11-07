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


class Facade:
    def __init__(self, services, *, wait_seconds=0.2, poll_seconds=0.01, mode="disabled"):
        if mode not in {"offline_prototype", "p1b_integration"}:
            raise ValueError("Explicit synthetic test mode required; production is disabled")
        if not services or not 0 <= wait_seconds <= 30 or not 0.001 <= poll_seconds <= 1:
            raise ValueError("Invalid facade configuration")
        for token, service in services.items():
            if not isinstance(token, str) or len(token) < 16 or not isinstance(service, Service):
                raise ValueError("Explicit service credentials required")
            self.validated_bindings(service)
        self.services = dict(services)
        self.mode = mode
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds

    @staticmethod
    def validated_bindings(service):
        if not isinstance(service, Service) or not isinstance(service.bindings, Mapping) or type(service.accepting_new) is not bool:
            raise ValueError("Invalid service configuration")
        identifier(service.source)
        values = dict(service.bindings)
        for alias, binding in values.items():
            if (not isinstance(alias, str) or not isinstance(binding, Binding)
                    or alias != binding.model_alias or not alias.startswith("synthetic-")
                    or not binding.native_model.startswith("fake-")):
                raise ValueError("Tests require matching, explicitly synthetic binding keys and aliases")
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
            bindings = self.validated_bindings(service)
        except (ValueError, ChannelError):
            raise ChannelError("INVALID_SERVICE_CONFIGURATION", 503) from None
        if method == "GET" and path == "/v1/models":
            models = sorted(bindings) if service.accepting_new else []
            owner = "p1a-synthetic" if self.mode == "offline_prototype" else "p1b-synthetic"
            return 200, {"object": "list", "data": [{"id": m, "object": "model", "owned_by": owner} for m in models]}, {}
        if method != "POST" or path != "/v1/chat/completions":
            raise ChannelError("NOT_FOUND", 404)
        if self.mode == "p1b_integration":
            if (single_header(headers, "X-Native-Source") != service.source
                    or single_header(headers, "X-Native-Ledger") != service.bridge.ledger_instance):
                raise ChannelError("FROZEN_ROUTE_MISMATCH", 409, accepted=False)
        identity = Identity(service.source, single_header(headers, "X-Native-Tenant"),
                            single_header(headers, "X-Native-Principal"), single_header(headers, "X-Native-Operation"))
        admission = single_header(headers, "X-Native-Admission-Allowed")
        frozen_binding = single_header(headers, "X-Native-Binding-Digest")
        if admission not in {"true", "false"}:
            raise ChannelError("INVALID_HEADER", 400)
        request = normalize_request(payload)
        request_digest = digest(request)
        task = service.bridge.lookup(identity.origin_key)
        replay = task is not None
        if task is None:
            if not service.accepting_new or admission != "true":
                raise ChannelError("NEW_ADMISSION_DISABLED", 503, accepted=False)
            binding = bindings.get(request["model"])
            if binding is None or binding.model_alias != request["model"] or digest(binding.wire()) != frozen_binding:
                raise ChannelError("MODEL_NOT_AVAILABLE", 400, accepted=False)
            task = service.bridge.submit(envelope(identity, request, binding, service.bridge.ledger_instance))
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
