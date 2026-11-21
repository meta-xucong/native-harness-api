"""Real Windows lifecycle acceptance, not simulated tracker completion evidence."""
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest
from native_harness_api.windows_job import OwnedWindowsJob, WindowsJobError
from test_supervised_acp_transport import DescendantWitness

PEER = Path(__file__).parent / 'fakes' / 'windows_job_tracking_tree.py'


@unittest.skipUnless(os.name == 'nt', 'Requires real Windows completion-port process tracking')
class WindowsJobTrackingTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(prefix='job-tracking-')
        self.root = Path(self.folder.name)
        self.owner = self.witness = None
        self.observation = {'fixture': 'synthetic-job-tracking', 'mode': 'unstarted',
                            'original_empty': None, 'original_close': None, 'immediate': None}
        self.addCleanup(self.finish_fixture)

    def finish_fixture(self):
        # Extra cleanup is hygiene only. Never replace original assertions/facts.
        failures = []
        if self.owner is not None:
            try:
                result = self.owner.close(timeout=5)
                if result.get('owned_job_empty') is not True:
                    failures.append('OWNER_UNCONFIRMED')
            except Exception:
                failures.append('OWNER_CLEANUP_FAILED')
        if self.witness is not None:
            try:
                self.witness.finish(timeout=2)
            except Exception:
                failures.append('WITNESS_CLEANUP_FAILED')
        deadline = time.monotonic() + 2
        for attempt in range(41):
            try:
                self.folder.cleanup()
                break
            except PermissionError as error:
                if getattr(error, 'winerror', None) not in (5, 32) or time.monotonic() >= deadline or attempt == 40:
                    failures.append('FOLDER_CLEANUP_FAILED')
                    break
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
            except Exception:
                failures.append('FOLDER_CLEANUP_FAILED')
                break
        print(json.dumps({**self.observation, 'hygiene_failures': failures}, sort_keys=True))
        if failures:
            self.fail('SYNTHETIC_TRACKING_HYGIENE_FAILED ' + ','.join(failures))

    def launch(self, mode):
        self.observation['mode'] = mode
        try:
            self.owner = OwnedWindowsJob.spawn([sys.executable, str(PEER.resolve()), mode, str(self.root)],
                                              self.root, dict(os.environ))
        except WindowsJobError as error:
            self.owner = error.owner  # Retain ambiguous partial creation for hygiene.
            raise
        return self.owner

    def wait_marker(self, name, timeout=30):
        deadline = time.monotonic() + timeout
        while not (self.root / name).is_file():
            if time.monotonic() >= deadline:
                self.fail('SYNTHETIC_TRACKING_MARKER_TIMEOUT')
            time.sleep(.01)

    def natural(self, mode, leaves):
        owner = self.launch(mode)
        self.wait_marker(mode + '.done')
        self.assertEqual(len(list(self.root.glob('leaf-*'))), leaves)
        # Total coverage must actually be demonstrated, not inferred merely from
        # host exit, list emptiness, completion messages or elapsed time.
        result = owner.wait_empty(10)
        self.observation['original_empty'] = result.get('owned_job_empty')
        self.assertIs(result.get('owned_job_empty'), True, 'TRACKING_COVERAGE_UNCONFIRMED')
        self.assertTrue(owner.host_exited())
        self.assertEqual(owner.exit_code(), 0)
        result = owner.close(5)
        self.observation['original_close'] = result.get('owned_job_empty')
        self.assertIs(result.get('owned_job_empty'), True)

    def test_short_lived_burst_natural_exit_without_capture_handshake(self):
        self.natural('burst', 12)

    def test_dynamic_grandchildren_natural_exit_without_capture_handshake(self):
        self.natural('dynamic', 12)

    def test_host_exits_before_persistent_descendants_close_waits_real_objects(self):
        owner = self.launch('orphan')
        self.wait_marker('orphan.ready')
        # Private API access is ONLY to acquire independent test witnesses with
        # exact owned-job membership. No tracker-private state proves success.
        self.witness = DescendantWitness(owner._api)
        self.witness.acquire(self.root, owner._job)
        deadline = time.monotonic() + 10
        while not owner.host_exited():
            if time.monotonic() >= deadline:
                self.fail('SYNTHETIC_HOST_EXIT_TIMEOUT')
            time.sleep(.01)
        self.assertIs(owner.wait_empty(0)['owned_job_empty'], False)
        result = owner.close(5)
        self.observation['original_close'] = result.get('owned_job_empty')
        immediate = self.witness.immediate()
        self.observation['immediate'] = dict(immediate)
        self.assertIs(result.get('owned_job_empty'), True, 'TRACKING_COVERAGE_UNCONFIRMED')
        self.assertEqual(immediate, {'child': True, 'grandchild': True},
                         'CLOSE_RETURNED_BEFORE_DESCENDANT_SIGNAL')
