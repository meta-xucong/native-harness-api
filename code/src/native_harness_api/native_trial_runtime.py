"""Explicit single-owner native trial wiring. No real login or automatic retry.

Tools live alongside src in the distribution. Loading configuration does not
import a vendor, verify/login an account, or start native work.
"""
from dataclasses import dataclass
import logging
from pathlib import Path
import sys
import uuid
import time

from .runtime import RuntimeConfig
from .contracts import Binding
from .native_bindings import parse_bindings, validate_bindings
from .bridge_client import BridgeClient, NodeClient
from .facade import Facade, Service
from .node import Journal, NodePolicy, Runner
from .public_sfe_adapter import PublicSfeAdapter

_QUARANTINED_PROFILES = []
_QUARANTINED_ENGINES = []


def hold_retained():
    """Keep unresolved process owners and locks, including after output failure."""
    while _QUARANTINED_PROFILES or _QUARANTINED_ENGINES:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            continue


def _tools():
    path = Path(__file__).resolve().parents[2] / 'tools'
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
    from public_harness_runtime_assets import verify_runtime_assets, checked_local_path
    from public_harness_login_profile import open_profile
    return verify_runtime_assets, checked_local_path, open_profile


@dataclass(frozen=True)
class NativeTrialConfig(RuntimeConfig):
    assets_root: Path = Path('.')
    profile_root: Path = Path('.')
    bindings: tuple[Binding, ...] = ()
    expected_profile_id: str | None = None
    expected_account_fingerprint: str | None = None
    workbuddy_node_adapter: str | None = None
    execution_timeout: float = 120
    cleanup_timeout: float = 10

    @classmethod
    def from_value(cls, value):
        data = dict(value)
        if 'workbuddy_node' in data or 'workbuddy_node_adapter' in data:
            raise ValueError('WorkBuddy Node requires a version 2 target')
        if data.pop('mode', None) != 'workbuddy_local_trial' or data.pop('adapter', None) != 'public_sfe':
            raise ValueError('Explicit local trial and public_sfe required')
        parse_bindings(data)
        owner = data.pop('owner')
        if type(owner) is not list or len(owner) != 2: raise ValueError('One trusted owner required')
        data['owner'] = tuple(owner)
        for key in ('assets_root', 'profile_root'):
            data[key] = Path(data[key])
        expected = data['profile_root'] / 'control'
        if 'data_dir' in data and Path(data['data_dir']) != expected:
            raise ValueError('Control evidence must stay in its profile')
        data['data_dir'] = expected
        obj = cls(**data); obj.validate(); return obj

    @property
    def configured_bindings(self):
        return self.bindings or (self.binding,)

    def _validate_binding(self):
        validate_bindings(self.configured_bindings)
        if self.bindings and self.binding != self.bindings[0]:
            raise ValueError('Inconsistent compatibility binding')

    def validate(self):
        super().validate()
        if self.fake_mode != 'success': raise ValueError('Synthetic behavior is not a native option')
        for path in (self.profile_root, self.assets_root):
            if not path.is_absolute() or '..' in path.parts:
                raise ValueError('Absolute dedicated paths required')
        if self.data_dir != self.profile_root / 'control': raise ValueError('Invalid control directory')
        for value, maximum in ((self.execution_timeout, 3600), (self.cleanup_timeout, 300)):
            if type(value) not in (int, float) or not 0 < value <= maximum:
                raise ValueError('Invalid bounded native timeout')

    def facade(self, environ=None):
        self.validate()
        source = self.credential(self.source_token_env, environ)
        facade = self.credential(self.facade_token_env, environ)
        if source == facade: raise ValueError('Distinct credentials required')
        bridge = BridgeClient(self.bridge_url, source, self.ledger_instance)
        service = Service(self.source, bridge, {b.model_alias:b for b in self.configured_bindings}, self.accepting_new, self.owner)
        return Facade({facade:service}, wait_seconds=self.wait_seconds, mode='workbuddy_local_trial')

    def runner(self, environ=None):
        self.validate()
        _, checked_path, open_profile = _tools()
        # Setup checks metadata/ACL only. Account readiness is not required for
        # replay of already persisted terminal evidence.
        with open_profile(self.profile_root) as profile:
            profile.verify(); checked_path(self.data_dir)
            journal = Journal(self.data_dir / 'journal')
            evidence_root = self.data_dir / 'native-evidence'
            evidence_root.mkdir(exist_ok=True)
            profile.verify()
        client = NodeClient(self.bridge_url, self.credential(self.node_token_env, environ))
        policy = NodePolicy(self.source, self.ledger_instance, frozenset({self.owner}), self.configured_bindings, mode='workbuddy_local_trial')
        adapter = PublicSfeAdapter(evidence_root, bindings=self.configured_bindings, invoke=self.invoke, event_horizon=self.execution_timeout)
        return Runner(client, journal, adapter, policy=policy)

    def invoke(self, *, model_id, messages, on_event=None, cancellation=None):
        if model_id not in {b.native_model for b in self.configured_bindings} or len(messages) != 1 or messages[0]['role'] != 'user':
            raise ValueError('One bound user prompt required')
        verify_assets, _, open_profile = _tools()
        from .supervised_acp_transport import SupervisedACPTransport
        from .public_sfe_owned_session import PublicSfeOwnedSession
        from public_harness_runtime_assets import verify_node_runtime_assets, workbuddy_command
        from .workbuddy_node_session import WorkBuddyNodeOwnedSession
        profile = open_profile(self.profile_root)
        begun = False
        clean = False
        reusable = False
        engine = None
        try:
            if profile.state != 'authenticated_confirmed': raise ValueError('Profile not ready')
            if self.expected_profile_id is not None and profile.root.name != self.expected_profile_id:
                raise ValueError('Frozen profile mismatch')
            if self.expected_account_fingerprint is not None and profile.expected_account_digest != self.expected_account_fingerprint:
                raise ValueError('Frozen account confirmation mismatch')
            assets = (verify_node_runtime_assets(self.assets_root, adapter_id=self.workbuddy_node_adapter)
                      if self.workbuddy_node_adapter else verify_assets(self.assets_root))
            expected_account = profile.expected_account_digest
            workspace = profile.root / 'workspace' / ('request-' + uuid.uuid4().hex)
            workspace.mkdir()
            profile.verify()
            begun = True  # Ambiguous durable state writes must also retain ownership.
            try:
                profile.begin_execution()
                if self.workbuddy_node_adapter:
                    env = profile.environment(assets['host_node'], node_runtime=True)
                    engine = WorkBuddyNodeOwnedSession(SupervisedACPTransport(),
                                                       product_version=assets['product_version'])
                else:
                    env = profile.environment(assets['host_node'])
                    engine = PublicSfeOwnedSession(SupervisedACPTransport())
                result = engine.run(
                    workbuddy_command(assets),
                    workspace, env, workspace=str(workspace), model_id=model_id,
                    prompt=messages[0]['content'], expected_account_digest=expected_account,
                    timeout=self.execution_timeout, cleanup_timeout=self.cleanup_timeout,
                    **({} if on_event is None else {'on_event': on_event}),
                    **({} if cancellation is None else {'cancellation': cancellation}))
                clean = result.get('cleanup_confirmed') is True
                if self.workbuddy_node_adapter:
                    session = result['session']
                    if result['fault'] and not session['prompt_intent_issued']:
                        logging.getLogger(__name__).warning(
                            'WorkBuddy stopped before prompt: last_setup_step=%s fault=%s protocol_failure=%s cleanup_confirmed=%s',
                            session['last_setup_step'], result['fault'], session['failure_code'], clean)
                return result
            finally:
                # Durable unknown state blocks reuse even after OS releases lock.
                if clean:
                    profile.finish_execution(cleanup_confirmed=True)
                    reusable = True
                else:
                    profile.quarantine()
        finally:
            if begun and not reusable:
                _QUARANTINED_PROFILES.append(profile)
                if engine is not None:
                    _QUARANTINED_ENGINES.append(engine)
            else:
                profile.close()
