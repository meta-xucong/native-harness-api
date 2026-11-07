"""TEST ONLY persistent route binding, NOT a Sub2API implementation."""
from contextlib import contextmanager
import json
import sqlite3
import uuid
from native_harness_api.bridge_client import HTTPTransport
from native_harness_api.contracts import canonical, digest, normalize_request, identifier
from native_harness_api.errors import ChannelError
from native_harness_api.http_server import bearer, single_header

USER_TOKEN = "test-only-user-credential"


class FakeGateway:
    def __init__(self, path, routes, selected_route, *, tenant="tenant-1", principal="key-1"):
        self.path, self.routes, self.selected_route = str(path), routes, selected_route
        self.tenant, self.principal = tenant, principal
        self.allow_new = True
        self.outbound_attempts = 0
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS operations (scope TEXT PRIMARY KEY, digest TEXT NOT NULL, operation TEXT NOT NULL, route TEXT NOT NULL, acceptance TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def handle(self, method, path, headers, payload):
        if bearer(headers) != USER_TOKEN:
            raise ChannelError("UNAUTHORIZED", 401)
        if method != "POST" or path != "/v1/chat/completions":
            raise ChannelError("NOT_FOUND", 404)
        request = normalize_request(payload)
        key = single_header(headers, "Idempotency-Key", required=False) or "unkeyed-" + uuid.uuid4().hex
        identifier(key)
        scope = canonical([self.tenant, self.principal, key])
        request_digest = digest(request)
        with self.connection() as con:
            row = con.execute("SELECT * FROM operations WHERE scope=?", (scope,)).fetchone()
            if row:
                if row["digest"] != request_digest:
                    raise ChannelError("IDEMPOTENCY_CONFLICT", 409)
                if row["acceptance"] == "not_accepted" and not self.allow_new:
                    raise ChannelError("EXECUTION_PERMISSION_REVOKED", 403, accepted=False)
                operation, route = row["operation"], row["route"]
            else:
                if not self.allow_new:
                    raise ChannelError("EXECUTION_PERMISSION_REVOKED", 403, accepted=False)
                operation, route_id = "op-" + uuid.uuid4().hex, self.selected_route
                if route_id not in self.routes:
                    raise ChannelError("ROUTE_UNAVAILABLE", 503, accepted=False)
                route = canonical(self.routes[route_id])
                con.execute("INSERT INTO operations VALUES(?,?,?,?,?)", (scope, request_digest, operation, route, "unknown"))
        config = json.loads(route)
        self.outbound_attempts += 1
        transport = HTTPTransport(config["url"], config["token"], timeout=3)
        accepted = "unknown"
        try:
            # Incoming internal headers and user tag are deliberately not copied.
            value = transport.request("POST", "/v1/chat/completions", request, extra_headers={
                "X-Native-Tenant": self.tenant, "X-Native-Principal": self.principal,
                "X-Native-Operation": operation,
                "X-Native-Admission-Allowed": str(self.allow_new).lower(),
                "X-Native-Binding-Digest": config["binding_digest"]})
            accepted = "accepted"
            return 200, value, {}
        except ChannelError as exc:
            accepted = {True: "accepted", False: "not_accepted", None: "unknown"}[exc.accepted]
            raise
        finally:
            with self.connection() as con:
                # Never downgrade known acceptance on a later transport failure.
                old = con.execute("SELECT acceptance FROM operations WHERE scope=?", (scope,)).fetchone()[0]
                if old == "accepted":
                    accepted = old
                con.execute("UPDATE operations SET acceptance=? WHERE scope=?", (accepted, scope))
