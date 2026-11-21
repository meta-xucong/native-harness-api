"""Offline synthetic packaging fixtures; never execute WorkBuddy or Windows binaries."""
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

PACKAGING=Path(__file__).resolve().parents[1]/'packaging'
spec=importlib.util.spec_from_file_location('installed_builder',PACKAGING/'build_installed_smoke.py')
b=importlib.util.module_from_spec(spec);spec.loader.exec_module(b)

class InstalledPackagingTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.code=self.root/'code';self.code.mkdir()
        for output,relative in b.SOURCES.items():
            p=self.code/relative;p.parent.mkdir(parents=True,exist_ok=True)
            p.write_bytes((PACKAGING/Path(relative).name).read_bytes() if relative.startswith('packaging/') else b'# fixture\n')
        (self.code/'packaging/build_installed_smoke.py').write_bytes((PACKAGING/'build_installed_smoke.py').read_bytes())
        buf=io.BytesIO()
        with zipfile.ZipFile(buf,'w') as z:
            for name in b.RUNTIME_NAMES:z.writestr(name,('fixture:'+name).encode())
        self.runtime=buf.getvalue();self.runtime_path=self.root/'runtime.zip';self.runtime_path.write_bytes(self.runtime)
        self.pin=patch.object(b,'RUNTIME_SHA256',b.digest(self.runtime));self.pin.start();self.addCleanup(self.pin.stop)
    def build(self,name='out.zip'):
        p=self.root/name;b.build(self.code,self.runtime_path,p,'a'*40);return p
    def test_exact_inventory_determinism_and_exclusive_output(self):
        a=self.build();c=self.build('second.zip');self.assertEqual(a.read_bytes(),c.read_bytes())
        with zipfile.ZipFile(a) as z:
            self.assertEqual(len(z.namelist()),42)
            self.assertEqual(z.read(b.EMPTY_INIT),b'')
            self.assertEqual(z.read('runtime/LICENSE.txt'),b'fixture:LICENSE.txt')
            self.assertFalse(any('probe' in n or 'account' in n or 'acp' in n for n in z.namelist()))
        b.verify(a)
        with self.assertRaises(FileExistsError):self.build()
    def test_runtime_pin_and_names(self):
        with self.assertRaises(ValueError):b.runtime_files(b'wrong')
        for name in ('../bad','a/./b','a//b','a\\b','/bad','C:/bad','','.'):
            self.assertFalse(b.safe_name(name))
        for names in (('a','a'),('a','A'),('a/./b',),('../b',)):
            out=io.BytesIO()
            with zipfile.ZipFile(out,'w') as z:
                for n in names:z.writestr(n,b'fixture')
            with self.assertRaises(ValueError):b.archive_files(out.getvalue())
    def test_tampering_and_extra_members_rejected(self):
        original=self.build()
        with zipfile.ZipFile(original) as z:files={n:z.read(n) for n in z.namelist()}
        for kind in ('bytes','extra','missing','manifest','sums'):
            data=dict(files)
            if kind=='bytes':data['launcher.py']=b'changed'
            if kind=='extra':data['private.env']=b'private'
            if kind=='missing':del data['runtime/LICENSE.txt']
            if kind=='manifest':data['RELEASE-MANIFEST.json']=b'{}'
            if kind=='sums':data['SHA256SUMS']=b'wrong'
            p=self.root/(kind+'.zip')
            with zipfile.ZipFile(p,'w') as z:
                for n,v in data.items():z.writestr(n,v)
            with self.assertRaises(ValueError):b.verify(p)
    def test_git_revision_gate_includes_builder_and_assets(self):
        def git(*args):
            return subprocess.run(['git','-C',str(self.root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode().strip()
        git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
        git('add','code');git('commit','-m','fixture');rev=git('rev-parse','HEAD')
        b.verify_source_revision(self.code,rev)
        with self.assertRaises(ValueError):b.verify_source_revision(self.code,'0'*40)
        for relative in (*b.SOURCES.values(),'packaging/build_installed_smoke.py'):
            p=self.code/relative;old=p.read_bytes();p.write_bytes(old+b'\n')
            with self.assertRaises(ValueError):b.verify_source_revision(self.code,rev)
            p.write_bytes(old)
    def test_cli_gate_prevents_build_with_uncommitted_source(self):
        def git(*args):
            return subprocess.run(['git','-C',str(self.root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout.decode().strip()
        git('init');git('config','user.name','Fixture');git('config','user.email','fixture@example.invalid')
        git('add','code');git('commit','-m','fixture');rev=git('rev-parse','HEAD')
        (self.code/'tools/installed_workbuddy_smoke.py').write_text('# unreviewed change')
        output=self.root/'never.zip'
        result=subprocess.run([sys.executable,str(self.code/'packaging/build_installed_smoke.py'),
            '--runtime-zip',str(self.runtime_path),'--output',str(output),'--source-revision',rev],capture_output=True)
        self.assertNotEqual(result.returncode,0);self.assertFalse(output.exists())

    def test_launcher_preserves_arguments_without_native_execution(self):
        p=self.code/'tools/installed_workbuddy_smoke.py'
        p.write_text('import json,sys\ndef main(argv=None):\n print(json.dumps(sys.argv[1:]))\n return 0\n')
        self.build()
        extracted=self.root/'extracted'
        with zipfile.ZipFile(self.root/'out.zip') as z:z.extractall(extracted)
        result=subprocess.run([sys.executable,'-I','-B',str(extracted/'launcher.py'),'--help'],check=True,capture_output=True,text=True)
        self.assertEqual(json.loads(result.stdout),['--help'])
        noflags=subprocess.run([sys.executable,str(extracted/'launcher.py'),'--help'],capture_output=True,text=True)
        self.assertNotEqual(noflags.returncode,0)
    def test_text_lf_and_unrelated_state_excluded(self):
        (self.code/'tools/installed_workbuddy_smoke.py').write_bytes(b'# fixture\r\n')
        (self.code/'secrets.env').write_text('MUST_NOT_BUNDLE')
        p=self.build()
        with zipfile.ZipFile(p) as z:
            self.assertEqual(z.read('tools/installed_workbuddy_smoke.py'),b'# fixture\n')
            self.assertNotIn(b'MUST_NOT_BUNDLE',p.read_bytes())

if __name__=='__main__':unittest.main()
