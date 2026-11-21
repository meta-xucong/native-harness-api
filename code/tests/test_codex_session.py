import json
import unittest
from native_harness_api.codex_session import CodexSession, account_identity_digest, project_result

ACCOUNT = dict(type='chatgpt', email='test@example.test', workspaceName=None, planType='plus')

def wire(value):
    return json.dumps(value).encode()

class CodexSessionTests(unittest.TestCase):
    def session(self):
        events = []
        s = CodexSession(deadline=100, clock=lambda: 0, expected_account_digest=account_identity_digest(ACCOUNT), on_event=events.append)
        s.start()
        frames = s.receive(wire(dict(id=1, result=dict(userAgent='codex', codexHome='/dedicated', platformFamily='windows', platformOs='windows'))))
        self.assertEqual(json.loads(frames[0])['method'], 'initialized')
        s.receive(wire(dict(id=2, result=dict(account=ACCOUNT))))
        self.assertTrue(s.ready)
        command = json.loads(s.new_session('/empty', 'model')[0])
        self.assertEqual(command['params']['modelProvider'], 'openai')
        s.receive(wire(dict(id=3, result=dict(cwd='/empty', model='model', modelProvider='openai', approvalPolicy='on-request', sandbox={'type': 'readOnly'}, thread={'id': 'thread'}))))
        s.prompt('hello')
        s.receive(wire(dict(id=4, result={'turn': {'id': 'turn', 'status': 'inProgress'}})))
        return s, events

    def notify(self, s, method, **p):
        return s.receive(wire(dict(method=method, params=dict(threadId='thread', **p))))

    def item(self, s, key, text, phase=None):
        self.notify(s, 'item/completed', turnId='turn', item=dict(type='agentMessage', id=key, text=text, phase=phase))

    def finish(self, s, status='completed', items=None):
        self.notify(s, 'turn/completed', turn=dict(id='turn', status=status, items=items or [], itemsView='partial', error=None))

    def owned(self, s):
        return dict(session=s.evidence(), forced_before_terminal=False, cleanup_confirmed=True, cleanup={k: True for k in ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete', 'io_threads_stopped')})

    def test_phase_order_replacement_partial_terminal(self):
        s, events = self.session()
        self.notify(s, 'item/started', turnId='turn', item=dict(type='agentMessage', id='a', text='partial', phase='partial_answer'))
        self.item(s, 'b', 'hidden', 'commentary')
        self.assertEqual(s.text, 'partial')
        self.item(s, 'c', 'final', 'final_answer')
        self.item(s, 'a', 'first', 'final_answer')
        self.assertEqual(s.text, 'first\n\nfinal')
        self.finish(s, items=[dict(type='agentMessage', id='c', text='corrected', phase='final_answer')])
        self.assertEqual(s.text, 'first\n\ncorrected')
        self.assertEqual(project_result(self.owned(s), 'model')['outcome'], 'succeeded')
        self.assertTrue(any(x['type'] == 'text_snapshot' for x in events))

    def test_null_phase_and_empty_completion(self):
        s, _ = self.session()
        self.item(s, 'a', 'answer')
        self.finish(s)
        self.assertIsNone(s.fault)
        s, _ = self.session()
        self.item(s, 'a', 'comment', 'commentary')
        self.finish(s)
        self.assertIsNotNone(s.fault)

    def test_cancel_ack_not_terminal(self):
        s, _ = self.session()
        frame = json.loads(s.cancel()[0])
        s.receive(wire(dict(id=frame['id'], result={})))
        self.assertFalse(s.terminal_observed)
        self.finish(s, 'interrupted')
        self.assertEqual(project_result(self.owned(s), 'model')['outcome'], 'cancelled')

    def test_wrong_ownership_and_reroute(self):
        for method, params in [('item/agentMessage/delta', dict(turnId='other', itemId='a', delta='wrong')), ('model/rerouted', dict(turnId='turn', fromModel='model', toModel='other', reason='unknown'))]:
            s, _ = self.session()
            self.notify(s, method, **params)
            self.assertIsNotNone(s.fault)

    def test_cleanup_and_post_terminal_fault(self):
        s, _ = self.session()
        self.item(s, 'a', 'answer')
        self.finish(s)
        owned = self.owned(s)
        owned['forced_cleanup'] = True
        self.assertEqual(project_result(owned, 'model')['outcome'], 'succeeded')
        owned['forced_before_terminal'] = True
        self.assertEqual(project_result(owned, 'model')['outcome'], 'pending')
        owned = self.owned(s)
        owned['cleanup']['owned_job_empty'] = False
        self.assertEqual(project_result(owned, 'model')['outcome'], 'pending')
        self.finish(s, 'failed')
        self.assertEqual(project_result(self.owned(s), 'model')['outcome'], 'pending')

    def test_approval_never_accepts(self):
        s, events = self.session()
        frames = s.receive(wire(dict(id=90, method='item/commandExecution/requestApproval', params=dict(threadId='thread', turnId='turn'))))
        self.assertEqual(json.loads(frames[0])['result'], {'decision': 'cancel'})
        self.assertEqual(json.loads(frames[1])['method'], 'turn/interrupt')
        self.assertIn({'type': 'native_status', 'state': 'requires_action'}, events)

    def test_pre_ack_events_and_cancel(self):
        s, _ = self.session()
        # Simulate the same pending turn/start boundary before its acknowledgement.
        s.turn_id = None
        s.phase = 'turn_starting'
        s._pending[5] = 'turn/start'
        self.notify(s, 'item/agentMessage/delta', turnId='turn', itemId='a', delta='early')
        self.assertEqual(s.text, '')
        s.cancel()
        frames = s.receive(wire(dict(id=5, result={'turn': {'id': 'turn'}})))
        self.assertEqual(s.text, 'early')
        self.assertEqual(json.loads(frames[0])['method'], 'turn/interrupt')
        self.assertIsNone(s.fault)

    def test_account_provider_and_timeout(self):
        s, _ = self.session()
        s.receive(wire(dict(method='account/updated', params={})))
        self.assertEqual(s.fault, 'account_changed')
        s, _ = self.session()
        s.clock = lambda: 101
        self.assertFalse(s.tick())
        self.assertFalse(s.terminal_observed)
        s, _ = self.session()
        s.phase = 'thread_starting'
        s._pending[5] = 'thread/start'
        s.receive(wire(dict(id=5, result=dict(cwd='/empty', model='model', modelProvider='third-party', approvalPolicy='on-request', sandbox={'type': 'readOnly'}, thread={'id': 'thread'}))))
        self.assertEqual(s.fault, 'thread_configuration_mismatch')

    def test_failed_terminal_and_output_fault(self):
        s, _ = self.session()
        self.finish(s, 'failed')
        self.assertEqual(project_result(self.owned(s), 'model')['outcome'], 'failed')
        s, _ = self.session()
        s.on_event = lambda event: False
        self.item(s, 'a', 'answer')
        self.assertEqual(s.fault, 'output_fault')
        self.assertEqual(project_result(self.owned(s), 'model')['outcome'], 'pending')

    def test_wrong_cwd_and_late_item_events(self):
        s, _ = self.session()
        s._pending[8] = 'thread/start'
        s.receive(wire(dict(id=8, result=dict(cwd='/wrong', model='model', modelProvider='openai', approvalPolicy='on-request', sandbox={'type': 'readOnly'}, thread={'id': 'thread'}))))
        self.assertEqual(s.fault, 'thread_configuration_mismatch')
        for method in ('item/started', 'item/agentMessage/delta'):
            s, _ = self.session()
            self.item(s, 'a', 'authoritative')
            if method == 'item/started':
                self.notify(s, method, turnId='turn', item=dict(type='agentMessage', id='a', text='changed', phase=None))
            else:
                self.notify(s, method, turnId='turn', itemId='a', delta='extra')
            self.assertIsNotNone(s.fault)
            self.assertEqual(s.text, 'authoritative')

    def test_identity_plan_not_binding(self):
        self.assertEqual(account_identity_digest(ACCOUNT), account_identity_digest(dict(ACCOUNT, planType='enterprise')))
        self.assertNotEqual(account_identity_digest(ACCOUNT), account_identity_digest(dict(ACCOUNT, email='different')))

    def test_unknown_id_and_malformed(self):
        for raw in [b'{', b'{"id":1,"id":1}', wire(dict(id=999, result={}))]:
            s, _ = self.session()
            s.receive(raw)
            self.assertIsNotNone(s.fault)

if __name__ == '__main__':
    unittest.main()
