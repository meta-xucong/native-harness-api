"""Bounded model registry. Multiple aliases share one node/profile/slot."""
from .contracts import Binding

MAX_BINDINGS = 32


def validate_bindings(bindings):
    if type(bindings) is not tuple or not 1 <= len(bindings) <= MAX_BINDINGS:
        raise ValueError('One to 32 explicit bindings required')
    for binding in bindings:
        if type(binding) is not Binding:
            raise ValueError('Invalid binding')
        Binding(**binding.wire())
        if (not binding.model_alias.startswith('workbuddy-') or binding.native_model.startswith('fake-')
                or binding.native_model.casefold() in {'auto', 'automatic', 'default'}):
            raise ValueError('Explicit WorkBuddy model required')
    if (len({b.model_alias for b in bindings}) != len(bindings)
            or len({b.binding_id for b in bindings}) != len(bindings)):
        raise ValueError('Distinct aliases and binding identities required')
    return bindings


def parse_bindings(data):
    """Mutate a private config copy; legacy singleton remains compatible."""
    if ('binding' in data) == ('bindings' in data):
        raise ValueError('Specify binding or bindings, never both')
    if 'bindings' not in data:
        data['binding'] = Binding(**data['binding'])
        return
    raw = data['bindings']
    if type(raw) is not list:
        raise ValueError('bindings must be a list')
    bindings = validate_bindings(tuple(Binding(**b) for b in raw))
    data['bindings'] = bindings
    data['binding'] = bindings[0]
