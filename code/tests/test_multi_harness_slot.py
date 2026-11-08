"""Two deliberately different fixture protocols, one real durable store/slot."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest

from native_harness_api.contracts import Identity, envelope, normalize_request
from native_harness_api.harness_registry import binding_for
from native_harness_api.node import Journal, NodeBusy, NodePolicy, Runner
from native_harness_api.public_sfe_adapter import DurableNativeAdapter, PENDING
from test_multi_harness_runtime import target
from test_node import Client


def task(binding, name):
    request=normalize_request({'model':binding.model_alias,'messages':[{'role':'user','content':'fixture'}]})
    value=envelope(Identity('s','u','k','op-'+name),request,binding,'l')
    value.update(remote_task_id=name,attempt_id='attempt-'+name,created=1,outcome='pending',
                 liveness='not_started',result=None,claim_id=None,epoch=0,start_grant_id=None,result_expired=False)
    return value


def terminal(text, model):
    return {'outcome':'succeeded','liveness':'stopped_confirmed','output_blocks':[{'type':'text','text':text}],
            'native_completion':{'confirmed':True},'model_evidence':{'selected':model,'reported':model},'usage':{'state':'unknown'}}


class MultiSlotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
        self.bindings=tuple(binding_for(target(*args)['recipe']) for args in
            [('workbuddy.public-sfe.v1','workbuddy-test'),('codex.app-server.v1','codex-test')])
        self.calls=[]
        self.routes={self.bindings[0]:(self.invoke_a,self.project_a),self.bindings[1]:(self.invoke_b,self.project_b)}
        self.client=Client([task(b,str(i)) for i,b in enumerate(self.bindings)])

    def invoke_a(self,**kwargs):self.calls.append('a');return {'a_text':'answer A'}
    def invoke_b(self,**kwargs):self.calls.append('b');return ['answer B']
    def project_a(self,evidence,model):return terminal(evidence['a_text'],model)
    def project_b(self,evidence,model):return terminal(evidence[0],model)

    def adapter(self,bindings=None,resolve=None,cls=DurableNativeAdapter):
        return cls(self.root,bindings=bindings or self.bindings,resolve=resolve or self.routes.__getitem__)

    def runner(self,adapter=None,bindings=None):
        return Runner(self.client,Journal(self.root/'journal'),adapter or self.adapter(),
            policy=NodePolicy('s','l',frozenset({('u','k')}),bindings or self.bindings,mode='harness_local_trial'))

    def test_same_model_uses_the_matching_invoke_and_projector(self):
        self.assertEqual(self.bindings[0].native_model,self.bindings[1].native_model)
        a=self.runner().run_once();b=self.runner().run_once()
        self.assertEqual(a['output_blocks'][0]['text'],'answer A')
        self.assertEqual(b['output_blocks'][0]['text'],'answer B')
        self.assertEqual(self.calls,['a','b'])
        self.assertEqual(len(Journal(self.root/'journal').records()),2)

    def test_running_a_prevents_b_in_same_journal(self):
        entered=threading.Event();release=threading.Event()
        def pause(**kwargs):
            entered.set()
            if not release.wait(10):raise RuntimeError('fixture deadline')
            return self.invoke_a(**kwargs)
        self.routes[self.bindings[0]]=(pause,self.project_a)
        runner=self.runner()
        with ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(runner.run_once)
            try:
                self.assertTrue(entered.wait(5))
                with self.assertRaises(NodeBusy):self.runner().run_once()
                self.assertEqual(self.client.next_calls,1)
                self.assertNotIn('b',self.calls)
            finally:release.set()
            self.assertEqual(future.result(10)['outcome'],'succeeded')
        self.assertEqual(self.runner().run_once()['outcome'],'succeeded')
        self.assertEqual(self.calls,['a','b'])

    def test_unknown_a_after_removal_blocks_b_without_resolving(self):
        self.routes[self.bindings[0]]=(self.invoke_a,lambda e,m:dict(PENDING))
        self.assertEqual(self.runner().run_once()['liveness'],'unknown')
        def no_route(binding):raise AssertionError('recovery must not resolve current adapter')
        only_b=(self.bindings[1],)
        result=self.runner(self.adapter(only_b,no_route),only_b).run_once()
        self.assertEqual(result['liveness'],'unknown')
        self.assertEqual(self.calls,['a'])
        self.assertEqual(self.client.next_calls,1)

    def test_removed_a_reconciles_saved_result_without_current_adapter(self):
        class LostReturn(DurableNativeAdapter):
            def start(self,*args,**kwargs):
                super().start(*args,**kwargs)
                raise OSError('lost observation')
        self.assertEqual(self.runner(self.adapter(cls=LostReturn)).run_once()['outcome'],'pending')
        before=self.root.joinpath('sfe-evidence.sqlite').read_bytes()
        def no_route(binding):raise AssertionError('read-only recovery must not import or invoke')
        only_b=(self.bindings[1],)
        adapter=self.adapter(only_b,no_route)
        result=self.runner(adapter,only_b).run_once(allow_new=False)
        self.assertEqual(result['outcome'],'succeeded')
        self.assertEqual(result['output_blocks'][0]['text'],'answer A')
        self.assertEqual(self.calls,['a']);self.assertEqual(self.client.next_calls,1)
        self.assertEqual(before,self.root.joinpath('sfe-evidence.sqlite').read_bytes())

    def test_legacy_workbuddy_evidence_replays_after_mode_and_binding_change(self):
        from native_harness_api.contracts import Binding, digest
        from native_harness_api.public_sfe_adapter import PublicSfeAdapter, project_result
        from test_public_sfe_adapter import success
        legacy=Binding('legacy-workbuddy',3,'workbuddy-legacy','native-model')
        frozen=task(legacy,'legacy')
        self.client=Client([frozen])
        calls=[]
        def invoke(**kwargs):calls.append('legacy');return success()
        class LostReturn(PublicSfeAdapter):
            def start(self,*args,**kwargs):
                super().start(*args,**kwargs)
                raise OSError('lost observation')
        old=LostReturn(self.root,binding=legacy,invoke=invoke)
        runner=Runner(self.client,Journal(self.root/'journal'),old,
            policy=NodePolicy('s','l',frozenset({('u','k')}),(legacy,),mode='workbuddy_local_trial'))
        self.assertEqual(runner.run_once()['outcome'],'pending')
        expected=project_result(success(),legacy.native_model)
        def no_route(binding):raise AssertionError('old evidence must not resolve')
        generic=self.adapter(resolve=no_route)
        got=self.runner(generic).run_once(allow_new=False)
        self.assertEqual(got,expected)
        self.assertEqual(digest(got),digest(expected))
        self.assertEqual(calls,['legacy'])
        self.assertEqual(self.client.next_calls,1)
