"""Legacy accepted tasks survive user-CLI addition; no vendor process or account."""
from pathlib import Path
import tempfile
import unittest

from native_harness_api.harness_registry import ExecutionTarget, binding_for, make_recipe
from native_harness_api.node import Journal, NodePolicy, Runner
from native_harness_api.public_sfe_adapter import DurableNativeAdapter, PENDING
from test_multi_harness_runtime import target
from test_multi_harness_slot import task, terminal
from test_node import Client


class ExistingCliRecoveryTests(unittest.TestCase):
    def check_recovery(self, old_adapter, alias, unknown=False, *, old=None, new=None):
        if old is None:
            old = binding_for(target(old_adapter, alias)['recipe'])
        if new is None:
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

    def test_node_replays_frozen_sfe_binding_and_leaves_unknown_occupied(self):
        old = ExecutionTarget.from_value({
            'recipe': {
                'recipe_version': 1, 'adapter_id': 'workbuddy.public-sfe.v1',
                'platform': 'windows-x64', 'product_version': '5.9.3',
                'protocol_version': 'public-harness-0.2.0-beta.16',
                'asset_identity': 'ae54a72414a194d979679149b7e218a9f9474fcf9409adb23b5f1c27c8193ec6',
                'policy_id': 'workbuddy-local-trial-v1', 'profile_path': r'c:\native\legacy',
                'profile_id': 'synthetic-retained', 'account_fingerprint': 'a' * 64,
                'native_model': 'native-model', 'public_alias': 'workbuddy-legacy',
            },
            'binding': {'binding_id': 'h2-ee8315cb40871fb43c8d9b4b959e14bebb767717be04c59ecd42ebd195dae3ee',
                        'revision': 1, 'model_alias': 'workbuddy-legacy', 'native_model': 'native-model'},
            'assets_root': r'C:\Native\old-client\assets',
        })
        new = binding_for(make_recipe(adapter_id='workbuddy.public-node.v2',
            profile_path=old.recipe['profile_path'], profile_id=old.recipe['profile_id'],
            account_fingerprint=old.recipe['account_fingerprint'], native_model=old.binding.native_model,
            public_alias=old.binding.model_alias))
        self.assertNotEqual(old.binding, new)
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                self.check_recovery(old.recipe['adapter_id'], old.binding.model_alias, unknown,
                                    old=old.binding, new=new)

    def test_v2_replays_v1_evidence_and_leaves_unknown_occupied(self):
        old = ExecutionTarget.from_value(target('workbuddy.public-node.v1', 'workbuddy-test'))
        new = ExecutionTarget.from_value(target('workbuddy.public-node.v2', 'workbuddy-test'))
        self.assertEqual(old.recipe['asset_identity'],
                         '46c557f9e0d5ec646410edd293fae3a855f12a6aa1a5b0fd7153e6d258d82f87')
        self.assertEqual(old.recipe['product_version'], '5.9.3')
        self.assertEqual(new.recipe['product_version'], '5.11.3')
        self.assertNotEqual(old.binding, new.binding)
        for unknown in (False, True):
            with self.subTest(unknown=unknown):
                self.check_recovery(old.recipe['adapter_id'], old.binding.model_alias, unknown,
                                    old=old.binding, new=new.binding)
