"""Generate matching secret-free deployment configs, never deploy or start work."""
import argparse
import json
import os
from pathlib import Path, PureWindowsPath
import re
import sys
import uuid
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from native_harness_api.contracts import Binding, identifier, digest
from native_harness_api.native_bindings import validate_bindings
from native_harness_api.bridge_client import origin
from native_harness_api.native_facade_runtime import NativeFacadeConfig

SPEC_KEYS = {'bridge_origin', 'user_id', 'api_key_id', 'model_alias', 'native_model',
             'windows_bundle_root', 'windows_profile_root', 'bridge_data_dir'}


def build_plan(spec, identities=None):
    multi_keys = (SPEC_KEYS - {'model_alias', 'native_model'}) | {'models'}
    if type(spec) is not dict or set(spec) not in (SPEC_KEYS, multi_keys):
        raise ValueError('Exact deployment specification required')
    remote = spec['bridge_origin']; origin(remote)
    parsed = urlsplit(remote)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.hostname in ('localhost', '127.0.0.1', '::1'):
        raise ValueError('Windows requires a trusted reachable HTTPS Bridge origin')
    for key in ('user_id', 'api_key_id'):
        if type(spec[key]) is not int or not 0 < spec[key] < 2**63:
            raise ValueError('Actual positive numeric Sub2API IDs required')
    for key in ('windows_bundle_root', 'windows_profile_root'):
        value = spec[key]
        if (type(value) is not str or not re.fullmatch(r'[A-Za-z]:[\\/].+', value)
                or '..' in PureWindowsPath(value).parts or any(ord(c) < 32 for c in value)):
            raise ValueError('Absolute Windows fixed-drive paths required')
    multi = 'models' in spec
    models = spec['models'] if multi else [{k:spec[k] for k in ('model_alias','native_model')}]
    if type(models) is not list or not 1 <= len(models) <= 32:
        raise ValueError('One to 32 model mappings required')
    for model in models:
        if type(model) is not dict or set(model) != {'model_alias','native_model'}:
            raise ValueError('Exact model mappings required')
        for value in model.values():
            if type(value) is not str or any(c in value for c in '<>'):
                raise ValueError('Choose real model IDs, not placeholders')
    state = spec['bridge_data_dir']
    if type(state) is not str or not state.startswith('/') or '..' in Path(state).parts or any(ord(c) < 32 for c in state):
        raise ValueError('Explicit absolute Linux Bridge state path required')
    identities = identities or {name: name+'-'+uuid.uuid4().hex for name in ('source','ledger','workspace','node','executor','binding')}
    if set(identities) != {'source','ledger','workspace','node','executor','binding'}:
        raise ValueError('Invalid deployment identities')
    for value in identities.values(): identifier(value)
    bindings = validate_bindings(tuple(Binding(
        ('binding-'+digest({'seed':identities['binding'],'alias':m['model_alias']})[:32]) if multi else identities['binding'],
        1, m['model_alias'], m['native_model']) for m in models))
    binding_wires = [b.wire() for b in bindings]
    owner = ['user-'+str(spec['user_id']), 'key-'+str(spec['api_key_id'])]
    common = {'mode':'workbuddy_local_trial', 'source':identities['source'], 'ledger_instance':identities['ledger'],
              'bridge_url':remote, 'owner':owner, 'accepting_new':False}
    common.update({'bindings':binding_wires} if multi else {'binding':binding_wires[0]})
    facade = dict(common, adapter='facade_only', port=8790)
    NativeFacadeConfig.from_value(facade)
    node = dict(common, adapter='public_sfe', assets_root=str(PureWindowsPath(spec['windows_bundle_root'])/'assets'),
                profile_root=spec['windows_profile_root'], execution_timeout=120, cleanup_timeout=10)
    bridge = {'data_dir':state, 'source_id':identities['source'], 'workspace_id':identities['workspace'],
              'node_id':identities['node'], 'executor_instance':identities['executor'],
              'host':'127.0.0.1', 'port':8787, 'dispatch_mode':'node_pull', 'enabled_profiles':['fake-image'],
              'accepting_new_tasks':False, 'harness':{'ledger_instance':identities['ledger'], 'allowed_owners':[owner],
              'bindings':binding_wires, 'accepting_new':False, 'execution_mode':'workbuddy_local_trial'}}
    routes = {}
    for binding in binding_wires:
        routes[binding['model_alias']] = {'execution_mode':'workbuddy_local_trial','origin':'http://127.0.0.1:8790',
             'credential_env':'NATIVE_FACADE_SERVICE_TOKEN','source':identities['source'],
             'ledger_instance':identities['ledger'], **binding}
    sub2api = {'gateway':{'native_harness':{'enabled':True,'accepting_new':False,'allowed_user_id':spec['user_id'],
               'allowed_api_key_id':spec['api_key_id'],'routes':routes}}}
    return {'bridge.json':bridge, 'facade.json':facade, 'windows-node.json':node,
            'sub2api.native-harness.yaml':sub2api,
            'deployment-identities.json':{'schema_version':1, 'identities':identities, 'specification':spec,
             'warning':'Preserve this identity set and the existing Bridge/Sub2API/Node ledgers. Never regenerate to retry an uncertain operation.'}}


def write_plan(output, plan):
    output = Path(output)
    # No account data/secrets are written; nevertheless never overwrite prior identities.
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    for name, value in plan.items():
        with (output/name).open('x', encoding='utf-8', newline='\n') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2); handle.write('\n')
        if os.name != 'nt': os.chmod(output/name, 0o600)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.spec.stat().st_size > 16384: raise ValueError('Specification too large')
        def unique(pairs):
            data={}
            for key,value in pairs:
                if key in data:raise ValueError('Duplicate specification key')
                data[key]=value
            return data
        value=json.loads(args.spec.read_text(encoding='utf-8'),object_pairs_hook=unique)
        write_plan(args.output,build_plan(value))
        print('Matching configs written. Admission remains closed. No credentials, deployment, account access or inference occurred.')
        return 0
    except Exception:
        print('Deployment preparation failed. Existing identities were not overwritten; inspect any new output directory.',file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
