"""No vendor imports/requests: shared-slot and frozen-binding regression tests."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
from native_harness_api.contracts import Binding, Identity, envelope, normalize_request
from native_harness_api.native_bindings import validate_bindings
from native_harness_api.native_trial_runtime import NativeTrialConfig
from native_harness_api.native_facade_runtime import NativeFacadeConfig
from native_harness_api.public_sfe_adapter import PublicSfeAdapter
from native_harness_api.node import Journal, NodePolicy, Runner, NodeBusy
from test_node import Client
from test_public_sfe_adapter import success
import test_native_deployment
from prepare_native_deployment import build_plan
from public_harness_preflight import check_sub2api

A=Binding('binding-a',1,'workbuddy-a','native-a')
B=Binding('binding-b',1,'workbuddy-b','native-b')

def task(binding, name):
    request=normalize_request({'model':binding.model_alias,'messages':[{'role':'user','content':'test'}]})
    value=envelope(Identity('source','user','key','op-'+name),request,binding,'ledger')
    value.update(remote_task_id=name,attempt_id='attempt-'+name,created=1,outcome='pending',liveness='not_started',result=None,claim_id=None,epoch=0,start_grant_id=None,result_expired=False)
    return value

class MultipleModelsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name).resolve();(self.root/'evidence').mkdir()
        self.value={'mode':'workbuddy_local_trial','adapter':'public_sfe','source':'source','ledger_instance':'ledger',
                    'bridge_url':'http://127.0.0.1:8791','bindings':[A.wire(),B.wire()],'owner':['user','key'],
                    'profile_root':str(self.root),'assets_root':str(self.root/'assets')}
        self.calls=[]
    def invoke(self,**kw):self.calls.append(kw['model_id']);return success()
    def adapter(self,bindings=(A,B),invoke=None):
        return PublicSfeAdapter(self.root/'evidence',bindings=bindings,invoke=invoke or self.invoke)
    def runner(self,client,adapter=None,bindings=(A,B)):
        return Runner(client,Journal(self.root/'journal'),adapter or self.adapter(),policy=NodePolicy('source','ledger',frozenset({('user','key')}),bindings,mode='workbuddy_local_trial'))
    def test_config_legacy_and_registry(self):
        c=NativeTrialConfig.from_value(self.value);self.assertEqual(c.configured_bindings,(A,B))
        old=dict(self.value);old.pop('bindings');old['binding']=A.wire()
        self.assertEqual(NativeTrialConfig.from_value(old).configured_bindings,(A,))
    def test_reject_ambiguous_duplicate_empty_large(self):
        for changes in ({'binding':A.wire()},{'bindings':[]},{'bindings':[A.wire(),A.wire()]},
                        {'bindings':[A.wire(),dict(B.wire(),model_alias=A.model_alias)]},
                        {'bindings':[A.wire(),dict(B.wire(),binding_id=A.binding_id)]},
                        {'bindings':[A.wire()]*33},{'bindings':[dict(A.wire(),native_model='auto')]}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):NativeTrialConfig.from_value(dict(self.value,**changes))
    def test_same_native_distinct_alias_allowed(self):
        self.assertEqual(len(validate_bindings((A,Binding('other',1,'workbuddy-other',A.native_model)))),2)
    def test_both_facades_publish_all_aliases(self):
        native=NativeTrialConfig.from_value(dict(self.value,accepting_new=True))
        data={k:v for k,v in self.value.items() if k not in ('profile_root','assets_root')};data['adapter']='facade_only'
        server=NativeFacadeConfig.from_value(data)
        for config in (native,server):
            facade=config.facade({'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32})
            self.assertEqual(set(next(iter(facade.services.values())).bindings),{A.model_alias,B.model_alias})
    def test_serial_models_share_journal_and_invocation_owner(self):
        client=Client([task(A,'a'),task(B,'b')]);runner=self.runner(client)
        self.assertEqual(runner.run_once()['outcome'],'succeeded')
        self.assertEqual(runner.run_once()['outcome'],'succeeded')
        self.assertEqual(self.calls,['native-a','native-b']);self.assertEqual(client.starts,2)
    def test_other_model_busy_while_call_active(self):
        entered=threading.Event();release=threading.Event();errors=[]
        def invoke(**kw):
            self.calls.append(kw['model_id']);entered.set()
            if not release.wait(5):raise AssertionError('fixture timeout')
            return success()
        first=self.runner(Client([task(A,'a')]),self.adapter(invoke=invoke))
        second_client=Client([task(B,'b')]);second=self.runner(second_client)
        def work():
            try:first.run_once()
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=work);thread.start()
        try:
            self.assertTrue(entered.wait(5))
            with self.assertRaises(NodeBusy):second.run_once()
            self.assertEqual(second_client.next_calls,0)
        finally:release.set();thread.join(6)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(self.calls,['native-a'])
        self.assertEqual(second.run_once()['outcome'],'succeeded');self.assertEqual(self.calls,['native-a','native-b'])
    def test_unknown_first_model_blocks_second(self):
        def uncertain(**kw):self.calls.append(kw['model_id']);return {}
        client=Client([task(A,'a'),task(B,'b')]);runner=self.runner(client,self.adapter(invoke=uncertain))
        self.assertEqual(runner.run_once()['outcome'],'pending');self.assertEqual(runner.run_once()['outcome'],'pending')
        self.assertEqual(self.calls,['native-a']);self.assertEqual(client.next_calls,1)
    def test_mutation_after_validation_cannot_switch_model(self):
        original=task(A,'a');adapter=self.adapter()
        identify=adapter._identity
        def mutate(frozen,grant,**kw):
            result=identify(frozen,grant,**kw)
            original['request']['binding']['native_model']=B.native_model
            return result
        with patch.object(adapter,'_identity',side_effect=mutate):
            self.assertEqual(adapter.start(original,{'start_grant_id':'g'})['outcome'],'succeeded')
        self.assertEqual(self.calls,[A.native_model])
    def test_removed_binding_receipt_reconciles_read_only(self):
        old=self.adapter();t=task(A,'a');g={'start_grant_id':'g'}
        result=old.start(t,g)
        current=self.adapter((B,))
        self.assertEqual(current.reconcile(t,g),result)
        with self.assertRaises(ValueError):current.start(t,g)
        self.assertEqual(self.calls,['native-a'])
    def test_unknown_removed_binding_never_restarts(self):
        t=task(A,'a');g={'start_grant_id':'g'}
        self.adapter(invoke=lambda **_:{}).start(t,g)
        self.assertEqual(self.adapter((B,)).reconcile(t,g)['outcome'],'pending');self.assertEqual(self.calls,[])
    def test_changed_model_cannot_reuse_grant(self):
        t=task(A,'a');g={'start_grant_id':'g'};self.adapter().start(t,g)
        changed=task(B,'a')
        with self.assertRaises(ValueError):self.adapter().start(changed,g)
        with self.assertRaises(ValueError):self.adapter().reconcile(changed,g)
        self.assertEqual(self.calls,['native-a'])
    def test_removed_binding_pending_upload_precedes_new_model(self):
        client=Client([task(A,'a')]);client.lose_upload=True
        self.assertEqual(self.runner(client).run_once()['code'],'RESULT_UPLOAD_PENDING')
        next_client=Client([task(B,'b')]);runner=self.runner(next_client,bindings=(B,))
        self.assertEqual(runner.run_once()['outcome'],'succeeded');self.assertEqual(next_client.next_calls,0)
        self.assertEqual(runner.run_once()['outcome'],'succeeded');self.assertEqual(self.calls,['native-a','native-b'])
    def test_plan_matching_bindings_and_stable_order(self):
        fixture=test_native_deployment.DeploymentTests();fixture.setUp();spec=fixture.spec.copy();spec.pop('model_alias');spec.pop('native_model')
        spec['models']=[{'model_alias':b.model_alias,'native_model':b.native_model} for b in (A,B)]
        plan=build_plan(spec,fixture.ids);bindings=plan['windows-node.json']['bindings']
        self.assertEqual(bindings,plan['facade.json']['bindings']);self.assertEqual(bindings,plan['bridge.json']['harness']['bindings'])
        routes=plan['sub2api.native-harness.yaml']['gateway']['native_harness']['routes']
        for b in bindings:
            self.assertEqual({k:routes[b['model_alias']][k] for k in b},b)
        spec['models'].reverse();reversed_plan=build_plan(spec,fixture.ids)
        self.assertEqual(list(reversed(bindings)),reversed_plan['windows-node.json']['bindings'])
        self.assertFalse(plan['facade.json']['accepting_new']);self.assertFalse(plan['bridge.json']['harness']['accepting_new'])
    def test_preflight_requires_every_model(self):
        data={k:v for k,v in self.value.items() if k not in ('profile_root','assets_root')};data['adapter']='facade_only'
        config=NativeFacadeConfig.from_value(data)
        for aliases,expected in (([A.model_alias],False),([A.model_alias,B.model_alias],True)):
            value={'object':'list','data':[{'object':'model','id':a} for a in aliases],
                   'native_harness':{'accepting_new':True,'live_readiness_checked':False}}
            with patch('public_harness_preflight.HTTPTransport') as http:
                http.return_value.request.return_value=value
                self.assertEqual(check_sub2api(config,'https://sub.example',{'NATIVE_SUB2API_TEST_KEY':'k'*32})['sub2api_model_listed'],expected)
