"""Hosted Windows, external-account WorkBuddy Product-only supervisor.

No stdin protocol; no raw vendor output export. OwnedWindowsJob is not a sandbox.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import struct
import threading
import time
from native_harness_api.windows_job import OwnedWindowsJob
from official_workbuddy_smoke import acquire, INSTALLER_SHA

LIMIT = 1024 * 1024
RETAINED = []
BOOLS = ('entry_verified','product_config_verified','import_ok','harness_created','client_created',
         'connect_returned','auth_methods_empty','connected','product_read_returned','product_ready',
         'product_identity_verified','client_closed','harness_closed','harness_settled','success')
KEYS = set(BOOLS) | {'schema_version','protocol_version','failure_stage','elapsed_ms','error_class','error_detail','elicitation_seen'}
PIN = {'package.json':'9f7aaf7ba9cf0f8bb961e22a80c9e7ac24c2fcb4232b145e91eb39bcecb539a4',
       'lib/public.js':'8bfbfd68b6f4c04c70a94e1943ecc2a391dd5618e4a1cff087f77ea9d5402733'}

class Rejected(Exception): pass

def require(ok):
    if not ok: raise Rejected()

def ancestors(path):
    for p in (path,*path.parents):
        s=p.lstat()
        require(not stat.S_ISLNK(s.st_mode) and not getattr(s,'st_file_attributes',0)&0x400)

def identity(path):
    ancestors(path); s=path.stat(); require(stat.S_ISDIR(s.st_mode))
    return (path.resolve(strict=True),s.st_dev,s.st_ino)

def unique(pairs):
    d={}
    for k,v in pairs:
        require(k not in d); d[k]=v
    return d

def parse_dto(raw):
    try:
        require(type(raw) is bytes and 0<len(raw)<=4096)
        def invalid(_): raise Rejected()
        d=json.loads(raw.decode('utf8'),object_pairs_hook=unique,parse_constant=invalid)
        require(type(d) is dict and set(d)==KEYS)
        require(all(type(d[k]) is bool for k in BOOLS))
        for key,maximum in [('schema_version',4),('protocol_version',1),('failure_stage',8),('elapsed_ms',120000),('error_class',17),('error_detail',11),('elicitation_seen',1)]:
            require(type(d[key]) is int and 0<=d[key]<=maximum)
        require(d['schema_version']==4)
        require(not d['product_config_verified'] or d['entry_verified'])
        require(not d['import_ok'] or d['entry_verified'] and d['product_config_verified'])
        require((d['error_class']==0)==(d['failure_stage']==0))
        require(d['failure_stage']!=0 or d['error_detail']==0 and d['elicitation_seen']==0)
        require(not d['auth_methods_empty'] or d['connect_returned'])
        require(not d['connected'] or d['connect_returned'] and d['auth_methods_empty'])
        require(not d['connect_returned'] or d['client_created'] and d['harness_created'] and d['import_ok'])
        require(not d['product_read_returned'] or d['connected'])
        require(not d['product_ready'] or d['product_read_returned'])
        require(not d['product_identity_verified'] or d['product_ready'])
        # Success cannot be inferred from a partial child report.
        complete=all(d[k] for k in BOOLS if k!='success') and d['protocol_version']==1 and d['failure_stage']==0 and d['error_class']==0 and d['elicitation_seen']==0
        require(d['success']==complete)
        require(d['failure_stage']!=0 or complete)
        require(not d['connected'] or d['client_created'] and d['harness_created'] and d['import_ok'])
        return d
    except (ValueError,TypeError,UnicodeError,RecursionError): raise Rejected() from None

def read_dto(directory,trusted):
    require(identity(directory)==trusted)
    with os.scandir(directory) as it: found=[next(it,None),next(it,None)]
    require(found[0] is not None and found[1] is None and found[0].name=='library-summary.json')
    path=directory/'library-summary.json'; ancestors(path); before=path.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and 0<before.st_size<=4096)
    with path.open('rb') as f:
        opened=os.fstat(f.fileno()); require((before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino))
        raw=f.read(4097); after=os.fstat(f.fileno())
    require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns) and len(raw)==before.st_size)
    require(identity(directory)==trusted)
    return parse_dto(raw)

def supervise(command,cwd,env,*,timeout=50,cleanup_timeout=5,job_class=OwnedWindowsJob):
    require(type(timeout) in (int,float) and math.isfinite(timeout) and 0<timeout<=120)
    r=dict(success=False,failure_stage=1,stdin_bytes=0,stdout_bytes=0,stderr_bytes=0,
           natural_exit_zero=False,natural_job_empty=False,host_exited=False,owned_job_empty=False,
           handles_closed=False,io_threads_stopped=False,cleanup_complete=False)
    owner=None; threads=[]; uncertain=False; fault=threading.Event(); totals={'stdout':0,'stderr':0}
    def reader(label,stream):
        try:
            while True:
                chunk=stream.read(4096)
                if not chunk: return
                totals[label]+=len(chunk)
                if totals[label]>LIMIT: fault.set(); return
        except BaseException: fault.set()
    try:
        end=time.monotonic()+timeout
        owner=job_class.spawn(command,cwd,env)
        owner.stdin.close()  # Never write or flush protocol bytes.
        for label in totals:
            t=threading.Thread(target=reader,args=(label,getattr(owner,label)),daemon=True)
            threads.append(t); uncertain=True; t.start(); uncertain=False
        while True:
            if fault.is_set(): r['failure_stage']=2; break
            if owner.host_exited():
                r['natural_exit_zero']=owner.exit_code()==0
                if all(not t.is_alive() for t in threads):
                    r['natural_job_empty']=owner.active_processes()==0
                    r['failure_stage']=0 if r['natural_exit_zero'] and r['natural_job_empty'] else 3; break
            if time.monotonic()>=end: r['failure_stage']=4; break
            time.sleep(.01)
    except BaseException as exc:
        if owner is None: owner=getattr(exc,'owner',None)
    finally:
        end=time.monotonic()+cleanup_timeout
        if owner is not None:
            try:
                r['owned_job_empty']=owner.terminate(max(0,end-time.monotonic())).get('owned_job_empty') is True
                r['host_exited']=owner.host_exited() is True
            except BaseException: pass
        for t in threads:
            try:
                if t.ident is not None: t.join(max(0,end-time.monotonic()))
            except BaseException: pass
        r['io_threads_stopped']=not uncertain and all(not t.is_alive() for t in threads)
        if owner is not None and r['io_threads_stopped']:
            try:
                closed=owner.close(max(0,end-time.monotonic()))
                r['owned_job_empty']=closed.get('owned_job_empty') is True; r['handles_closed']=True
            except BaseException: pass
        r['cleanup_complete']=all(r[k] is True for k in ('host_exited','owned_job_empty','handles_closed','io_threads_stopped'))
        if owner is not None and not r['cleanup_complete']: RETAINED.append(owner)
        if not r['cleanup_complete']: r['failure_stage']=5
        elif fault.is_set(): r['failure_stage']=2
        for label in totals: r[label+'_bytes']=min(totals[label],LIMIT+4096)
        r['success']=r['failure_stage']==0 and r['natural_exit_zero'] and r['cleanup_complete']
    return r

def collect(controller,directory,trusted):
    result={'controller':controller,'dto_valid':False,'success':False}
    if not controller['cleanup_complete']: return result
    try:
        result['dto']=read_dto(directory,trusted); result['dto_valid']=True
        result['success']=controller['success'] and result['dto']['success']
    except (Rejected,OSError): pass
    return result

def verify_tree(root,expected_hash,expected_count):
    """Full pinned payload regular-file digest, including all transitive imports."""
    ancestors(root); entries=[]
    for directory,dirs,files in os.walk(root,followlinks=False):
        for name in dirs:
            path=Path(directory)/name; ancestors(path)
            require(path.is_dir())
        for name in files:
            path=Path(directory)/name; ancestors(path); info=path.stat()
            require(stat.S_ISREG(info.st_mode) and info.st_nlink==1)
            require(len(entries)<20000 and info.st_size<=1024**3)
            with path.open('rb') as stream:
                digest=hashlib.file_digest(stream,'sha256').hexdigest()
            entries.append((path.relative_to(root).as_posix().encode('utf8'),info.st_size,digest))
    require(len(entries)==expected_count)
    digest=hashlib.sha256()
    for name,size,content_hash in sorted(entries):
        digest.update(name+b'\0'+str(size).encode('ascii')+b'\0'+content_hash.encode('ascii')+b'\n')
    require(digest.hexdigest()==expected_hash)


ASAR_SHA = 'dcabcd90f07a627a06f1fd55bc872a1c25a79aab43c3012930b6287840ab3d20'
ASAR_SIZE = 181996084
PAIR_SIZES = {'package.json':18632, 'lib/public.js':296}

def verify_asar_pair(archive, package):
    """Read only: pinned archive header and exact unpacked pair, never extract/resolve imports."""
    ancestors(archive); before=archive.stat()
    require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and before.st_size==ASAR_SIZE)
    with archive.open('rb') as stream:
        opened=os.fstat(stream.fileno())
        require((before.st_dev,before.st_ino)==(opened.st_dev,opened.st_ino))
        require(hashlib.file_digest(stream,'sha256').hexdigest()==ASAR_SHA)
        stream.seek(0); prefix=stream.read(16); require(len(prefix)==16)
        first,header_size,payload_size,json_size=struct.unpack('<4I',prefix)
        require(first==4 and 8<=header_size<=8*1024*1024 and header_size%4==0)
        require(payload_size==header_size-4 and 0<json_size<=payload_size-4)
        require(header_size==8+((json_size+3)//4)*4 and 8+header_size<=before.st_size)
        raw=stream.read(json_size); require(len(raw)==json_size)
        def invalid(_): raise Rejected()
        header=json.loads(raw.decode('utf8'),object_pairs_hook=unique,parse_constant=invalid)
        after=os.fstat(stream.fileno())
        require((before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns))
    for name,expected in PIN.items():
        record=header
        for part in ('node_modules','@genie','agent-harness',*name.split('/')):
            require(type(record) is dict and type(record.get('files')) is dict)
            record=record['files'].get(part)
        require(type(record) is dict and record.get('unpacked') is True)
        require('link' not in record and 'offset' not in record and 'files' not in record)
        require(type(record.get('size')) is int and record['size']==PAIR_SIZES[name])
        integrity=record.get('integrity')
        require(type(integrity) is dict and integrity.get('algorithm')=='SHA256' and integrity.get('hash')==expected)
        path=package/name; ancestors(path); info=path.stat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and info.st_size==PAIR_SIZES[name])
        with path.open('rb') as stream:
            opened=os.fstat(stream.fileno())
            require((info.st_dev,info.st_ino)==(opened.st_dev,opened.st_ino))
            require(hashlib.file_digest(stream,'sha256').hexdigest()==expected)
            after=os.fstat(stream.fileno())
            require((info.st_size,info.st_mtime_ns)==(after.st_size,after.st_mtime_ns))


def environment(root):
    private=root/'isolated'; private.mkdir()
    env={'SystemRoot':os.environ.get('SystemRoot',r'C:\Windows'),'ELECTRON_RUN_AS_NODE':'1',
         'GITHUB_ACTIONS':'true','RUNNER_ENVIRONMENT':'github-hosted'}
    env['WINDIR']=env['SystemRoot']; env['PATH']=str(Path(env['SystemRoot'])/'System32')
    for folder,keys in [('home',('HOME','USERPROFILE')),('config',('CODEBUDDY_CONFIG_DIR','WORKBUDDY_CONFIG_DIR')),
          ('appdata',('APPDATA',)),('localappdata',('LOCALAPPDATA',)),('temp',('TEMP','TMP'))]:
        path=private/folder; path.mkdir()
        for key in keys: env[key]=str(path)
    return env

def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root',type=Path,required=True)
    parser.add_argument('--seven-zip',type=Path,required=True)
    parser.add_argument('--summary',type=Path,required=True)
    args=parser.parse_args(argv); report={'success':False,'preflight':False,'preflight_stage':1}
    try:
        require(os.name=='nt' and os.environ.get('GITHUB_ACTIONS')=='true' and os.environ.get('RUNNER_ENVIRONMENT')=='github-hosted')
        require(args.run_root.is_absolute() and args.summary.is_absolute())
        require(args.run_root==Path(os.environ['RUNNER_TEMP'])/'native-harness-library-product-once')
        require(args.summary==Path(os.environ['GITHUB_WORKSPACE'])/'library-product-sanitized-summary.json')
        ancestors(args.run_root.parent); ancestors(args.summary.parent); require(not args.summary.exists())
        report['preflight_stage']=2
        args.run_root.mkdir()
        with (args.run_root/'attempt.intent').open('xb') as f: f.write(b'one-attempt-no-retry\n'); f.flush(); os.fsync(f.fileno())
        report['preflight_stage']=3
        executable,_=acquire(args.run_root,args.seven_zip)
        report['preflight_stage']=4
        verify_tree(executable.parent,
            '5c1f3ccee6ac24a630e9f18b3a49f559b3fbad76c1ec47a2a2448cb9b6f54ab2',6166)
        ancestors(executable); require(executable.stat().st_nlink==1 and executable.stat().st_size==204590360)
        with executable.open('rb') as f: require(hashlib.file_digest(f,'sha256').hexdigest()=='bbf504003825e0e22dd8c401b396d79db78ec603558088565d1b2e81561704a0')
        package=executable.parent/'resources/app.asar.unpacked/node_modules/@genie/agent-harness'
        verify_asar_pair(executable.parent/'resources/app.asar',package)
        report['preflight_stage']=5
        env=environment(args.run_root); workspace=args.run_root/'workspace'; workspace.mkdir()
        output=args.run_root/'output'; output.mkdir(); trusted=identity(output)
        driver=Path(__file__).with_name('workbuddy_library_driver.mjs').resolve(strict=True)
        ancestors(driver); require(driver.stat().st_nlink==1)
        command=[str(executable),str(driver),'--package-root',str(package),'--run-root',str(args.run_root)]
        report['preflight_stage']=6
        report=collect(supervise(command,workspace,env),output,trusted); report['preflight']=True; report['preflight_stage']=0
        report['loader_revision']=5; report['archive_pair_verified']=True
    except (Exception,KeyboardInterrupt): pass
    # Reconstructed own DTO only; no raw strings or errors.
    try:
        with args.summary.open('x',encoding='utf8') as f:
            json.dump(report,f,separators=(',',':')); f.write('\n'); f.flush(); os.fsync(f.fileno())
    except OSError: return 1
    return 0 if report['success'] else 1

if __name__=='__main__': raise SystemExit(main())
