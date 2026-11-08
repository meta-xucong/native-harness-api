"""Self-authored protocol/loader/DTO fixtures, never vendor execution."""
import hashlib
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import official_workbuddy_client_mock as p


def dto():
    return {**{k: True for k in p.PASS_BOOLS}, 'success': True,
            'all_account_data_synthetic': True, 'native_account_verified': False,
            'real_model_ready': False, 'schema_version': 1, 'cases_completed': 4,
            'clients_closed': 4, 'peers_closed': 4, 'wire_messages': 20,
            'failure_stage': 0, 'elapsed_ms': 1}


class Tests(unittest.TestCase):
    def test_exact_dto_and_synthetic_provenance(self):
        good = dto(); self.assertTrue(p.parse_dto(json.dumps(good).encode())['success'])
        for key, value in [('native_account_verified', True), ('real_model_ready', True),
                           ('all_account_data_synthetic', False), ('schema_version', 2),
                           ('clients_closed', True), ('peers_closed', 5), ('wire_messages', 82),
                           ('elapsed_ms', -1), ('failure_stage', 6), ('secret', 'PRIVATE')]:
            changed = {**good, key: value}
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(changed).encode())
        for key in good:
            changed = dict(good); del changed[key]
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(changed).encode())

    def test_dto_invalid_bounds_and_duplicate_keys(self):
        for raw in [b'', b' ' * 4097, b'null', b'[]', b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff']:
            with self.assertRaises(p.Rejected): p.parse_dto(raw)

    def test_partial_cannot_claim_success(self):
        for key in p.PASS_BOOLS:
            d = dto(); d[key] = False
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(d).encode())
        for key in ['cases_completed', 'clients_closed', 'peers_closed']:
            d = dto(); d[key] = 3
            with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(d).encode())
        d = dto(); d['success'] = False
        with self.assertRaises(p.Rejected): p.parse_dto(json.dumps(d).encode())

    def test_early_failure_and_cleanup_gate(self):
        d = {**dto(), **{k: False for k in p.PASS_BOOLS}, 'success': False,
             'cases_completed': 0, 'clients_closed': 0, 'peers_closed': 0,
             'wire_messages': 0, 'failure_stage': 1}
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['success'])
        with patch.object(p, 'read_dto') as read:
            self.assertFalse(p.collect({'cleanup_complete': False}, Path('unused'), None)['dto_valid'])
            read.assert_not_called()

    def test_fixed_bounded_output_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'out'; root.mkdir(); trusted = p.identity(root)
            output = root / 'client-mock-summary.json'; output.write_text(json.dumps(dto()))
            self.assertTrue(p.read_dto(root, trusted)['success'])
            (root / 'extra').touch()
            with self.assertRaises(p.Rejected): p.read_dto(root, trusted)
            (root / 'extra').unlink(); root.rename(root.with_name('old')); root.mkdir()
            (root / output.name).write_text(json.dumps(dto()))
            with self.assertRaises(p.Rejected): p.read_dto(root, trusted)

    def test_pair_manifest_all_public_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); package = root / 'package'; package.mkdir()
            manifest = {'exports': {'./client': {'default': './lib/client/public.js'}, './transport': {'default': './lib/transport/effect.js'}}}
            data = {'package.json': json.dumps(manifest).encode(), 'lib/client/public.js': b'export{};', 'lib/transport/effect.js': b'export{};'}
            pins = {k: (hashlib.sha256(v).hexdigest(), len(v)) for k, v in data.items()}
            header = {'files': {}}; current = header
            for part in ('node_modules', '@genie', 'agent-harness'): current = current['files'].setdefault(part, {'files': {}})
            for name, content in data.items():
                target = current
                for part in name.split('/')[:-1]: target = target['files'].setdefault(part, {'files': {}})
                target['files'][name.split('/')[-1]] = {'size': len(content), 'unpacked': True, 'integrity': {'algorithm': 'SHA256', 'hash': pins[name][0]}}
                path = package / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(content)
            archive = root / 'app.asar'
            def check():
                raw = json.dumps(header).encode(); padded = (len(raw) + 3) // 4 * 4
                blob = struct.pack('<4I', 4, padded + 8, padded + 4, len(raw)) + raw + b'\0' * (padded - len(raw)); archive.write_bytes(blob)
                with patch.object(p, 'PINS', pins), patch.object(p, 'ASAR_SIZE', len(blob)), patch.object(p, 'ASAR_SHA', hashlib.sha256(blob).hexdigest()): p.verify_pairs(archive, package)
            check()
            member = current['files']['lib']['files']['transport']['files']['effect.js']
            member['unpacked'] = False
            with self.assertRaises(p.Rejected): check()
            member['unpacked'] = True; (package / 'lib/transport/effect.js').write_bytes(b'changed!;')
            with self.assertRaises(p.Rejected): check()

    @unittest.skipUnless(shutil.which('node'), 'Node fixture runtime unavailable')
    def test_node_fake_peer_and_loader(self):
        root = Path(__file__).resolve().parents[1]
        for name in ['beta15_client_mock_test.mjs', 'workbuddy_client_mock_driver_test.mjs']:
            result = subprocess.run(['node', str(root / 'tests/fakes' / name)], cwd=root, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout.decode(errors='replace') + result.stderr.decode(errors='replace'))


if __name__ == '__main__': unittest.main()
