import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'packaging'))
import build_client as b
from public_harness_catalog_assets import tree_digest,sha
class ClientPackagingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name).resolve();self.assets=self.root/'assets';self.assets.mkdir();self.code=self.root/'code';self.code.mkdir()
  for name in ('sfe/package/bin/a.exe','node/node-v22.16.0-win-x64/node.exe','product.json'):
   p=self.assets/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'fixture')
  self.source=self.code/'client.py';self.source.write_bytes(b'# fixture\n');self.runtime=self.root/'runtime.zip';self.runtime.write_bytes(b'fake')
  self.patches=[patch.object(b,'verify_runtime_assets',lambda _:{}),patch.object(b,'sources',lambda *_:{'client.py':self.source}),patch.object(b,'runtime_files',lambda _:{'runtime/python.exe':b'fixture-python','runtime/LICENSE.txt':b'fixture-license'}),patch.multiple(b,SFE_TREE=tree_digest(self.assets/'sfe'),NODE_TREE=tree_digest(self.assets/'node'),OVERLAY_SHA=sha(self.assets/'product.json'))]
  for p in self.patches:p.start()
 def tearDown(self):
  for p in reversed(self.patches):p.stop()
  self.tmp.cleanup()
 def build(self,name='a.zip'):p=self.root/name;b.build_client(self.code,self.assets,self.runtime,p,'a'*40);return p
 def test_complete_and_deterministic(self):
  a=self.build();c=self.build('b.zip');self.assertEqual(a.read_bytes(),c.read_bytes());m=b.verify_client(a);self.assertIn('runtime/LICENSE.txt',m['files']);self.assertEqual(m['real_account_model_acceptance'],'NOT_RUN')
 def test_no_overwrite(self):
  self.build()
  with self.assertRaises(FileExistsError):self.build()
 def test_asset_changes_during_copy_rejected(self):
  (self.assets/'sfe/package/bin/a.exe').write_bytes(b'changed')
  with self.assertRaises(ValueError):self.build()
 def test_zip_tampering(self):
  original=self.build();target=self.root/'tampered.zip'
  with zipfile.ZipFile(original) as src,zipfile.ZipFile(target,'w') as out:
   for name in src.namelist():out.writestr(name,b'changed' if name=='client.py' else src.read(name))
  with self.assertRaises(ValueError):b.verify_client(target)
 def test_manually_rehashed_asset_not_accepted(self):
  original=self.build();target=self.root/'tampered.zip'
  with zipfile.ZipFile(original) as src,zipfile.ZipFile(target,'w') as out:
   m=json.loads(src.read('CLIENT-MANIFEST.json'));key='assets/sfe/package/bin/a.exe';m['files'][key]=hashlib.sha256(b'changed').hexdigest()
   for name in src.namelist():out.writestr(name,json.dumps(m) if name=='CLIENT-MANIFEST.json' else b'changed' if name==key else src.read(name))
  with self.assertRaises(ValueError):b.verify_client(target)
 def test_no_untracked_profile(self):
  (self.assets/'account-secret').write_bytes(b'notincluded');m=b.verify_client(self.build());self.assertNotIn('assets/account-secret',m['files'])
 def test_symlink_member_type_rejected(self):
  import stat
  original=self.build();target=self.root/'tampered.zip'
  with zipfile.ZipFile(original) as src,zipfile.ZipFile(target,'w') as out:
   for info in src.infolist():
    if info.filename=='client.py':info.external_attr=(stat.S_IFLNK|0o777)<<16
    out.writestr(info,src.read(info.filename))
  with self.assertRaises(ValueError):b.verify_client(target)
