"""Reproducible three-repository source handoff. Never builds images or deploys."""
import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import zipfile

LABELS=('native-harness-api','render-bridge','sub2api')


def git(root,*args,input=None):
    return subprocess.run(['git','-C',str(root),*args],input=input,check=True,
                          stdout=subprocess.PIPE,stderr=subprocess.PIPE).stdout


def revision_files(root, revision):
    if type(revision) is not str or re.fullmatch('[0-9a-f]{40}',revision) is None:
        raise ValueError('Full source commit required')
    if git(root,'cat-file','-t',revision).strip()!=b'commit':raise ValueError('Not a commit')
    listing=git(root,'ls-tree','-rz','--full-tree',revision).split(b'\0')
    entries=[]
    for line in listing:
        if not line:continue
        metadata,name=line.split(b'\t',1);mode,kind,sha=metadata.decode('ascii').split()
        name=name.decode('utf-8');parts=PurePosixPath(name).parts
        if (mode not in ('100644','100755') or kind!='blob' or not parts or name.startswith('/')
                or '\\' in name or any(p in ('.','..','.git') for p in parts) or any(ord(c)<32 for c in name)):
            raise ValueError('Unsafe source tree entry')
        entries.append((name,mode,sha))
    if not entries or len(entries)>20000:raise ValueError('Invalid source tree size')
    requests=('\n'.join(e[2] for e in entries)+'\n').encode()
    checks=git(root,'cat-file','--batch-check',input=requests).decode().splitlines()
    if len(checks)!=len(entries):raise ValueError('Incomplete Git size check')
    sizes=[]
    for entry,line in zip(entries,checks):
        fields=line.split()
        if len(fields)!=3 or fields[:2]!=[entry[2],'blob']:raise ValueError('Unexpected Git object')
        size=int(fields[2])
        if not 0<=size<=64*1024*1024:raise ValueError('Source file budget exceeded')
        sizes.append(size)
    if sum(sizes)>512*1024*1024:raise ValueError('Source tree budget exceeded')
    data=git(root,'cat-file','--batch',input=requests)
    offset=0;files={};total=0
    for name,mode,sha in entries:
        end=data.index(b'\n',offset);header=data[offset:end].decode().split();offset=end+1
        if len(header)!=3 or header[0]!=sha or header[1]!='blob':raise ValueError('Unexpected Git object')
        size=int(header[2]);total+=size
        if size>64*1024*1024 or total>512*1024*1024:raise ValueError('Source budget exceeded')
        content=data[offset:offset+size];offset+=size
        if len(content)!=size or data[offset:offset+1]!=b'\n':raise ValueError('Truncated Git object')
        offset+=1;files[name]=(int(mode,8),content)
    if offset!=len(data):raise ValueError('Unexpected Git trailing output')
    return files


def build_server(repositories, output):
    if set(repositories)!=set(LABELS):raise ValueError('All three fixed repositories required')
    files={};revisions={}
    for label in LABELS:
        root,revision=repositories[label];revisions[label]=revision
        for name,item in revision_files(root,revision).items():files[label+'/'+name]=item
    manifest={'format':1,'kind':'native-harness-three-repository-source','revisions':revisions,
              'windows_client_source_revision':revisions['native-harness-api'],
              'deployment':'NOT_RUN','real_account_model':'NOT_RUN',
              'files':{name:{'sha256':hashlib.sha256(content).hexdigest(),'mode':mode}
                       for name,(mode,content) in sorted(files.items())}}
    readme='''# 配套服务器源码，不是已部署服务

三个文件夹均来自 SERVER-SOURCE-MANIFEST.json 的精确 Git 提交，不包含本机未提交文件、.git、原生 vendor 资产或用户 profile。原仓库许可证/蓝图/源码和测试分别保留。

从 native-harness-api/code/docs/DEPLOYMENT_CHAIN.zh-CN.md 开始。生成器是 native-harness-api/code/tools/prepare_native_deployment.py。Windows客户端 CLIENT-MANIFEST.json 的 source_revision 必须等于本包 windows_client_source_revision；不要混用不同版本。

Render Bridge/Sub2API 使用本包对应源码自行构建，不用 upstream/latest 替代。不要直接覆盖既有 Render Bridge 图像账本或生产 Sub2API 配置/数据库。真实部署、TLS/容器可达性、真实用户认证及模型调用仍须分别验收。此源码包没有安装依赖、运行Docker、执行迁移或部署任何服务。
'''
    files['START-HERE.zh-CN.md']=(0o100644,readme.encode())
    manifest['files']['START-HERE.zh-CN.md']={'sha256':hashlib.sha256(readme.encode()).hexdigest(),'mode':0o100644}
    files['SERVER-SOURCE-MANIFEST.json']=(0o100644,(json.dumps(manifest,sort_keys=True,ensure_ascii=False,indent=2)+'\n').encode())
    with Path(output).open('xb') as stream,zipfile.ZipFile(stream,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name,(mode,content) in sorted(files.items()):
            safe_name(name)
            info=zipfile.ZipInfo(name,date_time=(1980,1,1,0,0,0));info.create_system=3;info.external_attr=mode<<16;info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,content)
    verify_server(output)
    return hashlib.sha256(Path(output).read_bytes()).hexdigest()


def safe_name(name):
    if type(name) is not str or not name or name.startswith('/') or any(c in name for c in ('\\', ':')):
        raise ValueError('Unsafe source archive name')
    if any(ord(c)<32 for c in name):raise ValueError('Unsafe source archive name')
    parts=name.split('/')
    if any(p in ('','.', '..') or p.casefold()=='.git' for p in parts):raise ValueError('Unsafe source path')
    if name not in ('START-HERE.zh-CN.md','SERVER-SOURCE-MANIFEST.json') and (len(parts)<2 or parts[0] not in LABELS):
        raise ValueError('Unexpected archive root')


def verify_server(path):
    with zipfile.ZipFile(path) as archive:
        infos=archive.infolist();names=[i.filename for i in infos]
        if len(names)!=len(set(names)):raise ValueError('Duplicate archive entry')
        for info in infos:
            safe_name(info.filename)
            if (info.is_dir() or info.flag_bits&1 or info.external_attr&0x400
                    or info.external_attr>>16 not in (0o100644,0o100755) or info.file_size>64*1024*1024):
                raise ValueError('Unsafe source archive member')
        if len(infos)>60002 or sum(i.file_size for i in infos)>1536*1024*1024:raise ValueError('Archive budget exceeded')
        if archive.getinfo('SERVER-SOURCE-MANIFEST.json').file_size>8*1024*1024:raise ValueError('Manifest too large')
        def unique(pairs):
            result={}
            for key,value in pairs:
                if key in result:raise ValueError('Duplicate JSON key')
                result[key]=value
            return result
        manifest=json.loads(archive.read('SERVER-SOURCE-MANIFEST.json'),object_pairs_hook=unique)
        if (type(manifest) is not dict or set(manifest)!={'format','kind','revisions','windows_client_source_revision','deployment','real_account_model','files'}
                or type(manifest['format']) is not int or manifest['format']!=1
                or manifest['kind']!='native-harness-three-repository-source'
                or manifest['deployment']!='NOT_RUN' or manifest['real_account_model']!='NOT_RUN'
                or type(manifest['revisions']) is not dict or set(manifest['revisions'])!=set(LABELS)
                or any(type(v) is not str or re.fullmatch('[0-9a-f]{40}',v) is None for v in manifest['revisions'].values())
                or manifest['windows_client_source_revision']!=manifest['revisions']['native-harness-api']
                or type(manifest['files']) is not dict):
            raise ValueError('Invalid source manifest')
        if set(names)!=set(manifest['files'])|{'SERVER-SOURCE-MANIFEST.json'}:
            raise ValueError('Archive inventory mismatch')
        for name,record in manifest['files'].items():
            info=archive.getinfo(name)
            if (type(record) is not dict or set(record)!={'mode','sha256'}
                    or type(record['mode']) is not int or info.external_attr>>16!=record['mode']
                    or type(record['sha256']) is not str or re.fullmatch('[0-9a-f]{64}',record['sha256']) is None
                    or hashlib.sha256(archive.read(name)).hexdigest()!=record['sha256']):
                raise ValueError('Source integrity failure')
        return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for label in LABELS:
        parser.add_argument('--'+label,required=True,type=Path)
        parser.add_argument('--'+label+'-revision',required=True)
    parser.add_argument('--output',required=True,type=Path)
    args=vars(parser.parse_args())
    repos={label:(args[label.replace('-','_')],args[label.replace('-','_')+'_revision']) for label in LABELS}
    print(build_server(repos,args['output']))
