"""Synthetic supervision/DTO fixtures; never loads official vendor code."""
import io
import hashlib
import json
import os
from pathlib import Path
import subprocess
import shutil
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
import official_workbuddy_library as p


def dto():
    return {**{k:True for k in p.BOOLS},'schema_version':4,'protocol_version':1,'failure_stage':0,'elapsed_ms':1,'error_class':0,'error_detail':0,'elicitation_seen':0}

def no_product_read(d):
    d.update(product_read_returned=False,product_ready=False,product_identity_verified=False)
    return d

class Input(io.BytesIO):
    def write(self,_): raise AssertionError('stdin must never be written')
    def flush(self): pass

class Owner:
    def __init__(self,output=b'',exited=True):
        self.stdin=Input(); self.stdout=io.BytesIO(output); self.stderr=io.BytesIO()
        self.exited=exited; self.killed=False
    def host_exited(self): return self.exited or self.killed
    def active_processes(self): return 0 if self.host_exited() else 1
    def exit_code(self): return 0 if self.exited else 1
    def terminate(self,_): self.killed=True; return {'owned_job_empty':True}
    def close(self,_): return {'owned_job_empty':True}

class Tests(unittest.TestCase):
    def test_asar_pair_bounded_header(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); package=root/'package'; (package/'lib').mkdir(parents=True)
            data={'package.json':b'{}','lib/public.js':b'export{};'}
            pins={k:hashlib.sha256(v).hexdigest() for k,v in data.items()}
            sizes={k:len(v) for k,v in data.items()}
            for k,v in data.items():(package/k).write_bytes(v)
            header={'files':{}}; current=header
            for part in ('node_modules','@genie','agent-harness'):
                current=current['files'].setdefault(part,{'files':{}})
            for name in data:
                target=current
                for part in name.split('/')[:-1]:target=target['files'].setdefault(part,{'files':{}})
                target['files'][name.split('/')[-1]]={'size':sizes[name],'unpacked':True,
                    'integrity':{'algorithm':'SHA256','hash':pins[name]}}
            def packed(obj):
                raw=json.dumps(obj,separators=(',',':')).encode(); padded=(len(raw)+3)//4*4
                return struct.pack('<4I',4,padded+8,padded+4,len(raw))+raw+b'\0'*(padded-len(raw))
            archive=root/'app.asar'
            def check(blob):
                archive.write_bytes(blob)
                with patch.object(p,'ASAR_SIZE',len(blob)),patch.object(p,'ASAR_SHA',hashlib.sha256(blob).hexdigest()),patch.object(p,'PIN',pins),patch.object(p,'PAIR_SIZES',sizes):
                    p.verify_asar_pair(archive,package)
            good=packed(header); check(good)
            for prefix in [(3,8,4,1),(4,9*1024*1024,8,1),(4,8,4,99)]:
                with self.assertRaises(p.Rejected):check(struct.pack('<4I',*prefix)+b'{}')
            current['files']['package.json']['unpacked']=False
            with self.assertRaises(p.Rejected):check(packed(header))
            current['files']['package.json']['unpacked']=True
            current['files']['package.json']['link']='other'
            with self.assertRaises(p.Rejected):check(packed(header))
            del current['files']['package.json']['link']
            (package/'lib/public.js').write_bytes(b'changed!!')
            with self.assertRaises(p.Rejected):check(good)

    def test_schema4_error_classes_and_entry_order(self):
        for error in range(1,18):
            d=no_product_read(dto()); d.update(success=False,import_ok=False,harness_created=False,client_created=False,
                connect_returned=False,connected=False,client_closed=False,harness_closed=False,harness_settled=False,
                auth_methods_empty=False,protocol_version=0,failure_stage=1,error_class=error)
            self.assertEqual(p.parse_dto(json.dumps(d).encode())['error_class'],error)
        d=dto();d['schema_version']=1
        with self.assertRaises(p.Rejected):p.parse_dto(json.dumps(d).encode())

    def test_connect_return_evidence(self):
        d=no_product_read(dto());d.update(connected=False,success=False,protocol_version=0,failure_stage=4,error_class=8)
        self.assertTrue(p.parse_dto(json.dumps(d).encode())['connect_returned'])
        d['connect_returned']=False;d['auth_methods_empty']=False
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['connect_returned'])
        d['connected']=True
        with self.assertRaises(p.Rejected):p.parse_dto(json.dumps(d).encode())
        d['connected']=False;d['connect_returned']=True;d['client_created']=False
        with self.assertRaises(p.Rejected):p.parse_dto(json.dumps(d).encode())
        d=no_product_read(dto());d.update(connected=False,success=False,protocol_version=0,failure_stage=4,error_class=8)
        for detail in range(12):
            d['error_detail']=detail
            self.assertEqual(p.parse_dto(json.dumps(d).encode())['error_detail'],detail)

    def test_complete_tree_pin(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); (root/'a').write_bytes(b'fixture')
            record=b'a\0'+b'7\0'+hashlib.sha256(b'fixture').hexdigest().encode()+b'\n'
            pin=hashlib.sha256(record).hexdigest()
            p.verify_tree(root,pin,1)
            with self.assertRaises(p.Rejected):p.verify_tree(root,pin,2)
            (root/'a').write_bytes(b'changed')
            with self.assertRaises(p.Rejected):p.verify_tree(root,pin,1)
            (root/'extra').touch()
            with self.assertRaises(p.Rejected):p.verify_tree(root,pin,1)

    def test_spawn_exception_owner_cleanup(self):
        owner=Owner();error=OSError();error.owner=owner
        with patch.object(p.OwnedWindowsJob,'spawn',side_effect=error):r=p.supervise([],Path.cwd(),{})
        self.assertTrue(r['cleanup_complete']);self.assertFalse(r['success'])

    def test_thread_start_uncertainty(self):
        owner=Owner()
        with patch.object(p.OwnedWindowsJob,'spawn',return_value=owner),patch.object(p.threading.Thread,'start',side_effect=RuntimeError()):
            r=p.supervise([],Path.cwd(),{})
        self.assertFalse(r['cleanup_complete']);self.assertFalse(r['io_threads_stopped'])
        with patch.object(p,'read_dto') as read:p.collect(r,Path('unused'),None)
        read.assert_not_called();self.assertIn(owner,p.RETAINED);p.RETAINED.remove(owner)

    def test_directory_identity_change(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'out';root.mkdir();trusted=p.identity(root)
            root.rename(base/'old');root.mkdir();(root/'library-summary.json').write_text(json.dumps(dto()))
            with self.assertRaises(p.Rejected):p.read_dto(root,trusted)

    def test_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);root=base/'out';root.mkdir();source=base/'source';source.write_text(json.dumps(dto()))
            try:(root/'library-summary.json').symlink_to(source)
            except OSError:self.skipTest('symlink privilege unavailable')
            with self.assertRaises(p.Rejected):p.read_dto(root,p.identity(root))

    def test_success_zero_stdin(self):
        o=Owner()
        with patch.object(p.OwnedWindowsJob,'spawn',return_value=o): r=p.supervise([],Path.cwd(),{})
        self.assertTrue(o.stdin.closed); self.assertEqual(r['stdin_bytes'],0)
        self.assertTrue(r['success']); self.assertTrue(r['cleanup_complete'])

    def test_descendant_not_graceful(self):
        o=Owner();o.active_processes=lambda:1
        with patch.object(p.OwnedWindowsJob,'spawn',return_value=o):r=p.supervise([],Path.cwd(),{})
        self.assertTrue(r['natural_exit_zero']);self.assertFalse(r['natural_job_empty'])
        self.assertTrue(r['cleanup_complete']);self.assertFalse(r['success'])

    def test_overflow(self):
        with patch.object(p,'LIMIT',8),patch.object(p.OwnedWindowsJob,'spawn',return_value=Owner(b'secret'*99)):
            r=p.supervise([],Path.cwd(),{})
        self.assertEqual(r['failure_stage'],2); self.assertNotIn('secret',json.dumps(r))

    def test_deadline(self):
        with patch.object(p.OwnedWindowsJob,'spawn',return_value=Owner(exited=False)):
            r=p.supervise([],Path.cwd(),{},timeout=.01)
        self.assertFalse(r['success']); self.assertEqual(r['failure_stage'],4)
        self.assertFalse(r['natural_exit_zero']); self.assertTrue(r['cleanup_complete'])

    def test_cleanup_false_no_read(self):
        with patch.object(p,'read_dto') as read:
            r=p.collect({'cleanup_complete':False},Path('unused'),None)
        read.assert_not_called(); self.assertFalse(r['success'])

    def test_invalid_dto(self):
        self.assertTrue(p.parse_dto(json.dumps(dto()).encode())['success'])
        for raw in [b'{}',b'{"a":1,"a":2}',b'{"a":NaN}',b'\xff',b' '*4097]:
            with self.assertRaises(p.Rejected): p.parse_dto(raw)
        for key,val in [('protocol_version',True),('failure_stage',9),('elapsed_ms',float('inf')),
                        ('entry_verified',False),('error_class',18),('error_detail',12),('error_detail',True),('error_detail',1),('elicitation_seen',1),('elicitation_seen',2),('elicitation_seen',True),('error_class',True),('import_ok',False),('extra','SECRET')]:
            d=dto();d[key]=val
            with self.subTest(key=key),self.assertRaises(p.Rejected):p.parse_dto(json.dumps(d).encode())

    def test_partial_not_success(self):
        d=no_product_read(dto());d['connected']=False;d['success']=False;d['failure_stage']=4;d['error_class']=8
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['success'])

    def test_product_evidence_order_and_partial_failure(self):
        d=no_product_read(dto());d.update(success=False,failure_stage=8,error_class=8)
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['product_read_returned'])
        d['product_read_returned']=True
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['product_ready'])
        d['product_ready']=True
        self.assertFalse(p.parse_dto(json.dumps(d).encode())['product_identity_verified'])
        for earlier,later in [('entry_verified','product_config_verified'),
                              ('product_config_verified','import_ok'),
                              ('connect_returned','auth_methods_empty'),
                              ('auth_methods_empty','connected'),
                              ('connected','product_read_returned'),
                              ('product_read_returned','product_ready'),
                              ('product_ready','product_identity_verified')]:
            broken=dto();broken.update(success=False,failure_stage=8,error_class=8)
            broken[earlier]=False;broken[later]=True
            with self.subTest(earlier=earlier,later=later),self.assertRaises(p.Rejected):
                p.parse_dto(json.dumps(broken).encode())
        for key in ['product_config_verified','auth_methods_empty','product_read_returned',
                    'product_ready','product_identity_verified']:
            bad=dto();bad[key]=1
            with self.subTest(key=key),self.assertRaises(p.Rejected):
                p.parse_dto(json.dumps(bad).encode())

    def test_fixed_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); trusted=p.identity(root); f=root/'library-summary.json'
            f.write_text(json.dumps(dto()))
            self.assertTrue(p.read_dto(root,trusted)['success'])
            (root/'extra').touch()
            with self.assertRaises(p.Rejected): p.read_dto(root,trusted)

    def test_hardlink(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp); output=root/'out';output.mkdir();source=root/'source';source.write_text(json.dumps(dto()))
            os.link(source,output/'library-summary.json')
            with self.assertRaises(p.Rejected): p.read_dto(output,p.identity(output))

    def test_environment_no_inheritance(self):
        ambient={key:'secret' for key in ['NODE_OPTIONS','GITHUB_TOKEN','HTTP_PROXY',
            'CODEBUDDY_API_KEY','CODEBUDDY_AUTH_TOKEN','CODEBUDDY_CONFIG_PATH',
            'ACC_PRODUCT_CONFIG_PATH','CODEBUDDY_CREDENTIALS_IN_MEMORY','SANDBOX_CENTER_IPC_ADDRESS']}
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,ambient):
            env=p.environment(Path(tmp))
            for key in ambient:self.assertNotIn(key,env)
            self.assertEqual(env['HOME'],env['USERPROFILE'])
            for key in ['HOME','USERPROFILE','APPDATA','LOCALAPPDATA','TEMP','TMP',
                        'CODEBUDDY_CONFIG_DIR','WORKBUDDY_CONFIG_DIR']:
                self.assertTrue(Path(env[key]).is_relative_to(Path(tmp)/'isolated'))

    def test_owned_cleanup_failure(self):
        o=Owner();o.close=lambda _: (_ for _ in ()).throw(OSError())
        with patch.object(p.OwnedWindowsJob,'spawn',return_value=o):r=p.supervise([],Path.cwd(),{})
        self.assertFalse(r['success']);self.assertFalse(r['cleanup_complete'])
        self.assertIn(o,p.RETAINED);p.RETAINED.remove(o)

class NodeFixtures(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node unavailable: self-written fixture not run')
    def test_self_written_driver(self):
        fixture=Path(__file__).parent/'fakes'/'workbuddy_library_driver_test.mjs'
        result=subprocess.run(['node',str(fixture)],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stdout.decode()+result.stderr.decode())

@unittest.skipUnless(os.name=='nt','Actual Windows lifecycle, not simulated')
class WindowsTests(unittest.TestCase):
    def test_real_zero_stdin(self):
        r=p.supervise([sys.executable,'-c','import sys; assert sys.stdin.buffer.read()==b""'],Path.cwd(),dict(os.environ))
        self.assertTrue(r['success']);self.assertTrue(r['cleanup_complete'])

    def test_real_remaining_descendant(self):
        script='import subprocess,sys; subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,stdin=subprocess.DEVNULL)'
        r=p.supervise([sys.executable,'-c',script],Path.cwd(),dict(os.environ))
        self.assertTrue(r['natural_exit_zero']);self.assertFalse(r['natural_job_empty'])
        self.assertTrue(r['cleanup_complete']);self.assertFalse(r['success'])

    def test_real_overflow(self):
        with patch.object(p,'LIMIT',8192):
            r=p.supervise([sys.executable,'-c','import sys; sys.stdout.buffer.write(b"x"*65536); sys.stdout.flush()'],Path.cwd(),dict(os.environ))
        self.assertFalse(r['success']);self.assertEqual(r['failure_stage'],2);self.assertTrue(r['cleanup_complete'])

    def test_real_timeout(self):
        r=p.supervise([sys.executable,'-c','import time; time.sleep(30)'],Path.cwd(),dict(os.environ),timeout=.1)
        self.assertFalse(r['success']);self.assertTrue(r['cleanup_complete'])

if __name__=='__main__':unittest.main()
