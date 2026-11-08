"""Server-only local-trial Facade config: no native assets, profile or execution."""
from dataclasses import dataclass
from .contracts import Binding, identifier
from .native_bindings import parse_bindings, validate_bindings
from .bridge_client import BridgeClient, origin
from .facade import Facade, Service
from .runtime import RuntimeConfig


@dataclass(frozen=True)
class NativeFacadeConfig:
    source: str
    ledger_instance: str
    bridge_url: str
    binding: Binding
    owner: tuple[str, str]
    bindings: tuple[Binding, ...] = ()
    source_token_env: str = 'NATIVE_BRIDGE_SOURCE_TOKEN'
    facade_token_env: str = 'NATIVE_FACADE_TOKEN'
    accepting_new: bool = False
    port: int = 8790
    wait_seconds: float = 10

    @classmethod
    def from_value(cls, value):
        data = dict(value)
        if data.pop('mode', None) != 'workbuddy_local_trial' or data.pop('adapter', None) != 'facade_only':
            raise ValueError('Explicit server-only Facade config required')
        parse_bindings(data)
        owner = data.pop('owner')
        if type(owner) is not list or len(owner) != 2:
            raise ValueError('One trusted owner required')
        data['owner'] = tuple(owner)
        obj = cls(**data)
        obj.validate()
        return obj

    def validate(self):
        import re
        identifier(self.source); identifier(self.ledger_instance); origin(self.bridge_url)
        if type(self.binding) is not Binding or type(self.owner) is not tuple or len(self.owner) != 2:
            raise ValueError('Invalid trusted owner or binding')
        for part in self.owner: identifier(part)
        validate_bindings(self.configured_bindings)
        if self.bindings and self.binding != self.bindings[0]:
            raise ValueError('Inconsistent compatibility binding')
        if type(self.accepting_new) is not bool or type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('Invalid admission or loopback port')
        if type(self.wait_seconds) not in (int, float) or not 0 <= self.wait_seconds <= 30:
            raise ValueError('Invalid bounded wait')
        names = (self.source_token_env, self.facade_token_env)
        if names[0] == names[1] or any(type(n) is not str or not re.fullmatch('[A-Z_][A-Z0-9_]*', n) for n in names):
            raise ValueError('Distinct credential environment names required')

    @property
    def configured_bindings(self):
        return self.bindings or (self.binding,)

    credential = RuntimeConfig.credential

    def facade(self, environ=None):
        self.validate()
        source = self.credential(self.source_token_env, environ)
        facade = self.credential(self.facade_token_env, environ)
        if source == facade: raise ValueError('Distinct service credentials required')
        bridge = BridgeClient(self.bridge_url, source, self.ledger_instance)
        service = Service(self.source, bridge, {b.model_alias:b for b in self.configured_bindings}, self.accepting_new, self.owner)
        return Facade({facade: service}, wait_seconds=self.wait_seconds, mode='workbuddy_local_trial')

    def runner(self, environ=None):
        raise ValueError('A server-only Facade cannot start a native node')
