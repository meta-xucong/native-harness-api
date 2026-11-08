"""Legacy accepted tasks survive user-CLI addition; no vendor process or account."""
from pathlib import Path
import tempfile
import unittest

from native_harness_api.harness_registry import binding_for, make_recipe
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.public_sfe_adapter import DurableNativeAdapter, PENDING
from test_multi_harness_runtime import target
from test_multi_harness_slot import task, terminal
from test_node import Client


class ExistingCliRecoveryTests(unittest.TestCase):
    def check_recovery(self, old_adapter, alias, unknown=False):
        old = binding_for(target(old_adapter, alias)['recipe'])
        new = binding_for(make_recipe(adapter_id='codex.user-cli.v1',
            profile_path=r'C:\Native\sidecar', profile_id='sidecar', account_fingerprint='a'*64,
            native_model='native-model', public_alias='codex-existing', cli_path=r'C:\Codex\codex.exe',
            cli_sha256='b'*64, codex_home=r'C:\Users\Fixture\.codex'))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            client = Client([task(old, 'old')])
            calls = []
            def invoke(**kwargs):
                calls.append(old_adapter)
                return 'saved legacy answer'
            expected = dict(PENDING) if unknown else terminal('saved legacy answer', old.native_model)
            class LostReturn(DurableNativeAdapter):
                def start(self, *args, **kwargs):
                    super().start(*args, **kwargs)
                    raise OSError('synthetic lost observation after durable save')
            previous = LostReturn(root, bindings=(old,), resolve=lambda b: (invoke, lambda e, m: expected))
            policy = lambda b: NodePolicy('s', 'l', frozenset({('u', 'k')}), (b,), mode='harness_local_trial')
            runner = Runner(client, Journal(root / 'journal'), previous, policy=policy(old))
            self.assertEqual(runner.run_once()['outcome'], 'pending')
            before = (root / 'sfe-evidence.sqlite').read_bytes()
            def no_current_adapter(binding):
                self.fail('Recovery must not load new CLI/home/account or invoke any adapter')
            current = DurableNativeAdapter(root, bindings=(new,), resolve=no_current_adapter)
            recovered = Runner(client, Journal(root / 'journal'), current, policy=policy(new)).run_once(allow_new=False)
            self.assertEqual(recovered, expected)
            self.assertEqual(calls, [old_adapter])
            self.assertEqual(client.next_calls, 1)
            self.assertEqual(before, (root / 'sfe-evidence.sqlite').read_bytes())

    def test_workbuddy_completed_replay_without_old_target(self):
        self.check_recovery('workbuddy.public-sfe.v1', 'workbuddy-legacy')

    def test_managed_codex_completed_replay_without_old_target(self):
        self.check_recovery('codex.app-server.v1', 'codex-legacy')

    def test_workbuddy_unknown_not_unlocked_by_new_cli(self):
        self.check_recovery('workbuddy.public-sfe.v1', 'workbuddy-legacy', True)

    def test_managed_codex_unknown_not_unlocked_by_new_cli(self):
        self.check_recovery('codex.app-server.v1', 'codex-legacy', True)
