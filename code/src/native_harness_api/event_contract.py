"""Validate durable Bridge pages; native status is never a success assertion."""
import re
from .contracts import (MAX_RESULT_BYTES, MAX_RESULT_WIRE_BYTES, canonical,
                        checked_task, completion, digest)
from .errors import ChannelError

SCHEMA = "native-events-v1"
MAX_SEQUENCE = 9007199254740991


def parse_cursor(value):
    if (not isinstance(value, str) or not re.fullmatch(r"0|[1-9][0-9]{0,15}", value)
            or int(value) > MAX_SEQUENCE):
        raise ChannelError("INVALID_EVENT_CURSOR", 400)
    return int(value)


def _integer(value):
    return type(value) is int and 0 <= value <= MAX_SEQUENCE


def _status(data):
    if (not isinstance(data, dict) or not {"outcome", "liveness"} <= set(data)
            or not set(data) <= {"outcome", "liveness", "code"}):
        return False
    return all(isinstance(v, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}", v)
               for v in data.values())


def project_events(page, task, identity, request_digest, frozen_binding, bridge, cursor):
    def invalid():
        raise ChannelError("BRIDGE_INVALID_EVENTS")
    if (not isinstance(page, dict) or set(page) != {"schema_version", "remote_task_id", "events",
                                                   "next_cursor", "has_more", "terminal_seq"}
            or page["schema_version"] != SCHEMA or page["remote_task_id"] != task["remote_task_id"]
            or not _integer(page["next_cursor"]) or page["next_cursor"] < cursor
            or type(page["has_more"]) is not bool or not isinstance(page["events"], list)
            or len(page["events"]) > 8):
        invalid()
    terminal = page["terminal_seq"]
    if terminal is not None and (not _integer(terminal) or terminal == 0 or terminal < page["next_cursor"]):
        invalid()
    events, seen = [], cursor
    for event in page["events"]:
        if (not isinstance(event, dict) or set(event) != {"seq", "type", "data"}
                or not _integer(event["seq"]) or event["seq"] != seen + 1
                or not isinstance(event["data"], dict)):
            invalid()
        seen = event["seq"]
        kind, data = event["type"], event["data"]
        if kind == "text_snapshot":
            if set(data) != {"text"} or not isinstance(data["text"], str) or len(data["text"].encode()) > MAX_RESULT_BYTES:
                invalid()
        elif kind == "native_status":
            if (not {"state"} <= set(data) or not set(data) <= {"state", "stop_reason"}
                    or not isinstance(data["state"], str) or data["state"] not in {"running", "requires_action", "idle", "unknown"}):
                invalid()
            if "stop_reason" in data:
                if (not isinstance(data["stop_reason"], str)
                        or data["stop_reason"] not in {"end_turn", "refusal", "cancelled", "_codebuddy.ai/error"}):
                    invalid()
        elif kind == "task_status":
            if not _status(data):
                invalid()
        elif kind == "task_terminal":
            if terminal != seen or not _status(data):
                invalid()
            final = bridge.task(task["remote_task_id"])
            checked_task(final, identity, request_digest, bridge.ledger_instance)
            if (final["remote_task_id"] != task["remote_task_id"]
                    or digest(final["request"]["binding"]) != frozen_binding):
                invalid()
            # Only a durable terminal result (including an explicitly expired
            # result) may be projected. Transport/validation errors are NOT task
            # failure events and never advance the caller's cursor.
            if not final.get("result") and not final.get("result_expired"):
                invalid()
            try:
                data, kind = completion(final), "completion"
            except ChannelError as exc:
                if exc.code not in {"RESULT_EXPIRED", "TASK_EXPIRED", "TASK_CANCELLED", "NATIVE_FAILED",
                                    "NATIVE_EMPTY_RESULT", "NATIVE_MODEL_MISMATCH"}:
                    raise
                data, kind = {"code": exc.code, "status": exc.status}, "task_error"
        else:
            invalid()
        if terminal == seen and kind not in {"completion", "task_error"}:
            invalid()
        events.append({"seq": seen, "type": kind, "data": data})
    if (seen != page["next_cursor"] or (page["has_more"] and not events)
            or (terminal == seen and page["has_more"])
            or (terminal is not None and seen < terminal and not page["has_more"])):
        invalid()
    result = {**page, "events": events}
    if len(canonical(result).encode()) > MAX_RESULT_WIRE_BYTES:
        invalid()
    return result
