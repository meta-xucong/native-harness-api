"""User-run native model catalog read: empty session only, never prompt or select.

Real authenticated catalog and model execution are NOT_RUN in development.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys
import time
import uuid
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from native_harness_api.beta15_session import decode_frame
from native_harness_api.public_sfe_owned_session import PublicSfeOwnedSession
from native_harness_api.supervised_acp_transport import SupervisedACPTransport
from native_harness_api.public_sfe_session import keys, meta

RETAINED = []
EXPLAIN = ('Reads the native model choices for an already confirmed retained CN WorkBuddy profile. '
           'Creates and closes ONE empty native session, which can perform native configuration/network work. '
           'No prompt, model selection, tool approval, login, logout or credential import is sent. '
           'Failures quarantine the profile rather than retry. Model IDs are displayed only after verified cleanup; no binding is saved.')


def require(ok):
    if not ok: raise ValueError('CATALOG_REJECTED')


def label(value):
    require(type(value) is str and 0 < len(value) <= 256 and all(ord(c) >= 32 and ord(c) != 127 for c in value))
    return value


def model_id(value):
    require(type(value) is str and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,255}', value) is not None and '://' not in value)
    return value


def project_models(options):
    """Pinned ACP SessionConfigSelect[Group/Option]; retain only IDs/names/current."""
    require(type(options) is list and 0 < len(options) <= 128)
    configs = [o for o in options if type(o) is dict and o.get('configId') == 'model']
    require(len(configs) == 1)
    model = configs[0]
    require(keys(model, ('configId','name','type','currentValue','options'), ('description','category','_meta')) and meta(model))
    require(model['type'] == 'select'); label(model['name'])
    require(model.get('description') is None or type(model['description']) is str)
    require(model.get('category') is None or type(model['category']) is str)
    values = model['options']; require(type(values) is list and 0 < len(values) <= 128)
    grouped = all(type(v) is dict and 'groupId' in v for v in values)
    flat = all(type(v) is dict and 'value' in v and 'groupId' not in v for v in values)
    require(grouped or flat)
    choices = []; groups = set()
    for value in values:
        if grouped:
            require(keys(value, ('groupId','name','options'), ('_meta',)) and meta(value))
            group = label(value['groupId']); label(value['name']); require(group not in groups); groups.add(group)
            require(type(value['options']) is list and len(value['options']) <= 128)
            choices.extend(value['options'])
        else: choices.append(value)
    require(0 < len(choices) <= 256)
    result = []; seen = set()
    for choice in choices:
        require(keys(choice, ('value','name'), ('description','_meta')) and meta(choice))
        require(choice.get('description') is None or type(choice['description']) is str)
        value = model_id(choice['value']); name = label(choice['name'])
        require(value not in seen); seen.add(value); result.append({'id':value,'name':name})
    current = model_id(model['currentValue']); require(current in seen)
    return {'current_model_id': current, 'models': result}


class ModelsOwnedSession(PublicSfeOwnedSession):
    def __init__(self, transport):
        super().__init__(transport); self.catalog = None

    def _stages(self, workspace, _model_id, _prompt):
        return [(self.protocol.start, lambda: self.protocol.ready),
                (lambda: self.protocol.new_session(workspace), lambda: self.protocol.coordinator.phase == 'created')]

    def _dispatch(self, raw):
        candidate = None
        try:
            if raw is not None:
                value = decode_frame(raw)
                entry = self.protocol._pending.get(value.get('id')) if type(value.get('id')) is int else None
                if entry is not None and entry[0] == 'session/new' and 'result' in value:
                    candidate = value['result'].get('configOptions')
                # A changing catalog is not a reusable model-selection result.
                if value.get('method') == 'session/update':
                    self._fail('CATALOG_CHANGED')
            super()._dispatch(raw)
            if candidate is not None and not self.fault and not self.protocol.failure_code:
                require(self.protocol.coordinator.phase == 'created' and self.catalog is None)
                self.catalog = project_models(candidate)
                model = self.protocol.coordinator.model
                require({x['id'] for x in self.catalog['models']} == set(model.admitted) and self.catalog['current_model_id'] == model.current)
        except Exception:
            self.catalog = None; self._fail('CATALOG_REJECTED')

    def discover(self, command, cwd, env, *, workspace, expected_account_digest, timeout=60, cleanup_timeout=10):
        result = super().run(command,cwd,env,workspace=workspace,model_id=None,prompt=None,
                             expected_account_digest=expected_account_digest,timeout=timeout,cleanup_timeout=cleanup_timeout)
        coordinator = self.protocol.coordinator
        ok = (self.catalog is not None and not self.fault and not self.protocol.failure_code and not coordinator.fault
              and not coordinator.prompt_intent_issued and coordinator.close_acknowledged and result['cleanup_confirmed'])
        # Do not expose generic session evidence, server content or account data.
        return {'schema_version':1, 'success':ok, 'catalog':self.catalog if ok else None,
                'cleanup_confirmed':result['cleanup_confirmed'], 'natural_exit_zero':result['natural_exit_zero'],
                'session_closed':coordinator.close_acknowledged, 'prompt_sent':False, 'model_selected':False,
                'real_model_ready':False}


def discover_models(assets, profile, *, transport=None, timeout=60, cleanup_timeout=10):
    """Caller holds the profile lock; returns local-only catalog, never persists model choice."""
    engine = ModelsOwnedSession(transport or SupervisedACPTransport())
    result = {'schema_version':1,'success':False,'catalog':None,'cleanup_confirmed':False,
              'profile_closed':False,'profile_restored':False,'prompt_sent':False,'model_selected':False,'real_model_ready':False}
    began = False
    try:
        require(profile.state == 'authenticated_confirmed' and profile.verify() is True)
        digest = profile.expected_account_digest
        profile.begin_execution(); began = True
        workspace = profile.root / 'workspace' / ('catalog-' + uuid.uuid4().hex)
        workspace.mkdir(); require(profile.verify() is True)
        env = profile.environment(assets['host_node'])
        command = [str(assets['executable']), '--stdio', '--strict-mcp-config', '--product-config', str(assets['product_config'])]
        result.update(engine.discover(command,workspace,env,workspace=str(workspace),expected_account_digest=digest,
                                     timeout=timeout,cleanup_timeout=cleanup_timeout))
        require(result['success'] and result['cleanup_confirmed'] and profile.verify() is True)
        profile.finish_execution(cleanup_confirmed=True)
        require(profile.verify() is True)
        result['profile_restored'] = True
        profile.close(); result['profile_closed'] = True
    except BaseException:
        result['success'] = False; result['catalog'] = None
        try:
            if began: profile.quarantine()
        except BaseException: pass
        # Only close the lock after independently confirmed process and IO cleanup.
        if not began and not engine._attempted:
            try: profile.close(); result['profile_closed'] = True
            except BaseException: pass
        if result['cleanup_confirmed']:
            try:
                require(profile.verify() is True and profile.state in ('unknown_retained','execution_unknown') and profile.expected_account_digest is None)
                profile.close(); result['profile_closed'] = True
            except BaseException: pass
        if not result['profile_closed']: RETAINED.append((engine,profile))
    return result


class SafeParser(argparse.ArgumentParser):
    def error(self, message): raise ValueError('ARGUMENTS_REJECTED')


def main(argv=None):
    parser = SafeParser(description=__doc__)
    parser.add_argument('--assets-root',type=Path)
    parser.add_argument('--profile',type=Path)
    parser.add_argument('--explain',action='store_true')
    try:
        args = parser.parse_args(argv)
        if args.explain: print(EXPLAIN); return 0
        require(os.name == 'nt' and sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty())
        require(args.assets_root is not None and args.profile is not None)
        from public_harness_runtime_assets import verify_runtime_assets
        from public_harness_login_profile import open_profile
        assets = verify_runtime_assets(args.assets_root)
        profile = open_profile(args.profile)
        print(EXPLAIN)
        result = discover_models(assets,profile)
        print('Retained profile: ' + ascii(str(profile.root)))
        if result['success']:
            print('Native catalog, not a model invocation or entitlement guarantee:')
            for model in result['catalog']['models']:
                print(model['id'] + '  ' + ascii(model['name']))
            print('Current model: ' + result['catalog']['current_model_id'])
            print('Choose an exact ID for native_model in your local configuration; no binding was written.')
        else: print('Catalog unavailable. No model binding saved; profile may require reconciliation.')
        while RETAINED:
            try: time.sleep(1)
            except KeyboardInterrupt: continue
        return 0 if result['success'] else 1
    except (Exception,KeyboardInterrupt):
        if RETAINED:
            # Even output/TTY failure must not release uncertain process owners.
            while RETAINED:
                try: time.sleep(1)
                except KeyboardInterrupt: continue
        print('Catalog not started or unconfirmed; no automatic retry or model binding.')
        return 1

if __name__=='__main__':raise SystemExit(main())
