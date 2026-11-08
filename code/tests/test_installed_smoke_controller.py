"""Windows-only real PowerShell/Process tests with private Python and synthetic launcher.
No WorkBuddy, accounts, native RPC or vendor process is executed.
"""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import zipfile

SCRIPT=Path(__file__).resolve().parents[1]/'tools/installed_smoke_controller.ps1'
RUNTIME_SHA='9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38'

@unittest.skipUnless(os.name=='nt','Windows PowerShell/Process integration required')
class ControllerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        runtime=os.environ.get('INSTALLED_SMOKE_RUNTIME_ZIP')
        if not runtime:raise unittest.SkipTest('Explicit INSTALLED_SMOKE_RUNTIME_ZIP required')
        cls.runtime=Path(runtime)
        if hashlib.sha256(cls.runtime.read_bytes()).hexdigest()!=RUNTIME_SHA:
            raise AssertionError('Wrong official runtime')
        cls.pwsh=shutil.which('pwsh')
        if not cls.pwsh:raise AssertionError('PowerShell 7 required; no execution-policy bypass')
    def run_controller(self, program, timeout_copy=False, bad_runtime=False):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);kit=root/'kit';kit.mkdir();workspace=root/'work';workspace.mkdir()
            with zipfile.ZipFile(self.runtime) as z:z.extractall(kit/'runtime')
            if bad_runtime:(kit/'runtime/python.exe').unlink()
            (kit/'launcher.py').write_text(program,encoding='utf-8')
            source=SCRIPT.read_bytes()
            if timeout_copy:
                old=b'$timer.Elapsed.TotalSeconds -ge 120';new=b'$timer.Elapsed.TotalSeconds -ge 2'
                self.assertEqual(source.count(old),1)
                changed=source.replace(old,new)
                self.assertEqual(changed.replace(new,old),source)
                source=changed  # Only test budget differs; production stays 120.
            script=root/'controller.ps1';script.write_bytes(source)
            result=subprocess.run([self.pwsh,'-NoLogo','-NoProfile','-NonInteractive','-File',str(script),
                '-Kit',str(kit),'-Install',str(root/'unused-install'),'-Workspace',str(workspace),
                '-ControlName','NativeHarnessControl-fixture','-RunName','NativeHarnessRun-fixture'],
                capture_output=True,timeout=30)
            self.assertEqual(result.stderr,b'',result.stderr.decode(errors='replace'))
            raw=result.stdout.decode('utf-8-sig').strip()
            self.assertNotIn('PRIVATE_SECRET',raw)
            report=json.loads(raw)
            summary=workspace/'NativeHarnessControl-fixture/summary.json'
            self.assertEqual(json.loads(summary.read_text()),report)
            self.assertTrue((summary.parent/'launch.intent').exists())
            return result.returncode,report
    def phase(self):
        return dict(status='OBSERVED',failure_code=None,stdout_bytes=1,stderr_bytes=0,
                    natural_exit=True,natural_exit_zero=True,host_exited=True,owned_job_empty=True,
                    handles_closed=True,io_threads_stopped=True,cleanup_complete=True)
    def test_success_actual_process_and_schema(self):
        report=dict(status='OBSERVED',help=self.phase(),initialize=self.phase())
        code,actual=self.run_controller('import sys\nsys.stdout.write('+repr(json.dumps(report))+')\n')
        self.assertEqual(code,0);self.assertEqual(actual,report)
    def test_observed_natural_nonzero_cannot_pass(self):
        phase=self.phase();phase['natural_exit_zero']=False
        report=dict(status='OBSERVED',help=self.phase(),initialize=phase)
        code,value=self.run_controller('print('+repr(json.dumps(report))+')')
        self.assertEqual(code,1);self.assertEqual(value['native_cleanup'],'UNKNOWN')

    def test_failed_complete_cleanup_requires_all_components(self):
        for key in ('host_exited','owned_job_empty','handles_closed','io_threads_stopped'):
            values=(False,None) if key in ('host_exited','owned_job_empty') else (False,)
            for invalid in values:
                with self.subTest(key=key,value=invalid):
                    phase=self.phase();phase.update(status='FAILED',failure_code='CLEANUP_UNCERTAIN')
                    phase[key]=invalid
                    report=dict(status='FAILED',help=self.phase(),initialize=phase)
                    code,value=self.run_controller('import sys\nprint('+repr(json.dumps(report))+')\nsys.exit(1)')
                    self.assertEqual(code,1);self.assertEqual(value['native_cleanup'],'UNKNOWN')

    def test_non_natural_initialize_with_confirmed_cleanup_is_valid(self):
        phase=self.phase();phase.update(natural_exit=False,natural_exit_zero=False)
        report=dict(status='OBSERVED',help=self.phase(),initialize=phase)
        code,value=self.run_controller('print('+repr(json.dumps(report))+')')
        self.assertEqual(code,0);self.assertEqual(value,report)

    def test_failed_partial_cleanup_can_preserve_natural_observation(self):
        # Python may observe natural exit, then its terminate/query raises before
        # assigning final host_exited. Do not promote that partial report to PASS.
        phase=self.phase();phase.update(status='FAILED',failure_code='CLEANUP_UNCERTAIN',
                                       host_exited=None,owned_job_empty=None,handles_closed=False,
                                       cleanup_complete=False)
        report=dict(status='FAILED',help=self.phase(),initialize=phase)
        code,value=self.run_controller('import sys\nprint('+repr(json.dumps(report))+')\nsys.exit(1)')
        self.assertEqual(code,1);self.assertEqual(value,report)

    def test_malformed_secret_duplicate_and_schema_rejected(self):
        reports=['PRIVATE_SECRET', '{"status":"UNSUPPORTED","status":"OBSERVED","failure_code":"UNSUPPORTED"}',
                 '{"status":"PREFLIGHT_REJECTED","failure_code":"PRIVATE_SECRET"}',
                 json.dumps(dict(status='OBSERVED',help=self.phase()))]
        for report in reports:
            with self.subTest(report=report):
                code,value=self.run_controller('import sys\nprint('+repr(report)+')\nsys.exit(1)\n')
                self.assertEqual(code,1);self.assertEqual(value['native_cleanup'],'UNKNOWN')
    def test_output_limit_counts_utf8_bytes_and_stderr(self):
        for program in ('import sys\nsys.stdout.buffer.write(("界"*9000).encode("utf-8"))', 'import sys\nsys.stderr.write("PRIVATE_SECRET"*2000)'):
            code,value=self.run_controller(program)
            self.assertEqual(code,1);self.assertEqual(value['controller'],'OUTPUT_LIMIT')
            self.assertTrue(value['controller_exited']);self.assertEqual(value['native_cleanup'],'UNKNOWN')
    def test_timeout_kills_only_retained_process(self):
        code,value=self.run_controller('import time\ntime.sleep(60)',timeout_copy=True)
        self.assertEqual(code,1);self.assertEqual(value['controller'],'TIMEOUT')
        self.assertTrue(value['controller_exited']);self.assertEqual(value['native_cleanup'],'UNKNOWN')
    def test_start_unconfirmed_is_unknown(self):
        code,value=self.run_controller('raise AssertionError()',bad_runtime=True)
        self.assertEqual(code,1);self.assertFalse(value['controller_exited'])
        self.assertEqual(value['native_cleanup'],'UNKNOWN')

if __name__=='__main__':unittest.main()
