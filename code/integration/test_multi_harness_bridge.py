"""Real Bridge/Facade sockets and journal, self-authored protocol fixtures only."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from dataclasses import replace
from pathlib import Path
import tempfile
from threading import Event
import time
import unittest

from render_bridge.api import create_app
from render_bridge.domain import BridgeStore, SourceScope
from render_bridge.harness import HarnessConfig, HarnessBinding
from render_bridge.node_api import NodeScope
from native_harness_api.bridge_client import BridgeClient, NodeClient, HTTPTransport
from native_harness_api.contracts import digest
from native_harness_api.facade import Facade, Service
from native_harness_api.errors import ChannelError
from native_harness_api.harness_registry import binding_for
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.public_sfe_adapter import DurableNativeAdapter, PENDING
from native_harness_api.runtime import node_tick
from test_multi_harness_runtime import target
from test_multi_harness_slot import terminal
from test_real_bridge import bridge_server
from tests.fakes.server import running


class MultiHarnessBridgeTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack(); self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.bindings = tuple(binding_for(target(*pair)['recipe']) for pair in
            [('workbuddy.public-sfe.v1', 'workbuddy-test'), ('codex.app-server.v1', 'codex-test')])
        cfg = HarnessConfig(source_id='s',workspace_id='w',ledger_instance='l',node_id='n',executor_instance='e',
            allowed_owners=(('u','k'),),bindings=tuple(HarnessBinding(**b.wire()) for b in self.bindings),
            accepting_new=True,execution_mode='harness_local_trial')
        self.store = BridgeStore(self.root/'bridge.sqlite',self.root/'artifacts',dispatch_mode='node_pull',harness=cfg)
        app = create_app(store=self.store,source_tokens={'fixture-source-token':SourceScope('s','w')},
            node_tokens={'fixture-node-token':NodeScope('s','w','n','e')})
        url = self.stack.enter_context(bridge_server(app))
        bridge = BridgeClient(url,'fixture-source-token','l')
        self.service = Service('s',bridge,{b.model_alias:b for b in self.bindings},True,('u','k'))
        self.token = 'fixture-facade-token'
        self.facade = Facade({self.token:self.service},mode='harness_local_trial',wait_seconds=0)
        self.http = HTTPTransport(self.stack.enter_context(running(self.facade)),self.token)
        self.calls=[]; self.release=Event(); self.entered=Event(); self.unknown=False
        (self.root/'evidence').mkdir()
        def invoke_a(**kw):
            self.calls.append('workbuddy')
            kw['on_event']({'type':'text_snapshot','text':'live fixture A'})
            self.entered.set()
            if not self.release.wait(5): raise RuntimeError('test release deadline')
            return {'answer':'fixture A'}
        def invoke_b(**kw):
            self.calls.append('codex')
            kw['on_event']({'type':'text_snapshot','text':'live fixture B'})
            return ['fixture B']
        routes={self.bindings[0]:(invoke_a,lambda e,m:dict(PENDING) if self.unknown else terminal(e['answer'],m)),
                self.bindings[1]:(invoke_b,lambda e,m:terminal(e[0],m))}
        adapter=DurableNativeAdapter(self.root/'evidence',bindings=self.bindings,resolve=routes.__getitem__)
        self.runner=Runner(NodeClient(url,'fixture-node-token'),Journal(self.root/'journal'),adapter,
            policy=NodePolicy('s','l',frozenset({('u','k')}),self.bindings,mode='harness_local_trial'))
        node_tick(self.runner)

    def send(self,index,key,events=False):
        binding=self.bindings[index]
        headers={'X-Native-Tenant':'u','X-Native-Principal':'k','X-Native-Operation':key,
            'X-Native-Source':'s','X-Native-Ledger':'l','X-Native-Binding-Digest':digest(binding.wire()),
            'X-Native-Admission-Allowed':'true'}
        if events: headers['X-Native-Event-Cursor']='0'
        return self.http.request('POST','/v1/native-harness/events' if events else '/v1/chat/completions',
            {'model':binding.model_alias,'messages':[{'role':'user','content':'fixture'}]},extra_headers=headers)

    def queue(self,index,key):
        with self.assertRaises(ChannelError) as queued:self.send(index,key)
        self.assertEqual(queued.exception.code,'TASK_WAIT_TIMEOUT')

    def test_live_snapshot_two_harnesses_and_removed_route_replay(self):
        self.queue(0,'a')
        with ThreadPoolExecutor(max_workers=1) as pool:
            work=pool.submit(node_tick,self.runner)
            try:
                self.assertTrue(self.entered.wait(2))
                deadline=time.monotonic()+3
                while True:
                    page=self.send(0,'a',True)
                    if any(e.get('data',{}).get('text')=='live fixture A' for e in page['events']):break
                    if time.monotonic()>deadline:self.fail('live snapshot not observed before terminal')
                    time.sleep(0.01)
                self.assertIsNone(page['terminal_seq'])
                with self.assertRaises(ChannelError) as busy:self.send(1,'b')
                self.assertEqual(busy.exception.status,429)
            finally:self.release.set()
            self.assertEqual(work.result(5)['outcome'],'succeeded')
        first=self.send(0,'a'); page=self.send(0,'a',True)
        self.assertIsNotNone(page['terminal_seq'])
        self.queue(1,'b'); self.assertEqual(node_tick(self.runner)['outcome'],'succeeded')
        self.assertEqual(self.send(1,'b')['model'],'codex-test')
        self.facade.services[self.token]=replace(self.service,bindings={},accepting_new=False)
        self.store.harness=replace(self.store.harness,bindings=(),accepting_new=False,execution_mode='workbuddy_local_trial')
        self.assertEqual(self.send(0,'a'),first)
        self.assertEqual(self.send(0,'a',True),page)
        self.assertEqual(self.calls,['workbuddy','codex'])

    def test_unknown_after_removal_keeps_shared_slot(self):
        self.unknown=True; self.release.set(); self.queue(0,'a')
        self.assertEqual(node_tick(self.runner)['liveness'],'unknown')
        self.facade.services[self.token]=replace(self.service,bindings={'codex-test':self.bindings[1]})
        with self.assertRaises(ChannelError) as busy:self.send(1,'b')
        self.assertEqual(busy.exception.status,429)
        self.assertEqual(node_tick(self.runner)['liveness'],'unknown')
        self.assertEqual(self.calls,['workbuddy'])
