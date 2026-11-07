"""Real bounded HTTP transport for the proposed harness extension.

Current Render Bridge main does not implement this extension. Missing capability
negotiation is an error, never a switch to a local/fake ledger. No model clients.
"""
from __future__ import annotations
import ipaddress
import http.client
import re
import urllib.error
import urllib.parse
import urllib.request
from .contracts import CONTRACT, MAX_RESULT_BYTES, NodeOffer, canonical, identifier, parse_json
from .errors import ChannelError


def origin(value):
    parsed = urllib.parse.urlsplit(value)
    if (parsed.username or parsed.password or parsed.query or parsed.fragment
            or parsed.path not in {"", "/"} or not parsed.hostname):
        raise ValueError("Expected an origin, not a URL containing path or credentials")
    try:
        loopback = ipaddress.ip_address(parsed.hostname).is_loopback
    except ValueError:
        loopback = parsed.hostname == "localhost"
    if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
        raise ValueError("HTTPS required outside loopback")
    parsed.port  # reject malformed port
    return value.rstrip("/")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HTTPTransport:
    def __init__(self, base_url, token, *, timeout=5, max_response=MAX_RESULT_BYTES + 128 * 1024):
        self.base_url = origin(base_url)
        if (not isinstance(token, str) or not 16 <= len(token) <= 4096
                or any(ord(c) < 33 or ord(c) > 126 for c in token)):
            raise ValueError("An explicit service credential is required")
        if not 0 < timeout <= 60 or not 1 <= max_response <= 1024 * 1024:
            raise ValueError("Invalid transport limits")
        self.token, self.timeout, self.max_response = token, timeout, max_response
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, method, path, payload=None, *, extra_headers=None):
        if not path.startswith("/") or path.startswith("//") or "?" in path or "#" in path:
            raise ValueError("Invalid internal path")
        headers = {"Authorization": "Bearer " + self.token, "Accept": "application/json"}
        if extra_headers:
            for key, value in extra_headers.items():
                if key.lower() in {"authorization", "host", "content-length", "content-type"}:
                    raise ValueError("Protected HTTP header")
                if not isinstance(value, str) or any(ord(c) < 32 or ord(c) > 126 for c in value):
                    raise ValueError("Invalid HTTP header")
                headers[key] = value
        body = None if payload is None else canonical(payload).encode()
        if body is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            try:
                response = self.opener.open(req, timeout=self.timeout)
            except urllib.error.HTTPError as exc:
                response = exc
            with response:
                if 300 <= response.status < 400:
                    raise ChannelError("BRIDGE_REDIRECT_REJECTED")
                # Bounded read(n) does NOT enforce Content-Length in http.client.
                # A peer can send complete JSON then EOF before its declared length.
                # Validate framing independently before interpreting success/error JSON.
                lengths = response.headers.get_all("Content-Length", [])
                transfers = response.headers.get_all("Transfer-Encoding", [])
                declared = None
                # P1a non-streaming contract requires one explicit length.
                # Chunked/close-delimited responses are not supported in this
                # version; do not infer completeness merely from valid JSON.
                if (len(lengths) != 1 or transfers or not re.fullmatch(r"[0-9]{1,10}", lengths[0])):
                    raise ChannelError("BRIDGE_INVALID_FRAMING")
                declared = int(lengths[0])
                if declared > self.max_response:
                    raise ChannelError("BRIDGE_RESPONSE_TOO_LARGE")
                raw = response.read(self.max_response + 1)
                if len(raw) > self.max_response:
                    raise ChannelError("BRIDGE_RESPONSE_TOO_LARGE")
                if declared is not None and len(raw) != declared:
                    raise ChannelError("BRIDGE_TRANSPORT_UNCERTAIN", 504)
                if response.headers.get_content_type() != "application/json":
                    raise ChannelError("BRIDGE_INVALID_RESPONSE")
                try:
                    value = parse_json(raw, self.max_response)
                except ChannelError:
                    raise ChannelError("BRIDGE_INVALID_RESPONSE") from None
                if not 200 <= response.status < 300:
                    error = value.get("error") if isinstance(value, dict) else None
                    code = error.get("code") if isinstance(error, dict) else None
                    if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", code):
                        code = "BRIDGE_REQUEST_FAILED"
                    accepted = response.headers.get("X-Native-Accepted")
                    accepted = {"true": True, "false": False}.get(accepted)
                    raise ChannelError(code, response.status, accepted=accepted,
                                       param=error.get("param") if isinstance(error, dict) else None)
                return value
        except ChannelError:
            raise
        except (OSError, urllib.error.URLError, http.client.HTTPException, ValueError, TimeoutError):
            raise ChannelError("BRIDGE_TRANSPORT_UNCERTAIN", 504) from None


class BridgeClient:
    def __init__(self, base_url, token, ledger_instance, **transport_options):
        self.http = HTTPTransport(base_url, token, **transport_options)
        self.ledger_instance = identifier(ledger_instance)

    @staticmethod
    def key(value):
        return urllib.parse.quote(identifier(value), safe="")

    def capabilities(self):
        result = self.http.request("GET", "/bridge/v1/capabilities")
        if (not isinstance(result, dict) or result.get("harness_contract") != CONTRACT
                or result.get("ledger_instance") != self.ledger_instance):
            raise ChannelError("BRIDGE_HARNESS_EXTENSION_REQUIRED", 503, accepted=False)
        return result

    def lookup(self, origin_key):
        try:
            return self.http.request("GET", "/bridge/v1/tasks/by-origin/" + self.key(origin_key))
        except ChannelError as exc:
            if exc.status == 404 and exc.code == "not_found":
                return None
            raise

    def submit(self, payload):
        self.capabilities()
        return self.http.request("POST", "/bridge/v1/tasks", payload)

    def task(self, task_id):
        return self.http.request("GET", "/bridge/v1/tasks/" + self.key(task_id))


class NodeClient:
    def __init__(self, base_url, token, **transport_options):
        self.http = HTTPTransport(base_url, token, **transport_options)

    def next_task(self):
        value = self.http.request("GET", "/bridge/node/v1/tasks/next")
        if not isinstance(value, dict) or set(value) != {"task_id"}:
            raise ChannelError("BRIDGE_INVALID_OFFER")
        task_id = value["task_id"]
        if task_id is None:
            return None
        task_id = identifier(task_id, code="BRIDGE_INVALID_OFFER")
        task = self.http.request("GET", "/bridge/node/v1/tasks/" + BridgeClient.key(task_id))
        if not isinstance(task, dict) or task.get("remote_task_id") != task_id:
            raise ChannelError("BRIDGE_OFFER_ID_MISMATCH")
        return NodeOffer(task_id, task)

    def claim(self, task_id):
        return self.http.request("POST", f"/bridge/node/v1/tasks/{BridgeClient.key(task_id)}/claim", {"mode": "execute"})

    def start(self, task_id, claim_id):
        return self.http.request("POST", f"/bridge/node/v1/tasks/{BridgeClient.key(task_id)}/start", {"claim_id": claim_id})

    def observation(self, task_id, claim_id, grant_id, outcome, liveness, code):
        return self.http.request("POST", f"/bridge/node/v1/tasks/{BridgeClient.key(task_id)}/harness-observation",
                                 {"claim_id": claim_id, "start_grant_id": grant_id,
                                  "outcome": outcome, "liveness": liveness, "code": code})

    def result(self, task_id, claim_id, grant_id, result):
        receipt = self.http.request("POST", f"/bridge/node/v1/tasks/{BridgeClient.key(task_id)}/harness-result",
                                    {"claim_id": claim_id, "start_grant_id": grant_id, "result": result})
        if (not isinstance(receipt, dict) or receipt.get("schema_version") != CONTRACT
                or receipt.get("remote_task_id") != task_id or receipt.get("claim_id") != claim_id
                or receipt.get("start_grant_id") != grant_id or receipt.get("result") != result):
            raise ChannelError("BRIDGE_INVALID_RESULT_ACK")
        return receipt
