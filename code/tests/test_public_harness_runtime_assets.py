from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import public_harness_runtime_assets as a
from public_harness_catalog_assets import tree_digest,sha,AssetError
class AssetsTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve()
  self.sfe=self.root/'sfe/package/bin/agent-harness.exe';self.node=self.root/'node/node-v22.16.0-win-x64/node.exe';self.product=self.root/'product.json'
  for p in (self.sfe,self.node,self.product):p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
  self.patches=patch.multiple(a,SFE_TREE=tree_digest(self.root/'sfe'),NODE_TREE=tree_digest(self.root/'node'),EXE_SHA=sha(self.sfe),NODE_EXE_SHA=sha(self.node),OVERLAY_SHA=sha(self.product));self.patches.start()
 def tearDown(self):self.patches.stop();self.tmp.cleanup()
 def test_valid_fixture(self):
  r=a.verify_runtime_assets(self.root);self.assertEqual(r['executable'],self.sfe);self.assertEqual(r['product_config'],self.product)
 def test_changed_tree(self):
  self.node.write_bytes(b'other')
  with self.assertRaises(AssetError):a.verify_runtime_assets(self.root)
 def test_added_file(self):
  (self.root/'sfe/injected.dll').write_bytes(b'other')
  with self.assertRaises(AssetError):a.verify_runtime_assets(self.root)
 def test_product_change(self):
  self.product.write_bytes(b'other')
  with self.assertRaises(AssetError):a.verify_runtime_assets(self.root)
 def test_relative_parent_unc(self):
  for path in ('relative','//host/share','\\\\host\\share',str(self.root/'..')):
   with self.subTest(path=path),self.assertRaises(AssetError):a.checked_local_path(path)
 def test_symlink(self):
  link=self.root/'link'
  try:link.symlink_to(self.root/'sfe',target_is_directory=True)
  except OSError:self.skipTest('symlink unavailable')
  with self.assertRaises(AssetError):a.checked_local_path(link)
 def test_pins_are_copies(self):
  r=a.verify_runtime_assets(self.root);r['pins']['sfe_tree']['files']=0
  self.assertGreater(a.verify_runtime_assets(self.root)['pins']['sfe_tree']['files'],0)
