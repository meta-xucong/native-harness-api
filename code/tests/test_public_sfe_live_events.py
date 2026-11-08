"""Only synthetic events/child processes; live output is never terminal proof."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from native_harness_api.beta15_reducer import SessionReducer, REQUEST_ID, ROOT_ID, SUBAGENT
from native_harness_api.public_sfe_session import PublicSfeSession, account_identity_digest
from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
from test_public_sfe_session import boot, identity, catalog, response, unpack, event
from test_public_sfe_owned_session import SyntheticTransport, CHILD, digest


def update(kind='agent_message_chunk', *, text='a', mid='m', session='root', run='run', **extra):
    value = {'sessionUpdate': kind, '_meta': {REQUEST_ID: run, 'secret': 'NOT_EMITTED'}}
    if kind == 'agent_message_chunk': value.update(messageId=mid, content={'type': 'text', 'text': text})
    value.update(extra)
    return {'sessionId': session, 'update': value}


def ack(r): r.acknowledge({'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})


class LiveEventTests(unittest.TestCase):
    def reducer(self, callback=None, **options):
        return SessionReducer('root', deadline=100, clock=lambda: 0, on_event=callback, **options)

    def test_early_buffer_ack_binds_root_and_preserves_order_replacement(self):
        events = []; r = self.reducer(events.append)
        r.update(update('state_update', state='running'))
        r.update(update(text='a')); r.update(update(text='b'))
        r.update(update('agent_message', messageId='m', content=[{'type': 'text', 'text': 'replacement'}]))
        self.assertEqual(events, [])
        ack(r)
        self.assertEqual(events, [{'type': 'native_status', 'state': 'running'},
            {'type': 'text_snapshot', 'text': 'a'}, {'type': 'text_snapshot', 'text': 'ab'},
            {'type': 'text_snapshot', 'text': 'replacement'}])
        self.assertEqual(r.outcome, 'pending')
        r.update(update('state_update', state='idle', stopReason='end_turn'))
        self.assertEqual(events[-1], {'type': 'native_status', 'state': 'idle', 'stop_reason': 'end_turn'})
        self.assertNotIn('NOT_EMITTED', json.dumps(events))

    def test_foreign_child_and_tools_never_emit(self):
        events = []; r = self.reducer(events.append); ack(r)
        r.update(update(session='child')); r.update(update(run='other'))
        for marker in ({SUBAGENT: {}}, {ROOT_ID: 'other'}):
            e = update(); e['update']['_meta'].update(marker); r.update(e)
        r.update(update('tool_call', content='SECRET_TOOL'))
        r.update(update('reasoning_message_chunk', content='SECRET_REASONING'))
        self.assertEqual(events, [])

    def test_multiple_messages_aggregate_and_empty_replacement(self):
        events = []; r = self.reducer(events.append); ack(r)
        r.update(update(text='x', mid='one')); r.update(update(text='y', mid='two'))
        r.update(update('agent_message', messageId='one', content=[]))
        self.assertEqual([e['text'] for e in events], ['x', 'xy', 'y'])

    def test_no_callback_preserves_old_evidence(self):
        r = self.reducer(); ack(r); r.update(update(text='text'))
        r.update(update('state_update', state='idle', stopReason='end_turn'))
        self.assertEqual(r.evidence()['text'], 'text'); self.assertEqual(r.outcome, 'succeeded')

    def test_invalid_callback_results_and_exception_fail_closed(self):
        def raises(_): raise RuntimeError('PRIVATE')
        async def asynchronous(_): pass
        for callback in (lambda _: False, lambda _: 1, lambda _: {}, raises, asynchronous):
            r = self.reducer(callback); ack(r); r.update(update())
            self.assertEqual(r.fault, 'EVENT_SINK_FAILED'); self.assertEqual(r.outcome, 'indeterminate')
            self.assertNotIn('PRIVATE', str(r.evidence()))

    def test_true_return_is_accepted_and_failure_is_sticky(self):
        events = []
        def sink(value): events.append(value); return len(events) == 1
        r = self.reducer(sink); ack(r)
        r.update(update()); r.update(update()); r.update(update())
        self.assertEqual(len(events), 2); self.assertEqual(r.fault, 'EVENT_SINK_FAILED')

    def test_callback_mutation_cannot_change_reducer_text(self):
        def sink(value): value['text'] = 'changed'
        r = self.reducer(sink); ack(r); r.update(update(text='original'))
        self.assertEqual(r.evidence()['text'], 'original')

    def test_reentrant_update_fails_closed(self):
        r = self.reducer(lambda _: r.update(update()))
        ack(r); r.update(update()); self.assertEqual(r.fault, 'EVENT_SINK_FAILED')

    def test_input_budget_not_relaxed(self):
        events = []; r = self.reducer(events.append, max_events=1); ack(r)
        r.update(update()); r.update(update())
        self.assertEqual(len(events), 1); self.assertEqual(r.fault, 'OVERFLOW')

    def test_replaceable_snapshots_are_not_charged_as_actual_wire(self):
        observed_bytes = [0]; latest = {}
        def sink(value):
            observed_bytes[0] += len(json.dumps(value, separators=(',', ':')).encode())
            latest.clear(); latest.update(value)
        r = self.reducer(sink); ack(r)
        r.update(update(text='x' * 100000))
        for _ in range(180): r.update(update(text='a'))
        self.assertIsNone(r.fault)
        self.assertGreater(observed_bytes[0], 16*1024*1024)
        self.assertEqual(latest['text'], 'x'*100000+'a'*180)
        # The caller's bounded coalescing pump owns actual transmitted-wire quota.

    def test_invalid_or_conflicting_terminal_is_not_published(self):
        events = []; r = self.reducer(events.append); ack(r); r.update(update())
        r.update(update('state_update', state='idle', stopReason='end_turn'))
        before = list(events)
        r.update(update('state_update', state='idle', stopReason='cancelled'))
        self.assertEqual(events, before); self.assertEqual(r.outcome, 'indeterminate')

    def selected(self, sink, clock=lambda: 0):
        p = boot(PublicSfeSession(deadline=100, expected_account_digest=account_identity_digest(identity()['account']), clock=clock, on_event=sink))
        req = unpack(p.new_session('/synthetic'))[0]; response(p, req, {'sessionId': 'root', 'configOptions': catalog()})
        req = unpack(p.select_model('b'))[0]; response(p, req, {'configOptions': catalog('b')})
        return p

    def test_public_protocol_live_before_final_and_native_idle_not_cleanup(self):
        events = []; p = self.selected(events.append); req = unpack(p.prompt('synthetic'))[0]
        event(p, update(text='early')['update']); self.assertEqual(events, [])
        response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
        self.assertEqual(events, [{'type': 'text_snapshot', 'text': 'early'}])
        self.assertEqual(p.evidence()['outcome'], 'pending')
        event(p, update('state_update', state='idle', stopReason='end_turn')['update'])
        self.assertEqual(events[-1]['state'], 'idle'); self.assertFalse(p.evidence()['owned_cleanup_confirmed'])

    def test_coordinator_rechecks_actual_clock_after_callback(self):
        now = [0]
        def sink(_): now[0] = 101
        p = self.selected(sink, clock=lambda: now[0]); req = unpack(p.prompt('synthetic'))[0]
        response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
        event(p, update()['update'])
        self.assertEqual(p.evidence()['outcome'], 'indeterminate')

    def test_owned_engine_callback_failure_cleans_without_retry(self):
        for fail in (False, True):
            with self.subTest(fail=fail), tempfile.TemporaryDirectory() as directory:
                events = []; transport = SyntheticTransport(); engine = PublicSfeOwnedSession(transport)
                def sink(value):
                    events.append(value)
                    # Callback happens inside run, before return/owned cleanup.
                    self.assertFalse(engine._closing)
                    if fail: raise RuntimeError('PRIVATE')
                env = {k:v for k,v in os.environ.items() if k.upper() in ('PATH','SYSTEMROOT','WINDIR','TEMP','TMP','COMSPEC')}
                result = engine.run([sys.executable, '-u', str(CHILD.resolve())], Path(directory), env,
                    workspace=directory, model_id='synthetic-b', prompt='synthetic', expected_account_digest=digest(),
                    timeout=15, cleanup_timeout=2, on_event=sink)
                self.assertEqual(transport.starts, 1); self.assertTrue(events)
                self.assertEqual(result['outcome'], 'indeterminate' if fail else 'succeeded')
                self.assertTrue(result['cleanup']['cleanup_complete']); self.assertFalse(result['retry_safe'])
                self.assertNotIn('PRIVATE', str(result))
