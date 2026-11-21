"""Offline bundle integrity and launcher tests; not Windows/WorkBuddy acceptance."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PACKAGING = Path(__file__).resolve().parents[1] / 'packaging'
spec = importlib.util.spec_from_file_location('probe_builder', PACKAGING / 'build.py')
builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(builder)


class PackagingTests(unittest.TestCase):
    def fixture(self, root):
        source = root / 'code/src/native_harness_api'
        source.mkdir(parents=True)
        (source / '__init__.py').write_text('')
        (source / 'workbuddy_acp.py').write_text('# offline fixture')
        (source / 'workbuddy_probe.py').write_text('def main():\n import sys,json\n print(json.dumps(sys.argv[1:]))\n return 0\n')
        (source / 'secret.env').write_text('never bundle')
        (source / '__pycache__').mkdir()
        (source / '__pycache__/secret.pyc').write_text('never bundle')
        assets = root / 'code/packaging'
        assets.mkdir()
        for name in builder.ASSETS:
            (assets / name).write_bytes((PACKAGING / name).read_bytes())
        runtime = root / 'runtime.zip'
        with zipfile.ZipFile(runtime, 'w') as z:
            for name in ('python.exe', 'python313.zip', 'python313._pth', 'LICENSE.txt'):
                z.writestr(name, 'TEST FIXTURE ONLY')
        return root / 'code', runtime

    def test_deterministic_allowlist_manifest_and_launcher(self):
        with tempfile.TemporaryDirectory(prefix='probe space ') as tmp:
            root = Path(tmp)
            code, runtime = self.fixture(root)
            with patch.object(builder, 'RUNTIME_SHA256', builder.digest(runtime.read_bytes())):
                first = builder.build(code, runtime, root / 'a.zip', 'a' * 40)
                second = builder.build(code, runtime, root / 'b.zip', 'a' * 40)
            self.assertEqual(first, second)
            manifest = builder.verify(root / 'a.zip')
            self.assertFalse(any('secret' in n or 'pycache' in n for n in manifest['files']))
            dest = root / 'extracted 中文'
            with zipfile.ZipFile(root / 'a.zip') as z:
                z.extractall(dest)
            args = ['doctor', '--config', str(root / 'my config.json')]
            result = subprocess.run([sys.executable, '-I', str(dest / 'launcher.py'), *args], capture_output=True, text=True, check=True)
            self.assertEqual(json.loads(result.stdout), args)
            with patch.object(builder, 'RUNTIME_SHA256', builder.digest(runtime.read_bytes())):
                with self.assertRaises(FileExistsError):
                    builder.build(code, runtime, root / 'a.zip', 'a' * 40)

    def test_text_line_endings_deterministic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, runtime = self.fixture(root)
            with patch.object(builder, 'RUNTIME_SHA256', builder.digest(runtime.read_bytes())):
                first = builder.build(code, runtime, root / 'lf.zip', 'a' * 40)
                for path in [*(code / 'src/native_harness_api').glob('*.py'), *(code / 'packaging').iterdir()]:
                    path.write_bytes(path.read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
                second = builder.build(code, runtime, root / 'crlf.zip', 'a' * 40)
            self.assertEqual(first, second)

    def test_cli_revision_gate_exact_head_and_blobs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, _ = self.fixture(root)
            (code / 'packaging/build.py').write_bytes((PACKAGING / 'build.py').read_bytes())
            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      capture_output=True).stdout.decode().strip()
            git('init')
            git('config', 'core.autocrlf', 'false')
            git('add', 'code')
            git('-c', 'user.name=Offline Test', '-c', 'user.email=test@example.invalid',
                'commit', '-m', 'fixture')
            revision = git('rev-parse', 'HEAD')
            builder.verify_source_revision(code, revision)
            with self.assertRaises(ValueError):
                builder.verify_source_revision(code, '0' * 40)
            for relative in ('src/native_harness_api/workbuddy_probe.py', 'packaging/launcher.py', 'packaging/build.py'):
                path = code / relative
                original = path.read_bytes()
                path.write_bytes(original + b'\n# unaudited\n')
                with self.assertRaises(ValueError):
                    builder.verify_source_revision(code, revision)
                path.write_bytes(original)
            path = code / 'packaging/launcher.py'
            path.write_bytes(path.read_bytes().replace(b'\n', b'\r\n'))
            with self.assertRaises(ValueError):
                builder.verify_source_revision(code, revision)

    def test_bad_runtime_and_revision_rejected(self):
        with self.assertRaises(ValueError):
            builder.runtime_files(b'not official python')
        with self.assertRaises(ValueError):
            builder.build('.', '.', '.', 'main')

    def test_symlink_source_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, runtime = self.fixture(root)
            path = code / 'src/native_harness_api/workbuddy_probe.py'
            path.unlink()
            path.symlink_to(code / 'src/native_harness_api/__init__.py')
            with patch.object(builder, 'RUNTIME_SHA256', builder.digest(runtime.read_bytes())):
                with self.assertRaises(ValueError):
                    builder.build(code, runtime, root / 'a.zip', 'a' * 40)

    def test_tampered_or_added_bundle_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            code, runtime = self.fixture(root)
            with patch.object(builder, 'RUNTIME_SHA256', builder.digest(runtime.read_bytes())):
                builder.build(code, runtime, root / 'a.zip', 'a' * 40)
            with zipfile.ZipFile(root / 'a.zip') as original:
                contents = {n: original.read(n) for n in original.namelist()}
            for variant in ('tamper', 'extra'):
                altered = dict(contents)
                altered['launcher.py' if variant == 'tamper' else 'unexpected.env'] = b'bad'
                with zipfile.ZipFile(root / (variant + '.zip'), 'w') as z:
                    for n, data in altered.items():
                        z.writestr(n, data)
                with self.assertRaises(ValueError):
                    builder.verify(root / (variant + '.zip'))

    def test_unsafe_paths(self):
        for name in ('../secret', '/root', 'C:/evil', 'a\\b', 'a/../b', './a'):
            self.assertFalse(builder.safe_name(name), name)
        self.assertTrue(builder.safe_name('runtime/LICENSE.txt'))


if __name__ == '__main__':
    unittest.main()
