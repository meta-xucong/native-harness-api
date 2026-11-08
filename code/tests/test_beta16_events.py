"""Only self-authored protocol objects; no SDK/vendor/account/model execution."""
import copy
import unittest
from native_harness_api.beta16_events import Beta16EventProjector, ProjectionError
from native_harness_api.beta15_reducer import SessionReducer, REQUEST_ID, ROOT_ID
from native_harness_api.beta15_session import SessionCoordinator


def usage(**fields):
    return {'sessionId': 'root', 'update': {'sessionUpdate': 'usage_update',
                                          'used': 0, 'size': 1000, **fields}}


def terminal(reason='end_turn'):
    return {'sessionId': 'root', 'update': {'sessionUpdate': 'state_update', 'state': 'idle',
            'stopReason': reason, '_meta': {REQUEST_ID: 'run'}}}


def reducer():
    result = SessionReducer('root', deadline=100, clock=lambda: 0)
    result.acknowledge({'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
    result.update({'sessionId': 'root', 'update': {'sessionUpdate': 'agent_message_chunk',
                   'messageId': 'output', 'content': {'type': 'text', 'text': 'synthetic answer'},
                   '_meta': {REQUEST_ID: 'run'}}})
    return result


class Beta16EventsTests(unittest.TestCase):
    def test_real_coordinator_early_updates_usage_ack_and_terminal_conflict(self):
        c = SessionCoordinator(deadline=100, clock=lambda: 0)
        def catalog(current):
            return [{'configId': 'model', 'type': 'select', 'currentValue': current,
                     'options': [{'value': 'synthetic-a', 'name': 'A'},
                                 {'value': 'synthetic-b', 'name': 'B'}]}]
        new = c.new_session('/synthetic/empty')
        c.response({'id': new['id'], 'result': {'sessionId': 'root', 'configOptions': catalog('synthetic-a')}})
        selection = c.select_model('synthetic-b')
        c.response({'id': selection['id'], 'result': {'configOptions': catalog('synthetic-b')}})
        prompt = c.prompt('Synthetic only')
        p = Beta16EventProjector()
        def deliver(event):
            projected = p.project(event)
            if projected is not None:
                c.update(projected)
        # Real beta16 can send context/cost with no run metadata. Never inject
        # fake correlation just to satisfy the existing result reducer.
        deliver(usage())
        deliver(usage(_meta={ROOT_ID: 'run'}))
        self.assertEqual(c.evidence()['outcome'], 'pending')
        deliver({'sessionId': 'root', 'update': {'sessionUpdate': 'agent_message_chunk',
                 'messageId': 'answer', 'content': {'type': 'text', 'text': 'Synthetic'},
                 '_meta': {REQUEST_ID: 'run'}}})
        deliver(terminal())
        self.assertEqual(c.evidence()['outcome'], 'pending')
        c.response({'id': prompt['id'], 'result': {'messageId': 'inserted', '_meta': {REQUEST_ID: 'run'}}})
        self.assertEqual(c.evidence()['outcome'], 'succeeded')
        self.assertEqual(c.evidence()['turn']['text'], 'Synthetic')
        self.assertEqual(c.evidence()['usage'], {'state': 'unknown'})
        deliver(usage(_meta={ROOT_ID: 'run'}))
        deliver(terminal('cancelled'))
        self.assertEqual(c.evidence()['outcome'], 'indeterminate')
        self.assertFalse(c.evidence()['retry_safe'])

    def test_usage_without_run_or_with_root_only_is_dropped(self):
        p = Beta16EventProjector()
        for event in [usage(), usage(_meta={}), usage(_meta=None),
                      usage(_meta={ROOT_ID: 'run', 'codebuddy.ai/cost': {
                          'sessionId': 'root', 'aggregate': 'cumulative',
                          'delta': {'amount': 0, 'currency': 'credits'}}})]:
            self.assertIsNone(p.project(event))
        self.assertEqual(p.usage_dropped, 4)
        self.assertEqual(p.summary()['usage'], {'state': 'unknown'})
        self.assertFalse(p.summary()['native_account_verified'])
        self.assertFalse(p.summary()['real_model_ready'])

    def test_valid_costs_and_null_metadata(self):
        for cost in [None, {'amount': 0, 'currency': 'credits'},
                     {'amount': 1.25, 'currency': 'USD', '_meta': None},
                     {'amount': -1, 'currency': 'EUR', '_meta': {}}]:
            self.assertIsNone(Beta16EventProjector().project(usage(cost=cost)))
        e = usage(); e['_meta'] = None
        self.assertIsNone(Beta16EventProjector().project(e))

    def test_usage_never_finishes_turn_or_invents_request_id(self):
        p, r = Beta16EventProjector(), reducer()
        event = usage(_meta={ROOT_ID: 'run'})
        original = copy.deepcopy(event)
        self.assertIsNone(p.project(event)); self.assertEqual(event, original)
        self.assertEqual(r.outcome, 'pending')
        r.update(p.project(terminal())); self.assertEqual(r.outcome, 'succeeded')
        self.assertEqual(r.evidence()['usage'], {'state': 'unknown'})

    def test_terminal_conflict_is_never_dropped(self):
        p, r = Beta16EventProjector(), reducer()
        r.update(p.project(terminal())); r.update(p.project(terminal('cancelled')))
        self.assertEqual(r.outcome, 'indeterminate')

    def test_result_correlation_is_not_synthesized(self):
        p, r = Beta16EventProjector(), reducer()
        e = terminal(); del e['update']['_meta']
        copied = p.project(e)
        self.assertEqual(copied, e)
        r.update(copied); self.assertEqual(r.outcome, 'indeterminate')

    def test_foreign_run_and_child_still_reach_correlation_validator(self):
        for session, run in [('child', 'run'), ('root', 'other')]:
            p, r = Beta16EventProjector(), reducer()
            e = terminal(); e['sessionId'] = session; e['update']['_meta'][REQUEST_ID] = run
            copied = p.project(e); self.assertEqual(copied, e)
            r.update(copied); self.assertEqual(r.outcome, 'pending')

    def test_other_events_unchanged_and_detached_from_caller(self):
        for kind in ['config_option_update', '_codebuddy.ai/error', 'agent_message',
                     'tool_call_update', 'session_info_update', 'future_event']:
            p = Beta16EventProjector()
            e = {'sessionId': 'root', 'update': {'sessionUpdate': kind, 'nested': [{'x': 1}]}}
            copied = p.project(e); self.assertEqual(copied, e)
            e['update']['nested'][0]['x'] = 2
            self.assertEqual(copied['update']['nested'][0]['x'], 1)

    def test_invalid_usage_is_not_silently_swallowed(self):
        cases = [usage(used=True), usage(size=-1), usage(used=1.5), usage(size=2**64),
                 usage(cost={}), usage(cost={'amount': True, 'currency': 'USD'}),
                 usage(cost={'amount': -1, 'currency': 'credits'}),
                 usage(cost={'amount': float('inf'), 'currency': 'USD'}),
                 usage(cost={'amount': 1, 'currency': 'usd'}),
                 usage(cost={'amount': 1, 'currency': 'USD', 'state': 'idle'}),
                 usage(_meta=[]), usage(state='idle', stopReason='end_turn')]
        missing = usage(); del missing['update']['used']; cases.append(missing)
        extra = usage(); extra['result'] = terminal(); cases.append(extra)
        for event in cases:
            with self.subTest(event=event):
                p = Beta16EventProjector()
                with self.assertRaises(ProjectionError): p.project(event)
                self.assertEqual(p.usage_dropped, 0)
                with self.assertRaises(ProjectionError): p.project(terminal())

    def test_dropped_and_foreign_events_consume_cumulative_budget(self):
        p = Beta16EventProjector(max_events=1)
        e = usage(); e['sessionId'] = 'child'
        self.assertIsNone(p.project(e))
        with self.assertRaises(ProjectionError): p.project(usage())
        self.assertEqual(p.fault, 'BETA16_EVENT_LIMIT')
        p = Beta16EventProjector(max_bytes=100)
        self.assertIsNone(p.project(usage()))
        with self.assertRaises(ProjectionError): p.project(usage())

    def test_frame_budget_and_invalid_types(self):
        with self.assertRaises(ProjectionError): Beta16EventProjector(max_frame_bytes=16).project(usage())
        for event in [None, [], {'sessionId': True, 'update': {}},
                      {'sessionId': 'root', 'update': {1: 'x'}},
                      {'sessionId': 'root', 'update': {'sessionUpdate': 'usage_update', 'used': 0, 'size': 1, '_meta': {'x': '\ud800'}}}]:
            with self.assertRaises(ProjectionError): Beta16EventProjector().project(event)

    def test_cycles_depth_nonfinite_rejected_without_payload_in_error(self):
        e = usage(); e['update']['_meta'] = e
        with self.assertRaises(ProjectionError) as error: Beta16EventProjector().project(e)
        self.assertEqual(str(error.exception), 'BETA16_EVENT_INVALID')
        e = usage(_meta={}); n = e['update']['_meta']
        for _ in range(70): n['next'] = {}; n = n['next']
        with self.assertRaises(ProjectionError): Beta16EventProjector().project(e)
        with self.assertRaises(ProjectionError): Beta16EventProjector().project(usage(used=float('nan')))

    def test_limits_reject_bool_and_invalid_ranges(self):
        for kwargs in [{'max_events': True}, {'max_bytes': 0}, {'max_frame_bytes': 262145}]:
            with self.assertRaises(ValueError): Beta16EventProjector(**kwargs)
