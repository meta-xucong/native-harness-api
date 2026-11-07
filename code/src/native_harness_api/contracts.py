"""Strict P1a wire types. The harness extension is NOT in Bridge main yet."""
from __future__ import annotations
from dataclasses import dataclass
import hashlib
import json
import re
from .errors import ChannelError

CONTRACT = "harness.invoke.v1"
MAX_REQUEST_BYTES = 64 * 1024
MAX_RESULT_BYTES = 256 * 1024
ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def identifier(value, *, code="INVALID_IDENTITY"):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ChannelError(code, 400)
    return value


def canonical(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError):
        raise ChannelError("INVALID_JSON", 400) from None


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def parse_json(raw: bytes, maximum=MAX_REQUEST_BYTES):
    if len(raw) > maximum:
        raise ChannelError("BODY_TOO_LARGE", 413)
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate")
            result[key] = value
        return result
    def invalid(_):
        raise ValueError("nonfinite")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
        canonical(value).encode("utf-8")  # also reject lone surrogates
    except (ValueError, UnicodeError, RecursionError, TypeError):
        raise ChannelError("INVALID_JSON", 400) from None
    return value


def normalize_request(value):
    if not isinstance(value, dict):
        raise ChannelError("INVALID_REQUEST", 400)
    extra = set(value) - {"model", "messages", "stream", "n", "user"}
    if extra:
        raise ChannelError("UNSUPPORTED_PARAMETER", 400, param=sorted(extra)[0])
    model = identifier(value.get("model"), code="INVALID_MODEL")
    if "stream" in value and type(value["stream"]) is not bool:
        raise ChannelError("INVALID_STREAM", 400)
    if value.get("stream", False):
        raise ChannelError("STREAM_NOT_SUPPORTED", 400)
    if type(value.get("n", 1)) is not int or value.get("n", 1) != 1:
        raise ChannelError("UNSUPPORTED_N", 400)
    if "user" in value and (not isinstance(value["user"], str) or len(value["user"]) > 256):
        raise ChannelError("INVALID_USER_TAG", 400)
    messages = value.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 64:
        raise ChannelError("INVALID_MESSAGES", 400)
    normalized = []
    for item in messages:
        if not isinstance(item, dict) or set(item) != {"role", "content"}:
            raise ChannelError("INVALID_MESSAGE", 400)
        if not isinstance(item["role"], str) or item["role"] not in {"user", "assistant"}:
            raise ChannelError("ROLE_NOT_SUPPORTED", 400)
        if not isinstance(item["content"], str) or not item["content"].strip():
            raise ChannelError("INVALID_CONTENT", 400)
        normalized.append({"role": item["role"], "content": item["content"]})
    if normalized[-1]["role"] != "user":
        raise ChannelError("LAST_MESSAGE_MUST_BE_USER", 400)
    result = {"model": model, "messages": normalized, "stream": False, "n": 1}
    if len(canonical(result).encode()) > MAX_REQUEST_BYTES:
        raise ChannelError("BODY_TOO_LARGE", 413)
    return result  # user is ignored and is not part of execution semantics/digest


@dataclass(frozen=True)
class Identity:
    source: str
    tenant: str
    principal: str
    operation: str

    def __post_init__(self):
        for value in (self.source, self.tenant, self.principal, self.operation):
            identifier(value)

    def wire(self):
        return {"source": self.source, "tenant": self.tenant,
                "principal": self.principal, "operation": self.operation}

    @property
    def origin_key(self):
        return "h_" + digest(self.wire())


@dataclass(frozen=True)
class Binding:
    binding_id: str
    revision: int
    model_alias: str
    native_model: str

    def __post_init__(self):
        for value in (self.binding_id, self.model_alias, self.native_model):
            identifier(value)
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("Invalid binding revision")

    def wire(self):
        return {"binding_id": self.binding_id, "revision": self.revision,
                "model_alias": self.model_alias, "native_model": self.native_model}


@dataclass(frozen=True)
class NodeOffer:
    """The requested offer ID plus its fetched body; Node must verify both."""
    task_id: str
    task: dict


def envelope(identity, request, binding, ledger_instance):
    return {"schema_version": CONTRACT, "origin_key": identity.origin_key,
            "identity": identity.wire(), "request_digest": digest(request),
            "ledger_instance": identifier(ledger_instance),
            "request": {"binding": binding.wire(), "messages": request["messages"],
                        "client_request": request}}


def checked_task(task, identity, request_digest, ledger_instance):
    if not isinstance(task, dict) or task.get("schema_version") != CONTRACT:
        raise ChannelError("BRIDGE_CONTRACT_MISMATCH")
    if (task.get("ledger_instance") != ledger_instance or task.get("identity") != identity.wire()
            or task.get("origin_key") != identity.origin_key):
        raise ChannelError("BRIDGE_IDENTITY_MISMATCH")
    if task.get("request_digest") != request_digest:
        raise ChannelError("IDEMPOTENCY_CONFLICT", 409)
    identifier(task.get("remote_task_id"), code="BRIDGE_INVALID_TASK")
    if type(task.get("created")) is not int or task["created"] < 0:
        raise ChannelError("BRIDGE_INVALID_TASK")
    try:
        frozen = task["request"]
        client = normalize_request(frozen["client_request"])
        binding = Binding(**frozen["binding"])
        if (digest(client) != request_digest or frozen["messages"] != client["messages"]
                or binding.model_alias != client["model"]):
            raise ValueError()
    except (KeyError, TypeError, ValueError, ChannelError):
        raise ChannelError("BRIDGE_INVALID_TASK") from None
    return task


def completion(task):
    result = task.get("result")
    if task.get("result_expired"):
        raise ChannelError("RESULT_EXPIRED", 410, accepted=True)
    if not result:
        raise ChannelError("TASK_WAIT_TIMEOUT", 504, accepted=True)
    if (not isinstance(result, dict) or not isinstance(result.get("outcome"), str)
            or result["outcome"] not in {"succeeded", "failed", "cancelled"}):
        raise ChannelError("NATIVE_INVALID_RESULT", accepted=True)
    if result["outcome"] == "failed":
        if result.get("code") == "TASK_EXPIRED":
            raise ChannelError("TASK_EXPIRED", 410, accepted=True)
        raise ChannelError("NATIVE_EMPTY_RESULT" if result.get("code") == "NATIVE_EMPTY_RESULT" else
                           "NATIVE_MODEL_MISMATCH" if result.get("code") == "MODEL_MISMATCH" else "NATIVE_FAILED", accepted=True)
    if result["outcome"] == "cancelled":
        raise ChannelError("TASK_CANCELLED", 409, accepted=True)
    if (not isinstance(result.get("native_completion"), dict)
            or set(result["native_completion"]) != {"confirmed"}
            or result["native_completion"]["confirmed"] is not True):
        raise ChannelError("NATIVE_UNCONFIRMED_RESULT", accepted=True)
    try:
        binding = task["request"]["binding"]
        evidence = result["model_evidence"]
        if evidence.get("selected") != binding["native_model"] or evidence.get("reported") != binding["native_model"]:
            raise ChannelError("NATIVE_MODEL_MISMATCH", accepted=True)
        blocks = result["output_blocks"]
        if not isinstance(blocks, list) or not blocks or len(blocks) > 32:
            raise ChannelError("NATIVE_EMPTY_RESULT", accepted=True)
        texts, refusals = [], []
        for block in blocks:
            if (not isinstance(block, dict) or set(block) != {"type", "text"}
                    or not isinstance(block["type"], str) or block["type"] not in {"text", "refusal"}
                    or not isinstance(block["text"], str) or not block["text"].strip()):
                raise ChannelError("NATIVE_INVALID_RESULT", accepted=True)
            (texts if block["type"] == "text" else refusals).append(block["text"])
        message = {"role": "assistant", "content": "".join(texts) or None}
        if refusals:
            message["refusal"] = "".join(refusals)
        response = {"id": "chatcmpl-" + task["remote_task_id"], "object": "chat.completion",
                    "created": task["created"], "model": binding["model_alias"],
                    "choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}
        usage = result.get("usage")
        if usage != {"state": "unknown"}:
            raise ChannelError("USAGE_NOT_SUPPORTED", accepted=True)  # measured usage is a later capability
        if len(canonical(response).encode()) > MAX_RESULT_BYTES:
            raise ChannelError("RESULT_TOO_LARGE", accepted=True)
        return response
    except (KeyError, TypeError, AttributeError, UnicodeError):
        raise ChannelError("NATIVE_INVALID_RESULT", accepted=True) from None
