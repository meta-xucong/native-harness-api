"""Explicit v2 local-trial configuration sharing one journal and evidence store."""
from dataclasses import dataclass
from functools import partial
from pathlib import Path

from .runtime import RuntimeConfig
from .harness_registry import ExecutionTarget, parse_targets
from .bridge_client import BridgeClient, NodeClient
from .facade import Facade, Service
from .node import Journal, NodePolicy, Runner
from .public_sfe_adapter import DurableNativeAdapter


@dataclass(frozen=True)
class MultiHarnessConfig(RuntimeConfig):
    targets: tuple[ExecutionTarget, ...] = ()
    execution_timeout: float = 120
    cleanup_timeout: float = 10

    @classmethod
    def from_value(cls, value):
        data = dict(value)
        if (data.pop('mode', None) != 'harness_local_trial'
                or type(data.get('schema_version')) is not int or data.pop('schema_version') != 2):
            raise ValueError('Explicit harness_local_trial schema version 2 required')
        if any(k in data for k in ('binding', 'bindings', 'fake_mode', 'adapter')):
            raise ValueError('Native v2 uses targets, not legacy adapter settings')
        targets = parse_targets(data.pop('targets'))
        owner = data.pop('owner')
        if type(owner) is not list or len(owner) != 2:
            raise ValueError('One trusted owner required')
        data.update(targets=targets, binding=targets[0].binding, owner=tuple(owner),
                    data_dir=Path(data['data_dir']))
        obj = cls(**data)
        obj.validate()
        return obj

    @property
    def configured_bindings(self):
        return tuple(t.binding for t in self.targets)

    def _validate_binding(self):
        # Targets and their complete recipe/Binding correspondence were checked
        # once at the config boundary. Internal routing uses those frozen values.
        if not self.targets or self.binding != self.targets[0].binding:
            raise ValueError('Explicit targets required')

    def validate(self):
        super().validate()
        for value, maximum in ((self.execution_timeout, 3600), (self.cleanup_timeout, 300)):
            if type(value) not in (int, float) or not 0 < value <= maximum:
                raise ValueError('Invalid bounded native timeout')

    def resolve(self, binding):
        target = next((t for t in self.targets if t.binding == binding), None)
        if target is None:
            raise ValueError('Frozen execution target is not configured')
        recipe = target.recipe
        if recipe['adapter_id'] == 'workbuddy.public-sfe.v1':
            from .native_trial_runtime import NativeTrialConfig
            from .public_sfe_adapter import project_result
            profile = Path(recipe['profile_path'])
            native = NativeTrialConfig(
                source=self.source, ledger_instance=self.ledger_instance,
                bridge_url=self.bridge_url, binding=binding, owner=self.owner,
                data_dir=profile / 'control', assets_root=Path(target.assets_root),
                profile_root=profile, execution_timeout=self.execution_timeout,
                cleanup_timeout=self.cleanup_timeout,
                expected_profile_id=recipe['profile_id'],
                expected_account_fingerprint=recipe['account_fingerprint'])
            return native.invoke, project_result
        if recipe['adapter_id'] == 'codex.app-server.v1':
            from .codex_trial_runtime import invoke
            from .codex_session import project_result
            return partial(invoke, target, execution_timeout=self.execution_timeout,
                           cleanup_timeout=self.cleanup_timeout), project_result
        raise ValueError('Unsupported native adapter')

    def facade(self, environ=None):
        source = self.credential(self.source_token_env, environ)
        facade = self.credential(self.facade_token_env, environ)
        if source == facade:
            raise ValueError('Distinct credentials required')
        client = BridgeClient(self.bridge_url, source, self.ledger_instance)
        service = Service(self.source, client, {b.model_alias: b for b in self.configured_bindings},
                          self.accepting_new, self.owner)
        return Facade({facade: service}, wait_seconds=self.wait_seconds, mode='harness_local_trial')

    def runner(self, environ=None):
        # No current target/profile/account is opened for recovery. The existing
        # data_dir and journal lock are the resource identity, not the targets.
        from .native_trial_runtime import _tools
        _, checked_path, _ = _tools()
        checked_path(self.data_dir)
        client = NodeClient(self.bridge_url, self.credential(self.node_token_env, environ))
        policy = NodePolicy(self.source, self.ledger_instance, frozenset({self.owner}),
                            self.configured_bindings, mode='harness_local_trial')
        evidence_root = self.data_dir / 'native-evidence'
        evidence_root.mkdir(exist_ok=True)
        adapter = DurableNativeAdapter(evidence_root,
            bindings=self.configured_bindings, resolve=self.resolve, event_horizon=self.execution_timeout)
        return Runner(client, Journal(self.data_dir / 'journal'), adapter, policy=policy)
