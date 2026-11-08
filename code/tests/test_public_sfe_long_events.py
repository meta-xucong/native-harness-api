"""Synthetic bounded long-answer source streams; never vendor/model execution."""
import unittest
from native_harness_api.beta15_reducer import SessionReducer, REQUEST_ID
from native_harness_api.public_sfe_session import PublicSfeSession, account_identity_digest
from test_public_sfe_session import boot, identity, catalog, response, unpack, event, terminal, wire
from test_public_sfe_live_events import update, ack


class LongSourceEventTests(unittest.TestCase):
    def selected(self, sink=None):
        p = boot(PublicSfeSession(deadline=100, expected_account_digest=account_identity_digest(identity()['account']),
                                 clock=lambda: 0, on_event=sink))
        req = unpack(p.new_session('/synthetic'))[0]
        response(p, req, {'sessionId': 'root', 'configOptions': catalog()})
        req = unpack(p.select_model('b'))[0]
        response(p, req, {'configOptions': catalog('b')})
        return p

    def test_4000_chunks_before_or_after_ack_complete_full_text(self):
        for early in (False, True):
            with self.subTest(early=early):
                latest = {}; count = [0]
                def sink(value): latest.clear(); latest.update(value); count[0] += 1
                p = self.selected(sink); req = unpack(p.prompt('synthetic'))[0]
                if not early: response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
                for _ in range(4000): event(p, update(text='x')['update'])
                if early:
                    self.assertEqual(count[0], 0)
                    response(p, req, {'messageId': 'input', '_meta': {REQUEST_ID: 'run'}})
                self.assertEqual(count[0], 4000); self.assertEqual(latest['text'], 'x'*4000)
                event(p, terminal())
                self.assertEqual(p.evidence()['outcome'], 'succeeded')
                self.assertEqual(p.evidence()['turn']['text'], 'x'*4000)
                self.assertEqual(p.coordinator.reducer._buffer, [])

    def test_aggregate_utf8_limit_separate_from_4mib_input(self):
        for replacement in (False, True):
            r = SessionReducer('root', deadline=100, clock=lambda: 0, max_events=4096, max_bytes=4*1024*1024)
            ack(r)
            r.update(update(text='😀'*32768, mid='one'))
            r.update(update(text='😀'*32768, mid='two'))
            self.assertEqual(len(r.evidence()['text'].encode()), 262144)
            if replacement:
                r.update(update('agent_message', messageId='one', content=[{'type':'text','text':'😀'*32768+'x'}]))
            else: r.update(update(text='x', mid='two'))
            self.assertEqual(r.fault, 'TEXT_OVERFLOW'); self.assertEqual(r.outcome, 'indeterminate')
            self.assertEqual(len(r.evidence()['text'].encode()), 262144)

    def test_replacement_releases_text_budget(self):
        r = SessionReducer('root', deadline=100, clock=lambda: 0, max_events=4096, max_bytes=4*1024*1024); ack(r)
        r.update(update(text='x'*200000))
        r.update(update('agent_message', messageId='m', content=[]))
        r.update(update(text='y'*200000))
        self.assertIsNone(r.fault); self.assertEqual(r.evidence()['text'], 'y'*200000)

    def test_single_frame_limit_not_enlarged(self):
        r = SessionReducer('root', deadline=100, clock=lambda: 0, max_events=4096, max_bytes=4*1024*1024); ack(r)
        r.update(update(text='x'*262144))  # envelope overhead exceeds per-frame bound
        self.assertEqual(r.fault, 'INVALID_UPDATE')

    def test_public_frame_count_is_bounded_even_for_dropped_usage(self):
        p = self.selected(); req = unpack(p.prompt('synthetic'))[0]
        response(p, req, {'messageId':'input','_meta':{REQUEST_ID:'run'}})
        for _ in range(4097): event(p, {'sessionUpdate':'usage_update','used':0,'size':1})
        self.assertEqual(p.evidence()['outcome'], 'indeterminate')
        self.assertEqual(p.evidence()['failure_code'], 'limit')
        self.assertFalse(p.evidence()['retry_safe'])

    def test_public_raw_wire_total_includes_whitespace(self):
        p = self.selected(); req = unpack(p.prompt('synthetic'))[0]
        response(p, req, {'messageId':'input','_meta':{REQUEST_ID:'run'}})
        frame = wire({'jsonrpc':'2.0','method':'session/update','params':update('state_update', state='running')}) + b' '*200000
        for _ in range(22): p.receive(frame)
        self.assertEqual(p.evidence()['outcome'], 'indeterminate')
        self.assertEqual(p.evidence()['failure_code'], 'limit')

    def test_reducer_event_and_input_totals_fail_without_replay(self):
        r = SessionReducer('root', deadline=100, clock=lambda: 0, max_events=4096, max_bytes=4*1024*1024); ack(r)
        for _ in range(4097): r.update(update('state_update', state='running'))
        self.assertEqual(r.fault, 'OVERFLOW')
        r = SessionReducer('root', deadline=100, clock=lambda: 0, max_events=4096, max_bytes=4*1024*1024); ack(r)
        for _ in range(22):
            e = update('state_update', state='running'); e['update']['_meta']['padding'] = 'x'*200000; r.update(e)
        self.assertEqual(r.fault, 'OVERFLOW')
