"""Real Bridge/Provider sockets and durable FakeHarness, never vendor execution."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from dataclasses import replace
from http.client import HTTPConnection
import json
from pathlib import Path
import tempfile
import threading
import unittest
from urllib.parse import urlsplit

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.harness import HarnessConfig, HarnessBinding
from render_bridge.node_api import NodeScope
from native_harness_api.adapters import FakeHarness
from native_harness_api.bridge_client import BridgeClient, NodeClient
from native_harness_api.contracts import Binding
from native_harness_api.facade import Service
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.openai_provider import OpenAIProvider
from native_harness_api.runtime import node_tick
from test_real_bridge import bridge_server, facade_server


class OpenAIProviderBridgeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.binding = Binding('fixture-binding', 1, 'synthetic-basic', 'fake-model')
        owner = ('fixture-tenant', 'fixture-principal')
        cfg = HarnessConfig(source_id='fixture-source', workspace_id='fixture-workspace',
            ledger_instance='fixture-ledger', node_id='fixture-node', executor_instance='fixture-executor',
            allowed_owners=(owner,), bindings=(HarnessBinding(**self.binding.wire()),), accepting_new=True)
        self.store = BridgeStore(root/'bridge.sqlite', root/'artifacts', dispatch_mode='node_pull', harness=cfg)
        app = create_app(store=self.store,
            source_tokens={'fixture-bridge-source-token': SourceScope('fixture-source', 'fixture-workspace')},
            node_tokens={'fixture-bridge-node-token': NodeScope('fixture-source','fixture-workspace','fixture-node','fixture-executor')})
        url = self.stack.enter_context(bridge_server(app))
        self.bridge = BridgeClient(url, 'fixture-bridge-source-token', 'fixture-ledger')
        self.service = Service('fixture-source', self.bridge, {self.binding.model_alias: self.binding}, True, owner)
        self.token = 'fixture-native-provider-token'
        self.provider = OpenAIProvider({self.token:self.service}, mode='p1b_integration', wait_seconds=3, poll_seconds=.01)
        self.url = self.stack.enter_context(facade_server(self.provider))
        self.fake = FakeHarness(root/'fake-native')
        self.runner = Runner(NodeClient(url, 'fixture-bridge-node-token'), Journal(root/'journal'), self.fake,
            policy=NodePolicy('fixture-source','fixture-ledger',frozenset({owner}),(self.binding,)))
        node_tick(self.runner)

    @contextmanager
    def pumping(self):
        stop, faults = threading.Event(), []
        def run():
            try:
                while not stop.wait(.01):
                    node_tick(self.runner)
            except Exception as exc:
                faults.append(exc)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            yield
        finally:
            stop.set()
            thread.join(5)
            self.assertFalse(thread.is_alive())
            self.assertEqual(faults, [])

    def connection(self):
        target = urlsplit(self.url)
        return HTTPConnection(target.hostname, target.port, timeout=6)

    def send(self, key=None, *, stream=False):
        conn = self.connection()
        headers = {'Authorization':'Bearer '+self.token, 'Content-Type':'application/json'}
        if key is not None:
            headers['Idempotency-Key'] = key
        body = {'model':'synthetic-basic', 'messages':[{'role':'user','content':'same prompt'}], 'stream':stream}
        if stream:
            body['stream_options'] = {'include_usage':True}
        conn.request('POST', '/v1/chat/completions', json.dumps(body), headers)
        return conn, conn.getresponse()

    def read(self, key=None, *, stream=False):
        conn, response = self.send(key, stream=stream)
        try:
            data = response.read().decode()
            return response.status, data if stream else json.loads(data)
        finally:
            response.close()
            conn.close()

    def test_concurrent_observers_leave_node_store_capacity(self):
        entered, release = threading.Event(), threading.Event()
        original = self.store.get_task_by_origin
        def held_lookup(*args):
            entered.set()
            if not release.wait(5):
                raise AssertionError("lookup was not released")
            return original(*args)
        self.store.get_task_by_origin = held_lookup
        heartbeat_started = threading.Event()
        heartbeat = self.runner.client.heartbeat
        def observed_heartbeat(*args):
            heartbeat_started.set()
            return heartbeat(*args)
        self.runner.client.heartbeat = observed_heartbeat
        with ThreadPoolExecutor(max_workers=4) as pool:
            first = pool.submit(self.read, 'concurrent')
            try:
                self.assertTrue(entered.wait(5))
                with self.pumping():
                    self.assertTrue(heartbeat_started.wait(5))
                    others = [pool.submit(self.read, 'concurrent') for _ in range(3)]
                    for future in others:
                        status, body = future.result(timeout=3)
                        self.assertEqual(status, 503)
                        self.assertEqual(body['error']['code'], 'PROVIDER_BUSY')
                    release.set()
                    self.assertEqual(first.result(timeout=5)[0], 200)
            finally:
                release.set()
        self.assertEqual(self.fake.start_count, 1)
        self.assertEqual(self.read('concurrent')[0], 200)
        self.assertEqual(self.fake.start_count, 1)

    def test_bearer_without_private_headers_executes(self):
        with self.pumping():
            status, body = self.read('simple')
        self.assertEqual(status, 200)
        self.assertEqual(body['choices'][0]['message']['content'], 'Synthetic FakeHarness result')
        self.assertNotIn('usage', body)
        self.assertEqual(self.fake.start_count, 1)

    def test_direct_key_replays_with_binding_removed_and_admission_closed(self):
        with self.pumping():
            first = self.read('stable')
        self.provider.services[self.token] = replace(self.service, bindings={}, accepting_new=False)
        self.store.accepting_new_tasks = False
        self.assertEqual(self.read('stable'), first)
        self.assertEqual(self.fake.start_count, 1)
        status, body = self.read('different')
        self.assertEqual(status, 503)
        self.assertEqual(body['error']['code'], 'NEW_ADMISSION_DISABLED')

    def test_without_key_same_body_executes_twice(self):
        with self.pumping():
            first = self.read()
            second = self.read()
        self.assertEqual((first[0],second[0]), (200,200))
        self.assertNotEqual(first[1]['id'],second[1]['id'])
        self.assertEqual(self.fake.start_count, 2)

    def test_sse_done_and_unknown_usage_omitted(self):
        with self.pumping():
            status, wire = self.read('stream', stream=True)
        self.assertEqual(status,200)
        self.assertTrue(wire.endswith('data: [DONE]\n\n'))
        chunks = [json.loads(line[6:]) for line in wire.splitlines() if line.startswith('data: ') and line != 'data: [DONE]']
        self.assertTrue(all('usage' not in chunk and 'error' not in chunk for chunk in chunks))
        self.assertEqual(''.join(c['choices'][0]['delta'].get('content','') for c in chunks), 'Synthetic FakeHarness result')
        self.assertEqual(chunks[-1]['choices'][0]['finish_reason'], 'stop')
        # Observation mode does not change execution identity.
        self.assertEqual(self.read('stream')[0],200)
        self.assertEqual(self.fake.start_count,1)

    def test_disconnect_only_stops_observation_then_same_key_reads_original(self):
        conn, response = self.send('disconnect', stream=True)
        self.assertEqual(response.status,200)
        self.assertIn(b'"role":"assistant"', response.readline())
        response.close()
        conn.close()
        self.assertEqual(self.fake.start_count,0)
        self.assertEqual(node_tick(self.runner)['outcome'],'succeeded')
        self.assertEqual(self.read('disconnect')[0],200)
        self.assertEqual(node_tick(self.runner),{'outcome':'idle'})
        self.assertEqual(self.fake.start_count,1)

    def test_unknown_liveness_keeps_slot_quarantined(self):
        self.fake.mode = 'unknown_liveness'
        with self.pumping():
            self.assertEqual(self.read('uncertain')[0],200)
        self.assertEqual(node_tick(self.runner)['code'],'SLOT_QUARANTINED')
        self.provider.wait_seconds = 0
        self.assertNotEqual(self.read('another')[0],200)
        self.assertEqual(node_tick(self.runner)['code'],'SLOT_QUARANTINED')
        self.assertEqual(self.fake.start_count,1)


if __name__ == '__main__':
    unittest.main()
