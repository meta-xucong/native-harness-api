"""Real Render Bridge/Facade/Node sockets, synthetic executor only.

Run explicitly with the checked-out Bridge src on PYTHONPATH and its declared
dependencies installed. Import failure is NOT a skipped or passed integration.
No tests.fakes gateway/ledger is imported or started here.
"""
from contextlib import contextmanager, ExitStack
from dataclasses import replace
from pathlib import Path
import socket
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest

import uvicorn
from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.harness import HarnessConfig, HarnessBinding
from render_bridge.node_api import NodeScope

from native_harness_api.adapters import FakeHarness
from native_harness_api.bridge_client import BridgeClient, NodeClient, HTTPTransport
from native_harness_api.contracts import Binding, digest
from native_harness_api.errors import ChannelError
from native_harness_api.facade import Facade, Service
from native_harness_api.http_server import make_server
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.runtime import node_tick

SOURCE_TOKEN = "test-only-p1b-source-token"
NODE_TOKEN = "test-only-p1b-node-token"
FACADE_TOKEN = "test-only-p1b-facade-token"


@contextmanager
def bridge_server(app):
    with socket.socket() as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False, lifespan="off"))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline:
                time.sleep(0.01)
            if not server.started:
                raise RuntimeError("Real Bridge HTTP server failed to start")
            yield f"http://127.0.0.1:{sock.getsockname()[1]}"
        finally:
            server.should_exit = True
            thread.join(5)
            if thread.is_alive():
                raise RuntimeError("Real Bridge HTTP server did not stop")


@contextmanager
def facade_server(facade):
    with make_server(facade) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()
            thread.join(5)


class RealBridgeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.binding = Binding("fake-basic", 1, "synthetic-basic", "fake-model")
        self.harness_config = HarnessConfig(source_id="fixture-source", workspace_id="fixture-workspace",
            ledger_instance="fixture-ledger", node_id="fixture-node", executor_instance="fixture-executor",
            allowed_owners=(("tenant-1", "key-1"),), bindings=(HarnessBinding(**self.binding.wire()),), accepting_new=True)
        self.store = BridgeStore(self.root / "bridge.sqlite", self.root / "artifacts",
                                dispatch_mode="node_pull", harness=self.harness_config)
        self.app = create_app(store=self.store,
            source_tokens={SOURCE_TOKEN: SourceScope("fixture-source", "fixture-workspace")},
            node_tokens={NODE_TOKEN: NodeScope("fixture-source", "fixture-workspace", "fixture-node", "fixture-executor")})
        self.bridge_url = self.stack.enter_context(bridge_server(self.app))
        self.bridge = BridgeClient(self.bridge_url, SOURCE_TOKEN, "fixture-ledger")
        self.service = Service("fixture-source", self.bridge, {self.binding.model_alias: self.binding}, True)
        self.facade = Facade({FACADE_TOKEN: self.service}, wait_seconds=0, mode="p1b_integration")
        self.facade_url = self.stack.enter_context(facade_server(self.facade))
        self.http = HTTPTransport(self.facade_url, FACADE_TOKEN)
        self.fake = FakeHarness(self.root / "synthetic-native")
        self.runner = Runner(NodeClient(self.bridge_url, NODE_TOKEN), Journal(self.root / "journal"), self.fake,
            policy=NodePolicy("fixture-source", "fixture-ledger", frozenset({("tenant-1", "key-1")}), (self.binding,)))
        node_tick(self.runner)

    def send(self, operation="operation-1", *, message="hello", admission=True, tenant="tenant-1"):
        return self.http.request("POST", "/v1/chat/completions",
            {"model": "synthetic-basic", "messages": [{"role": "user", "content": message}]}, extra_headers={
                "X-Native-Tenant": tenant, "X-Native-Principal": "key-1", "X-Native-Operation": operation,
                "X-Native-Source": "fixture-source", "X-Native-Ledger": "fixture-ledger",
                "X-Native-Binding-Digest": digest(self.binding.wire()),
                "X-Native-Admission-Allowed": str(admission).lower()})

    def expect(self, code, call):
        with self.assertRaises(ChannelError) as caught:
            call()
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def queue(self, **kwargs):
        return self.expect("TASK_WAIT_TIMEOUT", lambda: self.send(**kwargs))

    def test_actual_bridge_socket_success_and_replay_without_usage(self):
        self.assertEqual(self.bridge.capabilities()["harness_contract"], "harness.invoke.v1")
        self.queue()
        self.assertEqual(node_tick(self.runner)["outcome"], "succeeded")
        value = self.send()
        self.assertEqual(value["choices"][0]["message"]["content"], "Synthetic FakeHarness result")
        self.assertNotIn("usage", value)
        self.assertEqual(self.send(), value)
        self.assertEqual(node_tick(self.runner), {"outcome": "idle"})
        self.assertEqual(self.fake.start_count, 1)

    def test_same_operation_changed_body_is_not_new_execution(self):
        self.queue()
        self.expect("IDEMPOTENCY_CONFLICT", lambda: self.send(message="different"))
        node_tick(self.runner)
        self.expect("IDEMPOTENCY_CONFLICT", lambda: self.send(message="different"))
        self.assertEqual(self.fake.start_count, 1)

    def test_prestart_expiry_retains_tombstone_and_never_respawns(self):
        self.queue()
        now = self.store.clock()
        self.store.clock = lambda: now + 3601
        self.expect("TASK_EXPIRED", self.send)
        self.assertEqual(node_tick(self.runner), {"outcome": "idle"})
        self.expect("TASK_EXPIRED", self.send)
        self.assertEqual(self.fake.start_count, 0)

    def test_result_replays_with_admission_disabled(self):
        self.queue()
        node_tick(self.runner)
        value = self.send()
        self.facade.services[FACADE_TOKEN] = replace(self.service, accepting_new=False, bindings={})
        self.store.accepting_new_tasks = False
        self.assertEqual(self.send(admission=False), value)
        self.expect("NEW_ADMISSION_DISABLED", lambda: self.send(operation="operation-2"))
        self.assertEqual(self.fake.start_count, 1)

    def test_unknown_liveness_is_not_released_by_terminal_response(self):
        self.runner.adapter = self.fake = FakeHarness(self.root / "synthetic-native", mode="unknown_liveness")
        self.queue()
        self.assertEqual(node_tick(self.runner)["liveness"], "unknown")
        self.send()
        self.assertEqual(node_tick(self.runner)["code"], "SLOT_QUARANTINED")
        # Admission may queue or reject, but neither can produce another start.
        with self.assertRaises(ChannelError):
            self.send(operation="operation-2")
        self.assertEqual(node_tick(self.runner)["code"], "SLOT_QUARANTINED")
        self.assertEqual(self.fake.start_count, 1)

    def test_unapproved_owner_never_creates_execution(self):
        with self.assertRaises(ChannelError) as caught:
            self.send(tenant="not-the-test-tenant")
        self.assertIn(caught.exception.status, {400, 403, 404})
        self.assertEqual(node_tick(self.runner), {"outcome": "idle"})
        self.assertEqual(self.fake.start_count, 0)

    def test_node_reconstructed_with_same_durable_journal_does_not_restart(self):
        self.queue()
        node_tick(self.runner)
        before = self.send()
        restored = Runner(NodeClient(self.bridge_url, NODE_TOKEN), Journal(self.root / "journal"),
                          FakeHarness(self.root / "synthetic-native"), policy=self.runner.policy)
        self.assertEqual(node_tick(restored), {"outcome": "idle"})
        self.assertEqual(self.send(), before)
        self.assertEqual(restored.adapter.start_count, 1)

    def test_actual_node_cli_process_uses_real_bridge_and_same_journal(self):
        self.queue()
        path = self.root / "node-config.json"
        path.write_text(json.dumps({"mode": "p1b_integration", "adapter": "fake",
            "source": "fixture-source", "ledger_instance": "fixture-ledger", "bridge_url": self.bridge_url,
            "binding": self.binding.wire(), "owner": ["tenant-1", "key-1"], "data_dir": str(self.root)}))
        env = dict(os.environ, NATIVE_BRIDGE_NODE_TOKEN=NODE_TOKEN)
        command = [sys.executable, "-m", "native_harness_api.runtime", "node", "--config", str(path), "--once"]
        first = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(json.loads(first.stdout), {"outcome": "succeeded", "liveness": "stopped_confirmed"})
        second = subprocess.run(command, env=env, capture_output=True, text=True, timeout=15)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(second.stdout), {"outcome": "idle"})
        self.assertEqual(self.fake.start_count, 1)

    def test_lost_real_start_ack_is_quarantined_without_execution(self):
        self.queue()
        client = self.runner.client
        real_start = client.start
        def lost_start(*args):
            real_start(*args)  # The real Bridge durably grants before ACK loss.
            raise TimeoutError("Injected response loss")
        client.start = lost_start
        self.assertEqual(node_tick(self.runner)["liveness"], "unknown")
        self.assertEqual(node_tick(self.runner)["liveness"], "unknown")
        self.assertEqual(self.fake.start_count, 0)
        self.assertIsNone(NodeClient(self.bridge_url, NODE_TOKEN).next_task())

    def test_lost_real_result_ack_reuploads_but_never_respawns(self):
        self.queue()
        client = self.runner.client
        real_result = client.result
        def lost_result(*args):
            real_result(*args)
            raise TimeoutError("Injected response loss")
        client.result = lost_result
        self.assertEqual(node_tick(self.runner)["code"], "RESULT_UPLOAD_PENDING")
        value = self.send()
        client.result = real_result
        self.assertEqual(node_tick(self.runner)["outcome"], "succeeded")
        self.assertEqual(self.send(), value)
        self.assertEqual(self.fake.start_count, 1)

    def test_removed_binding_cannot_block_real_result_ack_recovery(self):
        self.queue()
        client = self.runner.client
        real_result = client.result
        def lost_result(*args):
            real_result(*args)
            raise TimeoutError("Injected response loss")
        client.result = lost_result
        self.assertEqual(node_tick(self.runner)["code"], "RESULT_UPLOAD_PENDING")
        self.store.harness = replace(self.store.harness, bindings=(), accepting_new=False)
        client.result = real_result
        self.assertEqual(node_tick(self.runner)["outcome"], "succeeded")
        self.assertTrue(self.runner.journal.records()[0]["acknowledged"])
        self.assertEqual(self.send()["choices"][0]["message"]["content"], "Synthetic FakeHarness result")
        self.assertEqual(self.fake.start_count, 1)

    def test_recovery_only_pass_cannot_claim_a_queued_task(self):
        self.queue()
        self.assertEqual(self.runner.run_once(allow_new=False), {"outcome": "idle"})
        self.assertEqual(self.runner.journal.records(), [])
        self.assertEqual(self.fake.start_count, 0)
        self.assertEqual(node_tick(self.runner)["outcome"], "succeeded")
        self.assertEqual(self.fake.start_count, 1)

    def test_concurrent_observer_does_not_reject_unconsumed_start(self):
        self.queue()
        original_start, original_get = self.runner.client.start, self.store.get_task
        entered, release = threading.Event(), threading.Event()
        read_errors = []
        def contended_start(task_id, claim_id):
            def slow_read(*args):
                entered.set()
                if not release.wait(3):
                    raise RuntimeError("Test observer not released")
                return original_get(*args)
            self.store.get_task = slow_read
            def read():
                try:
                    self.bridge.task(task_id)
                except Exception as exc:
                    read_errors.append(type(exc).__name__)
            reader = threading.Thread(target=read)
            reader.start()
            timer = threading.Timer(0.1, release.set)
            try:
                self.assertTrue(entered.wait(3))
                timer.start()
                return original_start(task_id, claim_id)
            finally:
                release.set()
                reader.join(5)
                timer.cancel()
                self.store.get_task = original_get
        self.runner.client.start = contended_start
        self.assertEqual(node_tick(self.runner)["outcome"], "succeeded")
        self.assertEqual(read_errors, [])
        self.assertEqual(self.fake.start_count, 1)

    def test_late_spawn_intent_holds_real_bridge_slot(self):
        self.queue()
        entered, release = threading.Event(), threading.Event()
        outcomes = []
        def barrier():
            entered.set()
            if not release.wait(5):
                raise RuntimeError("Test did not release old consumer")
        self.runner.before_start = barrier
        def run():
            try:
                outcomes.append(node_tick(self.runner))
            except Exception as exc:
                outcomes.append(exc)
        thread = threading.Thread(target=run)
        thread.start()
        try:
            self.assertTrue(entered.wait(5))
            self.assertEqual(self.fake.start_count, 0)
            # A different local journal is not a license to bypass the remote
            # grant latch; the old consumer can still spawn after this read.
            other = Runner(NodeClient(self.bridge_url, NODE_TOKEN), Journal(self.root / "other-journal"),
                           self.fake, policy=self.runner.policy)
            self.assertEqual(other.run_once(), {"outcome": "idle"})
            self.assertEqual(self.fake.start_count, 0)
        finally:
            release.set()
            thread.join(5)
        self.assertEqual(outcomes[0]["outcome"], "succeeded")
        self.assertEqual(self.fake.start_count, 1)


if __name__ == "__main__":
    unittest.main()
