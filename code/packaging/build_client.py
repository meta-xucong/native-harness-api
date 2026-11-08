"""Offline complete client packager. Copies pinned assets; never executes them."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import stat
import re
import sys
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from public_harness_runtime_assets import verify_runtime_assets
from public_harness_catalog_assets import tree_digest, regular, member_parts, validate_inventory, SFE_TREE, NODE_TREE, OVERLAY_SHA
from build import runtime_files, read_regular, digest
from codex_assets import verify_assets as verify_codex_assets, TREE as CODEX_TREE

CODEX_TOOLS = ('codex_assets.py', 'codex_profile.py', 'codex_control_protocol.py', 'codex_login.py')
CODEX_KIND = 'native-harness-windows-codex-client'
CODEX_EXE_SIZE = 280844080

TOOLS = ('prepare_native_deployment.py', 'public_harness_preflight.py', 'public_harness_models.py', 'public_harness_login.py', 'public_harness_login_profile.py', 'public_harness_login_protocol.py',
         'public_harness_runtime_assets.py', 'public_harness_catalog_assets.py', 'public_harness_catalog_protocol.py')
PACKAGING = ('client.py', 'Client.cmd', 'CLIENT-README.zh-CN.md', 'client-config.example.json')


def sources(code, revision, harness='workbuddy'):
    root = code.parent
    def git(*args):
        return subprocess.run(['git','-C',str(root),*args],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout
    if len(revision) != 40 or any(c not in '0123456789abcdef' for c in revision) or git('rev-parse','HEAD').decode().strip()!=revision:
        raise ValueError('Exact full source revision required')
    if Path(git('rev-parse','--show-toplevel').decode().strip()).resolve()!=root:
        raise ValueError('Repository root mismatch')
    tracked = git('ls-tree','-r','--name-only',revision,'code/src/native_harness_api').decode().splitlines()
    for name in ('native_trial_runtime.py','public_sfe_adapter.py','public_sfe_owned_session.py','public_sfe_session.py'):
        if 'code/src/native_harness_api/'+name not in tracked: raise ValueError('Client implementation incomplete')
    selected = [(p, p[len('code/'):]) for p in tracked if p.endswith('.py') and '/__pycache__/' not in p]
    selected += [('code/tools/'+n,'tools/'+n) for n in TOOLS + CODEX_TOOLS]
    selected += [('code/packaging/'+n,n) for n in PACKAGING if harness != 'codex' or n not in ('CLIENT-README.zh-CN.md', 'client-config.example.json')]
    if harness == 'codex':
        selected += [('code/packaging/CODEX-CLIENT-README.zh-CN.md', 'CLIENT-README.zh-CN.md'),
                     ('code/packaging/CODEX-client-config.example.json', 'client-config.example.json'),
                     ('code/packaging/CODEX-LICENSE.txt', 'licenses/codex/LICENSE'),
                     ('code/packaging/CODEX-NOTICE.txt', 'licenses/codex/NOTICE')]
    selected += [('code/docs/DEPLOYMENT_CHAIN.zh-CN.md','DEPLOYMENT_CHAIN.zh-CN.md'),
                 ('code/docs/NATIVE_EVENT_STREAM.md','NATIVE_EVENT_STREAM.md'),
                 ('code/docs/MULTI-HARNESS-CONFIG.md','MULTI-HARNESS-CONFIG.md')]
    required = ['code/packaging/build_client.py','code/packaging/build.py']
    for path in [p for p,_ in selected]+required:
        if read_regular(root/path) != git('show',revision+':'+path):
            raise ValueError('Working tree differs from source revision')
    return {dest:root/path for path,dest in selected}


def build_client(code, assets, runtime_zip, output, revision, *, harness='workbuddy'):
    code,assets,output = Path(code).resolve(),Path(assets),Path(output)
    if harness not in ('workbuddy', 'codex'): raise ValueError('Unknown harness')
    (verify_codex_assets if harness == 'codex' else verify_runtime_assets)(assets)
    files = sources(code,revision,harness) if harness == 'codex' else sources(code,revision)
    runtime = runtime_files(read_regular(Path(runtime_zip)))
    for folder in (('',) if harness == 'codex' else ('sfe','node')):
        for path in sorted((assets/folder).rglob('*')):
            if path.is_file():
                regular(path)
                files['assets/'+path.relative_to(assets).as_posix()] = path
    if harness == 'workbuddy': files['assets/product.json'] = assets/'product.json'
    sizes = [(name,path.stat().st_size,False) for name,path in files.items()]
    sizes += [(name,len(data),False) for name,data in runtime.items()]
    client_inventory(sizes, harness)
    hashes={}
    with output.open('xb') as stream, zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as bundle:
        for name in sorted(set(files)|set(runtime)):
            data=read_regular(files[name]) if name in files else runtime[name]
            hashes[name]=digest(data)
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            info.create_system=3;info.external_attr=0o100644<<16
            bundle.writestr(info,data)
        manifest={'format':1,'kind':CODEX_KIND if harness == 'codex' else 'native-harness-windows-client','source_revision':revision,
                  'real_account_model_acceptance':'NOT_RUN','files':hashes}
        info=zipfile.ZipInfo('CLIENT-MANIFEST.json',date_time=(1980,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
        info.create_system=3;info.external_attr=0o100644<<16
        bundle.writestr(info,json.dumps(manifest,sort_keys=True,indent=2)+'\n')
    verify_client(output)
    return digest(output.read_bytes())


def client_inventory(entries, harness):
    if harness != 'codex':
        return validate_inventory(entries)
    # Do not raise the legacy 200 MiB ceiling for other files or WorkBuddy.
    if sum(size for _, size, _ in entries) > 600 * 1024 * 1024:
        raise ValueError('Client total size exceeded')
    bounded = [(name, 200 * 1024 * 1024 if name == 'assets/bin/codex-app-server.exe'
                and size == CODEX_EXE_SIZE and not directory else size, directory)
               for name, size, directory in entries]
    return validate_inventory(bounded)


def verify_client(path):
    with zipfile.ZipFile(path) as bundle:
        members=bundle.infolist()
        for member in members:
            mode=stat.S_IFMT(member.external_attr >> 16)
            if member.is_dir() or member.flag_bits & 1 or mode not in (0,stat.S_IFREG) or member.external_attr & 0x400:
                raise ValueError('Unsafe archive member type')
        # Validate names and sizes before reading; only the pinned Codex exe gets a size exception.
        client_inventory([(m.filename,m.file_size,m.is_dir()) for m in members], 'codex')
        if bundle.getinfo('CLIENT-MANIFEST.json').file_size > 1024*1024: raise ValueError('Manifest too large')
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate manifest key')
                result[key]=value
            return result
        manifest=json.loads(bundle.read('CLIENT-MANIFEST.json'),object_pairs_hook=unique)
        if (type(manifest) is not dict or set(manifest)!={'format','kind','source_revision','real_account_model_acceptance','files'}
                or type(manifest['source_revision']) is not str or re.fullmatch('[0-9a-f]{40}',manifest['source_revision']) is None
                or manifest['real_account_model_acceptance']!='NOT_RUN' or type(manifest['files']) is not dict):
            raise ValueError('Invalid release metadata')
        if type(manifest.get('format')) is not int or manifest.get('format')!=1 or manifest.get('kind') not in ('native-harness-windows-client', CODEX_KIND):raise ValueError('Invalid manifest')
        harness = 'codex' if manifest['kind'] == CODEX_KIND else 'workbuddy'
        if harness == 'workbuddy': validate_inventory([(m.filename,m.file_size,m.is_dir()) for m in members])
        hashes=manifest['files']
        if set(bundle.namelist()) != set(hashes)|{'CLIENT-MANIFEST.json'}:raise ValueError('Incomplete client')
        actual={}
        for name,expected in hashes.items():
            data=bundle.read(name)
            actual[name]=(len(data),digest(data))
            if actual[name][1] != expected:raise ValueError('Client member hash mismatch')
        for folder,pin in ((('',CODEX_TREE),) if harness == 'codex' else (('sfe',SFE_TREE),('node',NODE_TREE))):
            prefix='assets/'+(folder+'/' if folder else '')
            records=sorted([name[len(prefix):],size,sha] for name,(size,sha) in actual.items() if name.startswith(prefix))
            observed={'sha256':digest(json.dumps(records,separators=(',',':')).encode()),'files':len(records),'bytes':sum(r[1] for r in records)}
            if observed!=pin:raise ValueError('Archive runtime tree mismatch')
        if harness == 'workbuddy' and actual.get('assets/product.json',(0,None))[1]!=OVERLAY_SHA:raise ValueError('Archive product mismatch')
    return manifest


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--assets-root',required=True);p.add_argument('--runtime-zip',required=True)
    p.add_argument('--output',required=True);p.add_argument('--source-revision',required=True)
    p.add_argument('--harness', choices=('workbuddy','codex'), default='workbuddy')
    a=p.parse_args()
    print(build_client(Path(__file__).resolve().parents[1],a.assets_root,a.runtime_zip,a.output,a.source_revision,harness=a.harness))
