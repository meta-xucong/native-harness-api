"""Independent launcher review regressions; no vendor process or account."""
import contextlib
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / p) for p in ('packaging', 'tools')]
import quick_start as quick
import public_harness_models as models


class QuickStartReviewTests(unittest.TestCase):
    def test_output_failure_after_catalog_keeps_retained_ownership(self):
        profile = SimpleNamespace(root=Path('C:/Profiles/workbuddy-managed-' + 'a'*32),
                                  state='authenticated_confirmed', expected_account_digest='a'*64)
        result = {'success': True, 'catalog': {'models': [{'id': 'vendor-model', 'name': 'Model'}]}}
        with patch('public_harness_runtime_assets.verify_runtime_assets', return_value={}), \
             patch('public_harness_login_profile.open_profile', return_value=profile), \
             patch.object(models, 'discover_models', return_value=result), \
             patch.object(models, 'hold_retained') as hold, \
             patch('builtins.print', side_effect=[None, OSError('fixture output closed')]):
            with self.assertRaises(OSError):
                quick.discover_target(Path('C:/Bundle'), profile.root)
        hold.assert_called_once()

    def test_invalid_selected_config_cannot_replace_saved_pointer(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            quick.remember(root, root / 'old.json')
            before = (root / 'launcher.json').read_bytes()
            with patch.object(quick, 'console_required'), \
                 patch.object(quick, 'load_node', side_effect=ValueError('invalid fixture')), \
                 patch.object(quick, 'start_node') as run, contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(quick.main(root, ['--config', str(root / 'bad.json')]), 1)
            self.assertEqual((root / 'launcher.json').read_bytes(), before)
            run.assert_not_called()

    def test_empty_token_does_not_start_or_persist(self):
        config = SimpleNamespace(node_token_env='REVIEW_NODE_TOKEN')
        with patch.dict(os.environ, {}, clear=True), \
             patch.object(quick.getpass, 'getpass', return_value=''), \
             patch('native_harness_api.runtime.main') as runtime:
            with self.assertRaises(quick.SetupError):
                quick.start_node(Path('node.json'), config)
            self.assertNotIn(config.node_token_env, os.environ)
        runtime.assert_not_called()

    def test_codex_default_starts_codex_and_explicit_help_has_no_start(self):
        import importlib.util
        location = Path(__file__).resolve().parents[1] / 'packaging' / 'client.py'
        spec = importlib.util.spec_from_file_location('review_client', location)
        client = importlib.util.module_from_spec(spec); spec.loader.exec_module(client)
        original_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), original_path))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / 'CLIENT-MANIFEST.json').write_text(json.dumps({'kind': 'native-harness-windows-codex-client'}))
            with patch.object(client, '__file__', str(root / 'client.py')), \
                 patch.object(quick, 'main', return_value=0) as start:
                self.assertEqual(client.main([]), 0)
                start.assert_called_once_with(root, [], harness='codex')
                start.reset_mock()
                with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as result:
                    client.main(['--help'])
                self.assertEqual(result.exception.code, 0)
                start.assert_not_called()

    def test_bad_arguments_never_echo_input_values(self):
        output = io.StringIO()
        with contextlib.redirect_stderr(output), patch.object(quick, 'console_required') as console, \
             self.assertRaises(SystemExit) as result:
            quick.main(Path('.'), ['--unknown', 'PRIVATE_ARGUMENT_FIXTURE'])
        self.assertEqual(result.exception.code, 2)
        self.assertNotIn('PRIVATE_ARGUMENT_FIXTURE', output.getvalue())
        console.assert_not_called()

    def test_redirected_legacy_encoding_can_print_help(self):
        import subprocess
        packaging = Path(__file__).resolve().parents[1] / 'packaging'
        script = "import sys;sys.path.insert(0,sys.argv[1]);import quick_start;sys.stdout.reconfigure(encoding='cp1252');sys.stderr.reconfigure(encoding='cp1252');quick_start.main(sys.argv[1],['--help'])"
        result = subprocess.run([sys.executable, '-I', '-c', script, str(packaging)], capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('首次设置', result.stdout.decode('utf-8'))
