"""Synthetic public Harness frames only; no vendor, credentials or inference."""
import copy
import json
import unittest
from native_harness_api.public_sfe_session import PublicSfeSession, account_identity_digest
from native_harness_api.beta15_reducer import REQUEST_ID


def wire(v): return json.dumps(v).encode()
def unpack(frames): return [json.loads(x) for x in frames]
def response(p, request, result): return p.receive(wire({'jsonrpc': '2.0', 'id': request['id'], 'result': result}))
def catalog(current='a'):
    return [{'configId': 'model', 'type': 'select', 'currentValue': current,
             'options': [{'value': 'a', 'name': 'A'}, {'value': 'b', 'name': 'B'}]}]
def initialize():
    return {'protocolVersion': 1, 'serverInfo': {'name': '@genie/agent-harness', 'version': 'synthetic'},
            'capabilities': {}, 'authMethods': [{'description': None, 'methodId': 'workbuddy-desktop', 'name': 'TencentCloud', 'type': 'agent'}]}
def product():
    return dict(state='ready', mode='managed', generation=1, revision='synthetic', productName='WorkBuddy',
                applicationName='WorkBuddy', productVersion='5.9.3', networkEnvironment=None, deploymentType=None,
                defaultFolderName=None, freshness='fresh', links={}, productFeatures={}, featureToggles={})
def identity(): return {'state': 'authenticated', 'mode': 'managed', 'account': {'id': 'synthetic-user'}}
def boot(p, account=None, status=None):
    req = unpack(p.start())[0]
    outputs = unpack(response(p, req, initialize()))
    assert outputs[0]['method'] == 'initialized'
    req = unpack(response(p, outputs[1], product()))[0]
    result = response(p, req, identity() if account is None else account)
    if result:
        response(p, unpack(result)[0], {'authenticated': True} if status is None else status)
    return p

def event(p, update):
    return p.receive(wire({'jsonrpc': '2.0', 'method': 'session/update', 'params': {'sessionId': 'root', 'update': update}}))
def text_event():
    return {'sessionUpdate': 'agent_message_chunk', 'messageId': 'out', 'content': {'type': 'text', 'text': 'Synthetic'}, '_meta': {REQUEST_ID: 'run'}}
def terminal(reason='end_turn'):
    return {'sessionUpdate': 'state_update', 'state': 'idle', 'stopReason': reason, '_meta': {REQUEST_ID: 'run'}}


class PublicSfeSessionTests(unittest.TestCase):
    def create(self, **kwargs): return PublicSfeSession(deadline=100, expected_account_digest=account_identity_digest(identity()['account']), clock=lambda: 0, **kwargs)
    def selected(self):
        p = boot(self.create())
        req = unpack(p.new_session('/synthetic/empty'))[0]
        response(p, req, {'sessionId': 'root', 'configOptions': catalog()})
        req = unpack(p.select_model('b'))[0]
        response(p, req, {'configOptions': catalog('b')})
        self.assertTrue(p.evidence()['model_confirmed'])
        return p
    def test_full_wire_early_events_and_close_no_shim_or_cleanup_claim(self):
        p = self.selected(); req = unpack(p.prompt('synthetic only'))[0]
        self.assertEqual(req['method'], 'session/prompt')
        event(p, {'sessionUpdate': 'usage_update', 'used': 0, 'size': 10})
        event(p, text_event()); event(p, terminal())
        self.assertEqual(p.evidence()['outcome'], 'pending')
        response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
        self.assertEqual(p.evidence()['outcome'], 'succeeded')
        close = unpack(p.close_session())[0]; self.assertEqual(close['method'], 'session/close')
        response(p, close, {})
        self.assertTrue(p.evidence()['session_close_acknowledged'])
        self.assertFalse(p.evidence()['owned_cleanup_confirmed'])
        self.assertEqual(p.evidence()['usage'], {'state': 'unknown'})
        event(p, terminal('cancelled'))
        self.assertEqual(p.evidence()['outcome'], 'indeterminate')
    def test_anonymous_and_inconsistent_auth_rejected(self):
        for a, s in [({'state': 'unauthenticated', 'mode': None, 'account': None}, None),
                     (identity(), {'authenticated': False}), (identity(), {'authenticated': 1}),
                     ({**identity(), 'mode': 'external'}, None)]:
            p = boot(self.create(), a, s)
            self.assertFalse(p.ready); self.assertEqual(p.new_session('/tmp'), ())
    def test_no_admission_before_handshake(self):
        p = self.create(); self.assertEqual(p.new_session('/tmp'), ())
        self.assertFalse(p.ready); self.assertEqual(p.start(), ())
    def test_duplicate_unknown_bool_response_id_fail(self):
        for id in [True, 99, '1']:
            p = self.create(); p.start()
            p.receive(wire({'jsonrpc': '2.0', 'id': id, 'result': initialize()}))
            self.assertIsNotNone(p.failure_code)
        p = boot(self.create()); p.receive(wire({'jsonrpc': '2.0', 'id': 4, 'result': {'authenticated': True}}))
        self.assertFalse(p.ready)
    def test_rpc_error_and_duplicate_json_keys_finite(self):
        for frame in [b'{"jsonrpc":"2.0","id":1,"id":1,"result":{}}',
                      wire({'jsonrpc': '2.0', 'id': 1, 'error': {'message': 'PRIVATE'}}), b'\xff', b'[]']:
            p = self.create(); p.start(); p.receive(frame)
            self.assertNotIn('PRIVATE', str(p.evidence())); self.assertFalse(p.ready)
    def test_reverse_permission_and_elicitation_deny_and_block(self):
        for method, expected in [('elicitation/create', {'action': 'cancel'}),
                                 ('session/request_permission', {'outcome': {'outcome': 'cancelled'}})]:
            p = self.selected()
            out = unpack(p.receive(wire({'jsonrpc': '2.0', 'id': 91, 'method': method, 'params': {}})))
            self.assertEqual(out, [{'jsonrpc': '2.0', 'id': 91, 'result': expected}])
            self.assertEqual(p.prompt('cannot run'), ())
            close = unpack(p.close_session())[0]; response(p, close, {})
            self.assertTrue(p.evidence()['session_close_acknowledged'])
            self.assertEqual(p.evidence()['outcome'], 'indeterminate')
    def test_cancel_is_notification_not_completion(self):
        p = self.selected(); req = unpack(p.prompt('synthetic'))[0]
        response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
        cancel = unpack(p.cancel())[0]
        self.assertEqual(cancel['method'], 'session/cancel'); self.assertNotIn('id', cancel)
        self.assertEqual(p.evidence()['outcome'], 'pending'); self.assertFalse(p.evidence()['retry_safe'])
    def test_account_product_changes_invalidate(self):
        for method in ['account/updated', 'product/updated']:
            p = boot(self.create()); p.receive(wire({'jsonrpc': '2.0', 'method': method, 'params': {}}))
            self.assertFalse(p.ready); self.assertEqual(p.failure_code, 'evidence_changed')
    def test_model_mismatch_no_prompt(self):
        p = boot(self.create()); req = unpack(p.new_session('/tmp'))[0]
        response(p, req, {'sessionId': 'root', 'configOptions': catalog()})
        req = unpack(p.select_model('b'))[0]; response(p, req, {'configOptions': catalog('a')})
        self.assertEqual(p.prompt('no'), ())
    def test_invalid_usage_not_dropped_and_missing_correlation_not_faked(self):
        for update in [{'sessionUpdate': 'usage_update', 'used': True, 'size': 2},
                       {'sessionUpdate': 'state_update', 'state': 'idle', 'stopReason': 'end_turn'}]:
            p = self.selected(); req = unpack(p.prompt('synthetic'))[0]
            response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
            event(p, update); self.assertEqual(p.evidence()['outcome'], 'indeterminate')
    def test_limits_clock_disconnect_and_single_prompt(self):
        p = self.create(max_frames=1); req = unpack(p.start())[0]
        response(p, req, initialize()); p.receive(b'{}'); self.assertEqual(p.failure_code, 'limit')
        p = PublicSfeSession(deadline=0, expected_account_digest=account_identity_digest(identity()['account']), clock=lambda: 0); self.assertEqual(p.start(), ())
        p = self.selected(); p.prompt('one'); self.assertEqual(p.prompt('two'), ())
        self.assertEqual(p.evidence()['outcome'], 'indeterminate')
        p = boot(self.create()); p.fail('eof'); self.assertFalse(p.ready)
    def test_product_malformed_and_account_not_retained(self):
        p = self.create(); req = unpack(p.start())[0]; req = unpack(response(p, req, initialize()))[1]
        r = product(); r['productName'] = 'other'; self.assertEqual(response(p, req, r), ())
        self.assertFalse(p.ready)
        p = boot(self.create()); self.assertNotIn('synthetic-user', repr(p.__dict__))

    def test_account_digest_stability_and_wrong_account_rejected(self):
        a = {'id': 'synthetic-user'}
        self.assertEqual(account_identity_digest(a), account_identity_digest({**a, 'displayName': 'Changed', 'avatarUrl': 'https://invalid.example'}))
        self.assertNotEqual(account_identity_digest(a), account_identity_digest({**a, 'providerIdentity': {'accountType': ''}}))
        for bad in [{**a, 'accessToken': 'SECRET'}, {**a, 'providerIdentity': None},
                    {**a, 'providerIdentity': {'accountType': 'x', 'idp': ''}}, {**a, 'id': ''}]:
            with self.assertRaises(ValueError): account_identity_digest(bad)
        p = boot(self.create(), {**identity(), 'account': {'id': 'different'}})
        self.assertFalse(p.ready); self.assertEqual(p.new_session('/tmp'), ())
        for digest in [None, '', 'x' * 64, 'A' * 64, True]:
            with self.assertRaises(ValueError): PublicSfeSession(deadline=100, expected_account_digest=digest)

    def test_product_version_must_match_verified_pin(self):
        p = self.create(); req = unpack(p.start())[0]
        req = unpack(response(p, req, initialize()))[1]
        r = product(); r['productVersion'] = '5.9.4'
        self.assertEqual(response(p, req, r), ())
        self.assertFalse(p.ready)
