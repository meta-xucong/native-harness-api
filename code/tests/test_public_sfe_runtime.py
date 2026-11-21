"""Execute only self-authored public-SFE client assembly fixtures."""
from pathlib import Path
import shutil
import subprocess
import unittest

NODE = shutil.which('node')


@unittest.skipUnless(NODE, 'Node is required for vendor-free runtime fixtures')
class PublicSfeRuntimeTests(unittest.TestCase):
    def test_vendor_free_client_assembly(self):
        fixture = Path(__file__).parent / 'fakes' / 'public_sfe_runtime_test.mjs'
        result = subprocess.run([NODE, str(fixture)], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr.decode('utf8', errors='replace'))
        self.assertIn(b'vendor-free', result.stdout)
