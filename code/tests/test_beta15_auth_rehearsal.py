"""Pure auth boundary fixtures: no vendor, network, process, file or account IO."""
import contextlib
import io
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from beta15_auth_rehearsal import (
    AuthRehearsal, METHOD_ID, Outcome, Phase, main, run_rehearsals,
    synthetic_catalog, synthetic_snapshot,
)


class HostilePayload:
    def __getattribute__(self, name):
        raise AssertionError("PRIVATE_CANARY must not be inspected")


class AuthRehearsalTests(unittest.TestCase):
    def pending(self):
        fixture = AuthRehearsal()
        fixture.advertise(synthetic_catalog())
        fixture.begin_login()
        return fixture

    def test_fixed_suite_all_synthetic_never_native_ready(self):
        results = run_rehearsals()
        self.assertEqual(len(results), 7)
        self.assertEqual({r["outcome"] for r in results}, {
            "incomplete", "unauthenticated", "simulated_authenticated",
            "cancelled_unverified", "timed_out", "denied", "protected_restore_blocked"})
        for result in results:
            self.assertTrue(result["all_authentication_synthetic"])
            self.assertFalse(result["native_account_verified"])
            self.assertFalse(result["real_model_ready"])
            self.assertFalse(result["cancellation_proves_native_rollback"])
            self.assertTrue(result["synthetic_fixture_closed"])
            self.assertEqual(result["synthetic_close_intents"], 2)

    def test_ack_only_never_counts_as_authentication(self):
        fixture = self.pending()
        fixture.acknowledge_login()
        self.assertTrue(fixture.login_acknowledged)
        self.assertEqual(fixture.phase, Phase.AWAITING_READ)
        self.assertFalse(fixture.summary()["simulated_authenticated"])
        self.assertEqual(fixture.intents, ["synthetic_login_intent", "synthetic_account_read_intent"])

    def test_separate_snapshot_can_only_prove_fake_success(self):
        fixture = self.pending()
        fixture.acknowledge_login()
        fixture.account_snapshot(synthetic_snapshot(True))
        self.assertTrue(fixture.summary()["simulated_authenticated"])
        self.assertFalse(fixture.summary()["native_account_verified"])

    def test_pre_ack_notification_is_not_readback(self):
        fixture = self.pending()
        fixture.account_snapshot(synthetic_snapshot(True))
        self.assertEqual(fixture.outcome, Outcome.INVALID)
        fixture.acknowledge_login()
        self.assertFalse(fixture.summary()["simulated_authenticated"])

    def test_cancel_does_not_read_payload_or_claim_rollback(self):
        fixture = self.pending()
        self.assertEqual(fixture.cancel_elicitation(HostilePayload()), {"action": "cancel"})
        fixture.acknowledge_login()
        fixture.account_snapshot(synthetic_snapshot(True))
        self.assertEqual(fixture.outcome, Outcome.CANCELLED_UNVERIFIED)
        self.assertEqual(fixture.summary()["synthetic_account_read_intents"], 0)
        self.assertFalse(fixture.summary()["cancellation_proves_native_rollback"])

    def test_timeout_and_denial_are_terminal_no_retry(self):
        for stop in ("timeout", "deny"):
            with self.subTest(stop=stop):
                fixture = self.pending()
                getattr(fixture, stop)()
                fixture.begin_login()
                fixture.acknowledge_login()
                fixture.account_snapshot(synthetic_snapshot(True))
                self.assertEqual(fixture.summary()["synthetic_login_intents"], 1)
                self.assertFalse(fixture.summary()["simulated_authenticated"])

    def test_protected_restore_never_decodes_or_requests_login(self):
        fixture = AuthRehearsal()
        fixture.request_protected_restore(HostilePayload())
        fixture.advertise(synthetic_catalog())
        fixture.begin_login()
        self.assertEqual(fixture.outcome, Outcome.PROTECTED_RESTORE_BLOCKED)
        self.assertEqual(fixture.summary()["synthetic_login_intents"], 0)

    def test_closed_fixture_cannot_be_revived(self):
        fixture = self.pending()
        fixture.close()
        fixture.acknowledge_login()
        fixture.account_snapshot(synthetic_snapshot(True))
        fixture.close()
        self.assertEqual(fixture.summary()["synthetic_close_intents"], 2)
        self.assertFalse(fixture.summary()["simulated_authenticated"])

    def test_duplicate_login_ack_or_read_is_invalid(self):
        for repeat in ("begin_login", "acknowledge_login", "account_snapshot"):
            fixture = self.pending()
            if repeat != "begin_login":
                fixture.acknowledge_login()
            if repeat == "account_snapshot":
                fixture.account_snapshot(synthetic_snapshot(True))
                fixture.account_snapshot(synthetic_snapshot(True))
            else:
                getattr(fixture, repeat)()
            self.assertEqual(fixture.outcome, Outcome.INVALID)
            self.assertFalse(fixture.summary()["simulated_authenticated"])

    def test_catalog_requires_unique_exact_advertised_provider(self):
        malformed = [None, {}, [], synthetic_catalog() * 2, [{}],
                     [{"methodId": METHOD_ID, "providerType": "cli-external-link", "type": "agent"}],
                     [{"methodId": "PRIVATE_CANARY", "type": "agent"}],
                     [dict(synthetic_catalog()[0], secret="PRIVATE_CANARY")],
                     synthetic_catalog() * 17]
        for catalog in malformed:
            fixture = AuthRehearsal()
            fixture.advertise(catalog)
            fixture.begin_login()
            self.assertEqual(fixture.outcome, Outcome.INVALID)
            self.assertEqual(fixture.summary()["synthetic_login_intents"], 0)
            self.assertNotIn("PRIVATE_CANARY", json.dumps(fixture.summary()))

    def test_account_data_is_fixture_only_and_never_exported(self):
        malformed = [None, {}, [],
                     {"state": "authenticated", "mode": "external", "account": {"id": "PRIVATE_CANARY"}},
                     {"state": "authenticated", "mode": "managed", "account": {"id": "PRIVATE_CANARY"}},
                     {"state": "authenticated", "mode": "managed", "account": {"id": 1}},
                     dict(synthetic_snapshot(True), accessToken="PRIVATE_CANARY"),
                     {**synthetic_snapshot(True), "account": {"id": "synthetic-auth-rehearsal-only", "token": "PRIVATE_CANARY"}}]
        for snapshot in malformed:
            fixture = self.pending()
            fixture.acknowledge_login()
            fixture.account_snapshot(snapshot)
            self.assertEqual(fixture.outcome, Outcome.INVALID)
            self.assertNotIn("PRIVATE_CANARY", json.dumps(fixture.summary()))

    def test_non_json_values_cannot_invoke_comparison_callbacks(self):
        class HostileValue:
            def __eq__(self, other):
                raise AssertionError("must not compare arbitrary objects")
        for field in ("methodId", "providerType", "type"):
            catalog = synthetic_catalog()
            catalog[0][field] = HostileValue()
            fixture = AuthRehearsal()
            fixture.advertise(catalog)
            self.assertEqual(fixture.outcome, Outcome.INVALID)
        for field in ("state", "mode"):
            snapshot = synthetic_snapshot(True)
            snapshot[field] = HostileValue()
            fixture = self.pending()
            fixture.acknowledge_login()
            fixture.account_snapshot(snapshot)
            self.assertEqual(fixture.outcome, Outcome.INVALID)

    def test_late_events_and_cancel_counters_are_bounded(self):
        fixture = self.pending()
        fixture.close()
        for _ in range(1000):
            fixture.cancel_elicitation(HostilePayload())
        self.assertEqual(fixture.late_events, 255)
        self.assertEqual(fixture.cancel_replies, 255)
        self.assertEqual(fixture.summary()["synthetic_close_intents"], 2)

    def test_cancel_after_synthetic_success_revokes_only_fake_result(self):
        fixture = self.pending()
        fixture.acknowledge_login()
        fixture.account_snapshot(synthetic_snapshot(True))
        fixture.cancel_elicitation()
        self.assertFalse(fixture.summary()["simulated_authenticated"])
        self.assertFalse(fixture.summary()["cancellation_proves_native_rollback"])

    def test_no_session_model_or_provider_commands_exist(self):
        allowed = {"synthetic_login_intent", "synthetic_account_read_intent",
                   "synthetic_client_close_intent", "synthetic_server_close_intent"}
        fixture = self.pending()
        fixture.acknowledge_login()
        fixture.account_snapshot(synthetic_snapshot(True))
        fixture.close()
        self.assertTrue(set(fixture.intents) <= allowed)
        for name in ("connect", "execute", "restore", "open_browser", "load_provider", "prompt"):
            self.assertFalse(hasattr(fixture, name))

    def test_cli_summary_is_bounded_and_explicitly_synthetic(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(), 0)
        raw = output.getvalue()
        self.assertLess(len(raw), 8192)
        self.assertTrue(json.loads(raw)["all_authentication_synthetic"])
        self.assertNotIn("synthetic-auth-rehearsal-only", raw)


if __name__ == "__main__":
    unittest.main()
