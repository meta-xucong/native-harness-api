"""Offline-only fixtures. No official executable is started."""
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import official_workbuddy_profile as p


def document():
    return dict(schemaVersion=1, startedAt='2026-11-07T00:00:00.000Z',
        clock='monotonic milliseconds from CLI entry', target='application-ready',
        reason='exit-before-target', exitCode=0, durationMs=10, coveredMs=10,
        uncoveredMs=0, bundle='lite-wb', milestones={'collector-ready': 0},
        spans=[dict(id=1, phase='bootstrap', label='PRIVATE_SECRET', startMs=0,
                    endMs=2, durationMs=2, status='complete'),
               dict(id=2, parentId=1, phase='module_load', label='PRIVATE_SECRET',
                    startMs=2, endMs=10, durationMs=8, status='pending')],
        events=[dict(phase='collector-ready', label='PRIVATE_SECRET', atMs=0)],
        warnings=['PRIVATE_SECRET'], droppedEntries=0, applicationName='PRIVATE_SECRET',
        version='PRIVATE_SECRET', pid=7, platform='win32', arch='x64', runtime='node',
        runtimeVersion='PRIVATE_SECRET', runtimeUptimeAtEntryMs=0,
        resources=dict(memoryBytes=dict(rss=1, heapTotal=1, heapUsed=1, external=0,
            arrayBuffers=0), cpuMicroseconds=dict(user=0, system=0)))


def encoded(d=None):
    return json.dumps(document() if d is None else d).encode()


class SchemaTests(unittest.TestCase):
    def reject(self, d):
        with self.assertRaises(p.ProfileError): p.summarize_profile(encoded(d))

    def test_sanitized(self):
        r = p.summarize_profile(encoded())
        self.assertNotIn('PRIVATE_SECRET', json.dumps(r))
        self.assertNotIn('pid', r)
        self.assertEqual(r['last_recorded_span']['phase'], 'module_load')
        self.assertEqual(r['span_status_counts']['module_load']['pending'], 1)

    def test_unknown_phase(self):
        d = document(); d['spans'][0]['phase'] = 'PRIVATE_SECRET'
        d['events'][0]['phase'] = 'PRIVATE_SECRET'; d['milestones'] = {'PRIVATE_SECRET': 0}
        r = p.summarize_profile(encoded(d))
        self.assertEqual(r['unknown_phase_occurrences'], 3)
        self.assertNotIn('PRIVATE_SECRET', json.dumps(r))

    def test_json_bounds(self):
        for raw in (b'{"a":1,"a":2}', b'{"a":NaN}', b'\xff', b'[]', b''):
            with self.subTest(raw=raw), self.assertRaises(p.ProfileError): p.summarize_profile(raw)
        with patch.object(p, 'MAX_PROFILE', 8), self.assertRaises(p.ProfileError): p.summarize_profile(encoded())

    def test_schema(self):
        for key, value in [('schemaVersion', True), ('schemaVersion', 2), ('target','other'),
                ('bundle','other'), ('platform','linux'), ('runtime','bun'),
                ('reason','secret'), ('clock','other'), ('unexpected',0)]:
            d = document(); d[key] = value
            with self.subTest(key=key): self.reject(d)
        d = document(); del d['resources']; self.reject(d)

    def test_numbers(self):
        for key, value in [('pid',True), ('durationMs',float('inf')), ('durationMs',-1),
                ('droppedEntries',-1), ('exitCode',256), ('coveredMs',11),
                ('uncoveredMs',1), ('runtimeUptimeAtEntryMs',True)]:
            d = document(); d[key] = value
            with self.subTest(key=key): self.reject(d)
        for key, value in [('startMs',-1), ('endMs',9), ('durationMs',5),
                ('status','secret'), ('id',True), ('parentId',2), ('sourceLabel','secret')]:
            d = document(); d['spans'][1][key] = value
            with self.subTest(key=key): self.reject(d)

    def test_nested(self):
        d = document(); d['spans'][1]['id'] = 1; self.reject(d)
        d = document(); d['events'][0]['atMs'] = 11; self.reject(d)
        d = document(); d['resources']['memoryBytes']['rss'] = True; self.reject(d)
        d = document(); d['resources']['cpuMicroseconds']['secret'] = 0; self.reject(d)
        d = document(); d['spans'][0]['label'] = '\ud800'; self.reject(d)

    def test_counts_and_strings(self):
        d = document(); d['warnings'] *= 65; self.reject(d)
        d = document(); d['events'] *= p.MAX_ENTRIES; self.reject(d)
        d = document(); d['milestones'] = {str(n): 0 for n in range(10001)}; self.reject(d)
        d = document(); d['spans'][0]['label'] = 'x'*4097; self.reject(d)

    def test_reasons(self):
        d = document(); del d['exitCode']; self.reject(d)
        d['reason'] = 'target-reached'; self.reject(d)
        d['milestones']['application-ready'] = 10
        self.assertIsNone(p.summarize_profile(encoded(d))['exit_code'])
        d['reason'] = 'timeout'; self.assertEqual(p.summarize_profile(encoded(d))['reason'], 'timeout')


class FileTests(unittest.TestCase):
    def test_missing_and_valid(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); ident=p.identity(root)
            self.assertEqual(p.read_profile(root, ident), {'status':'MISSING'})
            (root/p.PROFILE_NAME).write_bytes(encoded())
            self.assertEqual(p.read_profile(root, ident)['status'], 'CAPTURED_SANITIZED')

    def test_empty_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); ident=p.identity(root); f=root/p.PROFILE_NAME; f.touch()
            with self.assertRaises(p.ProfileError): p.read_profile(root, ident)
            f.write_bytes(encoded()); (root/'other').touch()
            with self.assertRaises(p.ProfileError): p.read_profile(root, ident)

    def test_hardlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); root=base/'profile'; root.mkdir(); source=base/'source'; source.write_bytes(encoded())
            os.link(source, root/p.PROFILE_NAME)
            with self.assertRaises(p.ProfileError): p.read_profile(root, p.identity(root))

    def test_symlink_and_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp); root=base/'profile'; root.mkdir(); ident=p.identity(root)
            source=base/'source'; source.write_bytes(encoded())
            try: (root/p.PROFILE_NAME).symlink_to(source)
            except OSError: self.skipTest('symlink privilege unavailable')
            with self.assertRaises(p.ProfileError): p.read_profile(root, ident)
            root.rename(base/'old'); root.mkdir()
            with self.assertRaises(p.ProfileError): p.read_profile(root, ident)


class FlowTests(unittest.TestCase):
    def prepare(self, root):
        root.mkdir(); entry=root/'entry.js'; entry.touch()
        collector=root/'startup-profile.cjs'; collector.write_bytes(b'fixture never executed')
        return entry, collector.read_bytes()

    def test_exact_one_initialize(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run'; entry, raw=self.prepare(root); calls=[]
            def fake(command, cwd, env, initialize):
                calls.append(command)
                self.assertTrue(initialize)
                self.assertEqual(command[2:], ['--startup-prof','--startup-prof-dir',str(root/'profile'),
                    '--startup-prof-name','acp-init-once','--acp'])
                self.assertNotIn('GITHUB_TOKEN', env); self.assertNotIn('NODE_OPTIONS',env)
                (root/'profile'/p.PROFILE_NAME).write_bytes(encoded())
                return {'status':'FAILED','cleanup_complete':True}
            with patch.object(p,'COLLECTOR_SIZE',len(raw)), patch.object(p,'COLLECTOR_SHA',hashlib.sha256(raw).hexdigest()), patch.object(p,'phase',fake):
                r=p.run_once(root/'fake.exe', entry, root)
                self.assertEqual(r['profile']['status'],'CAPTURED_SANITIZED')
                with self.assertRaises(FileExistsError): p.run_once(root/'fake.exe',entry,root)
            self.assertEqual(len(calls),1)

    def test_wrong_collector_no_launch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run'; entry,_=self.prepare(root)
            with patch.object(p,'phase') as launch, self.assertRaises(p.ProfileError): p.run_once(root/'fake.exe',entry,root)
            launch.assert_not_called()

    def test_cleanup_false_no_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)/'run'; entry,raw=self.prepare(root)
            with patch.object(p,'COLLECTOR_SIZE',len(raw)), patch.object(p,'COLLECTOR_SHA',hashlib.sha256(raw).hexdigest()), patch.object(p,'phase',return_value={'status':'FAILED','cleanup_complete':False}), patch.object(p,'read_profile') as read:
                r=p.run_once(root/'fake.exe',entry,root)
            read.assert_not_called(); self.assertEqual(r['profile']['status'],'NOT_READ_CLEANUP_UNCONFIRMED')


if __name__ == '__main__': unittest.main()
