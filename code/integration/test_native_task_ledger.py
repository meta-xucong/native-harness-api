"""Exercise Native migration and recovery over real loopback sockets with FakeHarness."""

from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.harness import HarnessBinding as LegacyBinding, HarnessConfig as LegacyConfig
from render_bridge.node_api import NodeScope

from native_harness_api.adapters import FakeHarness
from native_harness_api.bridge_client import BridgeClient, HTTPTransport, NodeClient
from native_harness_api.contracts import Binding
from native_harness_api.errors import ChannelError
from native_harness_api.facade import Service
from native_harness_api.ledger_contract import HarnessBinding, HarnessConfig
from native_harness_api.ledger_http import LedgerHTTP
from native_harness_api.ledger_migrate import migrate_settled
from native_harness_api.ledger_store import NativeLedger
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.openai_provider import OpenAIProvider
from native_harness_api.runtime import node_tick

from test_real_bridge import FACADE_TOKEN, NODE_TOKEN, SOURCE_TOKEN, bridge_server, facade_server


class EventFakeHarness(FakeHarness):
    supports_live_events = True

    def start(self, task, grant, *, publish_event):
        publish_event(1, {"type": "text_snapshot", "text": self.text})
        return super().start(task, grant)


class NativeTaskLedgerTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve(strict=True)
        self.binding = Binding("fixture-binding", 1, "synthetic-basic", "fake-model")
        self.owner = ("fixture-tenant", "fixture-principal")
        self.journal = Journal(self.root / "journal")
        self.fake = EventFakeHarness(self.root / "fake-native")
        self.policy = NodePolicy("fixture-source", "fixture-ledger", frozenset({self.owner}), (self.binding,))
        self.identity = dict(source_id="fixture-source", workspace_id="fixture-workspace",
                             ledger_instance="fixture-ledger", node_id="fixture-node",
                             executor_instance="fixture-executor", allowed_owners=(self.owner,))
        self.config = HarnessConfig(**self.identity, bindings=(HarnessBinding(**self.binding.wire()),),
                                    accepting_new=True)
        self.payload = {"model": "synthetic-basic", "messages": [{"role": "user", "content": "fixture"}]}

    def connect(self, url, stack):
        source = BridgeClient(url, SOURCE_TOKEN, "fixture-ledger")
        service = Service("fixture-source", source, {self.binding.model_alias: self.binding}, True, self.owner)
        provider = OpenAIProvider({FACADE_TOKEN: service}, mode="p1b_integration", wait_seconds=0)
        api = HTTPTransport(stack.enter_context(facade_server(provider)), FACADE_TOKEN)
        runner = Runner(NodeClient(url, NODE_TOKEN), Journal(self.journal.root), self.fake, policy=self.policy)
        return api, source, runner

    def send(self, api, key):
        return api.request("POST", "/v1/chat/completions", self.payload, extra_headers={"Idempotency-Key": key})

    def queue(self, api, runner, key):
        self.assertEqual(node_tick(runner), {"outcome": "idle"})
        with self.assertRaises(ChannelError) as caught:
            self.send(api, key)
        self.assertEqual(caught.exception.code, "TASK_WAIT_TIMEOUT")
        return runner.client.next_task()

    def test_migrate_settled_bridge_task_with_original_id_key_and_events(self):
        old_db = self.root / "bridge.sqlite"
        config = LegacyConfig(**self.identity, bindings=(LegacyBinding(**self.binding.wire()),), accepting_new=True)
        store = BridgeStore(old_db, self.root / "artifacts", dispatch_mode="node_pull", harness=config)
        app = create_app(store=store,
                         source_tokens={SOURCE_TOKEN: SourceScope("fixture-source", "fixture-workspace")},
                         node_tokens={NODE_TOKEN: NodeScope("fixture-source", "fixture-workspace",
                                                            "fixture-node", "fixture-executor")})
        key = "fixture-original-request"
        with ExitStack() as legacy:
            api, source, runner = self.connect(legacy.enter_context(bridge_server(app)), legacy)
            offer = self.queue(api, runner, key)
            self.assertEqual(node_tick(runner)["outcome"], "succeeded")
            old_reply = self.send(api, key)
            old_events = source.events(offer.task_id, 1)
            self.assertTrue(old_events["events"])
        journal_before = self.journal.records()
        native = migrate_settled(old_db, self.journal.db, self.root / "native.sqlite",
                                 replace(self.config, accepting_new=False))
        url = self.stack.enter_context(facade_server(LedgerHTTP(native, SOURCE_TOKEN, NODE_TOKEN)))
        api, source, runner = self.connect(url, self.stack)
        self.assertEqual(source.lookup(offer.task["origin_key"])["remote_task_id"], offer.task_id)
        self.assertEqual(source.events(offer.task_id, 1), old_events)
        self.assertEqual(self.send(api, key), old_reply)
        self.assertEqual(node_tick(runner), {"outcome": "idle"})
        self.assertEqual(self.journal.records(), journal_before)
        self.assertEqual(self.fake.start_count, 1)

    def test_single_slot_and_unacknowledged_result_recover_on_restart(self):
        path = self.root / "native.sqlite"
        store = NativeLedger.initialize(path, self.config)
        with ExitStack() as first:
            url = first.enter_context(facade_server(LedgerHTTP(store, SOURCE_TOKEN, NODE_TOKEN)))
            api, source, runner = self.connect(url, first)
            offer = self.queue(api, runner, "first")
            self.assertEqual(source.capabilities()["queue"]["active_tasks"], 1)
            with self.assertRaises(ChannelError) as denied:
                self.send(api, "second")
            self.assertEqual(denied.exception.status, 429)
            self.assertEqual(denied.exception.code, "queue_capacity")

            real_result = runner.client.result

            def lose_result_ack(*args):
                real_result(*args)
                raise TimeoutError("Synthetic result ACK loss")

            runner.client.result = lose_result_ack
            self.assertEqual(node_tick(runner)["code"], "RESULT_UPLOAD_PENDING")
            previous = self.send(api, "first")
        restarted = NativeLedger(path, self.config)
        url = self.stack.enter_context(facade_server(LedgerHTTP(restarted, SOURCE_TOKEN, NODE_TOKEN)))
        api, source, runner = self.connect(url, self.stack)
        self.assertEqual(node_tick(runner)["outcome"], "succeeded")
        self.assertEqual(self.send(api, "first"), previous)
        self.assertEqual(source.lookup(offer.task["origin_key"])["remote_task_id"], offer.task_id)
        self.assertEqual(source.capabilities()["queue"]["active_tasks"], 0)
        self.assertTrue(self.journal.records()[0]["acknowledged"])
        self.assertEqual(self.fake.start_count, 1)
