"""Wizard contract tests. No transport, account, native code or filesystem IO."""
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from beta15_login_flow import (LoginFlow, FlowError, REVIEW_ITEMS, SCENARIOS,
                               SYNTHETIC_ACCOUNT, SUMMARY_KEYS, validate_summary)


def reviewed():
    flow = LoginFlow()
    for key, _ in REVIEW_ITEMS:
        flow.acknowledge(key)
    return flow


def pending():
    flow = reviewed(); flow.profile_result('fresh'); flow.start()
    return flow


def identity_review():
    flow = pending()
    for event in ('login_ack', 'account_present', 'status_authenticated'):
        flow.peer_event(event)
    return flow


class Hostile:
    def __eq__(self, other): raise AssertionError('caller comparison')
    def __str__(self): raise AssertionError('caller text')
    def __repr__(self): raise AssertionError('caller repr')
    def __iter__(self): raise AssertionError('caller iteration')


class Tests(unittest.TestCase):
    def test_native_mode_rejected_without_callbacks(self):
        for mode in ('native', 'real', '', True, None, Hostile()):
            with self.assertRaisesRegex(FlowError, '^NATIVE_LOGIN_NOT_AVAILABLE$'):
                LoginFlow(mode)
        self.assertEqual(LoginFlow().phase, 'review')

    def test_review_is_ordered_and_contains_material_disclosures(self):
        flow = LoginFlow()
        self.assertEqual(len(REVIEW_ITEMS), 7)
        for key, text in REVIEW_ITEMS:
            self.assertEqual(flow.view()['review_key'], key)
            self.assertEqual(flow.view()['review_text'], text)
            self.assertEqual(flow.drain_intents(), ())
            flow.acknowledge(key)
        self.assertEqual(flow.phase, 'profile_required')
        self.assertFalse(flow.summary()['profile_created'])
        self.assertTrue(flow.summary()['simulation_consent_only'])

    def test_review_duplicate_and_out_of_order_fail_closed(self):
        for key in (REVIEW_ITEMS[1][0], 'accept-all', Hostile()):
            flow = LoginFlow()
            with self.assertRaises(FlowError): flow.acknowledge(key)
            self.assertEqual(flow.summary()['outcome'], 'invalid')
            self.assertEqual(flow.drain_intents(), ())
        flow = LoginFlow(); flow.acknowledge(REVIEW_ITEMS[0][0])
        with self.assertRaises(FlowError): flow.acknowledge(REVIEW_ITEMS[0][0])

    def test_profile_only_after_all_review_and_no_existing_reads(self):
        flow = LoginFlow()
        with self.assertRaises(FlowError): flow.profile_result('fresh')
        for value in ('existing', 'protected'):
            flow = reviewed(); flow.profile_result(value)
            self.assertEqual(flow.summary()['outcome'], 'profile_refused')
            self.assertFalse(flow.summary()['profile_created'])
            self.assertEqual(flow.drain_intents(), ())
            flow.start(); self.assertFalse(flow.summary()['started'])

    def test_cancel_before_start_emits_nothing(self):
        for count in range(len(REVIEW_ITEMS) + 1):
            flow = LoginFlow()
            for key, _ in REVIEW_ITEMS[:count]: flow.acknowledge(key)
            flow.cancel(); flow.close()
            self.assertFalse(flow.summary()['profile_created'])
            self.assertEqual(flow.summary()['outcome'], 'cancelled_before_start')
            self.assertEqual(flow.drain_intents(), ())

    def test_profile_created_but_cancelled_before_start_is_retained(self):
        flow = reviewed(); flow.profile_result('fresh'); flow.cancel(); flow.close()
        self.assertEqual(flow.summary()['profile_disposition'], 'retain')
        self.assertFalse(flow.summary()['started']); self.assertEqual(flow.drain_intents(), ())

    def test_start_only_once_after_fresh_profile(self):
        flow = reviewed()
        with self.assertRaises(FlowError): flow.start()
        flow = pending(); self.assertEqual(flow.drain_intents(), ('synthetic.begin_login',))
        self.assertEqual(flow.drain_intents(), ())
        with self.assertRaises(FlowError): flow.start()
        self.assertEqual(flow.summary()['profile_disposition'], 'quarantine')

    def test_ack_read_status_and_manual_confirmation_are_separate(self):
        flow = pending(); flow.drain_intents(); flow.peer_event('login_ack')
        self.assertEqual(flow.phase, 'read_pending')
        self.assertFalse(flow.summary()['simulated_account_present'])
        self.assertEqual(flow.drain_intents(), ('synthetic.account_read',))
        flow.peer_event('account_present'); self.assertEqual(flow.phase, 'status_pending')
        self.assertFalse(flow.summary()['manual_identity_confirmed'])
        self.assertEqual(flow.drain_intents(), ('synthetic.auth_status',))
        flow.peer_event('status_authenticated'); self.assertEqual(flow.phase, 'identity_review')
        flow.confirm_identity(SYNTHETIC_ACCOUNT); flow.close()
        result = flow.summary(); self.assertEqual(result['outcome'], 'simulated_complete')
        self.assertTrue(result['manual_identity_confirmed'])
        self.assertFalse(result['real_account_verified']); self.assertFalse(result['real_model_ready'])
        self.assertEqual(flow.drain_intents(), ('synthetic.close_client', 'synthetic.close_harness'))

    def test_cancel_delivery_does_not_settle_request(self):
        flow = pending(); flow.drain_intents(); flow.cancel(); flow.cancel()
        self.assertEqual(flow.drain_intents(), ('synthetic.cancel_request',))
        flow.peer_event('cancel_delivered')
        self.assertEqual(flow.phase, 'cancel_pending')
        self.assertFalse(flow.summary()['login_settled']); self.assertEqual(flow.drain_intents(), ())
        flow.peer_event('login_error'); self.assertEqual(flow.phase, 'cancel_read_pending')
        self.assertEqual(flow.drain_intents(), ('synthetic.account_read',))
        flow.peer_event('account_absent'); flow.close()
        self.assertEqual(flow.summary()['outcome'], 'cancelled_no_account')
        self.assertFalse(flow.summary()['cancellation_proves_rollback'])

    def test_cancelled_race_retains_account_without_model_or_logout(self):
        for response in ('login_ack', 'login_error'):
            flow = pending(); flow.cancel(); flow.peer_event(response); flow.peer_event('account_present'); flow.close()
            result = flow.summary(); self.assertEqual(result['outcome'], 'cancelled_account_present')
            self.assertEqual(result['profile_disposition'], 'retain')
            self.assertFalse(result['manual_identity_confirmed']); self.assertFalse(result['real_model_ready'])
            self.assertNotIn('synthetic.auth_status', flow.drain_intents())

    def test_cancel_after_ack_does_not_duplicate_read(self):
        flow = pending(); flow.peer_event('login_ack'); flow.drain_intents(); flow.cancel()
        self.assertEqual(flow.phase, 'cancel_read_pending'); self.assertEqual(flow.drain_intents(), ())
        flow.peer_event('account_present')
        self.assertEqual(flow.summary()['outcome'], 'cancelled_account_present')

    def test_cancel_during_status_or_identity_retains_known_account(self):
        for status in (False, True):
            flow = pending(); flow.peer_event('login_ack'); flow.peer_event('account_present')
            if status: flow.peer_event('status_authenticated')
            flow.cancel(); flow.peer_event('status_authenticated')
            self.assertEqual(flow.summary()['outcome'], 'cancelled_account_present')
            self.assertFalse(flow.summary()['manual_identity_confirmed'])

    def test_error_with_account_present_is_not_retryable(self):
        flow = pending(); flow.peer_event('login_error'); flow.peer_event('account_present')
        self.assertEqual(flow.summary()['outcome'], 'account_present_unconfirmed')
        self.assertFalse(flow.summary()['automatic_retry_allowed'])
        self.assertFalse(flow.summary()['manual_identity_confirmed'])

    def test_unauthenticated_read_stops_without_status_or_retry(self):
        flow = pending(); flow.peer_event('login_ack'); flow.peer_event('account_absent')
        self.assertEqual(flow.summary()['outcome'], 'unauthenticated')
        self.assertNotIn('synthetic.auth_status', flow.drain_intents())

    def test_status_contradiction_quarantines(self):
        flow = pending(); flow.peer_event('login_ack'); flow.peer_event('account_present'); flow.peer_event('status_unauthenticated')
        self.assertEqual(flow.summary()['outcome'], 'unknown_retained')
        self.assertEqual(flow.summary()['profile_disposition'], 'quarantine')

    def test_manual_rejection_and_actual_identity_never_accepted(self):
        flow = identity_review(); flow.confirm_identity('reject')
        self.assertEqual(flow.summary()['outcome'], 'identity_rejected')
        for identity in ('real-user@example.invalid', Hostile(), {'token': 'PRIVATE'}):
            flow = identity_review()
            with self.assertRaises(FlowError): flow.confirm_identity(identity)
            self.assertEqual(flow.summary()['outcome'], 'invalid')
            self.assertNotIn('PRIVATE', flow.canonical_summary_bytes().decode())

    def test_timeout_and_late_success_never_revive(self):
        flow = pending(); flow.timeout()
        for _ in range(300): flow.peer_event('login_ack')
        flow.start(); flow.confirm_identity(SYNTHETIC_ACCOUNT); flow.close()
        result = flow.summary(); self.assertEqual(result['outcome'], 'unknown_retained')
        self.assertEqual(result['late_events'], 255); self.assertEqual(result['profile_disposition'], 'quarantine')
        self.assertFalse(result['manual_identity_confirmed'])
        self.assertEqual(flow.drain_intents().count('synthetic.begin_login'), 1)

    def test_close_pending_is_unknown_not_rollback_and_idempotent(self):
        flow = pending(); flow.close(); first = flow.drain_intents(); flow.close()
        self.assertEqual(flow.drain_intents(), ())
        self.assertEqual(first, ('synthetic.begin_login', 'synthetic.cancel_request', 'synthetic.close_client', 'synthetic.close_harness'))
        self.assertEqual(flow.summary()['outcome'], 'unknown_retained')
        self.assertFalse(flow.summary()['native_cleanup_verified'])

    def test_hostile_events_and_secrets_not_retained(self):
        for value in (Hostile(), {'password': 'PRIVATE'}, ['token', 'PRIVATE'], 'https://private.invalid/code=PRIVATE', 'PRIVATE'):
            flow = pending()
            with self.assertRaisesRegex(FlowError, '^INVALID_SIMULATION_ACTION$'): flow.peer_event(value)
            self.assertNotIn('PRIVATE', flow.canonical_summary_bytes().decode())
            self.assertEqual(flow.summary()['outcome'], 'invalid')

    def test_duplicate_out_of_order_events_fail_closed(self):
        for event in ('account_present', 'status_authenticated', 'cancel_delivered'):
            flow = pending()
            with self.assertRaises(FlowError): flow.peer_event(event)
        flow = pending(); flow.peer_event('login_ack')
        with self.assertRaises(FlowError): flow.peer_event('login_ack')

    def test_summary_exact_bounded_and_immutable_safety_flags(self):
        flow = identity_review(); flow.confirm_identity(SYNTHETIC_ACCOUNT); flow.close()
        summary = flow.summary(); self.assertEqual(set(summary), SUMMARY_KEYS)
        payload = flow.canonical_summary_bytes(); self.assertLessEqual(len(payload), 4096)
        self.assertEqual(json.loads(payload), validate_summary(summary))
        for key in ('real_account_verified', 'real_model_ready', 'native_backend_available',
                    'automatic_logout_allowed', 'automatic_profile_delete_allowed', 'cancellation_proves_rollback'):
            with self.assertRaises(FlowError): validate_summary({**summary, key: True})
        for key, value in [('simulation_consent_only', False), ('late_events', 256), ('acknowledgments', True),
                           ('phase', Hostile()), ('phase', 'x' * 100000), ('extra', 'PRIVATE')]:
            with self.assertRaises(FlowError): validate_summary({**summary, key: value})
        with self.assertRaises(FlowError): validate_summary(Hostile())

    def test_every_phase_summary_is_valid_and_ui_contains_no_input(self):
        flow = LoginFlow()
        validate_summary(flow.summary())
        for key, _ in REVIEW_ITEMS:
            flow.acknowledge(key); validate_summary(flow.summary())
        for action in (lambda: flow.profile_result('fresh'), flow.start,
                       lambda: flow.peer_event('login_ack'), lambda: flow.peer_event('account_present'),
                       lambda: flow.peer_event('status_authenticated'),
                       lambda: flow.confirm_identity(SYNTHETIC_ACCOUNT), flow.close):
            action(); validate_summary(flow.summary()); self.assertTrue(flow.view()['simulation_only'])
        self.assertEqual(len(SCENARIOS), 8)


if __name__ == '__main__': unittest.main()
