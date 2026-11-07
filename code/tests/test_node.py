from __future__ import annotations

import copy
import multiprocessing
from pathlib import Path
import tempfile
import unittest

from native_harness_api.adapters import FakeHarness
from native_harness_api.node import Journal, JournalError, NodeBusy, NodePolicy, Runner
from native_harness_api.contracts import Binding, Identity, NodeOffer, digest, envelope, normalize_request


def policy():
    return NodePolicy('test-source', 'test-ledger', frozenset({('test-tenant', 'test-principal')}),
                      (Binding('fake-binding', 1, 'synthetic-model', 'fake-v1'),))


def task(name='task-1'):
    request = normalize_request({'model': 'synthetic-model',
                                 'messages': [{'role': 'user', 'content': 'synthetic test'}]})
    identity = Identity('test-source', 'test-tenant', 'test-principal', 'op-' + name)
    value = envelope(identity, request, policy().bindings[0], 'test-ledger')
    value.update(remote_task_id=name, attempt_id='attempt-' + name, created=1,
                 outcome='pending', liveness='not_started', result=None, claim_id=None,
                 epoch=0, start_grant_id=None, result_expired=False)
    return value


class Client:
    def __init__(self, tasks=None):
        self.tasks = tasks if tasks is not None else [task()]
        self.starts = 0
        self.claim_calls = 0
        self.next_calls = 0
        self.results = []
        self.result_calls = 0
        self.observations = []
        self.lose_grant = False
        self.lose_upload = False
        self.grant_allowed = True

    def next_task(self):
        self.next_calls += 1
        if not self.tasks:
            return None
        value = copy.deepcopy(self.tasks.pop(0))
        return value if isinstance(value, NodeOffer) else NodeOffer(value['remote_task_id'], value)

    def claim(self, task_id):
        self.claim_calls += 1
        return {'attempt_id': 'attempt-' + task_id, 'claim_id': 'claim-' + task_id, 'epoch': 1}

    def start(self, task_id, claim_id):
        self.starts += 1
        if self.lose_grant:
            raise OSError('response was lost')
        return {'start_grant_id': 'grant-' + task_id, 'attempt_id': 'attempt-' + task_id,
                'may_execute': self.grant_allowed}

    def observation(self, *args):
        self.observations.append(args)

    def result(self, task_id, claim_id, grant_id, result):
        self.result_calls += 1
        if self.results and self.results[-1][0] == task_id:
            if self.results[-1][1] != result:
                raise ValueError('contradictory terminal result')
        else:
            self.results.append((task_id, copy.deepcopy(result)))
        if self.lose_upload:
            self.lose_upload = False
            raise OSError('commit response lost')
        return {'acknowledged': True}


class SimulatedCrash(BaseException):
    pass


def paused_worker(root, ready, proceed, queue):
    try:
        root = Path(root)
        def pause():
            ready.set()
            if not proceed.wait(10):
                raise SimulatedCrash()
        runner = Runner(Client(), Journal(root / 'node'), FakeHarness(root / 'native'), policy=policy(), before_start=pause)
        queue.put(runner.run_once()['outcome'])
    except BaseException as error:
        queue.put(type(error).__name__)


class NodeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.journal = Journal(self.root / 'node')
        self.client = Client()

    def runner(self, mode='success', **kwargs):
        self.adapter = FakeHarness(self.root / 'native', mode=mode)
        return Runner(self.client, self.journal, self.adapter, policy=policy(), **kwargs)

    def test_success_is_durable_and_never_respawned(self):
        runner = self.runner()
        value = runner.run_once()
        self.assertEqual(value['outcome'], 'succeeded')
        self.assertEqual(value['usage'], {'state': 'unknown'})
        self.assertEqual(self.adapter.start_count, 1)
        self.client.tasks = [task()]
        self.assertEqual(runner.run_once()['outcome'], 'replayed')
        self.assertEqual(self.adapter.start_count, 1)
        record = Journal(self.root / 'node').get('task-1')
        self.assertTrue(record['launch_intent'])
        self.assertTrue(record['acknowledged'])

    def test_second_independent_task_only_after_confirmed_stop(self):
        runner = self.runner()
        self.client.tasks.append(task('task-2'))
        runner.run_once()
        runner.run_once()
        self.assertEqual(self.adapter.start_count, 2)

    def test_observation_error_is_not_terminal_failure(self):
        runner = self.runner('observation_error')
        self.assertEqual(runner.run_once()['outcome'], 'pending')
        record = self.journal.get('task-1')
        self.assertEqual(record['liveness'], 'unknown')
        self.assertNotIn('result', record)
        runner.run_once()
        self.assertEqual(self.client.next_calls, 1)
        self.assertEqual(self.adapter.start_count, 1)

    def test_partial_output_is_not_success(self):
        runner = self.runner('partial')
        self.assertEqual(runner.run_once()['outcome'], 'pending')
        self.assertEqual(self.client.results, [])
        self.assertEqual(self.adapter.start_count, 1)

    def test_approval_retains_slot(self):
        runner = self.runner('approval')
        self.assertEqual(runner.run_once()['liveness'], 'waiting_approval')
        runner.run_once()
        self.assertEqual(self.client.next_calls, 1)
        self.assertEqual(self.adapter.start_count, 1)

    def test_native_failure_does_not_become_success(self):
        runner = self.runner('failure')
        value = runner.run_once()
        self.assertEqual(value['outcome'], 'failed')
        self.assertEqual(value['code'], 'NATIVE_FAILED')
        self.assertEqual(value['liveness'], 'stopped_confirmed')

    def test_success_with_unknown_liveness_keeps_slot(self):
        runner = self.runner('unknown_liveness')
        self.assertEqual(runner.run_once()['outcome'], 'succeeded')
        self.assertEqual(runner.run_once()['code'], 'SLOT_QUARANTINED')
        self.assertEqual(self.client.next_calls, 1)

    def test_failed_result_with_unknown_liveness_keeps_slot(self):
        class FailedActive(FakeHarness):
            def start(self, task, grant):
                value = super().start(task, grant)
                value.update(outcome='failed', liveness='unknown')
                return value
        adapter = FailedActive(self.root / 'native')
        runner = Runner(self.client, self.journal, adapter, policy=policy())
        self.assertEqual(runner.run_once()['outcome'], 'failed')
        self.assertEqual(runner.run_once()['code'], 'SLOT_QUARANTINED')
        self.assertEqual(adapter.start_count, 1)

    def test_model_mismatch_not_labelled_success(self):
        runner = self.runner('model_mismatch')
        value = runner.run_once()
        self.assertEqual(value['outcome'], 'failed')
        self.assertEqual(value['code'], 'MODEL_MISMATCH')
        self.assertEqual(value['model_evidence']['reported'], 'synthetic-wrong-model')

    def test_empty_output_not_filled_with_placeholder(self):
        runner = self.runner('empty')
        value = runner.run_once()
        self.assertEqual(value['outcome'], 'failed')
        self.assertEqual(value['code'], 'NATIVE_EMPTY_RESULT')
        self.assertEqual(value['output_blocks'], [])

    def test_grant_response_lost_does_not_retry_start_or_spawn(self):
        self.client.lose_grant = True
        runner = self.runner()
        runner.run_once()
        self.client.lose_grant = False
        runner.run_once()
        self.assertEqual(self.client.starts, 1)
        self.assertEqual(self.adapter.start_count, 0)
        self.assertEqual(self.client.next_calls, 1)

    def test_consumed_grant_false_is_not_permission(self):
        self.client.grant_allowed = False
        runner = self.runner()
        self.assertEqual(runner.run_once()['outcome'], 'pending')
        self.assertEqual(self.adapter.start_count, 0)

    def test_crash_between_intent_and_spawn_never_respawns(self):
        def crash():
            raise SimulatedCrash()
        runner = self.runner(before_start=crash)
        with self.assertRaises(SimulatedCrash):
            runner.run_once()
        self.assertTrue(self.journal.get('task-1')['launch_intent'])
        recovered = Runner(self.client, Journal(self.root / 'node'), self.adapter, policy=policy())
        self.assertEqual(recovered.run_once()['outcome'], 'pending')
        self.assertEqual(self.adapter.start_count, 0)

    def test_start_returns_late_result_reconcile_does_not_execute_again(self):
        class LostReturn(FakeHarness):
            def start(self, task, grant):
                super().start(task, grant)
                raise OSError('lost final observation')
        adapter = LostReturn(self.root / 'native')
        runner = Runner(self.client, self.journal, adapter, policy=policy())
        self.assertEqual(runner.run_once()['outcome'], 'pending')
        self.assertEqual(runner.run_once()['outcome'], 'succeeded')
        self.assertEqual(adapter.start_count, 1)

    def test_upload_ack_loss_replays_result_only(self):
        self.client.lose_upload = True
        runner = self.runner()
        self.assertEqual(runner.run_once()['code'], 'RESULT_UPLOAD_PENDING')
        value = Runner(self.client, Journal(self.root / 'node'), self.adapter, policy=policy()).run_once()
        self.assertEqual(value['outcome'], 'succeeded')
        self.assertEqual(self.adapter.start_count, 1)
        self.assertEqual(len(self.client.results), 1)

    def test_journal_missing_after_initialization_fails_closed(self):
        self.journal.db.unlink()
        with self.assertRaises(JournalError):
            Journal(self.root / 'node')

    def test_journal_does_not_allow_terminal_or_identity_rewrite(self):
        self.runner().run_once()
        record = self.journal.get('task-1')
        record['result']['outcome'] = 'cancelled'
        with self.assertRaises(JournalError):
            self.journal.save(record)
        record = self.journal.get('task-1')
        record['epoch'] = 2
        with self.assertRaises(JournalError):
            self.journal.save(record)

    def test_invalid_task_rejected_before_claim_start(self):
        runner = self.runner()
        self.client.tasks[0]['request']['binding']['revision'] = True
        with self.assertRaises(ValueError):
            runner.run_once()
        self.assertEqual(self.client.starts, 0)
        self.assertEqual(self.adapter.start_count, 0)

    def test_every_offer_mutation_rejected_before_claim_or_native_start(self):
        mutations = [
            ('tenant', lambda t: t['identity'].update(tenant='another-tenant')),
            ('principal', lambda t: t['identity'].update(principal='another-principal')),
            ('source', lambda t: t['identity'].update(source='another-source')),
            ('ledger', lambda t: t.update(ledger_instance='another-ledger')),
            ('origin', lambda t: t.update(origin_key='h_wrong')),
            ('digest', lambda t: t.update(request_digest='0' * 64)),
            ('messages', lambda t: t['request'].update(messages=[{'role': 'user', 'content': 'changed'}])),
            ('binding', lambda t: t['request']['binding'].update(binding_id='other-binding')),
            ('native model', lambda t: t['request']['binding'].update(native_model='fake-other')),
            ('revision', lambda t: t['request']['binding'].update(revision=2)),
            ('alias', lambda t: t['request']['binding'].update(model_alias='synthetic-other')),
            ('task id', lambda t: t.update(remote_task_id='other-task')),
            ('attempt id', lambda t: t.update(attempt_id='')),
            ('outcome', lambda t: t.update(outcome='succeeded')),
            ('liveness', lambda t: t.update(liveness='running')),
            ('grant', lambda t: t.update(start_grant_id='grant-existing')),
            ('result', lambda t: t.update(result={})),
            ('expiry', lambda t: t.update(result_expired=True)),
            ('expiry integer', lambda t: t.update(result_expired=0)),
            ('created boolean', lambda t: t.update(created=True)),
            ('created zero', lambda t: t.update(created=0)),
            ('epoch boolean', lambda t: t.update(epoch=False)),
            ('unknown top key', lambda t: t.update(unknown='ignored?')),
            ('unknown request key', lambda t: t['request'].update(unknown='ignored?')),
            ('unknown binding key', lambda t: t['request']['binding'].update(unknown='ignored?')),
            ('unknown identity key', lambda t: t['identity'].update(unknown='ignored?')),
            ('missing key', lambda t: t.pop('epoch')),
            ('client request changed', lambda t: t['request']['client_request']['messages'][0].update(content='changed')),
        ]
        runner = self.runner()
        for name, mutate in mutations:
            with self.subTest(mutation=name):
                value = task()
                mutate(value)
                self.client.tasks = [NodeOffer('task-1', value)]
                with self.assertRaisesRegex(ValueError, 'Invalid or unauthorized node offer'):
                    runner.run_once()
                self.assertEqual(self.client.claim_calls, 0)
                self.assertEqual(self.client.starts, 0)
                self.assertEqual(self.adapter.start_count, 0)
                self.assertEqual(self.journal.records(), [])

    def test_self_consistent_unapproved_owner_is_rejected(self):
        runner = self.runner()
        for identity in [Identity('test-source', 'other-tenant', 'test-principal', 'op-task-1'),
                         Identity('test-source', 'test-tenant', 'other-principal', 'op-task-1'),
                         Identity('other-source', 'test-tenant', 'test-principal', 'op-task-1')]:
            with self.subTest(identity=identity):
                value = task()
                value.update(identity=identity.wire(), origin_key=identity.origin_key)
                self.client.tasks = [NodeOffer('task-1', value)]
                with self.assertRaises(ValueError):
                    runner.run_once()
                self.assertEqual(self.client.claim_calls, 0)
                self.assertEqual(self.adapter.start_count, 0)

    def test_plain_task_dict_not_an_offer(self):
        runner = self.runner()
        self.client.next_task = lambda: task()
        with self.assertRaises(ValueError):
            runner.run_once()
        self.assertEqual(self.client.claim_calls, 0)

    def test_policy_is_mandatory_and_immutable(self):
        adapter = FakeHarness(self.root / 'native')
        with self.assertRaises(TypeError):
            Runner(self.client, self.journal, adapter)
        from dataclasses import FrozenInstanceError
        with self.assertRaises(FrozenInstanceError):
            policy().source = 'changed'
        candidates = [
            ('', 'test-ledger', frozenset({('t', 'p')}), policy().bindings),
            ('s', '', frozenset({('t', 'p')}), policy().bindings),
            ('s', 'l', set({('t', 'p')}), policy().bindings),
            ('s', 'l', frozenset(), policy().bindings),
            ('s', 'l', frozenset({('t', '')}), policy().bindings),
            ('s', 'l', frozenset({('t', 'p')}), list(policy().bindings)),
            ('s', 'l', frozenset({('t', 'p')}), ()),
            ('s', 'l', frozenset({('t', 'p')}), (policy().bindings[0], policy().bindings[0])),
        ]
        for args in candidates:
            with self.subTest(args=args), self.assertRaises(ValueError):
                NodePolicy(*args)

    def test_changed_policy_allows_only_readonly_recovery_of_old_intent(self):
        runner = self.runner('observation_error')
        runner.run_once()
        changed = NodePolicy('new-source', 'new-ledger', frozenset({('new-tenant', 'new-principal')}),
                             (Binding('new-binding', 2, 'synthetic-other', 'fake-other'),))
        recovered = Runner(self.client, self.journal, self.adapter, policy=changed)
        self.assertEqual(recovered.run_once()['outcome'], 'pending')
        self.assertEqual(self.client.claim_calls, 1)
        self.assertEqual(self.client.next_calls, 1)
        self.assertEqual(self.adapter.start_count, 1)

    def test_route_changed_does_not_upload_old_results_to_new_route(self):
        self.client.lose_upload = True
        runner = self.runner()
        runner.run_once()
        previous_uploads = self.client.result_calls
        changed = NodePolicy('new-source', 'new-ledger', policy().allowed_owners, policy().bindings)
        recovered = Runner(self.client, self.journal, self.adapter, policy=changed)
        self.assertEqual(recovered.run_once()['code'], 'ROUTE_RECONCILIATION_REQUIRED')
        self.assertEqual(self.client.result_calls, previous_uploads)
        self.assertFalse(self.journal.get('task-1').get('acknowledged', False))
        self.assertEqual(self.adapter.start_count, 1)

    def test_removed_binding_does_not_block_same_route_result_recovery(self):
        self.client.lose_upload = True
        runner = self.runner()
        runner.run_once()
        changed = NodePolicy(policy().source, policy().ledger_instance, policy().allowed_owners,
                             (Binding('new-binding', 2, 'synthetic-new', 'fake-new'),))
        recovered = Runner(self.client, self.journal, self.adapter, policy=changed)
        self.assertEqual(recovered.run_once()['outcome'], 'succeeded')
        self.assertTrue(self.journal.get('task-1')['acknowledged'])
        self.assertEqual(self.adapter.start_count, 1)
        self.assertEqual(self.client.claim_calls, 1)

    def test_boolean_completion_is_strict(self):
        value = {'outcome': 'succeeded', 'liveness': 'stopped_confirmed',
                 'native_completion': {'confirmed': 1}, 'output_blocks': []}
        with self.assertRaises(ValueError):
            Runner._safe_result(task(), value)

    def test_terminated_old_consumer_leaves_intent_quarantined(self):
        ctx = multiprocessing.get_context('spawn')
        ready, proceed, queue = ctx.Event(), ctx.Event(), ctx.Queue()
        process = ctx.Process(target=paused_worker, args=(str(self.root), ready, proceed, queue))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            process.terminate()
            process.join(10)
            self.assertFalse(process.is_alive())
            adapter = FakeHarness(self.root / 'native')
            value = Runner(self.client, Journal(self.root / 'node'), adapter, policy=policy()).run_once()
            self.assertEqual(value['outcome'], 'pending')
            self.assertEqual(adapter.start_count, 0)
            self.assertEqual(self.client.next_calls, 0)
        finally:
            if process.is_alive():
                process.terminate()
            process.join(10)
            queue.close()

    def test_late_spawn_keeps_slot_locked_until_old_consumer_finishes(self):
        ctx = multiprocessing.get_context('spawn')
        ready, proceed, queue = ctx.Event(), ctx.Event(), ctx.Queue()
        process = ctx.Process(target=paused_worker, args=(str(self.root), ready, proceed, queue))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            adapter = FakeHarness(self.root / 'native')
            self.assertEqual(adapter.start_count, 0)
            self.assertTrue(self.journal.get('task-1')['launch_intent'])
            with self.assertRaises(NodeBusy):
                Runner(self.client, Journal(self.root / 'node'), adapter, policy=policy()).run_once()
            # No API exists to force-clear the occupied slot on this evidence.
            self.assertFalse(hasattr(self.journal, 'force_release'))
            proceed.set()
            self.assertEqual(queue.get(timeout=10), 'succeeded')
            process.join(10)
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(adapter.start_count, 1)
        finally:
            proceed.set()
            if process.is_alive():
                process.terminate()
            process.join(10)
            queue.close()


if __name__ == '__main__':
    unittest.main()
