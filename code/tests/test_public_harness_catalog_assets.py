"""Offline fixtures only. No vendor, downloads, installations, or account access."""
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import stat
import types
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PATH = Path(__file__).resolve().parents[1] / 'tools/public_harness_catalog_assets.py'
spec = importlib.util.spec_from_file_location('catalog_assets', PATH)
a = importlib.util.module_from_spec(spec)
spec.loader.exec_module(a)

class AssetsTests(unittest.TestCase):
    def test_paths(self):
        for name in ['../x','/x','C:/x','x\\y','x//y','x/./y','x/../y','x.','x ','CON','con.txt','COM1.log','a:stream','a~1','a?','a\x00b']:
            with self.subTest(name=name), self.assertRaises(a.AssetError):
                a.member_parts(name)
        self.assertEqual(a.member_parts('package/bin/a.exe'), ('package','bin','a.exe'))

    def test_aliases(self):
        for entries in [[('a/x',1,False),('A/y',1,False)], [('a',1,False),('a/b',1,False)], [('a',0,True),('a',0,True)], [('a',-1,False)], [('a',a.MAX_FILE+1,False)]]:
            with self.assertRaises(a.AssetError): a.validate_inventory(entries)
        self.assertEqual(a.validate_inventory([('a',0,True),('a/x',1,False)]),1)
        with patch.object(a,'MAX_TOTAL',1), self.assertRaises(a.AssetError):
            a.validate_inventory([('a',2,False)])
        with patch.object(a,'MAX_FILES',0), self.assertRaises(a.AssetError):
            a.validate_inventory([('a',0,False)])

    def tar(self, path, kind=tarfile.REGTYPE, name='package/bin/a.exe'):
        with tarfile.open(path,'w:gz') as t:
            m=tarfile.TarInfo(name);m.type=kind;m.size=3 if kind==tarfile.REGTYPE else 0
            if kind in (tarfile.SYMTYPE,tarfile.LNKTYPE):m.linkname='elsewhere'
            t.addfile(m,io.BytesIO(b'abc') if m.size else None)

    def test_tar_success_and_exclusive(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'a.tgz';self.tar(p)
            result=a.extract_tar(p,root/'out',a.sha(p))
            self.assertEqual(result['files'],1);self.assertEqual(result['bytes'],3)
            self.assertEqual(result,a.tree_digest(root/'out'))
            with self.assertRaises(FileExistsError):a.extract_tar(p,root/'out',a.sha(p))
            with self.assertRaises(a.AssetError):a.extract_tar(p,root/'bad','0'*64)
            self.assertFalse((root/'bad').exists())

    def test_tar_reject_before_extract(self):
        for kind,name in [(tarfile.SYMTYPE,'a'),(tarfile.LNKTYPE,'a'),(tarfile.CHRTYPE,'a'),(tarfile.FIFOTYPE,'a'),(tarfile.REGTYPE,'../bad')]:
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);p=root/'a.tgz';self.tar(p,kind,name)
                with self.assertRaises(a.AssetError):a.extract_tar(p,root/'out',a.sha(p))
                self.assertFalse((root/'out').exists())

    def test_zip_success_and_rejections(self):
        for name,mode,ok in [('node/node.exe',stat.S_IFREG|0o644,True),('../bad',stat.S_IFREG,False),('node',stat.S_IFLNK|0o777,False),('node',stat.S_IFCHR,False)]:
            with tempfile.TemporaryDirectory() as td:
                root=Path(td);p=root/'a.zip';info=zipfile.ZipInfo(name);info.create_system=3;info.external_attr=mode<<16
                with zipfile.ZipFile(p,'w') as z:z.writestr(info,b'abc')
                if ok:
                    result=a.extract_zip(p,root/'out',a.sha(p));self.assertEqual(result['bytes'],3)
                else:
                    with self.assertRaises(a.AssetError):a.extract_zip(p,root/'out',a.sha(p))
                    self.assertFalse((root/'out').exists())

    def test_zip_case_alias(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);p=root/'a.zip'
            with zipfile.ZipFile(p,'w') as z:
                z.writestr('A/a',b'');z.writestr('a/b',b'')
            with self.assertRaises(a.AssetError):a.extract_zip(p,root/'out',a.sha(p))

    def test_node_sums(self):
        valid=f'{a.NODE_SHA}  node-v22.16.0-win-x64.zip\n'.encode()
        a.verify_node_sums(valid)
        for invalid in [b'',valid+valid,valid.replace(a.NODE_SHA.encode(),b'0'*64)]:
            with self.assertRaises(a.AssetError):a.verify_node_sums(invalid)

    def test_product_projection(self):
        config={'productName':'WorkBuddy','applicationName':'WorkBuddy','authentication':{'id':'workbuddy-desktop','type':'account-scoped-external-link','attributes':{'platform':'workbuddy'}},'endpoint':'synthetic'}
        for k in ('include','productConfigEnv','productConfigPathEnv','replacements','mergeStrategy'):config[k]='synthetic'
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)/'product.json';p.write_text(json.dumps(config))
            with patch.object(a,'PRODUCT_SHA',a.sha(p)):
                result=a.product_overlay(p);self.assertEqual(result['configuration']['productVersion'],'5.9.3')
                self.assertNotIn('include',result['configuration']);self.assertEqual(result['configuration']['endpoint'],'synthetic')
            with self.assertRaises(a.AssetError):a.product_overlay(p)
            config['productName']='Other';p.write_text(json.dumps(config))
            with patch.object(a,'PRODUCT_SHA',a.sha(p)),self.assertRaises(a.AssetError):a.product_overlay(p)

    def test_tree_links(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td);(root/'a').write_bytes(b'a')
            try:
                (root/'link').symlink_to(root/'a')
            except OSError as error:
                self.skipTest(f'symlink unavailable: {type(error).__name__}')
            with self.assertRaises(a.AssetError):a.tree_digest(root)

    def test_acquire_offline_contract(self):
        calls=[]
        sfe_measurement=dict(a.SFE_TREE);node_measurement=dict(a.NODE_TREE)
        def download(url,path,limit):
            calls.append(url)
            path.write_bytes((f'{a.NODE_SHA}  node-v22.16.0-win-x64.zip\n').encode() if url==a.SUMS_URL else b'synthetic')
        def tar(archive,target,pin):
            self.assertEqual(pin,a.SFE_SHA)
            p=target/'package/bin/agent-harness.exe';p.parent.mkdir(parents=True);p.write_bytes(b'synthetic-exe')
            return dict(sfe_measurement)
        def zip_(archive,target,pin):
            self.assertEqual(pin,a.NODE_SHA)
            p=target/'node-v22.16.0-win-x64/node.exe';p.parent.mkdir(parents=True);p.write_bytes(b'synthetic-node')
            return dict(node_measurement)
        def acquire(root,seven):
            self.assertTrue(root.is_dir());self.assertEqual(seven,Path('synthetic-7z'))
        with tempfile.TemporaryDirectory() as td, patch.object(a,'download',download), patch.object(a,'extract_tar',tar), patch.object(a,'extract_zip',zip_), patch.object(a,'EXE_SHA',hashlib.sha256(b'synthetic-exe').hexdigest()), patch.object(a,'NODE_EXE_SHA',hashlib.sha256(b'synthetic-node').hexdigest()), patch.object(a,'OVERLAY_SHA',hashlib.sha256(b'{"schemaVersion":1,"kind":"overlay","configuration":{}}').hexdigest()), patch.object(a,'product_overlay',return_value={'schemaVersion':1,'kind':'overlay','configuration':{}}), patch.dict('sys.modules',{'official_workbuddy_smoke':types.SimpleNamespace(acquire=acquire)}):
            result=a.acquire_assets(Path(td),Path('synthetic-7z'))
            self.assertEqual(set(result),{'executable','host_node','product_config','pins'})
            self.assertEqual(calls,[a.SFE_URL,a.SUMS_URL,a.NODE_URL])
            self.assertEqual(result['pins']['host_node'],hashlib.sha256(b'synthetic-node').hexdigest())
            self.assertTrue(result['product_config'].is_file())
            for key in ('SFE_TREE','NODE_TREE','NODE_EXE_SHA','OVERLAY_SHA'):
                with tempfile.TemporaryDirectory() as bad, patch.object(a,key,{} if key.endswith('TREE') else '0'*64):
                    # Extraction callbacks return the fixed successful measurements, not mutated expectations.
                    with self.assertRaises(a.AssetError):a.acquire_assets(Path(bad),Path('synthetic-7z'))

    def test_no_dynamic_url(self):
        with self.assertRaises(a.AssetError):a.download('https://example.invalid',Path('/unused'),1)

if __name__ == '__main__':
    unittest.main()
