"""Configuration/startup checks do not contact a provider or a real Bridge."""
from dataclasses import replace
import json
from pathlib import Path
from email.message import Message
import tempfile
import unittest

from native_harness_api.contracts import Binding
from native_harness_api.runtime import RuntimeConfig, node_tick
from native_harness_api.errors import ChannelError


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = RuntimeConfig("source", "ledger", "http://127.0.0.1:8787",
            Binding("binding", 1, "synthetic-model", "fake-model"), ("tenant", "key"), Path(self.tmp.name))

    def test_defaults_do_not_admit_tasks(self):
        self.config.validate()
        self.assertFalse(self.config.accepting_new)

    def test_configuration_requires_explicit_test_mode_and_adapter(self):
        data = {"source": "source", "ledger_instance": "ledger", "bridge_url": "http://127.0.0.1:8787",
                "binding": self.config.binding.wire(), "owner": ["tenant", "key"], "data_dir": self.tmp.name}
        path = Path(self.tmp.name) / "config.json"
        for extra in ({}, {"mode": "p1b_integration"}, {"mode": "p1b_integration", "adapter": "workbuddy"}):
            path.write_text(json.dumps({**data, **extra}))
            with self.assertRaises(ValueError):
                RuntimeConfig.load(path)
        path.write_text(json.dumps({**data, "mode": "p1b_integration", "adapter": "fake"}))
        self.assertEqual(RuntimeConfig.load(path), self.config)

    def test_non_synthetic_bindings_rejected(self):
        for binding in (Binding("b", 1, "gpt-5", "fake-model"), Binding("b", 1, "synthetic-model", "gpt-5")):
            with self.assertRaises(ValueError):
                replace(self.config, binding=binding).validate()

    def test_distinct_credential_names_no_literal_secrets(self):
        with self.assertRaises(ValueError):
            replace(self.config, node_token_env=self.config.source_token_env).validate()
        with self.assertRaises(ValueError):
            self.config.facade({})
        with self.assertRaises(ValueError):
            self.config.facade({self.config.source_token_env: "test-token-identical", self.config.facade_token_env: "test-token-identical"})

    def test_facade_can_be_built_without_creating_journal_or_contacting_bridge(self):
        self.config.facade({self.config.source_token_env: "test-only-source-token", self.config.facade_token_env: "test-only-facade-token"})
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    def test_unbounded_timing_and_untrusted_origin_rejected(self):
        for change in ({"bridge_url": "http://external.invalid"}, {"wait_seconds": float("nan")},
                       {"poll_seconds": 0}, {"port": True}, {"accepting_new": 1}, {"data_dir": Path("relative")}):
            with self.subTest(change=change), self.assertRaises(Exception):
                replace(self.config, **change).validate()

    def test_single_owner_configuration(self):
        with self.assertRaises(ValueError):
            replace(self.config, owner=(("a", "b"), ("c", "d"))).validate()

    def test_frozen_route_checked_before_bridge_io(self):
        facade = self.config.facade({self.config.source_token_env: "test-only-source-token",
                                     self.config.facade_token_env: "test-only-facade-token"})
        for source, ledger in (("different-source", "ledger"), ("source", "different-ledger")):
            headers = Message()
            headers["Authorization"] = "Bearer test-only-facade-token"
            headers["X-Native-Source"] = source
            headers["X-Native-Ledger"] = ledger
            with self.assertRaises(ChannelError) as caught:
                facade.handle("POST", "/v1/chat/completions", headers, {})
            self.assertEqual(caught.exception.code, "FROZEN_ROUTE_MISMATCH")

    def test_heartbeat_does_not_clear_uncertain_journal(self):
        class Journal:
            def records(self):
                return [{"acknowledged": True, "liveness": "unknown"}]
        class Client:
            sent = None
            def heartbeat(self, state, bindings):
                self.sent = (state, bindings)
        class Policy:
            bindings = (self.config.binding,)
        class Runner:
            journal, client, policy = Journal(), Client(), Policy()
            def run_once(self, *, allow_new=True):
                if allow_new:
                    raise AssertionError("Quarantined recovery must not poll new work")
                return {"code": "SLOT_QUARANTINED"}
        runner = Runner()
        self.assertEqual(node_tick(runner), {"code": "SLOT_QUARANTINED"})
        self.assertEqual(runner.client.sent[0], "attention")

    def test_recovery_survives_heartbeat_failure_and_never_admits(self):
        class Client:
            def heartbeat(self, state, bindings):
                raise ChannelError("harness_binding_unavailable", 422)
        class Runner:
            client = Client()
            def run_once(self, *, allow_new=True):
                if allow_new:
                    raise AssertionError("Must not launch during recovery")
                return {"outcome": "succeeded", "liveness": "stopped_confirmed"}
        self.assertEqual(node_tick(Runner())["outcome"], "succeeded")


if __name__ == "__main__":
    unittest.main()
