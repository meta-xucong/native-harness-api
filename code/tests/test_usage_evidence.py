import json
import unittest
from native_harness_api.usage_evidence import UsageEvidence
from tests import test_public_sfe_session as session_tests
from tests.test_public_sfe_session import event, unpack, response, text_event, terminal
from native_harness_api.beta15_reducer import REQUEST_ID


def sample(fields=None, *, run='run', root='run', message='model-call', child=None):
    meta = {'codebuddy.ai/rootRequestId': root, 'codebuddy.ai/requestId': run,
            'codebuddy.ai/messageId': message, 'usage': {'input_tokens': 0, 'output_tokens': 5} if fields is None else fields}
    if child is not None:
        meta['codebuddy.ai/subagent'] = {'sessionId': child}
    return {'sessionId': 'root', 'update': {'sessionUpdate': 'usage_update', 'used': 99, 'size': 1000, '_meta': meta}}


class UsageEvidenceTests(unittest.TestCase):
    def summary(self, evidence, **kwargs):
        return evidence.summary('root', 'run', accepted=True, **kwargs)

    def test_duplicates_zero_and_no_invented_total_or_price(self):
        e = UsageEvidence()
        for _ in range(3): e.observe(sample())
        result = self.summary(e, terminal_observed=True)
        self.assertEqual(result['state'], 'partial')
        self.assertEqual(result['coverage'], 'unproven')
        self.assertIsNone(result['total_usage'])
        self.assertFalse(result['billing_eligible'])
        self.assertEqual(len(result['records']), 1)
        self.assertEqual(result['records'][0]['native_fields'], {'input_tokens': 0, 'output_tokens': 5})
        self.assertEqual(result['records'][0]['duplicate_observations'], 2)
        self.assertNotIn('price', json.dumps(result))

    def test_root_child_same_message_and_foreign_scopes_isolate(self):
        e = UsageEvidence()
        e.observe(sample())
        e.observe(sample(run='child-run', child='child'))
        e.observe(sample(run='foreign', root='foreign', fields={'credit': 999}))
        result = self.summary(e)
        self.assertEqual(len(result['records']), 2)
        self.assertEqual({r['scope'] for r in result['records']}, {'root', 'subagent'})
        self.assertNotIn('999', json.dumps(result))
        self.assertNotIn('child-run', json.dumps(result))

    def test_child_cannot_impersonate_root_scope(self):
        e = UsageEvidence()
        e.observe(sample(run='child-run', child='root'))
        result = self.summary(e)
        self.assertEqual(result['records'], [])
        self.assertIn('identity_conflict', result['issues'])

    def test_scope_cost_context_idle_never_become_turn_tokens(self):
        e = UsageEvidence()
        e.observe({'sessionId': 'root', 'update': {'sessionUpdate': 'usage_update', 'used': 123,
                   'size': 1000, 'cost': {'amount': 88, 'currency': 'credits'}}})
        e.observe({'sessionId': 'root', 'update': {'sessionUpdate': 'state_update', 'state': 'idle',
                   'usage': {'inputTokens': 1234}}})
        result = self.summary(e, terminal_observed=True)
        self.assertEqual(result['state'], 'unknown')
        self.assertEqual(result['records'], [])
        self.assertIsNone(result['total_usage'])
        self.assertEqual(result['context_or_session_frames_ignored'], 1)

    def test_updates_enrich_but_conflicts_never_overwrite(self):
        e = UsageEvidence()
        e.observe(sample({'input_tokens': 2}))
        e.observe(sample({'input_tokens': 2, 'output_tokens': 3}))
        e.observe(sample({'input_tokens': 99, 'output_tokens': 3}))
        record = self.summary(e)['records'][0]
        self.assertEqual(record['native_fields'], {'input_tokens': 2, 'output_tokens': 3})
        self.assertIn('conflicting_revision', record['issues'])

    def test_missing_identity_invalid_fields_and_secrets_not_exposed(self):
        e = UsageEvidence()
        no_id = sample(); del no_id['update']['_meta']['codebuddy.ai/messageId']
        e.observe(no_id)
        e.observe(sample({'input_tokens': True, 'output_tokens': -1, 'credit': float('nan'),
                          'account': 123456789, 'secret': 'do-not-include', 'nested': {'password': 'do-not-include'}}))
        result = self.summary(e)
        self.assertIn('unattributed_usage', result['issues'])
        self.assertIn('invalid_counter', result['records'][0]['issues'])
        self.assertEqual(result['records'][0]['native_fields'], {})
        self.assertNotIn('123456789', json.dumps(result))
        self.assertNotIn('do-not-include', json.dumps(result))

    def test_budgets_and_copies_no_unbounded_evidence(self):
        e = UsageEvidence(max_records=1, max_events=2)
        e.observe(sample()); e.observe(sample(message='second')); e.observe(sample())
        result = self.summary(e)
        self.assertEqual(set(result['issues']), {'record_limit', 'event_limit', 'coverage_not_proven'})
        result['records'][0]['native_fields']['input_tokens'] = 999
        self.assertEqual(self.summary(e)['records'][0]['native_fields']['input_tokens'], 0)
        self.assertEqual(e.summary('root', 'run')['records'], [])

    def test_public_session_pre_ack_capture_no_execution_usage_change(self):
        p = session_tests.PublicSfeSessionTests().selected()
        req = unpack(p.prompt('synthetic only'))[0]
        original = sample()['update']; event(p, original)
        self.assertEqual(p.evidence()['metering_evidence']['records'], [])
        event(p, text_event()); event(p, terminal())
        response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
        evidence = p.evidence()
        self.assertEqual(evidence['outcome'], 'succeeded')
        self.assertEqual(evidence['usage'], {'state': 'unknown'})
        self.assertEqual(evidence['metering_evidence']['state'], 'partial')
        self.assertEqual(evidence['metering_evidence']['records'][0]['native_fields']['input_tokens'], 0)
        self.assertFalse(evidence['metering_evidence']['billing_eligible'])
