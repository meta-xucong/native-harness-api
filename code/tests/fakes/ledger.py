"""TEST ONLY SQLite contract fixture, NOT the production Render Bridge."""
from __future__ import annotations
from contextlib import contextmanager
import sqlite3
import time
import uuid
from native_harness_api.contracts import CONTRACT, Identity, canonical, digest, identifier
from native_harness_api.errors import ChannelError
from native_harness_api.http_server import bearer

SOURCE_TOKEN = "test-only-source-credential"
NODE_TOKEN = "test-only-node-credential"
SAFE = {"not_started", "stopped_confirmed"}
LIVE = SAFE | {"may_be_running", "running", "waiting_approval", "unknown"}


class FakeLedger:
    max_body_bytes = 384 * 1024
    def __init__(self, path, *, instance="fixture-ledger", source="fixture-source"):
        self.path, self.instance, self.source = str(path), instance, source
        self.online, self.accepting_new, self.fail_create_response = True, True, False
        self.fail_result_response = False
        with self.connection() as con:
            con.execute("CREATE TABLE IF NOT EXISTS tasks (id TEXT PRIMARY KEY, origin TEXT UNIQUE NOT NULL, payload TEXT NOT NULL)")

    @contextmanager
    def connection(self):
        con = sqlite3.connect(self.path, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA synchronous=FULL")
        try:
            con.execute("BEGIN IMMEDIATE")
            yield con
            con.commit()
        except BaseException:
            con.rollback()
            raise
        finally:
            con.close()

    def _read(self, row):
        import json
        return json.loads(row["payload"]) if row else None

    def _get(self, con, task_id):
        task = self._read(con.execute("SELECT payload FROM tasks WHERE id=?", (task_id,)).fetchone())
        if task is None:
            raise ChannelError("not_found", 404)
        return task

    def _save(self, con, task):
        con.execute("UPDATE tasks SET payload=? WHERE id=?", (canonical(task), task["remote_task_id"]))

    def all_tasks(self):
        with self.connection() as con:
            return [self._read(row) for row in con.execute("SELECT payload FROM tasks")]

    def expire_result(self, task_id):
        with self.connection() as con:
            task = self._get(con, task_id)
            if task["outcome"] == "pending":
                raise ValueError("Cannot expire unresolved task")
            task["result"] = None
            task["result_expired"] = True
            self._save(con, task)

    def handle(self, method, path, headers, payload):
        token = bearer(headers)
        is_node = path.startswith("/bridge/node/")
        if token != (NODE_TOKEN if is_node else SOURCE_TOKEN):
            raise ChannelError("unauthorized", 401)
        result = self.node(method, path, payload) if is_node else self.source_api(method, path, payload)
        return 200, result, {}

    def source_api(self, method, path, payload):
        if path == "/bridge/v1/capabilities" and method == "GET":
            return {"harness_contract": CONTRACT, "ledger_instance": self.instance, "test_fixture": True}
        if method == "GET" and path.startswith("/bridge/v1/tasks/by-origin/"):
            key = path.rsplit("/", 1)[1]
            with self.connection() as con:
                value = self._read(con.execute("SELECT payload FROM tasks WHERE origin=?", (key,)).fetchone())
                if value is None:
                    raise ChannelError("not_found", 404)
                return value
        if method == "GET" and path.startswith("/bridge/v1/tasks/"):
            with self.connection() as con:
                return self._get(con, path.rsplit("/", 1)[1])
        if method != "POST" or path != "/bridge/v1/tasks":
            raise ChannelError("not_found", 404)
        identity = Identity(**payload["identity"])
        if (identity.source != self.source or payload["schema_version"] != CONTRACT
                or payload["ledger_instance"] != self.instance or payload["origin_key"] != identity.origin_key
                or digest(payload["request"]["client_request"]) != payload["request_digest"]):
            raise ChannelError("invalid_request", 400, accepted=False)
        with self.connection() as con:
            old = self._read(con.execute("SELECT payload FROM tasks WHERE origin=?", (identity.origin_key,)).fetchone())
            if old:
                if old["request_digest"] != payload["request_digest"]:
                    raise ChannelError("IDEMPOTENCY_CONFLICT", 409, accepted=True)
                return old
            if not self.online:
                raise ChannelError("NODE_UNAVAILABLE", 503, accepted=False)
            if not self.accepting_new:
                raise ChannelError("NEW_ADMISSION_DISABLED", 503, accepted=False)
            for row in con.execute("SELECT payload FROM tasks"):
                other = self._read(row)
                if other["outcome"] == "pending" or other["liveness"] not in SAFE:
                    raise ChannelError("CAPACITY_EXHAUSTED", 429, accepted=False)
            task = dict(payload, remote_task_id="task-" + uuid.uuid4().hex,
                        attempt_id="attempt-" + uuid.uuid4().hex, created=int(time.time()),
                        outcome="pending", liveness="not_started", result=None,
                        claim_id=None, epoch=0, start_grant_id=None, result_expired=False)
            con.execute("INSERT INTO tasks VALUES(?,?,?)", (task["remote_task_id"], identity.origin_key, canonical(task)))
        if self.fail_create_response:
            self.fail_create_response = False
            raise ChannelError("BRIDGE_TRANSPORT_UNCERTAIN", 504)
        return task

    def node(self, method, path, payload):
        prefix = "/bridge/node/v1/tasks/"
        if not path.startswith(prefix):
            raise ChannelError("not_found", 404)
        parts = path[len(prefix):].split("/")
        with self.connection() as con:
            if parts == ["next"] and method == "GET":
                for row in con.execute("SELECT payload FROM tasks ORDER BY rowid"):
                    task = self._read(row)
                    if task["outcome"] == "pending" and task["start_grant_id"] is None:
                        return {"task_id": task["remote_task_id"]}
                return {"task_id": None}
            task = self._get(con, parts[0])
            if len(parts) == 1 and method == "GET":
                return task
            if method != "POST" or len(parts) != 2:
                raise ChannelError("not_found", 404)
            action = parts[1]
            if action == "claim":
                if task["start_grant_id"] or task["outcome"] != "pending":
                    raise ChannelError("execution_already_started", 409)
                if not task["claim_id"]:
                    task["claim_id"], task["epoch"] = "claim-" + uuid.uuid4().hex, task["epoch"] + 1
                self._save(con, task)
                return {"claim_id": task["claim_id"], "epoch": task["epoch"], "attempt_id": task["attempt_id"]}
            if payload.get("claim_id") != task["claim_id"] or task["claim_id"] is None:
                raise ChannelError("stale_claim", 409)
            if action == "start":
                if task["start_grant_id"]:
                    return {"start_grant_id": task["start_grant_id"], "attempt_id": task["attempt_id"], "may_execute": False}
                if task["outcome"] != "pending":
                    raise ChannelError("task_not_executable", 409)
                task["start_grant_id"], task["liveness"] = "grant-" + uuid.uuid4().hex, "may_be_running"
                self._save(con, task)
                return {"start_grant_id": task["start_grant_id"], "attempt_id": task["attempt_id"], "may_execute": True}
            if payload.get("start_grant_id") != task["start_grant_id"] or task["start_grant_id"] is None:
                raise ChannelError("stale_grant", 409)
            if action == "harness-observation":
                if payload["outcome"] != "pending" or payload["liveness"] not in LIVE:
                    raise ChannelError("invalid_observation", 400)
                # Observation is not a result transition. Terminal result is immutable.
                task["liveness"] = payload["liveness"]
                self._save(con, task)
                return task
            if action != "harness-result":
                raise ChannelError("not_found", 404)
            value = payload["result"]
            if value.get("outcome") not in {"succeeded", "failed", "cancelled"} or value.get("liveness") not in LIVE:
                raise ChannelError("invalid_result", 400)
            if task["result"] is not None:
                if task["result"] != value:
                    raise ChannelError("TERMINAL_CONFLICT", 409)
                return task
            if task["result_expired"] or task["outcome"] != "pending":
                raise ChannelError("TERMINAL_CONFLICT", 409)
            task["result"], task["outcome"], task["liveness"] = value, value["outcome"], value["liveness"]
            self._save(con, task)
        if self.fail_result_response:
            self.fail_result_response = False
            raise ChannelError("BRIDGE_TRANSPORT_UNCERTAIN", 504)
        return task
