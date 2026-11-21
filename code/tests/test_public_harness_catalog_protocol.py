"""Self-authored synthetic fixtures; no vendor, credentials, processes or IO."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from public_harness_catalog_protocol import CatalogProtocol, CATALOG


def wire(value):
    return json.dumps(value).encode()


def reply(number, result):
    return wire(dict(jsonrpc='2.0', id=number, result=result))


def init():
    return dict(protocolVersion=1, serverInfo=dict(name='@genie/agent-harness', version='synthetic-version'),
                capabilities={}, authMethods=[dict(CATALOG[0])])


def product():
    return dict(state='ready', mode='managed', generation=0, revision='synthetic', productName='WorkBuddy',
                applicationName='WorkBuddy', productVersion='5.9.3', networkEnvironment=None, deploymentType=None,
                defaultFolderName=None, freshness='fresh', links={}, productFeatures={}, featureToggles={})


class CatalogTests(unittest.TestCase):
    def ready(self, stage=1):
        p = CatalogProtocol()
        p.start()
        if stage >= 2:
            p.receive(reply(1, init()))
        if stage >= 3:
            p.receive(reply(2, product()))
        if stage >= 4:
            p.receive(reply(3, dict(state='unauthenticated', mode=None, account=None)))
        return p

    def test_complete_order_and_fixed_identity(self):
        p = CatalogProtocol()
        first = json.loads(p.start()[0])
        self.assertEqual(first['id'], 1)
        self.assertEqual(first['params']['clientInfo'], dict(name='native-harness-public-catalog', version='1'))
        self.assertEqual(first['params']['capabilities']['account'], {'mode': 'managed'})
        out = [json.loads(x) for x in p.receive(reply(1, init()))]
        self.assertEqual([x['method'] for x in out], ['initialized', 'product/read'])
        self.assertNotIn('id', out[0])
        self.assertFalse(p.summary()['catalog_verified'])
        self.assertEqual(json.loads(p.receive(reply(2, product()))[0])['id'], 3)
        self.assertTrue(p.summary()['catalog_verified'])
        self.assertEqual(json.loads(p.receive(reply(3, dict(state='unauthenticated', mode=None, account=None)))[0])['method'], 'auth/status')
        self.assertEqual(p.receive(reply(4, {'authenticated': False})), ())
        self.assertTrue(p.succeeded)
        self.assertEqual(p.summary()['frames_received'], 4)
        self.assertEqual(p.summary()['frames_sent'], 5)
        self.assertFalse(p.summary()['native_account_verified'])
        self.assertFalse(p.summary()['real_model_ready'])
        self.assertNotIn('cleanup', json.dumps(p.summary()))

    def test_malformed_and_unbounded(self):
        bad = [b'', b'x', b'\xff', b'{} {}', b'[]', b'null', b'{"x":1,"x":2}',
               b'{"jsonrpc":"2.0","id":1,"result":{"x":NaN}}', b'x' * 65537,
               b'[' * 1000 + b']' * 1000, 'not bytes', bytearray(b'{}'),
               reply(1, {'x': 2 ** 100}), reply(1, {'x': 'a' * 8193})]
        for frame in bad:
            with self.subTest(frame_type=type(frame)):
                p = self.ready()
                self.assertEqual(p.receive(frame), ())
                self.assertTrue(p.summary()['failed'])

    def test_duplicate_nested_key(self):
        p = self.ready()
        p.receive(b'{"jsonrpc":"2.0","id":1,"result":{"a":1,"a":2}}')
        self.assertEqual(p.summary()['failure_code'], 'malformed_frame')

    def test_no_batch_or_notifications_or_errors(self):
        for value in [[], {'jsonrpc': '2.0', 'method': 'config/warning', 'params': {}},
                      {'jsonrpc': '2.0', 'id': 1, 'error': {'code': -1, 'message': 'secret'}},
                      {'jsonrpc': '2.0', 'id': True, 'result': init()},
                      {'jsonrpc': '2.0', 'id': 2, 'result': init()}]:
            p = self.ready()
            self.assertEqual(p.receive(wire(value)), ())
            self.assertFalse(p.succeeded)
            self.assertTrue(p.done)
            self.assertNotIn('secret', json.dumps(p.summary()))

    def test_init_catalog_exact(self):
        for patch in [{'protocolVersion': True}, {'protocolVersion': 2}, {'authMethods': []},
                      {'authMethods': [dict(CATALOG[0], extra='bad')]},
                      {'serverInfo': {'name': 'other', 'version': '1'}}, {'capabilities': []}]:
            p = self.ready()
            data = init(); data.update(patch)
            p.receive(reply(1, data))
            self.assertEqual(p.summary()['failure_code'], 'initialize_rejected')

    def test_product_all_fields_and_identity(self):
        for patch in [{'mode': 'external'}, {'productVersion': '5.9.4'}, {'state': 'unavailable'},
                      {'generation': True}, {'links': {'unknown': 'secret'}}, {'productFeatures': {'x': 1}},
                      {'applicationName': 'Other'}, {'extra': 'secret'}]:
            p = self.ready(2)
            data = product(); data.update(patch)
            p.receive(reply(2, data))
            self.assertEqual(p.summary()['failure_code'], 'product_rejected')
            self.assertFalse(p.summary()['catalog_verified'])
        data = product(); del data['revision']
        p = self.ready(2); p.receive(reply(2, data))
        self.assertTrue(p.done)

    def test_account_must_be_exact_anonymous(self):
        for data in [dict(state='authenticated', mode='managed', account={'id': 'secret'}),
                     dict(state='unauthenticated', mode='managed', account=None),
                     dict(state='unauthenticated', mode=None, account={})]:
            p = self.ready(3); p.receive(reply(3, data))
            self.assertEqual(p.summary()['failure_code'], 'account_rejected')
            self.assertNotIn('secret', json.dumps(p.summary()))

    def test_status_false_not_falsey(self):
        for value in [0, None, 'false', True, [], {}]:
            p = self.ready(4); p.receive(reply(4, {'authenticated': value}))
            self.assertEqual(p.summary()['failure_code'], 'auth_status_rejected')

    def test_reverse_fixed_cancel_then_stop(self):
        for method, expected, flag in [('elicitation/create', {'action': 'cancel'}, 'elicitation_rejected'),
                                      ('session/request_permission', {'outcome': {'outcome': 'cancelled'}}, 'permission_rejected')]:
            p = self.ready()
            output = p.receive(wire(dict(jsonrpc='2.0', id=9, method=method, params={'url': 'secret'})))
            self.assertEqual(json.loads(output[0]), dict(jsonrpc='2.0', id=9, result=expected))
            self.assertTrue(p.summary()[flag]); self.assertTrue(p.done)
            self.assertEqual(p.receive(reply(1, init())), ())
            self.assertNotIn('secret', json.dumps(p.summary()))

    def test_reverse_bad_id_or_method_never_echoed(self):
        for id_, method in [('secret', 'elicitation/create'), (True, 'elicitation/create'),
                            (-1, 'elicitation/create'), (2, 'auth/login')]:
            p = self.ready()
            self.assertEqual(p.receive(wire(dict(jsonrpc='2.0', id=id_, method=method, params={}))), ())
            self.assertTrue(p.done)

    def test_duplicate_out_of_order_late_no_retry(self):
        p = self.ready(2)
        self.assertEqual(p.receive(reply(1, init())), ())
        self.assertEqual(p.start(), ())
        self.assertEqual(p.receive(reply(2, product())), ())
        self.assertEqual(p.summary()['frames_sent'], 3)
        p = self.ready(4); p.receive(reply(4, {'authenticated': False}))
        p.receive(reply(4, {'authenticated': False}))
        self.assertFalse(p.succeeded)

    def test_external_fail_and_summary_copy(self):
        for reason in ['timeout', 'eof', 'secret', object()]:
            p = self.ready(); p.fail(reason)
            self.assertTrue(p.done)
            self.assertNotIn('secret', json.dumps(p.summary()))
            copy = p.summary(); copy['real_model_ready'] = True
            self.assertFalse(p.summary()['real_model_ready'])
        p = CatalogProtocol(); p.receive(reply(1, init()))
        self.assertFalse(p.summary()['started'])

if __name__ == '__main__':
    unittest.main()
