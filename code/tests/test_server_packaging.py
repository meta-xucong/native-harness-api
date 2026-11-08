import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import zipfile
spec=importlib.util.spec_from_file_location('native_build_server',Path(__file__).resolve().parents[1]/'packaging/build_server.py')
build=importlib.util.module_from_spec(spec);spec.loader.exec_module(build)

class ServerPackagingTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)
  repo=self.root/'repo';repo.mkdir();self.repo=repo
  self.git('init','-q');self.git('config','user.name','fixture');self.git('config','user.email','fixture@example.invalid')
  (repo/'LICENSE').write_text('fixture license');(repo/'README.md').write_text('committed')
  self.git('add','.');self.git('commit','-qm','fixture');self.sha=self.git('rev-parse','HEAD').decode().strip()
 def git(self,*args):return subprocess.check_output(['git','-C',str(self.repo),*args],stderr=subprocess.DEVNULL)
 def repos(self):return {label:(self.repo,self.sha) for label in build.LABELS}
 def test_exact_git_objects_not_worktree_and_preserved_license(self):
  (self.repo/'README.md').write_text('uncommitted');(self.repo/'private.txt').write_text('never include')
  path=self.root/'source.zip';build.build_server(self.repos(),path)
  with zipfile.ZipFile(path) as z:
   m=json.loads(z.read('SERVER-SOURCE-MANIFEST.json'))
   self.assertEqual(m['windows_client_source_revision'],self.sha)
   for label in build.LABELS:
    self.assertEqual(z.read(label+'/README.md'),b'committed');self.assertEqual(z.read(label+'/LICENSE'),b'fixture license')
    self.assertNotIn(label+'/private.txt',z.namelist())
   self.assertEqual(set(z.namelist()),set(m['files'])|{'SERVER-SOURCE-MANIFEST.json'})
 def test_deterministic_and_no_overwrite(self):
  a,b=self.root/'a.zip',self.root/'b.zip'
  self.assertEqual(build.build_server(self.repos(),a),build.build_server(self.repos(),b))
  with self.assertRaises(FileExistsError):build.build_server(self.repos(),a)
 def test_requires_full_commit_and_three_repos(self):
  with self.assertRaises(ValueError):build.revision_files(self.repo,self.sha[:7])
  with self.assertRaises(ValueError):build.build_server({},self.root/'x.zip')
 def test_git_symlink_and_submodule_rejected(self):
  for mode,sha in [('120000',self.git('rev-parse','HEAD:README.md').decode().strip()),('160000',self.sha)]:
   self.git('update-index','--add','--cacheinfo',mode,sha,'unsafe')
   self.git('commit','-qm','unsafe fixture')
   with self.assertRaises(ValueError):build.revision_files(self.repo,self.git('rev-parse','HEAD').decode().strip())
   self.git('update-index','--force-remove','unsafe')
 def test_tamper_metadata_manifest_and_path_rejected(self):
  path=self.root/'source.zip';build.build_server(self.repos(),path)
  with zipfile.ZipFile(path) as z: original=[(i,z.read(i)) for i in z.infolist()]
  for variant in ('symlink','content','duplicate_json','path','short_revision'):
   out=self.root/(variant+'.zip')
   with zipfile.ZipFile(out,'w') as z:
    for old,data in original:
     info=zipfile.ZipInfo(old.filename);info.create_system=3;info.external_attr=old.external_attr
     if variant=='symlink' and old.filename=='SERVER-SOURCE-MANIFEST.json':info.external_attr=0o120777<<16
     if variant=='content' and old.filename.endswith('/README.md'):data=b'changed'
     if variant=='path' and old.filename.endswith('/README.md'):info.filename='C:/escape/'+old.filename
     if old.filename=='SERVER-SOURCE-MANIFEST.json':
      if variant=='duplicate_json':data=b'{"format":1,'+data.lstrip()[1:]
      if variant=='short_revision':
       m=json.loads(data);m['revisions']['render-bridge']='abc';data=json.dumps(m).encode()
     z.writestr(info,data)
   with self.subTest(variant=variant),self.assertRaises(ValueError):build.verify_server(out)
