"""One-slot local execution evidence and fail-closed P1a Node runner.

There is no force-release or automatic re-spawn operation. The OS lock covers
intent, the entire adapter call, and result persistence. A paused old consumer
therefore keeps the lock; an exited consumer leaves durable intent behind. Both
cases prevent a new task from being launched in an uncertain slot.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
import copy
import json
import os
from pathlib import Path
import sqlite3

from .contracts import (Binding, Identity, NodeOffer, MAX_RESULT_BYTES, canonical,
                        checked_task, digest, identifier, normalize_request)
from .errors import ChannelError


class NodeBusy(RuntimeError):
    pass


class JournalError(RuntimeError):
    pass


def _encoded(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


class Journal:
    def __init__(self, root: Path):
        self.root = Path(root)
        if self.root.is_symlink():
            raise JournalError('Symlink journal roots are not permitted')
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'node.sqlite'
        marker = self.root / 'journal-created'
        if marker.exists() != self.db.exists():
            raise JournalError('Journal identity or database missing; reconciliation required')
        if not marker.exists():
            with marker.open('x') as handle:
                handle.write('native-node-evidence-v1\n')
                handle.flush()
                os.fsync(handle.fileno())
            with self._connect() as con:
                con.execute('CREATE TABLE records(task_id TEXT PRIMARY KEY, record TEXT NOT NULL)')
        with self._connect() as con:
            if con.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise JournalError('Journal corrupt; reconciliation required')
            con.execute('SELECT task_id FROM records LIMIT 1')

    @contextmanager
    def _connect(self):
        if self.db.is_symlink():
            raise JournalError('Symlink journal database is not permitted')
        con = sqlite3.connect(self.db, timeout=5)
        con.execute('PRAGMA synchronous=FULL')
        try:
            with con:
                yield con
        finally:
            con.close()

    @contextmanager
    def locked(self):
        """Cross-process, nonblocking single-slot ownership; never unlink."""
        path = self.root / 'node.lock'
        if path.is_symlink():
            raise JournalError('Symlink lock is not permitted')
        handle = path.open('a+b')
        if handle.seek(0, 2) == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        acquired = False
        try:
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except OSError:
                raise NodeBusy('The local slot has another consumer') from None
            yield
        finally:
            if acquired:
                handle.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            handle.close()

    def records(self):
        with self._connect() as con:
            return [json.loads(row[0]) for row in con.execute('SELECT record FROM records ORDER BY rowid')]

    def get(self, task_id):
        with self._connect() as con:
            row = con.execute('SELECT record FROM records WHERE task_id=?', (task_id,)).fetchone()
        return json.loads(row[0]) if row else None

    def save(self, record):
        """Evidence writes preserve attempt/grant and immutable terminal results."""
        encoded = _encoded(record)
        with self._connect() as con:
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT record FROM records WHERE task_id=?', (record['task_id'],)).fetchone()
            if row:
                old = json.loads(row[0])
                for key in ('attempt_id', 'claim_id', 'epoch', 'task'):
                    if old[key] != record[key]:
                        raise JournalError('Execution identity changed')
                if old.get('grant') and old['grant'] != record.get('grant'):
                    raise JournalError('Start grant changed')
                if old.get('result') and old['result'] != record.get('result'):
                    raise JournalError('Terminal result changed')
                if old.get('launch_intent') and not record.get('launch_intent'):
                    raise JournalError('Launch intent cannot be removed')
            con.execute('INSERT INTO records VALUES(?,?) ON CONFLICT(task_id) DO UPDATE SET record=excluded.record',
                        (record['task_id'], encoded))


@dataclass(frozen=True)
class NodePolicy:
    """Explicit immutable node authorization, never derived from an offer."""
    source: str
    ledger_instance: str
    allowed_owners: frozenset[tuple[str, str]]
    bindings: tuple[Binding, ...]
    mode: str = "p1b_integration"

    def __post_init__(self):
        try:
            if self.mode not in {"p1b_integration", "workbuddy_local_trial"}:
                raise ValueError()
            if self.mode == "workbuddy_local_trial" and len(self.allowed_owners) != 1:
                raise ValueError()
            identifier(self.source)
            identifier(self.ledger_instance)
            if type(self.allowed_owners) is not frozenset or not self.allowed_owners:
                raise ValueError()
            for owner in self.allowed_owners:
                if type(owner) is not tuple or len(owner) != 2:
                    raise ValueError()
                identifier(owner[0])
                identifier(owner[1])
            if type(self.bindings) is not tuple or not self.bindings:
                raise ValueError()
            for binding in self.bindings:
                if type(binding) is not Binding:
                    raise ValueError()
                # Revalidate even an object constructed through unsafe reflection.
                Binding(**binding.wire())
                if self.mode == "workbuddy_local_trial" and (not binding.model_alias.startswith("workbuddy-") or binding.native_model.startswith("fake-")):
                    raise ValueError()
            fingerprints = [digest(binding.wire()) for binding in self.bindings]
            if len(set(fingerprints)) != len(fingerprints):
                raise ValueError()
        except (ChannelError, TypeError, ValueError, AttributeError):
            raise ValueError('Invalid immutable node policy') from None

    def validate_offer(self, offer):
        """Bind offered ID, trusted route, ownership and exact approved semantics.

        Validation happens before claim and journal writes. A private copy keeps
        a mutable client return value from changing after it has been checked.
        """
        try:
            if not isinstance(offer, NodeOffer) or not isinstance(offer.task, dict):
                raise ValueError()
            task = copy.deepcopy(offer.task)
            if set(task) != {
                    'schema_version', 'origin_key', 'identity', 'request_digest',
                    'ledger_instance', 'request', 'remote_task_id', 'attempt_id',
                    'created', 'outcome', 'liveness', 'result', 'claim_id', 'epoch',
                    'start_grant_id', 'result_expired'}:
                raise ValueError()
            if identifier(offer.task_id) != identifier(task['remote_task_id']):
                raise ValueError()
            identifier(task['attempt_id'])
            if type(task['created']) is not int or task['created'] <= 0:
                raise ValueError()
            identity = Identity(**task['identity'])
            if (identity.source != self.source or
                    (identity.tenant, identity.principal) not in self.allowed_owners or
                    task['ledger_instance'] != self.ledger_instance):
                raise ValueError()
            frozen = task['request']
            if not isinstance(frozen, dict) or set(frozen) != {'binding', 'messages', 'client_request'}:
                raise ValueError()
            client = normalize_request(frozen['client_request'])
            if self.mode == "workbuddy_local_trial" and (len(client["messages"]) != 1 or client["messages"][0]["role"] != "user"):
                raise ValueError()
            if canonical(frozen['client_request']) != canonical(client):
                raise ValueError()
            checked_task(task, identity, digest(client), self.ledger_instance)
            binding = Binding(**frozen['binding'])
            if digest(binding.wire()) not in {digest(b.wire()) for b in self.bindings}:
                raise ValueError()
            if (task['outcome'] != 'pending' or task['liveness'] != 'not_started'
                    or task['start_grant_id'] is not None or task['result'] is not None
                    or task['result_expired'] is not False):
                raise ValueError()
            if type(task['epoch']) is not int or task['epoch'] < 0:
                raise ValueError()
            if task['claim_id'] is None:
                if task['epoch'] != 0:
                    raise ValueError()
            elif not identifier(task['claim_id']) or task['epoch'] < 1:
                raise ValueError()
            return task
        except (ChannelError, TypeError, ValueError, AttributeError, KeyError,
                RecursionError, UnicodeError):
            raise ValueError('Invalid or unauthorized node offer') from None


class Runner:
    def __init__(self, client, journal: Journal, adapter, *, policy: NodePolicy, before_start=None):
        if type(policy) is not NodePolicy:
            raise ValueError('An explicit immutable NodePolicy is required')
        self.policy = policy
        self.client, self.journal, self.adapter = client, journal, adapter
        # A test-only crash/barrier injection, never loaded from external input.
        self.before_start = before_start

    @staticmethod
    def _safe_result(task, value):
        if not isinstance(value, dict):
            raise ValueError('Invalid adapter result')
        if value.get('outcome') == 'pending':
            if value.get('liveness') not in {'waiting_approval', 'running', 'unknown', 'may_be_running'}:
                raise ValueError('Invalid pending liveness')
            return {'outcome': 'pending', 'liveness': value['liveness'],
                    'code': 'APPROVAL_PENDING' if value['liveness'] == 'waiting_approval' else 'EXECUTION_UNCERTAIN'}
        if value.get('outcome') not in {'succeeded', 'failed', 'cancelled'}:
            raise ValueError('Invalid native outcome')
        if value.get('liveness') not in {'unknown', 'stopped_confirmed'}:
            raise ValueError('Terminal output does not establish liveness')
        if value.get('native_completion') != {'confirmed': True}:
            raise ValueError('Native completion not confirmed')
        # Strict true: Python dictionary equality would otherwise accept 1.
        if type(value['native_completion']['confirmed']) is not bool:
            raise ValueError('Native completion must be boolean')
        blocks = value.get('output_blocks')
        if not isinstance(blocks, list) or len(blocks) > 32:
            raise ValueError('Invalid native output')
        if any(not isinstance(b, dict) or set(b) != {'type', 'text'} or b['type'] not in {'text', 'refusal'}
               or not isinstance(b['text'], str) for b in blocks):
            raise ValueError('Unsupported native output')
        if sum(len(b['text'].encode('utf-8')) for b in blocks) > MAX_RESULT_BYTES:
            raise ValueError('Native output too large')
        result = {k: value[k] for k in ('outcome', 'liveness', 'output_blocks', 'native_completion', 'model_evidence', 'usage')}
        if result['usage'] != {'state': 'unknown'}:
            raise ValueError('P1a adapter only supports explicitly unknown usage')
        expected = task['request']['binding']['native_model']
        evidence = result['model_evidence']
        if not isinstance(evidence, dict) or set(evidence) != {'selected', 'reported'}:
            raise ValueError('Invalid native model evidence')
        code = value.get('code')
        if evidence['selected'] != expected or evidence['reported'] != expected:
            result.update(outcome='failed', output_blocks=[])
            code = 'MODEL_MISMATCH'
        elif result['outcome'] == 'succeeded' and not any(b['text'].strip() for b in blocks):
            result.update(outcome='failed', output_blocks=[])
            code = 'NATIVE_EMPTY_RESULT'
        if result['outcome'] == 'failed':
            result['code'] = code if code in {'MODEL_MISMATCH', 'NATIVE_EMPTY_RESULT'} else 'NATIVE_FAILED'
        return result

    def _observe(self, record, liveness='unknown', code='EXECUTION_UNCERTAIN'):
        if record.get('result'):
            # Never turn persisted terminal output into a pending operation.
            return {'outcome': record['outcome'], 'liveness': record['liveness'],
                    'code': 'RESULT_UPLOAD_PENDING'}
        record.update(outcome='pending', liveness=liveness)
        self.journal.save(record)
        if record.get('grant'):
            try:
                self.client.observation(record['task_id'], record['claim_id'],
                    record['grant']['start_grant_id'], 'pending', liveness, code)
            except Exception:
                pass  # Local evidence remains authoritative for this consumer.
        return {'outcome': 'pending', 'liveness': liveness, 'code': code}

    def _upload(self, record):
        try:
            self.client.result(record['task_id'], record['claim_id'],
                               record['grant']['start_grant_id'], record['result'])
        except Exception:
            return {'outcome': 'pending', 'liveness': record['liveness'], 'code': 'RESULT_UPLOAD_PENDING'}
        record['acknowledged'] = True
        self.journal.save(record)
        return record['result']

    def _accept(self, record, value):
        value = self._safe_result(record['task'], value)
        if value['outcome'] == 'pending':
            return self._observe(record, value['liveness'], value['code'])
        record.update(result=value, outcome=value['outcome'], liveness=value['liveness'])
        self.journal.save(record)  # Must succeed before any upload or slot reuse.
        return self._upload(record)

    def run_once(self, *, allow_new=True):
        if type(allow_new) is not bool:
            raise ValueError('allow_new must be a boolean')
        with self.journal.locked():
            for record in self.journal.records():
                frozen = record['task']
                if (frozen.get('ledger_instance') != self.policy.ledger_instance
                        or frozen.get('identity', {}).get('source') != self.policy.source):
                    # A changed route must not receive an old operation's data.
                    # Keep its slot quarantined until the original route returns.
                    return {'outcome': record['outcome'], 'liveness': record['liveness'],
                            'code': 'ROUTE_RECONCILIATION_REQUIRED'}
                if record.get('result'):
                    if not record.get('acknowledged'):
                        return self._upload(record)
                    if record['liveness'] != 'stopped_confirmed':
                        return {'outcome': record['outcome'], 'liveness': record['liveness'], 'code': 'SLOT_QUARANTINED'}
                    continue
                # Any durable pre-start request might have received a grant.
                # Recovery is read-only; lack of evidence never means re-spawn.
                if record.get('grant'):
                    try:
                        result = self.adapter.reconcile(record['task'], record['grant'])
                        if result is not None:
                            return self._accept(record, result)
                    except Exception:
                        pass
                return self._observe(record)
            if not allow_new:
                return {'outcome': 'idle'}
            offer = self.client.next_task()
            if offer is None:
                return {'outcome': 'idle'}
            task = self.policy.validate_offer(offer)
            task_id, attempt_id = task['remote_task_id'], task['attempt_id']
            if self.journal.get(task_id):
                return {'outcome': 'replayed', 'code': 'ALREADY_CONSUMED'}
            claim = self.client.claim(task_id)
            if (not isinstance(claim, dict) or claim.get('attempt_id') != attempt_id
                    or type(claim.get('epoch')) is not int or claim['epoch'] < 1):
                raise ValueError('Invalid claim identity')
            identifier(claim['claim_id'])
            record = {'task_id': task_id, 'attempt_id': attempt_id, 'task': task,
                      'claim_id': claim['claim_id'], 'epoch': claim['epoch'],
                      'outcome': 'pending', 'liveness': 'may_be_running', 'launch_intent': False}
            self.journal.save(record)  # Covers start-grant response loss.
            try:
                grant = self.client.start(task_id, claim['claim_id'])
            except Exception:
                return self._observe(record)
            if (not isinstance(grant, dict) or grant.get('may_execute') is not True
                    or grant.get('attempt_id') != attempt_id
                    or not isinstance(grant.get('start_grant_id'), str) or not grant['start_grant_id']):
                return self._observe(record)
            identifier(grant['start_grant_id'])
            record.update(grant=grant, launch_intent=True)
            self.journal.save(record)
            try:
                if self.before_start is not None:
                    self.before_start()
                if getattr(self.adapter, 'supports_live_events', False) is True:
                    def publish_event(seq, event):
                        return self.client.event(task_id, claim['claim_id'], grant['start_grant_id'], seq, event)
                    value = self.adapter.start(task, grant, publish_event=publish_event)
                else:
                    value = self.adapter.start(task, grant)
                return self._accept(record, value)
            except Exception:
                return self._observe(record, code='OBSERVATION_ERROR')
