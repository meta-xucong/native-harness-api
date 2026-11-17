"""Node startup must prove the account-bound Product commit before any new session."""
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from native_harness_api.public_sfe_session import account_identity_digest
from native_harness_api.workbuddy_node_assets import ADAPTER_ID, HOST_ARGS, LEGACY_ADAPTER_ID, NODE_RELEASES, PIPE_ADAPTER_ID
from native_harness_api.workbuddy_node_session import WorkBuddyNodeSession
import public_harness_recover as recovery
import public_harness_runtime_assets as runtime_assets
from public_harness_models import ModelsOwnedSession
from test_public_sfe_session import wire, unpack, response, initialize, product, identity


class WorkBuddyNodeSessionTests(unittest.TestCase):
    def start(self, **kwargs):
        peer = WorkBuddyNodeSession(deadline=100, clock=lambda: 0, **kwargs)
        messages = unpack(response(peer, unpack(peer.start())[0], initialize()))
        self.assertEqual([m['method'] for m in messages], ['initialized'])
        self.assertFalse(peer.ready)
        return peer

    def notify(self, peer, method, params):
        return unpack(peer.receive(wire({'jsonrpc': '2.0', 'method': method, 'params': params})))

    def test_ready_after_committed_product_and_matching_readback(self):
        peer = self.start(expected_account_digest=None, capture_account=True)
        self.assertEqual(self.notify(peer, 'account/updated', identity()), [])
        self.assertFalse(peer.ready)
        committed = dict(product(), generation=2, revision='synthetic-authenticated')
        read_product, = self.notify(peer, 'product/updated', committed)
        read_account, = unpack(response(peer, read_product, committed))
        status, = unpack(response(peer, read_account, identity()))
        response(peer, status, {'authenticated': True})
        self.assertTrue(peer.ready)
        self.assertEqual(peer.restored_digest, account_identity_digest(identity()['account']))

    def test_anonymous_product_and_changed_account_cannot_admit_session(self):
        for invalid in ('anonymous', 'different_account'):
            with self.subTest(invalid=invalid):
                expected = '0' * 64 if invalid == 'different_account' else account_identity_digest(identity()['account'])
                peer = self.start(expected_account_digest=expected)
                self.notify(peer, 'account/updated', identity())
                self.notify(peer, 'product/updated', product())
                self.assertFalse(peer.ready)
                self.assertTrue(peer.failure_code)
                self.assertEqual(peer.new_session('/synthetic'), ())

    def test_signed_release_version_checked_in_notification_and_readback(self):
        for published, returned in (('5.11.3', '5.11.3'), ('5.9.3', None), ('5.11.3', '5.9.3')):
            with self.subTest(published=published, returned=returned):
                peer = self.start(capture_account=True, expected_account_digest=None, product_version='5.11.3')
                self.notify(peer, 'account/updated', identity())
                ready = dict(product(), generation=2, productVersion=published)
                options = self.notify(peer, 'product/updated', ready)
                if returned is not None:
                    read_product, = options
                    replies = unpack(response(peer, read_product, dict(ready, productVersion=returned)))
                    if returned == '5.11.3':
                        read_account, = replies
                        auth, = unpack(response(peer, read_account, identity()))
                        response(peer, auth, {'authenticated': True})
                self.assertEqual(peer.ready, returned == '5.11.3')
                self.assertEqual(peer.failure_code, None if peer.ready else 'protocol')

    def test_failed_or_interrupted_process_with_verified_cleanup_releases_profile(self):
        for fault, expected in (('EXECUTION_UNCONFIRMED', 'ACCOUNT_OR_PRODUCT_NOT_READY'),
                                ('CANCEL_REQUESTED', 'RECOVERY_CANCELLED')):
            with self.subTest(fault=fault):
                profile = Mock(state='unknown_retained', root=Path('synthetic'))
                engine = Mock(_attempted=True)
                engine.run.return_value = {'fault': fault, 'cleanup_confirmed': False,
                    'cleanup': dict.fromkeys(('host_exited', 'owned_job_empty', 'handles_closed',
                                             'cleanup_complete', 'io_threads_stopped'), True)}
                assets = {'adapter_id': 'workbuddy.public-node.v1', 'host_node': 'node.exe',
                          'launcher': 'launcher.mjs', 'product_config': 'product.json',
                          'product_version': '5.9.3'}
                with patch.object(recovery, 'WorkBuddyRecoverySession', return_value=engine):
                    confirm = Mock()
                    self.assertEqual(recovery.recover(assets, profile, confirm), expected)
                profile.finish_recovery.assert_called_once_with(None)
                profile.close.assert_called_once()
                confirm.assert_not_called()

    def test_interrupted_owned_run_returns_cleanup_facts(self):
        from native_harness_api.workbuddy_node_session import WorkBuddyRecoverySession
        transport = Mock()
        transport.start.side_effect = KeyboardInterrupt
        transport.observe.return_value = {'host_exited': True, 'owned_job_empty': True, 'host_exit_code': 1}
        transport.close.return_value = dict.fromkeys(
            ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete'), True)
        session = WorkBuddyRecoverySession(transport, capture_account=True)
        result = session.run(['synthetic.exe'], Path('.'), {}, workspace='synthetic', model_id=None,
                             prompt=None, expected_account_digest=None, timeout=1, cleanup_timeout=.1)
        self.assertEqual(result['fault'], 'CANCEL_REQUESTED')
        self.assertTrue(result['cleanup']['cleanup_complete'])
        self.assertFalse(result['cleanup_confirmed'])

    def test_node_catalog_accepts_command_inventory_but_refuses_other_session_updates(self):
        engine = ModelsOwnedSession(Mock(), node_runtime=True)
        engine.protocol = Mock()
        engine.protocol.receive.return_value = ()
        engine._dispatch(wire({'jsonrpc': '2.0', 'method': 'session/update', 'params': {
            'update': {'sessionUpdate': 'available_commands_update', 'availableCommands': []}}}))
        self.assertIsNone(engine.fault)
        engine.protocol.receive.assert_called_once()
        engine._dispatch(wire({'jsonrpc': '2.0', 'method': 'session/update', 'params': {
            'update': {'sessionUpdate': 'config_option_update', 'configOptions': []}}}))
        self.assertEqual(engine.fault, 'CATALOG_CHANGED')

    def test_default_package_rejects_legacy_product_but_frozen_v1_config_accepts_it(self):
        pins = NODE_RELEASES[LEGACY_ADAPTER_ID][1]
        with patch.object(runtime_assets, 'checked_local_path', return_value=Path('assets')), \
                patch.object(runtime_assets, 'tree_digest', side_effect=lambda path: pins[path.name]), \
                patch.object(runtime_assets, 'regular'), \
                patch.object(runtime_assets, 'sha', side_effect=[pins['node_exe_sha256'], pins['product_sha256']] * 2):
            with self.assertRaises(runtime_assets.AssetError):
                runtime_assets.verify_node_runtime_assets(Path('assets'))
            verified = runtime_assets.verify_node_runtime_assets(Path('assets'), adapter_id=LEGACY_ADAPTER_ID)
        self.assertEqual(verified['product_version'], '5.9.3')
        legacy = runtime_assets.workbuddy_command(verified)
        self.assertNotIn('--ide-name', legacy)
        self.assertEqual(runtime_assets.workbuddy_command(dict(verified, adapter_id=ADAPTER_ID)),
                         legacy + list(HOST_ARGS))
        command = runtime_assets.workbuddy_command(dict(verified, adapter_id=PIPE_ADAPTER_ID))
        self.assertEqual(Path(command[1]).name, 'workbuddy_named_pipe.mjs')
        self.assertEqual(command[2], legacy[1])
        self.assertNotIn('--stdio', command)
        self.assertEqual(command[-len(HOST_ARGS):], list(HOST_ARGS))
