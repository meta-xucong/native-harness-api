"""Explicit reachability/configuration checks. Never claim, start or prompt a task."""
import argparse
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from native_harness_api.runtime import RuntimeConfig
from native_harness_api.native_facade_runtime import NativeFacadeConfig
from native_harness_api.native_trial_runtime import NativeTrialConfig
from native_harness_api.bridge_client import BridgeClient, NodeClient, HTTPTransport


def check(config, environ=None):
    config.validate()
    if isinstance(config,NativeFacadeConfig):
        bridge=BridgeClient(config.bridge_url,config.credential(config.source_token_env,environ),config.ledger_instance)
        capability=bridge.capabilities()
        # GET only. Models may be empty while admission is intentionally closed.
        models=HTTPTransport('http://127.0.0.1:'+str(config.port),config.credential(config.facade_token_env,environ)).request('GET','/v1/models')
        if type(models) is not dict or models.get('object')!='list' or type(models.get('data')) is not list:
            raise ValueError('Invalid Facade response')
        return {'checks_passed':True,'role':'facade','bridge_contract':True,'facade_auth':True,'inference_started':False,
                'native_account_verified':False,'sub2api_auth_verified':False}
    if not isinstance(config,NativeTrialConfig):
        raise ValueError('Explicit native configuration required')
    from public_harness_runtime_assets import verify_runtime_assets
    from public_harness_login_profile import open_profile
    verify_runtime_assets(config.assets_root)
    with open_profile(config.profile_root) as profile:
        profile.verify()
        ready=profile.state=='authenticated_confirmed'
    # This obtains at most an existing queued task; it never claims or starts it.
    # Do not log the returned task/body or treat it as account readiness evidence.
    NodeClient(config.bridge_url,config.credential(config.node_token_env,environ)).next_task()
    return {'checks_passed':ready,'role':'node','assets_verified':True,'profile_metadata_confirmed':ready,'bridge_node_auth':True,
            'inference_started':False,'native_account_verified':False,'sub2api_auth_verified':False}


def check_sub2api(config, base_url, environ=None):
    key=config.credential('NATIVE_SUB2API_TEST_KEY',environ)
    value=HTTPTransport(base_url,key).request('GET','/v1/native-harness/models')
    if (type(value) is not dict or value.get('object')!='list' or type(value.get('data')) is not list
            or type(value.get('native_harness')) is not dict):
        raise ValueError('Native preflight endpoint unavailable')
    status=value['native_harness']
    if type(status.get('accepting_new')) is not bool or status.get('live_readiness_checked') is not False:
        raise ValueError('Unexpected native preflight contract')
    for model in value['data']:
        if type(model) is not dict or model.get('object')!='model' or type(model.get('id')) is not str:
            raise ValueError('Unexpected model list')
    return {'sub2api_auth_verified':True,'sub2api_accepting_new':status['accepting_new'],
            'sub2api_model_listed':any(m['id']==config.binding.model_alias for m in value['data']),
            'sub2api_live_readiness_checked':False}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',type=Path,required=True)
    parser.add_argument('--sub2api-origin',help='Optional explicit origin; reads NATIVE_SUB2API_TEST_KEY only for this GET')
    args=parser.parse_args(argv)
    try:
        import json
        config=RuntimeConfig.load(args.config)
        result=check(config)
        if args.sub2api_origin is not None:
            result.update(check_sub2api(config,args.sub2api_origin))
        print(json.dumps(result,sort_keys=True))
        return 0 if result['checks_passed'] else 1
    except Exception:
        print('Preflight failed. Check local configuration, service credentials and reachable trusted HTTPS. No task was claimed or started.',file=sys.stderr)
        return 1


if __name__=='__main__':raise SystemExit(main())
