import contextlib
import importlib.util
import io
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch
spec=importlib.util.spec_from_file_location('native_client_launcher',Path(__file__).resolve().parents[1]/'packaging/client.py')
client=importlib.util.module_from_spec(spec);spec.loader.exec_module(client)
class ClientLauncherTests(unittest.TestCase):
 def setUp(self):self.path=list(sys.path)
 def tearDown(self):sys.path[:]=self.path
 def test_login_pins_asset_arg(self):
  seen=[];module=types.SimpleNamespace(main=lambda args:seen.append(args) or 0)
  with patch.dict(sys.modules,{'public_harness_login':module}):self.assertEqual(client.main(['login','--profile-parent','C:/example']),0)
  self.assertEqual(seen[0][-2:],['--profile-parent','C:/example']);self.assertEqual(seen[0][0],'--assets-root')
 def test_override_and_abbreviation_rejected(self):
  for args in [['--assets-root','C:/x'],['--assets-root=C:/x'],['--a','C:/x']]:
   with self.subTest(args=args),contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as e:client.main(['login',*args])
   self.assertEqual(e.exception.code,2)
 def test_node_help_reaches_runtime(self):
  seen=[];module=types.SimpleNamespace(main=lambda args:seen.append(args) or 0)
  with patch.dict(sys.modules,{'native_harness_api.runtime':module}):self.assertEqual(client.main(['node','--help']),0)
  self.assertEqual(seen,[['node','--help']])
 def test_explicit_help_no_login(self):
  with contextlib.redirect_stdout(io.StringIO()),self.assertRaises(SystemExit) as e:client.main(['--help'])
  self.assertEqual(e.exception.code,0)
 def test_models_pinned_assets(self):
  seen=[];module=types.SimpleNamespace(main=lambda args:seen.append(args) or 0)
  with patch.dict(sys.modules,{'public_harness_models':module}):self.assertEqual(client.main(['models','--profile','C:/profile']),0)
  self.assertEqual(seen[0][-2:],['--profile','C:/profile']);self.assertEqual(seen[0][0],'--assets-root')
 def test_models_override_rejected(self):
  with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit) as e:client.main(['models','--assets-root=C:/bad'])
  self.assertEqual(e.exception.code,2)

 def test_preflight_forwards_without_runtime_start(self):
  seen=[];module=types.SimpleNamespace(main=lambda args:seen.append(args) or 0)
  with patch.dict(sys.modules,{'public_harness_preflight':module}):self.assertEqual(client.main(['preflight','--config','C:/node.json']),0)
  self.assertEqual(seen,[['--config','C:/node.json']])

 def test_workbuddy_target_pair_forwarded(self):
  seen=[];module=types.SimpleNamespace(main=lambda args:seen.append(args) or 0)
  with patch.dict(sys.modules,{'public_harness_models':module}):
   self.assertEqual(client.main(['models','--profile','C:/profile','--target-model','native','--public-alias','workbuddy-test']),0)
  self.assertEqual(seen[0][-4:],['--target-model','native','--public-alias','workbuddy-test'])
