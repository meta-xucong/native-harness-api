"""Self-authored objects only. No SDK import or execution."""
import json
import unittest
from native_harness_api.beta15_session import SessionCoordinator, decode_frame
from native_harness_api.beta15_reducer import ContractError
from test_beta15_reducer import catalog, chunk, terminal, ack


class SessionTests(unittest.TestCase):
    def make(self):
        self.now = [0]
        return SessionCoordinator(deadline=100, clock=lambda: self.now[0])

    def reply(self, c, command, result):
        c.response({'id': command['id'], 'result': result})

    def created(self):
        c = self.make()
        q = c.new_session('/synthetic/empty')
        self.assertEqual(q['method'], 'session/new')
        self.reply(c, q, {'sessionId': 'root', 'configOptions': catalog()})
        return c

    def selected(self):
        c = self.created()
        q = c.select_model('synthetic-b')
        self.assertEqual(q['params'], {'sessionId': 'root', 'configId': 'model', 'type': 'id', 'value': 'synthetic-b'})
        self.reply(c, q, {'configOptions': catalog('synthetic-b')})
        return c

    def running(self):
        c = self.selected()
        q = c.prompt('synthetic prompt')
        return c, q

    def test_flow_early_events_and_close(self):
        c, q = self.running()
        c.update(chunk()); c.update(terminal())
        self.assertEqual(c.evidence()['outcome'], 'pending')
        self.reply(c, q, ack())
        self.assertEqual(c.evidence()['outcome'], 'succeeded')
        self.reply(c, c.close_session(), {})
        self.assertEqual(c.phase, 'closed')
        self.assertTrue(c.close_acknowledged)
        self.assertEqual(c.evidence()['liveness'], 'unknown')
        self.assertFalse(c.evidence()['retry_safe'])

    def test_no_prompt_without_readback(self):
        c = self.created()
        c.select_model('synthetic-b')
        with self.assertRaises(ContractError): c.prompt('x')
        self.assertFalse(c.prompt_intent_issued)

    def test_single_new_selection_prompt(self):
        c = self.make(); c.new_session('/synthetic')
        with self.assertRaises(ContractError): c.new_session('/synthetic')
        c = self.created(); c.select_model('synthetic-b')
        with self.assertRaises(ContractError): c.select_model('synthetic-b')
        c, _ = self.running()
        with self.assertRaises(ContractError): c.prompt('retry')
        self.assertEqual(c.evidence()['outcome'], 'indeterminate')

    def test_wrong_duplicate_response(self):
        c = self.created()
        c.response({'id': 999, 'result': {}})
        self.assertEqual(c.phase, 'indeterminate')
        c = self.make(); q = c.new_session('/synthetic')
        r = {'sessionId': 'root', 'configOptions': catalog()}
        self.reply(c, q, r); self.reply(c, q, r)
        self.assertEqual(c.phase, 'indeterminate')

    def test_malformed_response_types(self):
        for response in [None, [], {'id': True, 'result': {}},
                         {'id': 1, 'result': {}, 'error': {}},
                         {'id': 1, 'error': {'message': 'private raw message'}},
                         {'id': 1, 'jsonrpc': '1.0', 'result': {}},
                         {'id': 1, 'result': [], 'other': 1}]:
            c = self.make(); c.new_session('/synthetic'); c.response(response)
            self.assertEqual(c.phase, 'indeterminate')
            self.assertNotIn('private raw message', str(c.evidence()))

    def test_selection_wrong_model(self):
        c = self.created(); q = c.select_model('synthetic-b')
        self.reply(c, q, {'configOptions': catalog()})
        self.assertEqual(c.phase, 'indeterminate')
        self.assertFalse(c.evidence()['model_confirmed'])

    def test_model_update_lost_after_prompt(self):
        c, q = self.running()
        c.update({'sessionId': 'root', 'update': {'sessionUpdate': 'config_option_update', 'configOptions': catalog()}})
        self.reply(c, q, ack()); c.update(chunk()); c.update(terminal())
        self.assertEqual(c.evidence()['outcome'], 'indeterminate')
        self.assertFalse(c.evidence()['model_confirmed'])

    def test_other_session_config_does_not_contaminate(self):
        c = self.selected()
        c.update({'sessionId': 'child', 'update': {'sessionUpdate': 'config_option_update', 'configOptions': catalog()}})
        self.assertTrue(c.evidence()['model_confirmed'])

    def test_foreign_early_notification_ignored(self):
        c = self.make(); c.new_session('/synthetic')
        c.update(chunk()); self.assertIsNone(c.fault)

    def test_timeout_never_revives(self):
        c, q = self.running(); self.now[0] = 100
        self.reply(c, q, ack()); c.update(chunk()); c.update(terminal())
        self.assertEqual(c.evidence()['outcome'], 'indeterminate')
        self.reply(c, c.close_session(), {})
        self.assertTrue(c.close_acknowledged)
        self.assertEqual(c.evidence()['outcome'], 'indeterminate')

    def test_disconnect_even_after_terminal_retains_unknown_cleanup(self):
        c, q = self.running(); self.reply(c, q, ack()); c.update(chunk()); c.update(terminal())
        c.disconnect()
        self.assertEqual(c.evidence()['outcome'], 'indeterminate')
        self.assertEqual(c.evidence()['liveness'], 'unknown')

    def test_close_response_schema(self):
        for result in ({'error': 'not closed'}, {'_meta': []}):
            c, _ = self.running(); self.reply(c, c.close_session(), result)
            self.assertFalse(c.close_acknowledged)
            self.assertEqual(c.phase, 'indeterminate')
        for result in ({'_meta': {}}, {'_meta': None}):
            c, _ = self.running(); self.reply(c, c.close_session(), result)
            self.assertTrue(c.close_acknowledged)

    def test_terminal_after_close_ack_preserves_closed(self):
        c, q = self.running(); self.reply(c, q, ack()); c.update(chunk())
        self.reply(c, c.close_session(), {})
        c.update(terminal()); c.update(terminal())
        self.assertEqual(c.phase, 'closed')
        self.assertEqual(c.evidence()['outcome'], 'succeeded')

    def test_close_not_completion_or_stop_proof(self):
        c, q = self.running(); self.reply(c, q, ack())
        self.reply(c, c.close_session(), {})
        self.assertEqual(c.evidence()['outcome'], 'pending')
        self.assertEqual(c.evidence()['liveness'], 'unknown')
        with self.assertRaises(ContractError): c.close_session()

    def test_cancel_notification_not_result(self):
        c, q = self.running(); self.reply(c, q, ack())
        self.assertEqual(c.cancel(), {'method': 'session/cancel', 'params': {'sessionId': 'root'}})
        self.assertEqual(c.evidence()['outcome'], 'pending')
        with self.assertRaises(ContractError): c.cancel()
        c.update(terminal('cancelled')); self.assertEqual(c.evidence()['outcome'], 'cancelled')

    def test_no_admission_after_close_intent(self):
        c = self.created(); c.close_session()
        with self.assertRaises(ContractError): c.select_model('synthetic-b')
        c = self.selected(); c.close_session()
        with self.assertRaises(ContractError): c.prompt('x')
        self.assertFalse(c.prompt_intent_issued)

    def test_close_before_selection_response_never_readmits(self):
        c = self.created(); select = c.select_model('synthetic-b')
        close = c.close_session()
        self.reply(c, close, {})
        self.reply(c, select, {'configOptions': catalog('synthetic-b')})
        with self.assertRaises(ContractError): c.prompt('x')
        self.assertFalse(c.prompt_intent_issued)

    def test_one_clock_read_per_response_or_update(self):
        c, q = self.running()
        calls = [0]
        def alternating():
            calls[0] += 1
            if calls[0] % 2 == 0: raise RuntimeError('private')
            return 0
        c.clock = alternating
        self.reply(c, q, ack())
        self.assertEqual(calls[0], 1)
        self.assertEqual(c.phase, 'running')
        c.update(chunk())
        self.assertEqual(calls[0], 2)
        self.assertEqual(c.phase, 'indeterminate')
        self.assertNotIn('private', str(c.evidence()))

    def test_one_clock_read_update_with_fixed_original_clock(self):
        c, q = self.running(); self.reply(c, q, ack())
        calls = [0]
        def once():
            calls[0] += 1
            if calls[0] != 1: raise RuntimeError('second read')
            return 0
        c.clock = once
        c.update(chunk()); self.assertEqual(calls[0], 1)
        self.assertIsNone(c.fault)

    def test_clock_exception_is_sticky(self):
        c = self.selected()
        def broken(): raise RuntimeError('private')
        c.clock = broken
        self.assertFalse(c.tick())
        c.clock = lambda: 0
        with self.assertRaises(ContractError): c.prompt('x')
        self.assertNotIn('private', str(c.evidence()))

    def test_permission_is_denied(self):
        c = self.make()
        self.assertEqual(c.permission({'options': [{'kind': 'allow_always', 'optionId': 'allow'}]}), {'outcome': {'outcome': 'cancelled'}})

    def test_invalid_clock_and_blank_prompt(self):
        c = self.selected(); self.now[0] = float('nan')
        with self.assertRaises(ContractError): c.prompt('hello')
        c = self.selected()
        with self.assertRaises(ContractError): c.prompt('   ')
        self.assertFalse(c.prompt_intent_issued)

    def test_returned_intent_does_not_mutate_internal_identity(self):
        c = self.selected(); q = c.prompt('x'); q['params']['sessionId'] = 'child'
        self.assertEqual(c.session_id, 'root')

    def test_bad_catalog_retains_session_for_cleanup(self):
        c = self.make(); q = c.new_session('/synthetic')
        self.reply(c, q, {'sessionId': 'root', 'configOptions': []})
        self.assertEqual(c.phase, 'indeterminate')
        self.assertEqual(c.close_session()['params'], {'sessionId': 'root'})


class FrameTests(unittest.TestCase):
    def test_round_trip(self):
        self.assertEqual(decode_frame(b'{"id":1,"result":{}}'), {'id': 1, 'result': {}})
    def test_reject_bad_json(self):
        for raw in [b'{"id":1,"id":2}', b'{"x":{"k":1,"k":2}}', b'[]',
                    b'{"x":NaN}', b'{"x":Infinity}', b'{} {}', b'\xff', b'{}\x00',
                    b'{"x":"\\ud800"}', b'']:
            with self.subTest(raw=raw), self.assertRaises(ContractError): decode_frame(raw)
    def test_size_and_type(self):
        for raw in ['{}', bytearray(b'{}'), None]:
            with self.assertRaises(ContractError): decode_frame(raw)
        with self.assertRaises(ContractError): decode_frame(b'{}', 1)
        with self.assertRaises(ContractError): decode_frame(b'{}', True)
