"""Synthetic public wire fixtures only; real native login and end-to-end NOT_RUN."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from public_harness_login_protocol import LoginProtocol
from public_harness_catalog_protocol import CATALOG


def frame(value):
    return json.dumps(value).encode()


def reply(number, value):
    return frame({'jsonrpc':'2.0','id':number,'result':value})


def product():
    return dict(state='ready',mode='managed',generation=1,revision='synthetic',productName='WorkBuddy',applicationName='WorkBuddy',productVersion='5.9.3',networkEnvironment=None,deploymentType=None,defaultFolderName=None,freshness='fresh',links={},productFeatures={},featureToggles={})


def account(auth=False):
    return {'state':'authenticated' if auth else 'unauthenticated','mode':'managed' if auth else None,'account':{'id':'SYNTHETIC_ACCOUNT','displayName':'Synthetic person','authMethodId':'workbuddy-desktop'} if auth else None}


def notify(method,params):
    return frame({'jsonrpc':'2.0','method':method,'params':params})


def url_request(number=80):
    return frame({'jsonrpc':'2.0','id':number,'method':'elicitation/create','params':{'mode':'url','message':'synthetic','requestId':5,'elicitationId':'synthetic-elicitation','url':'https://example.invalid/auth?state=SYNTHETIC_PRIVATE'}})


class LoginTests(unittest.TestCase):
    def baseline(self, existing=False):
        p=LoginProtocol();p.start()
        p.receive(reply(1,dict(protocolVersion=1,serverInfo=dict(name='@genie/agent-harness',version='synthetic'),capabilities={},authMethods=CATALOG)))
        p.receive(reply(2,product()));p.receive(reply(3,account(existing)));p.receive(reply(4,{'authenticated':existing}))
        return p

    def login(self):
        p=self.baseline();out,_=p.intent(True)
        self.assertEqual(json.loads(out[0])['method'],'auth/login');return p

    def settle(self,p,authenticated):
        p.receive(reply(5,{}));p.receive(reply(6,account(authenticated)))
        return p.receive(reply(7,{'authenticated':authenticated}))

    def test_baseline_intent_and_existing_account(self):
        p=self.baseline();self.assertEqual(p.state,'awaiting_intent');self.assertFalse(p.summary()['login_sent'])
        p.intent();self.assertEqual(p.state,'failed')
        p=self.baseline(True);self.assertEqual(p.state,'existing_account');p.intent(True);self.assertFalse(p.login_sent)

    def test_ack_not_ready_private_identity_separate(self):
        p=self.login();p.receive(reply(5,{}));self.assertTrue(p.summary()['login_ack']);self.assertFalse(p.summary()['native_account_verified'])
        _,events=p.receive(reply(6,account(True)));self.assertEqual(events,())
        _,events=p.receive(reply(7,{'authenticated':True}));self.assertEqual(events[0]['kind'],'account_identity');self.assertEqual(len(events[0]['digest']),64);self.assertEqual(p.state,'awaiting_identity')
        self.assertNotIn('SYNTHETIC_ACCOUNT',json.dumps(p.summary()));p.confirm_identity(True)
        self.assertEqual(p.state,'verified');self.assertTrue(p.summary()['native_account_verified']);self.assertFalse(p.summary()['real_model_ready'])

    def test_url_handoff_no_auto_action(self):
        p=self.login();w,e=p.receive(url_request());self.assertEqual(w,());self.assertEqual(e[0]['kind'],'url_handoff')
        self.assertNotIn('SYNTHETIC_PRIVATE',json.dumps(p.summary()));self.assertIsNone(p._identity_event)
        w,e=p.elicitation_reply(True);self.assertEqual(json.loads(w[0])['result'],{'action':'accept'})
        p.receive(notify('elicitation/complete',{'elicitationId':'synthetic-elicitation'}));self.settle(p,False)
        self.assertEqual(p.state,'not_authenticated')

    def test_login_error_denies_outstanding_url_then_reconciles(self):
        for authenticated in (False, True):
            p=self.login();p.receive(url_request())
            self.assertEqual(p.next_action,'confirm_url_handoff')
            w,e=p.receive(frame({'jsonrpc':'2.0','id':5,'error':{'code':-32800,'message':'PRIVATE_ERROR'}}))
            self.assertEqual(e,())
            self.assertEqual(json.loads(w[0]),{'jsonrpc':'2.0','id':80,'result':{'action':'cancel'}})
            self.assertEqual(json.loads(w[1])['method'],'account/read')
            self.assertIsNone(p.pending_elicitation);self.assertEqual(p.next_action,'wait')
            p.receive(notify('elicitation/complete',{'elicitationId':'synthetic-elicitation'}))
            p.receive(reply(6,account(authenticated)))
            _,events=p.receive(reply(7,{'authenticated':authenticated}))
            self.assertEqual(p.next_action,'confirm_account_identity' if authenticated else 'close')
            self.assertEqual(bool(events),authenticated)
            self.assertFalse(p.summary()['login_ack']);self.assertFalse(p.summary()['native_account_verified'])
            self.assertNotIn('PRIVATE_ERROR',json.dumps(p.summary()))

    def test_cancel_notification_not_settlement(self):
        p=self.login();w,_=p.cancel(timeout=True);self.assertEqual(json.loads(w[0])['params'],{'requestId':5})
        self.assertFalse(p.done);self.assertFalse(p.summary()['login_settled']);self.assertEqual(p.cancel()[0],())
        p.receive(frame({'jsonrpc':'2.0','id':5,'error':{'code':-32800,'message':'PRIVATE_ERROR'}}))
        p.receive(reply(6,account(False)));p.receive(reply(7,{'authenticated':False}));self.assertEqual(p.state,'cancelled')
        self.assertNotIn('PRIVATE_ERROR',json.dumps(p.summary()))

    def test_cancel_late_commit_not_ready(self):
        p=self.login();p.cancel();self.settle(p,True);self.assertEqual(p.state,'cancelled_committed');self.assertFalse(p.summary()['native_account_verified'])
        p.intent(True);self.assertEqual(p.state,'unknown');self.assertEqual(p.summary()['frames_sent'],9)

    def test_unknown_refuses_retry_and_late_ack(self):
        p=self.login();p.cancel();p.closed();self.assertEqual(p.state,'unknown')
        p.intent(True);p.receive(reply(5,{}));self.assertEqual(p.state,'unknown');self.assertFalse(p.summary()['native_account_verified'])

    def test_form_never_collects_input(self):
        p=self.login();w,e=p.receive(frame({'jsonrpc':'2.0','id':90,'method':'elicitation/create','params':{'mode':'form','message':'synthetic','requestId':5,'requestedSchema':{'type':'object','properties':{'password':{'type':'string'}}}}}))
        self.assertEqual(e,());self.assertEqual(json.loads(w[0])['result'],{'action':'cancel'});self.assertTrue(p.cancelled)

    def test_notifications_before_ack_do_not_prove_login(self):
        p=self.login();p.receive(notify('account/updated',account(True)));p.receive(notify('product/updated',product()))
        self.assertEqual(p.state,'login_pending');self.assertFalse(p.summary()['native_account_verified']);self.settle(p,True)
        p.receive(notify('account/updated',account(True)));self.assertEqual(p.state,'unknown')

    def test_notification_after_read_invalidates_pending_identity(self):
        p=self.login();p.receive(reply(5,{}));p.receive(reply(6,account(True)));p.receive(notify('account/updated',account(False)))
        self.assertEqual(p.state,'unknown');self.assertIsNone(p._identity_event)

    def test_identity_rejection_and_status_mismatch(self):
        p=self.login();self.settle(p,True);p.confirm_identity(False);self.assertEqual(p.state,'identity_rejected')
        p=self.login();p.receive(reply(5,{}));p.receive(reply(6,account(True)));p.receive(reply(7,{'authenticated':False}));self.assertEqual(p.state,'unknown')

    def test_malformed_unpaired_and_secret_fields(self):
        cases=[b'{"jsonrpc":"2.0","id":5,"id":5,"result":{}}',reply(500,{}),reply(True,{}),b'[]',b'NaN',b'x'*65537]
        for value in cases:
            with self.subTest(value=value[:50]):
                p=self.login();p.receive(value);self.assertEqual(p.state,'unknown')
        p=self.login();p.receive(reply(5,{}));bad=account(True);bad['account']['credential']='SECRET';p.receive(reply(6,bad));self.assertEqual(p.state,'unknown')

    def test_reject_url_and_wrong_correlation(self):
        for url in ['http://example.invalid','https://user:pass@example.invalid','file:///tmp/a','https://example.invalid/\x1b']:
            p=self.login();v=json.loads(url_request());v['params']['url']=url;p.receive(frame(v));self.assertEqual(p.state,'unknown')
        p=self.login();v=json.loads(url_request());v['params']['requestId']=4;p.receive(frame(v));self.assertEqual(p.state,'unknown')

    def test_single_request_and_no_forbidden_wire(self):
        p=self.baseline();w,_=p.intent(True);self.assertEqual(len(w),1);p.intent(True);self.assertEqual(p.state,'unknown')
        p=self.login();p.receive(frame({'jsonrpc':'2.0','id':80,'method':'session/request_permission','params':{}}));self.assertEqual(p.state,'unknown')
        self.assertFalse(p.summary()['real_model_ready'])

if __name__=='__main__':unittest.main()
