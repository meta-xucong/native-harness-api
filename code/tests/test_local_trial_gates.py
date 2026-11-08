"""No native process: local-trial admission contract only."""
from dataclasses import replace
import unittest
from email.message import Message
from native_harness_api.contracts import Binding, Identity, NodeOffer, digest, envelope, normalize_request
from native_harness_api.errors import ChannelError
from native_harness_api.facade import Facade, Service
from native_harness_api.node import NodePolicy

BINDING = Binding('wb', 1, 'workbuddy-test', 'test-model')
TOKEN = 'explicit-test-token-only'

class BridgeSpy:
    ledger_instance = 'ledger'
    def __init__(self): self.lookups = 0; self.submits = 0
    def lookup(self, key): self.lookups += 1; return None
    def submit(self, request):
        self.submits += 1
        raise ChannelError('TEST_SUBMIT_REACHED', 503)

class LocalTrialTests(unittest.TestCase):
    def setUp(self):
        self.bridge = BridgeSpy()
        self.service = Service('source', self.bridge, {BINDING.model_alias:BINDING}, True, ('user-7','key-9'))
        self.facade = Facade({TOKEN:self.service}, mode='workbuddy_local_trial')
        self.headers = {'Authorization':'Bearer '+TOKEN,'X-Native-Source':'source','X-Native-Ledger':'ledger',
                        'X-Native-Tenant':'user-7','X-Native-Principal':'key-9','X-Native-Operation':'op-1',
                        'X-Native-Admission-Allowed':'true','X-Native-Binding-Digest':digest(BINDING.wire())}
        self.request = {'model':BINDING.model_alias,'messages':[{'role':'user','content':'fixture only'}]}
    def invoke(self):
        headers=Message()
        for k,v in self.headers.items(): headers[k]=v
        return self.facade.handle('POST','/v1/chat/completions',headers,self.request)
    def error(self, code):
        with self.assertRaises(ChannelError) as ctx: self.invoke()
        self.assertEqual(ctx.exception.code,code)
    def test_explicit_mode_and_owner_required(self):
        for mode in ['disabled','production','p1b_integration','offline_prototype']:
            with self.assertRaises(ValueError): Facade({TOKEN:self.service},mode=mode)
        for owner in [None,('user-7',),['user-7','key-9']]:
            with self.assertRaises(ValueError): Facade({TOKEN:replace(self.service,owner=owner)},mode='workbuddy_local_trial')
        with self.assertRaises(ValueError): Facade({TOKEN:self.service,TOKEN+'2':self.service},mode='workbuddy_local_trial')
    def test_no_synthetic_binding_in_trial(self):
        fake = Binding('f',1,'synthetic-f','fake-f')
        with self.assertRaises(ValueError): Facade({TOKEN:replace(self.service,bindings={fake.model_alias:fake})},mode='workbuddy_local_trial')
    def test_owner_and_frozen_scope_before_bridge_io(self):
        for field,value,code in [('X-Native-Tenant','user-other','FORBIDDEN'),('X-Native-Source','other','FROZEN_ROUTE_MISMATCH'),('X-Native-Ledger','other','FROZEN_ROUTE_MISMATCH')]:
            old=self.headers[field]; self.headers[field]=value; self.error(code); self.headers[field]=old
        self.assertEqual(self.bridge.lookups,0)
    def test_no_multi_turn_prompt_flattening(self):
        self.request['messages']=[{'role':'assistant','content':'old'}, {'role':'user','content':'new'}]
        self.error('LOCAL_TRIAL_SINGLE_USER_MESSAGE_REQUIRED')
        self.assertEqual(self.bridge.submits,0)
    def test_single_prompt_reaches_existing_ledger(self):
        self.error('TEST_SUBMIT_REACHED'); self.assertEqual(self.bridge.submits,1)
    def test_trial_node_policy_is_single_owner_and_real_binding(self):
        args=('source','ledger',frozenset({('u','k')}),(BINDING,))
        self.assertEqual(NodePolicy(*args,mode='workbuddy_local_trial').bindings,(BINDING,))
        with self.assertRaises(ValueError): NodePolicy('source','ledger',frozenset({('u','k'),('x','y')}),(BINDING,),mode='workbuddy_local_trial')
        with self.assertRaises(ValueError): NodePolicy(*args,mode='production')

    def test_node_rejects_multi_message_offer_before_claim(self):
        policy=NodePolicy('source','ledger',frozenset({('user-7','key-9')}),(BINDING,),mode='workbuddy_local_trial')
        def offer(messages):
            req=normalize_request({'model':BINDING.model_alias,'messages':messages})
            task=envelope(Identity('source','user-7','key-9','op-1'),req,BINDING,'ledger')
            task.update(remote_task_id='task-1',attempt_id='attempt-1',created=1,outcome='pending',liveness='not_started',result=None,claim_id=None,epoch=0,start_grant_id=None,result_expired=False)
            return NodeOffer('task-1',task)
        policy.validate_offer(offer([{'role':'user','content':'one'}]))
        with self.assertRaises(ValueError):
            policy.validate_offer(offer([{'role':'assistant','content':'old'},{'role':'user','content':'new'}]))

if __name__=='__main__': unittest.main()
