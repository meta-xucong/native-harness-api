"""Offline probe/orchestration tests. The fake here is not WorkBuddy evidence."""
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from native_harness_api.workbuddy_acp import ACPError
from native_harness_api.workbuddy_probe import (
    MODE, SCHEMA, TrialConfig, TrialError, TrialStore, doctor, export_summary,
    main, run_trial, trial_lock, _native_summary, _permission, _display,
)


class FakeACP:
    """Explicit local protocol double; never claims to execute a vendor model."""
    starts = 0
    prompts = 0
    mode = "success"
    text = "Synthetic ACP test output"
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.session_id = None
    def start(self):
        type(self).starts += 1
    def initialize(self):
        return {"agentInfo": {"name": "synthetic-peer", "version": "1", "secret": "never-export"}}
    def new_session(self, cwd):
        self.session_id = "test-session-private"
        return {"sessionId": self.session_id, "models": {"currentModelId": "model-one", "availableModels": [
            {"modelId": "model-one", "secret": "never-export"}]}, "modes": {"currentModeId": "default"}}
    def set_model(self, model):
        if self.mode == "bad-model":
            raise ACPError("ACP_MODEL_UNAVAILABLE")
        return {"model_id": model, "readback_confirmed": True}
    def prompt(self, text, before_send):
        before_send()
        type(self).prompts += 1
        if self.mode == "eof":
            raise ACPError("ACP_EOF")
        if self.mode == "interrupt":
            raise KeyboardInterrupt()
        return {"session_id": self.session_id, "text": self.text, "stop_reason": "end_turn",
                "model_id": "model-one", "native_completion": True, "permissions": []}
    def close(self):
        return {"host_exited": True, "descendants_stopped": False}


class ProbeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.install = self.root / "Official WorkBuddy"
        self.install.mkdir()
        executable = self.install / "WorkBuddy.exe"
        executable.write_bytes(b"Explicit nonexecutable fixture, not vendor software")
        entry = self.install / "resources" / "cli" / "bin" / "codebuddy-lite-wb"
        entry.parent.mkdir(parents=True)
        entry.write_bytes(b"Explicit synthetic bundle fixture")
        workspace = self.root / "empty workspace"
        workspace.mkdir()
        self.config = TrialConfig(executable, entry, self.root / "state", workspace, "model-one")
        self.config_path = self.root / "config.json"
        self.write_config()
        FakeACP.starts = FakeACP.prompts = 0
        FakeACP.mode, FakeACP.text = "success", "Synthetic ACP test output"

    def write_config(self, **changes):
        value = {"schema": SCHEMA, "mode": MODE, "executable": str(self.config.executable),
                 "cli_entry": str(self.config.cli_entry), "data_dir": str(self.config.data_dir),
                 "workspace": str(self.config.workspace), "model_id": self.config.model_id,
                 "timeout_seconds": 120, "native_config_dir": None}
        value.update(changes)
        self.config_path.write_text(json.dumps(value), encoding="utf-8")

    def test_config_strict_schema_mode_fields_and_timeout(self):
        self.assertEqual(TrialConfig.load(self.config_path), self.config)
        for change in ({"schema": "wrong"}, {"mode": "fake"}, {"token": "private"},
                       {"timeout_seconds": True}, {"timeout_seconds": 601}, {"executable": "relative"}):
            with self.subTest(change=change):
                self.write_config(**change)
                with self.assertRaises(TrialError):
                    TrialConfig.load(self.config_path)

    def test_duplicate_json_nonfinite_and_large_config_rejected(self):
        for raw in ['{"schema":"x","schema":"x"}', '{"timeout_seconds":NaN}', ' ' * 65537]:
            self.config_path.write_text(raw)
            with self.assertRaises(TrialError):
                TrialConfig.load(self.config_path)

    def test_only_selected_bundle_entry_not_other_cli(self):
        for changed in (replace(self.config, executable=self.root / "CodeBuddy.exe"),
                        replace(self.config, cli_entry=self.root / "codebuddy"),
                        replace(self.config, cli_entry=self.install / "credentials.json")):
            with self.assertRaises(TrialError):
                changed.validate()
        self.assertEqual(self.config.command()[-3:], ["--acp", "--permission-mode", "default"])

    def test_environment_never_inherits_api_keys_or_proxies(self):
        env = self.config.environment({"PATH": "safe", "SYSTEMROOT": "safe", "HOME": "safe",
            "OPENAI_API_KEY": "secret", "WORKBUDDY_TOKEN": "secret", "NATIVE_BRIDGE_NODE_TOKEN": "secret",
            "HTTP_PROXY": "secret", "NODE_OPTIONS": "secret", "PYTHONPATH": "secret"})
        self.assertNotIn("secret", env.values())
        self.assertEqual(env["ELECTRON_RUN_AS_NODE"], "1")
        self.assertNotIn("CODEBUDDY_CONFIG_DIR", env)
        selected = replace(self.config, native_config_dir=self.root / "native-settings")
        self.assertEqual(selected.environment({})["CODEBUDDY_CONFIG_DIR"], str(selected.native_config_dir))

    def test_state_and_workspace_ancestor_overlap_rejected_on_filesystem(self):
        for state, workspace in ((self.config.workspace, self.config.workspace),
                (self.config.workspace / "state", self.config.workspace),
                (self.root / "state", self.root / "state" / "workspace")):
            with self.subTest(state=state, workspace=workspace):
                state.mkdir(parents=True, exist_ok=True)
                workspace.mkdir(parents=True, exist_ok=True)
                config = replace(self.config, data_dir=state, workspace=workspace)
                with self.assertRaisesRegex(TrialError, "DEDICATED_DIRECTORY_REQUIRED"):
                    run_trial(config, "one", client_factory=FakeACP)
                self.assertFalse((state / "prompt-intent").exists())
        self.assertEqual(FakeACP.prompts, 0)

    def test_state_cannot_be_erased_with_bundle_or_native_config(self):
        for config in (replace(self.config, data_dir=self.install / "state"),
                       replace(self.config, native_config_dir=self.config.data_dir / "settings"),
                       replace(self.config, native_config_dir=self.root)):
            with self.assertRaisesRegex(TrialError, "DEDICATED_DIRECTORY_REQUIRED"):
                config.validate()

    @unittest.skipUnless(os.name == "nt", "Windows case-insensitive filesystem semantics")
    def test_windows_case_spelling_and_extended_paths_do_not_bypass_overlap(self):
        workspace = self.config.workspace
        for state in (Path(str(workspace).upper()) / "state", Path("\\\\?\\" + str(workspace)) / "state"):
            with self.assertRaisesRegex(TrialError, "DEDICATED_DIRECTORY_REQUIRED"):
                replace(self.config, data_dir=state).validate()

    def test_static_doctor_does_not_create_state_or_launch(self):
        value = doctor(self.config, client_factory=FakeACP)
        self.assertEqual(value["native_probe"], "NOT_RUN")
        self.assertFalse(self.config.data_dir.exists())
        self.assertEqual(FakeACP.starts, 0)

    def test_launch_doctor_no_prompt_and_no_secret_metadata(self):
        value = doctor(self.config, launch=True, client_factory=FakeACP)
        self.assertEqual(FakeACP.starts, 1)
        self.assertEqual(FakeACP.prompts, 0)
        self.assertNotIn("never-export", json.dumps(value))
        self.assertNotIn("test-session-private", json.dumps(value))
        self.assertFalse((self.config.data_dir / "trial.sqlite").exists())
        self.assertEqual(value["native_summary"]["agent_name"], "synthetic-peer")

    def test_malformed_native_metadata_is_not_dumped(self):
        result = _native_summary({"agentInfo": "secret"}, {"models": 1, "modes": [], "configOptions": {"secret": 1}})
        self.assertNotIn("secret", json.dumps(result))

    def test_console_escapes_unicode_control_and_bidi(self):
        value = _display("中文\n\t\x1b[31m\u009b31m\u202ehidden\u2066text")
        for hidden in ("\x1b", "\u009b", "\u202e", "\u2066"):
            self.assertNotIn(hidden, value)
        self.assertIn("\\u202e", value)
        self.assertIn("中文\n\t", value)

    def test_single_send_replay_without_native_readiness(self):
        first = run_trial(self.config, "private prompt", client_factory=FakeACP)
        self.assertEqual(first["status"], "native_turn_returned")
        self.assertTrue(first["host_exited"])
        self.assertFalse(first["descendants_stopped"])
        self.config.executable.unlink()
        second = run_trial(self.config, "private prompt", client_factory=FakeACP)
        self.assertTrue(second["replay"])
        self.assertEqual((FakeACP.starts, FakeACP.prompts), (1, 1))

    def test_different_request_or_model_cannot_reuse_root(self):
        run_trial(self.config, "one", client_factory=FakeACP)
        for config, text in ((self.config, "two"), (replace(self.config, model_id="other"), "one")):
            with self.assertRaisesRegex(TrialError, "TRIAL_ALREADY_USED"):
                run_trial(config, text, client_factory=FakeACP)
        self.assertEqual(FakeACP.prompts, 1)

    def test_unknown_intent_never_resends(self):
        FakeACP.mode = "eof"
        with self.assertRaisesRegex(ACPError, "ACP_EOF"):
            run_trial(self.config, "one", client_factory=FakeACP)
        FakeACP.mode = "success"
        replay = run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(replay["status"], "unknown")
        self.assertTrue(replay["replay"])
        self.assertEqual((FakeACP.starts, FakeACP.prompts), (1, 1))

    def test_interrupt_keeps_unknown_and_one_intent(self):
        FakeACP.mode = "interrupt"
        with self.assertRaisesRegex(TrialError, "INTERRUPTED_NO_RETRY"):
            run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(TrialStore(self.config.data_dir).load()["status"], "unknown")
        self.assertEqual(FakeACP.prompts, 1)

    def test_intent_write_failure_prevents_prompt(self):
        with patch.object(TrialStore, "intent", side_effect=TrialError("TRIAL_INTENT_NOT_COMMITTED")):
            with self.assertRaisesRegex(TrialError, "TRIAL_INTENT_NOT_COMMITTED"):
                run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.prompts, 0)

    def test_model_failure_before_intent_does_not_claim_execution(self):
        FakeACP.mode = "bad-model"
        with self.assertRaisesRegex(ACPError, "ACP_MODEL_UNAVAILABLE"):
            run_trial(self.config, "one", client_factory=FakeACP)
        self.assertIsNone(TrialStore(self.config.data_dir).load())
        self.assertEqual(FakeACP.prompts, 0)
        FakeACP.mode = "success"
        run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.prompts, 1)

    def test_auto_or_placeholder_model_never_launches(self):
        for model in ("auto", "default", "MODEL_ID_FROM_DOCTOR"):
            with self.assertRaisesRegex(TrialError, "EXPLICIT_MODEL_REQUIRED"):
                run_trial(replace(self.config, model_id=model), "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.starts, 0)

    def test_bad_prompt_never_launches(self):
        for text in ("", " ", "a\0b", "x" * 65537):
            with self.assertRaisesRegex(TrialError, "INVALID_PROMPT"):
                run_trial(self.config, text, client_factory=FakeACP)
        self.assertEqual(FakeACP.starts, 0)

    def test_missing_database_or_corrupt_record_stops_without_retry(self):
        run_trial(self.config, "one", client_factory=FakeACP)
        db = self.config.data_dir / "trial.sqlite"
        with sqlite3.connect(db) as con:
            con.execute("UPDATE trial SET record='{}'")
        con.close()
        with self.assertRaisesRegex(TrialError, "TRIAL_STATE_CORRUPT"):
            run_trial(self.config, "one", client_factory=FakeACP)
        db.unlink()
        with self.assertRaisesRegex(TrialError, "TRIAL_STATE_MISSING"):
            run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.prompts, 1)

    def test_launch_doctor_never_spawns_after_any_intent(self):
        run_trial(self.config, "one", client_factory=FakeACP)
        with self.assertRaisesRegex(TrialError, "NO_NATIVE_RELAUNCH_AFTER_INTENT"):
            doctor(self.config, launch=True, client_factory=FakeACP)
        self.assertEqual(FakeACP.starts, 1)

    def test_lost_row_or_marker_never_allows_repeat(self):
        run_trial(self.config, "one", client_factory=FakeACP)
        marker = self.config.data_dir / "prompt-intent"
        original = marker.read_text()
        marker.unlink()
        with self.assertRaisesRegex(TrialError, "TRIAL_INTENT_MARKER_MISMATCH"):
            run_trial(self.config, "one", client_factory=FakeACP)
        marker.write_text(original)
        con = sqlite3.connect(self.config.data_dir / "trial.sqlite")
        try:
            with con:
                con.execute("DELETE FROM trial")
        finally:
            con.close()
        with self.assertRaisesRegex(TrialError, "TRIAL_INTENT_INCOMPLETE"):
            run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.prompts, 1)

    def test_partial_intent_marker_blocks_future_send(self):
        with trial_lock(self.config.data_dir):
            store = TrialStore(self.config.data_dir, create=True)
            store.intent_marker.write_text("0" * 64 + "\n")
        with self.assertRaisesRegex(TrialError, "TRIAL_INTENT_INCOMPLETE"):
            run_trial(self.config, "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.starts, 0)

    def test_bundle_upgrade_during_handshake_blocks_before_intent(self):
        config = self.config
        class UpdatingACP(FakeACP):
            def set_model(self, model):
                config.cli_entry.write_bytes(b"changed bundle")
                return super().set_model(model)
        with self.assertRaisesRegex(TrialError, "BUNDLE_CHANGED_BEFORE_PROMPT"):
            run_trial(config, "one", client_factory=UpdatingACP)
        self.assertIsNone(TrialStore(config.data_dir).load())

    def test_export_contains_no_prompt_output_session_or_paths(self):
        FakeACP.text = "PRIVATE ANSWER secret-value"
        record = run_trial(self.config, "PRIVATE PROMPT", client_factory=FakeACP)
        record["unknown"] = "secret-value"
        value = export_summary(record)
        encoded = json.dumps(value)
        for forbidden in ("PRIVATE", "secret-value", "test-session-private", str(self.root)):
            self.assertNotIn(forbidden, encoded)
        self.assertEqual(value["liveness"], "unknown")
        self.assertEqual(value["usage"], "unknown")
        self.assertFalse(value["provider_model_independently_verified"])
        self.assertFalse(value["formal_model_channel"])

    def test_export_never_copies_native_identity_or_arbitrary_metadata(self):
        record = run_trial(self.config, "one", client_factory=FakeACP)
        private = "C:/Users/private/secret-token-value"
        record["native_agent"] = {"name": private, "version": private}
        record["model_id"] = private
        record["bundle"]["executable_sha256"] = private
        record["bundle"]["secret"] = private
        record["error_code"] = private
        record["result"]["stop_reason"] = private
        value = export_summary(record)
        self.assertNotIn(private, json.dumps(value))
        self.assertNotIn("native_agent", value)
        self.assertNotIn("model_id", value)
        self.assertEqual(value["native_stop_reason"], "unverified")
        self.assertEqual(value["error_code"], "UNRECOGNIZED_ERROR")

    def test_empty_output_is_not_text_success(self):
        FakeACP.text = ""
        value = export_summary(run_trial(self.config, "one", client_factory=FakeACP))
        self.assertFalse(value["text_turn_complete"])

    def test_native_stop_other_than_end_turn_not_complete(self):
        record = run_trial(self.config, "one", client_factory=FakeACP)
        record["result"]["stop_reason"] = "max_tokens"
        self.assertFalse(export_summary(record)["text_turn_complete"])

    def test_cross_process_lock_prevents_new_launcher(self):
        source = str(Path(__file__).resolve().parents[1] / "src")
        script = "from pathlib import Path; from native_harness_api.workbuddy_probe import trial_lock; import sys;\nwith trial_lock(Path(sys.argv[1])): print('UNEXPECTED')"
        env = dict(os.environ, PYTHONPATH=source)
        with trial_lock(self.config.data_dir):
            result = subprocess.run([sys.executable, "-c", script, str(self.config.data_dir)],
                                    env=env, text=True, capture_output=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("TRIAL_BUSY", result.stderr)
        self.assertNotIn("UNEXPECTED", result.stdout)

    def test_cli_refusal_no_state_or_prompt(self):
        with patch("builtins.input", return_value="no"), redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            code = main(["run", "--config", str(self.config_path), "--prompt-file", str(self.root / "absent")])
        self.assertEqual(code, 2)
        self.assertFalse(self.config.data_dir.exists())

    def test_piped_confirmation_is_rejected_before_prompt_read(self):
        with patch("sys.stdin", io.StringIO("RUN REAL WORKBUDDY\n")), redirect_stderr(io.StringIO()) as err:
            code = main(["run", "--config", str(self.config_path), "--prompt-file", str(self.root / "absent")])
        self.assertEqual(code, 2)
        self.assertIn("INTERACTIVE_TERMINAL_REQUIRED", err.getvalue())
        self.assertFalse(self.config.data_dir.exists())

    def test_release_has_no_confirmation_bypass_flag(self):
        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            main(["run", "--config", str(self.config_path), "--prompt-file", "absent", "--confirm-real-run"])
        self.assertEqual(caught.exception.code, 2)
        self.assertFalse(self.config.data_dir.exists())

    def test_interactive_cli_confirmation_executes_one_synthetic_trial(self):
        class Terminal(io.StringIO):
            def isatty(self):
                return True
        prompt = self.root / "prompt.txt"
        prompt.write_text("one", encoding="utf-8")
        def offline(config, text, permission_handler):
            return run_trial(config, text, client_factory=FakeACP, permission_handler=permission_handler)
        output, errors = Terminal(), Terminal()
        with patch("sys.stdin", Terminal()), patch("sys.stdout", output), patch("sys.stderr", errors), patch("builtins.input", return_value="RUN REAL WORKBUDDY"), patch("native_harness_api.workbuddy_probe.run_trial", side_effect=offline):
            self.assertEqual(main(["run", "--config", str(self.config_path), "--prompt-file", str(prompt)]), 0)
            self.assertEqual(main(["run", "--config", str(self.config_path), "--prompt-file", str(prompt)]), 0)
        self.assertEqual(FakeACP.prompts, 1)
        self.assertIn("Synthetic ACP test output", output.getvalue())
        self.assertIn("Existing native allow rules", errors.getvalue())

    def test_piped_permission_never_approves(self):
        with patch("sys.stdin", io.StringIO("ALLOW ONCE\n")), redirect_stderr(io.StringIO()), patch("builtins.input") as prompt:
            self.assertEqual(_permission({"toolCall": {"title": "test"}}), "reject_once")
            prompt.assert_not_called()

    def test_interactive_permission_only_explicit_once(self):
        with patch.object(sys.stdin, "isatty", return_value=True), patch.object(sys.stdout, "isatty", return_value=True), patch.object(sys.stderr, "isatty", return_value=True), patch("builtins.input", return_value="ALLOW ONCE"):
            self.assertEqual(_permission({"toolCall": {"title": "synthetic test"}}), "allow_once")

    def test_inspect_and_export_never_start_native(self):
        out = io.StringIO()
        with redirect_stdout(out):
            self.assertEqual(main(["inspect", "--config", str(self.config_path)]), 0)
        self.assertEqual(json.loads(out.getvalue())["status"], "NOT_RUN")
        target = self.root / "summary.json"
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main(["export", "--config", str(self.config_path), "--output", str(target)]), 0)
        self.assertEqual(json.loads(target.read_text())["status"], "NOT_RUN")
        with redirect_stderr(io.StringIO()):
            self.assertEqual(main(["export", "--config", str(self.config_path), "--output", str(target)]), 2)
        self.assertFalse(self.config.data_dir.exists())

    def test_symlink_state_or_entry_rejected(self):
        link = self.root / "link-state"
        try:
            link.symlink_to(self.config.workspace, target_is_directory=True)
        except OSError:
            self.skipTest("symlink creation unavailable")
        with self.assertRaisesRegex(TrialError, "LINK_PATH_REJECTED"):
            run_trial(replace(self.config, data_dir=link), "one", client_factory=FakeACP)
        self.assertEqual(FakeACP.prompts, 0)


if __name__ == "__main__":
    unittest.main()
