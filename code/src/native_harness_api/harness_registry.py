"""Two pinned native adapters and canonical, local-only execution recipes.

No plugin loading or discovery occurs here. Binding remains the v1 wire type.
"""
from dataclasses import dataclass
import ntpath
import re
from types import MappingProxyType

from .contracts import Binding, digest, identifier
from .native_bindings import MAX_BINDINGS, validate_bindings

# These are the complete identities checked by the concrete asset tools. Paths
# to identical assets may move without changing the chosen execution recipe.
WORKBUDDY_ASSETS = {
    'sfe_tree': {'sha256': 'ba4ea62cc1a2be04098416a91d97951bd6fe4e6a7aee02713d1a5e9912420488', 'files': 65, 'bytes': 193650089},
    'node_tree': {'sha256': '560eaae5d491412a3a2b0b84a39e3d9ded151d7e103ff6b2ff54df53fde4fdb0', 'files': 2447, 'bytes': 98429225},
    'executable_sha256': '5a55b64f73eadc515120212f117cdfe4b25d4633ab4a57717aab2ba90b6e97b2',
    'node_sha256': 'c5ff4c736112dd483c750fd4149d30c8a116db1a49b8b3ec88be4b65e6c86c19',
    'product_sha256': '65e7e270a13413de3fcdb1810cd18e3cfd0037d9e2d4d1213cc26d48c6f054ac',
}
CODEX_ASSETS = {'archive_sha256': '16f99769bd4accc519cf22079645db445907182c8b96bc626f496b7390f5ad46',
                'archive_bytes': 164336310}
ADAPTERS = MappingProxyType({
    'workbuddy.public-sfe.v1': MappingProxyType({
        'platform': 'windows-x64', 'product_version': '5.9.3',
        'protocol_version': 'public-harness-0.2.0-beta.16', 'asset_identity': digest(WORKBUDDY_ASSETS),
        'policy_id': 'workbuddy-local-trial-v1'}),
    'codex.app-server.v1': MappingProxyType({
        'platform': 'windows-x64', 'product_version': 'rust-v0.167.0',
        'protocol_version': 'app-server-v2', 'asset_identity': digest(CODEX_ASSETS),
        'policy_id': 'codex-read-only-on-request-v1'}),
})
RECIPE_FIELDS = frozenset({'recipe_version', 'adapter_id', 'platform', 'product_version',
    'protocol_version', 'asset_identity', 'profile_path', 'profile_id',
    'account_fingerprint', 'policy_id', 'native_model', 'public_alias'})


def profile_path(value):
    """Canonical Windows local path; the profile tool checks the actual disk."""
    if (not isinstance(value, str) or not re.match(r'^[A-Za-z]:[\\/]', value)
            or any(c in value for c in ('%', '$', '~', '\x00'))
            or '..' in value.replace('\\', '/').split('/')):
        raise ValueError('Absolute fixed-drive profile path required')
    return ntpath.normcase(ntpath.normpath(value))


def make_recipe(*, adapter_id, profile_path: str, profile_id, account_fingerprint,
                native_model, public_alias):
    if adapter_id not in ADAPTERS:
        raise ValueError('Unsupported native adapter')
    return normalize_recipe(dict(recipe_version=1, adapter_id=adapter_id,
        **ADAPTERS[adapter_id], profile_path=profile_path, profile_id=profile_id,
        account_fingerprint=account_fingerprint, native_model=native_model, public_alias=public_alias))


def normalize_recipe(value):
    if type(value) is not dict or set(value) != RECIPE_FIELDS:
        raise ValueError('Exact execution recipe required')
    recipe = dict(value)
    adapter = ADAPTERS.get(recipe['adapter_id'])
    if (adapter is None or type(recipe['recipe_version']) is not int or recipe['recipe_version'] != 1
            or any(recipe[k] != v for k, v in adapter.items())):
        raise ValueError('Pinned adapter recipe required')
    recipe['profile_path'] = profile_path(recipe['profile_path'])
    identifier(recipe['profile_id'])
    if not isinstance(recipe['account_fingerprint'], str) or not re.fullmatch('[0-9a-f]{64}', recipe['account_fingerprint']):
        raise ValueError('Confirmed public account fingerprint required')
    binding = binding_for(recipe)
    prefix = 'workbuddy-' if recipe['adapter_id'] == 'workbuddy.public-sfe.v1' else 'codex-'
    if not binding.model_alias.startswith(prefix):
        raise ValueError('Alias must name its harness')
    validate_bindings((binding,), mode='harness_local_trial')
    return recipe


def binding_for(recipe):
    return Binding('h2-' + digest(dict(recipe)), 1, recipe['public_alias'], recipe['native_model'])


@dataclass(frozen=True)
class ExecutionTarget:
    recipe: object
    binding: Binding
    assets_root: str

    @classmethod
    def from_value(cls, value):
        if type(value) is not dict or set(value) != {'recipe', 'binding', 'assets_root'}:
            raise ValueError('Explicit recipe, binding and asset root required')
        recipe = normalize_recipe(value['recipe'])
        binding = Binding(**value['binding'])
        if binding != binding_for(recipe):
            raise ValueError('Binding does not freeze this execution recipe')
        assets = profile_path(value['assets_root'])
        return cls(MappingProxyType(recipe), binding, assets)


def parse_targets(value):
    if type(value) is not list or not 1 <= len(value) <= MAX_BINDINGS:
        raise ValueError('One to 32 explicit targets required')
    targets = tuple(ExecutionTarget.from_value(v) for v in value)
    validate_bindings(tuple(t.binding for t in targets), mode='harness_local_trial')
    return targets
