"""Ordinary API-key Chat Completions ingress over the existing Bridge ledger.

One trusted resource domain, not downstream-user isolation. Without a directly
received Idempotency-Key every HTTP request is a new operation, including a
gateway retry. This process never retries submission or executes a model.
"""
import hashlib
import math
import time
import uuid

from .contracts import Identity, canonical, checked_task, completion, digest, envelope, identifier, normalize_request
from .errors import ChannelError
from .event_contract import project_events
from .facade import Facade
from .http_server import EventStream, single_header


def normalize_provider_request(payload):
    if not isinstance(payload, dict):
        raise ChannelError("INVALID_REQUEST", 400)
    value = dict(payload)
    stream = value.get("stream", False)
    if type(stream) is not bool:
        raise ChannelError("INVALID_STREAM", 400)
    if "stream_options" in value:
        options = value.pop("stream_options")
        if (not stream or not isinstance(options, dict) or set(options) - {"include_usage"}
                or ("include_usage" in options and type(options["include_usage"]) is not bool)):
            raise ChannelError("INVALID_STREAM_OPTIONS", 400, param="stream_options")
    value["stream"] = False  # Observation preferences do not change execution.
    request = normalize_request(value)
    if len(request["messages"]) != 1 or request["messages"][0]["role"] != "user":
        raise ChannelError("SINGLE_USER_MESSAGE_REQUIRED", 400, param="messages")
    return request, stream


def sse(value):
    return ("data: " + canonical(value) + "\n\n").encode()


class OpenAIProvider:
    def __init__(self, services, *, mode="disabled", wait_seconds=180, poll_seconds=.05):
        # Reuse credential / execution-mode / binding validation, not its private
        # X-Native ingress. No second state store or dispatcher is introduced.
        self.core = Facade(services, mode=mode, wait_seconds=0)
        if len(services) != 1:
            raise ValueError("One trusted provider resource domain required")
        service = next(iter(services.values()))
        if type(service.owner) is not tuple or len(service.owner) != 2:
            raise ValueError("Explicit provider owner required")
        for part in service.owner:
            identifier(part)
        if (type(wait_seconds) not in (int, float) or not math.isfinite(wait_seconds)
                or not 0 <= wait_seconds <= 300 or type(poll_seconds) not in (int, float)
                or not math.isfinite(poll_seconds) or not .001 <= poll_seconds <= 1):
            raise ValueError("Invalid bounded provider observation timing")
        self.services = self.core.services
        self.wait_seconds, self.poll_seconds = wait_seconds, poll_seconds

    def handle(self, method, path, headers, payload):
        service = self.core.authenticate(headers)
        if method == "GET" and path == "/v1/models":
            bindings = self.core.validated_bindings(service, self.core.mode)
            models = sorted(bindings) if service.accepting_new else []
            return 200, {"object": "list", "data": [
                {"id": alias, "object": "model", "created": 0, "owned_by": "native-harness"}
                for alias in models]}, {}
        if method != "POST" or path != "/v1/chat/completions":
            raise ChannelError("NOT_FOUND", 404)
        request, stream = normalize_provider_request(payload)
        key = single_header(headers, "Idempotency-Key", required=False)
        if key is not None and (len(key) > 256 or any(ord(c) < 33 or ord(c) > 126 for c in key)):
            raise ChannelError("INVALID_IDEMPOTENCY_KEY", 400)
        resume = single_header(headers, "Last-Event-ID", required=False)
        if resume is not None and (resume != "0" or key is None or not stream):
            raise ChannelError("STREAM_RESUME_NOT_SUPPORTED", 400)
        operation = "provider-" + (hashlib.sha256(key.encode()).hexdigest() if key else uuid.uuid4().hex)
        identity = Identity(service.source, *service.owner, operation)
        request_digest = digest(request)
        task = service.bridge.lookup(identity.origin_key)
        if task is None:
            if resume is not None:
                raise ChannelError("TASK_NOT_FOUND", 404, accepted=False)
            bindings = self.core.validated_bindings(service, self.core.mode)
            if not service.accepting_new:
                raise ChannelError("NEW_ADMISSION_DISABLED", 503, accepted=False)
            binding = bindings.get(request["model"])
            if binding is None:
                raise ChannelError("MODEL_NOT_AVAILABLE", 400, accepted=False, param="model")
            frozen_binding = digest(binding.wire())
            try:
                task = service.bridge.submit(envelope(identity, request, binding, service.bridge.ledger_instance))
            except ChannelError as error:
                if error.accepted is False:
                    raise
                raise ChannelError("EXECUTION_UNCERTAIN", 409, accepted=True) from None
            checked_task(task, identity, request_digest, service.bridge.ledger_instance)
            if digest(task["request"]["binding"]) != frozen_binding:
                raise ChannelError("BRIDGE_BINDING_MISMATCH", 409, accepted=True)
        else:
            checked_task(task, identity, request_digest, service.bridge.ledger_instance)
            frozen_binding = digest(task["request"]["binding"])
        deadline = time.monotonic() + self.wait_seconds
        context = (service.bridge, identity, request_digest, frozen_binding, task["remote_task_id"])
        if stream:
            # Reserve the final error flush inside, never beyond, the budget.
            observe_deadline = deadline - min(.05, self.wait_seconds / 2)
            return 200, EventStream(self._stream(task, context, observe_deadline), deadline), {}
        try:
            while True:
                self._check(task, context)
                if task.get("result") or task.get("result_expired"):
                    return 200, completion(task), {}
                self._pause(task, deadline)
                task = service.bridge.task(context[4])
        except ChannelError as error:
            raise self._accepted_error(error) from None

    @staticmethod
    def _accepted_error(error):
        # Conflict is not an anti-retry mechanism: many SDKs retry HTTP 409.
        return ChannelError(error.code, 410 if error.status == 410 else 409,
                            accepted=True, param=error.param)

    @staticmethod
    def _check(task, context):
        bridge, identity, request_digest, frozen_binding, task_id = context
        checked_task(task, identity, request_digest, bridge.ledger_instance)
        if task["remote_task_id"] != task_id or digest(task["request"]["binding"]) != frozen_binding:
            raise ChannelError("BRIDGE_BINDING_MISMATCH", 409, accepted=True)

    def _pause(self, task, deadline):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            code = ("APPROVAL_PENDING" if task.get("liveness") == "waiting_approval" else
                    "EXECUTION_UNCERTAIN" if task.get("liveness") == "unknown" else "TASK_WAIT_TIMEOUT")
            raise ChannelError(code, 409, accepted=True)
        time.sleep(min(self.poll_seconds, remaining))

    def _stream(self, task, context, deadline):
        bridge, identity, request_digest, frozen_binding, task_id = context
        cursor, sent = 0, ""
        def chunk(delta, finish=None):
            return sse({"id": "chatcmpl-" + task_id, "object": "chat.completion.chunk",
                        "created": task["created"], "model": task["request"]["binding"]["model_alias"],
                        "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]})
        def suffix(text):
            nonlocal sent
            if not text.startswith(sent):
                raise ChannelError("NATIVE_STREAM_REWRITE_REQUIRED", 409, accepted=True)
            delta, sent = text[len(sent):], text
            return delta
        try:
            self._check(task, context)
            yield chunk({"role": "assistant"})
            while True:
                if task.get("result") or task.get("result_expired"):
                    final = completion(task)
                else:
                    page = project_events(bridge.events(task_id, cursor), task, identity,
                                          request_digest, frozen_binding, bridge, cursor)
                    final = None
                    for event in page["events"]:
                        kind, data = event["type"], event["data"]
                        if kind == "text_snapshot":
                            delta = suffix(data["text"])
                            if delta:
                                yield chunk({"content": delta})
                        elif kind == "completion":
                            final = data
                        elif kind == "task_error":
                            raise ChannelError(data["code"], data["status"], accepted=True)
                        else:
                            yield b": observing\n\n"
                    cursor = page["next_cursor"]
                    if final is None and page["has_more"]:
                        if time.monotonic() >= deadline:
                            raise ChannelError("TASK_WAIT_TIMEOUT", 409, accepted=True)
                        continue
                if final is not None:
                    message = final["choices"][0]["message"]
                    delta = suffix(message["content"] or "")
                    if delta:
                        yield chunk({"content": delta})
                    if message.get("refusal"):
                        yield chunk({"refusal": message["refusal"]})
                    yield chunk({}, "stop")
                    yield b"data: [DONE]\n\n"
                    return
                self._pause(task, deadline)
                yield b": observing\n\n"
                # Refresh liveness and validate the same task. Durable event
                # pages still own the sequence; no subsequent call can submit.
                task = bridge.task(task_id)
                self._check(task, context)
        except ChannelError as error:
            yield sse(self._accepted_error(error).body())
        except Exception:
            yield sse(ChannelError("OBSERVATION_UNAVAILABLE", 409, accepted=True).body())
