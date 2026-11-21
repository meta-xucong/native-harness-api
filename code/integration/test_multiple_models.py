"""Actual Bridge+Facade HTTP and shared Node slot, self-authored executor only."""
from contextlib import ExitStack
from dataclasses import replace
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from prepare_native_deployment import build_plan
from render_bridge.runtime import RuntimeConfig as BridgeRuntime
from native_harness_api.runtime import RuntimeConfig, node_tick
from native_harness_api.bridge_client import NodeClient, HTTPTransport
from native_harness_api.node import NodePolicy, Journal, Runner
from native_harness_api.public_sfe_adapter import PublicSfeAdapter
from native_harness_api.contracts import digest
from native_harness_api.errors import ChannelError
from test_real_bridge import bridge_server, facade_server


def synthetic_success():
    return {'outcome':'succeeded','fault':None,'session':{'outcome':'succeeded','fault':None,'model_confirmed':True,'protocol_ready':True,'account_verified':True,'turn':{'outcome':'succeeded','fault':None,'insert_acknowledged':True,'terminal_observed':True,'terminal_reason':'end_turn','text':'fixture'}},'cleanup_confirmed':True,'natural_exit_zero':True,'cleanup':{k:True for k in ('host_exited','owned_job_empty','handles_closed','cleanup_complete','io_threads_stopped')}}

class RealMultipleModelsTests(unittest.TestCase):
    def test_two_models_reject_busy_then_release_and_replay_removed(self):
        with ExitStack() as stack:
            root=Path(stack.enter_context(tempfile.TemporaryDirectory()))
            spec={'bridge_origin':'https://bridge.example.com','user_id':7,'api_key_id':9,
                  'models':[{'model_alias':'workbuddy-a','native_model':'model-a'},{'model_alias':'workbuddy-b','native_model':'model-b'}],
                  'windows_bundle_root':'C:/Client','windows_profile_root':'C:/Profile','bridge_data_dir':str(root/'state')}
            plan=build_plan(spec)
            plan['bridge.json']['accepting_new_tasks']=True;plan['bridge.json']['harness']['accepting_new']=True
            plan['facade.json']['accepting_new']=True;plan['facade.json']['wait_seconds']=0
            for name,value in plan.items():(root/name).write_text(json.dumps(value))
            app=BridgeRuntime.load(root/'bridge.json').build_app({'BRIDGE_SOURCE_TOKEN':'s'*32,'BRIDGE_NODE_TOKEN':'n'*32})
            url=stack.enter_context(bridge_server(app))
            config=replace(RuntimeConfig.load(root/'facade.json'),bridge_url=url)
            facade=config.facade({'NATIVE_BRIDGE_SOURCE_TOKEN':'s'*32,'NATIVE_FACADE_TOKEN':'f'*32})
            http=HTTPTransport(stack.enter_context(facade_server(facade)),'f'*32)
            calls=[];(root/'evidence').mkdir()
            def invoke(**kw):calls.append(kw['model_id']);return synthetic_success()
            adapter=PublicSfeAdapter(root/'evidence',bindings=config.configured_bindings,invoke=invoke)
            runner=Runner(NodeClient(url,'n'*32),Journal(root/'journal'),adapter,
                          policy=NodePolicy(config.source,config.ledger_instance,frozenset({config.owner}),config.configured_bindings,mode='workbuddy_local_trial'))
            node_tick(runner)
            def send(index,operation):
                binding=config.configured_bindings[index]
                return http.request('POST','/v1/chat/completions',{'model':binding.model_alias,'messages':[{'role':'user','content':'fixture'}]},extra_headers={
                    'X-Native-Tenant':config.owner[0],'X-Native-Principal':config.owner[1],'X-Native-Operation':operation,
                    'X-Native-Source':config.source,'X-Native-Ledger':config.ledger_instance,
                    'X-Native-Binding-Digest':digest(binding.wire()),'X-Native-Admission-Allowed':'true'})
            with self.assertRaises(ChannelError) as queued:send(0,'a')
            self.assertEqual(queued.exception.code,'TASK_WAIT_TIMEOUT')
            with self.assertRaises(ChannelError) as busy:send(1,'b')
            self.assertEqual(busy.exception.status,429)
            self.assertEqual(node_tick(runner)['outcome'],'succeeded')
            first=send(0,'a')
            with self.assertRaises(ChannelError) as queued:send(1,'b')
            self.assertEqual(queued.exception.code,'TASK_WAIT_TIMEOUT')
            self.assertEqual(node_tick(runner)['outcome'],'succeeded');self.assertEqual(send(1,'b')['model'],'workbuddy-b')
            service=facade.services['f'*32]
            facade.services['f'*32]=replace(service,bindings={'workbuddy-b':config.configured_bindings[1]})
            self.assertEqual(send(0,'a'),first)
            self.assertEqual(calls,['model-a','model-b'])
