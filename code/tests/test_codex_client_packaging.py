"""Codex selection fixtures: package only data, never launch the vendor."""
import contextlib
import io
import json
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'packaging'))
import build_client as builder
from public_harness_catalog_assets import tree_digest
from test_client_launcher import client


class CodexPackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.assets = self.root / 'assets'
        (self.assets / 'bin').mkdir(parents=True)
        (self.assets / 'bin/codex-app-server.exe').write_bytes(b'fixture only')
        self.source = self.root / 'client.py'
        self.source.write_bytes(b'# fixture\n')
        self.runtime = self.root / 'runtime.zip'
        self.runtime.write_bytes(b'fixture')
        for item in (patch.object(builder, 'verify_codex_assets', lambda _: None),
                     patch.object(builder, 'sources', lambda *_: {'client.py': self.source}),
                     patch.object(builder, 'runtime_files', lambda _: {'runtime/python.exe': b'fixture python'}),
                     patch.object(builder, 'CODEX_TREE', tree_digest(self.assets))):
            item.start()
            self.addCleanup(item.stop)

    def test_codex_manifest_and_deterministic_package(self):
        a, b = self.root / 'a.zip', self.root / 'b.zip'
        for output in (a, b):
            builder.build_client(self.root, self.assets, self.runtime, output, 'a' * 40, harness='codex')
        self.assertEqual(a.read_bytes(), b.read_bytes())
        manifest = builder.verify_client(a)
        self.assertEqual(manifest['kind'], builder.CODEX_KIND)
        self.assertIn('assets/bin/codex-app-server.exe', manifest['files'])
        self.assertNotIn('assets/product.json', manifest['files'])
        self.assertEqual(manifest['real_account_model_acceptance'], 'NOT_RUN')

    def test_large_exception_exact_path_and_size_only(self):
        exact = [('assets/bin/codex-app-server.exe', 280844080, False)]
        builder.client_inventory(exact, 'codex')
        for entries, harness in ((exact, 'workbuddy'),
            ([('other.exe', 280844080, False)], 'codex'),
            ([('assets/bin/codex-app-server.exe', 280844081, False)], 'codex')):
            with self.assertRaises((ValueError, RuntimeError)):
                builder.client_inventory(entries, harness)


class CodexLauncherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'CLIENT-MANIFEST.json').write_text(json.dumps({'kind': builder.CODEX_KIND}))
        self.old_path = list(sys.path)
        self.addCleanup(lambda: sys.path.__setitem__(slice(None), self.old_path))
        item = patch.object(client, '__file__', str(self.root / 'client.py'))
        item.start()
        self.addCleanup(item.stop)

    def test_package_defaults_login_to_codex(self):
        seen = []
        with patch.dict(sys.modules, {'codex_login': types.SimpleNamespace(main=lambda a: seen.append(a) or 0)}):
            self.assertEqual(client.main(['login', '--profile-parent', 'C:/profiles']), 0)
        self.assertEqual(seen[0], ['login', '--assets-root', str(self.root / 'assets'), '--profile-parent', 'C:/profiles'])

    def test_explicit_matching_selection_and_models(self):
        seen = []
        with patch.dict(sys.modules, {'codex_login': types.SimpleNamespace(main=lambda a: seen.append(a) or 0)}):
            client.main(['--harness', 'codex', 'models', '--profile-root', 'C:/profile'])
        self.assertEqual(seen[0][-2:], ['--profile-root', 'C:/profile'])

    def test_target_pair_forwarded_without_asset_override(self):
        seen = []
        with patch.dict(sys.modules, {'codex_login': types.SimpleNamespace(main=lambda a: seen.append(a) or 0)}):
            client.main(['models', '--profile-root', 'C:/profile', '--target-model', 'native', '--public-alias', 'codex-test'])
        self.assertEqual(seen[0][-4:], ['--target-model', 'native', '--public-alias', 'codex-test'])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            client.main(['models', '--profile-root', 'C:/profile', '--target-model', 'native'])

    def test_mismatch_and_asset_override_rejected(self):
        for args in (['--harness', 'workbuddy', 'login'], ['login', '--assets-root', 'C:/other'], ['preflight']):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                client.main(args)

    def test_codex_asset_verification(self):
        seen = []
        with patch.dict(sys.modules, {'codex_assets': types.SimpleNamespace(verify_assets=lambda p: seen.append(p))}), contextlib.redirect_stdout(io.StringIO()):
            client.main(['verify-assets'])
        self.assertEqual(seen, [self.root / 'assets'])
