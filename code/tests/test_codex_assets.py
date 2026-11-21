"""Data-only archive fixtures. Never execute downloaded binaries."""
import hashlib
import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import codex_assets as assets


class CodexAssetsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def archive(self, extra=None):
        archive = self.root / 'package.tar.gz'
        records = []
        with tarfile.open(archive, 'w:gz') as output:
            for name in assets.FILES:
                data = name.encode()
                member = tarfile.TarInfo(name)
                member.size = len(data)
                output.addfile(member, io.BytesIO(data))
                records.append([name, len(data), hashlib.sha256(data).hexdigest()])
            if extra is not None:
                output.addfile(extra)
        tree = dict(sha256=hashlib.sha256(json.dumps(sorted(records), separators=(',', ':')).encode()).hexdigest())
        return archive, tree

    def test_exact_layout_pinned_and_no_overwrite(self):
        archive, tree = self.archive()
        with patch.multiple(assets, PACKAGE_SIZE=archive.stat().st_size, PACKAGE_SHA=assets.sha(archive), TREE=tree):
            path = assets.prepare_assets(archive, self.root / 'assets')
            self.assertEqual(path.name, 'codex-app-server.exe')
            self.assertTrue(path.is_absolute())
            self.assertEqual(assets.verify_assets(self.root / 'assets'), path)
            with self.assertRaises(FileExistsError):
                assets.prepare_assets(archive, self.root / 'assets')
            path.write_bytes(b'modified')
            with self.assertRaises(assets.AssetError):
                assets.verify_assets(self.root / 'assets')

    def test_links_never_extracted(self):
        member = tarfile.TarInfo('unexpected')
        member.type = tarfile.SYMTYPE
        member.linkname = '/outside'
        archive, tree = self.archive(member)
        with patch.multiple(assets, PACKAGE_SIZE=archive.stat().st_size, PACKAGE_SHA=assets.sha(archive), TREE=tree):
            with self.assertRaises(assets.AssetError):
                assets.prepare_assets(archive, self.root / 'assets')
        self.assertFalse((self.root / 'assets').exists())

    def test_wrong_package_hash(self):
        archive, _ = self.archive()
        with self.assertRaises(assets.AssetError):
            assets.prepare_assets(archive, self.root / 'assets')
