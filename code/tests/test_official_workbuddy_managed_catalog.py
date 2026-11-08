"""Self-authored managed-catalog fixtures. Never starts native runtime."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import official_workbuddy_managed_catalog as p


def dto():
    return {**{k: True for k in p.PASS_BOOLS}, 'success': True,
            'native_account_verified': False, 'real_model_ready': False,
            'schema_version': 1, 'elicitation_seen': 0, 'failure_stage': 0, 'elapsed_ms': 1}


class Tests(unittest.TestCase):
    def test_exact_dto_and_no_authentication_claim(self):
        good = dto(); self.assertTrue(p.parse_dto(json.dumps(good).encode())['success'])
        for key, value in [('native_account_verified', True), ('real_model_ready', True),
                           ('schema_version', 2), ('elicitation_seen', True), ('elicitation_seen', 1),
                           ('elapsed_ms', -1), ('failure_stage', 11), ('secret', 'PRIVATE')]:
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps({**good, key: value}).encode())
        for key in good:
            changed = dict(good); del changed[key]
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(changed).encode())

    def test_bounds_duplicate_and_non_json(self):
        for raw in [b'', b' ' * 4097, b'null', b'[]', b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff']:
            with self.assertRaises(p.Rejected): p.parse_dto(raw)

    def test_partial_progress_never_success(self):
        for key in p.PASS_BOOLS:
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps({**dto(), key: False}).encode())
        d = {**dto(), **{k: False for k in p.PASS_BOOLS}, 'success': False, 'failure_stage': 1}
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['success'])
        for before, after in zip(p.STEPS, p.STEPS[1:]):
            d = {**dto(), 'success': False, 'failure_stage': 9, before: False}
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(d).encode())

    def test_empty_root_and_unauthenticated_gates(self):
        d = {**dto(), 'success': False, 'failure_stage': 7, 'account_unauthenticated': False,
             'auth_status_returned': False, 'auth_status_unauthenticated': False}
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['success'])
        d['auth_status_returned'] = True
        with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(d).encode())
        with patch.object(p, 'read_dto') as read:
            self.assertFalse(p.collect({'cleanup_complete': False}, Path('unused'), None)['dto_valid'])
            read.assert_not_called()

    def test_fixed_output_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'out'; root.mkdir(); trusted = p.identity(root)
            output = root / 'managed-catalog-summary.json'; output.write_text(json.dumps(dto()))
            self.assertTrue(p.read_dto(root, trusted)['success'])
            (root / 'extra').touch()
            with self.assertRaises(p.Rejected): p.read_dto(root, trusted)
            (root / 'extra').unlink(); root.rename(root.with_name('old')); root.mkdir()
            (root / output.name).write_text(json.dumps(dto()))
            with self.assertRaises(p.Rejected): p.read_dto(root, trusted)

    @unittest.skipUnless(shutil.which('node'), 'Node fixture runtime unavailable')
    def test_node_fake_runtime_and_loader(self):
        root = Path(__file__).resolve().parents[1]
        for name in ['beta15_managed_catalog_test.mjs', 'workbuddy_managed_catalog_driver_test.mjs']:
            result = subprocess.run(['node', str(root / 'tests/fakes' / name)], cwd=root, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))


if __name__ == '__main__': unittest.main()
