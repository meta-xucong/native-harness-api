"""Native SFE evidence adapter for Bridge Runner; never a queue or retry engine.

The caller supplies the reviewed launch/account/profile owner as ``invoke``.
This component does not discover software, authenticate, approve tools or enable
production routing. SQLite records only one-shot intent and sanitized evidence.
"""
from contextlib import contextmanager
import copy
import json
from pathlib import Path
import sqlite3
import stat

from .contracts import Binding, MAX_RESULT_BYTES, canonical, digest, identifier, normalize_request

PENDING = {'outcome': 'pending', 'liveness': 'unknown', 'code': 'EXECUTION_UNCERTAIN'}


def project_result(value, model):
    """Require terminal, model and full owned-lifecycle evidence independently."""
    if type(value) is not dict or value.get('fault') is not None:
        return dict(PENDING)
    session = value.get('session')
    if (type(session) is not dict or session.get('fault') is not None or session.get('model_confirmed') is not True or
            session.get('protocol_ready') is not True or session.get('account_verified') is not True or
            session.get('failure_code') is not None):
        return dict(PENDING)
    turn = session.get('turn')
    if (type(turn) is not dict or turn.get('fault') is not None or
            turn.get('insert_acknowledged') is not True or turn.get('terminal_observed') is not True):
        return dict(PENDING)
    outcome = turn.get('outcome')
    reasons = {'succeeded': ('end_turn', 'refusal'), 'failed': ('_codebuddy.ai/error',), 'cancelled': ('cancelled',)}
    if (type(outcome) is not str or outcome not in reasons or turn.get('terminal_reason') not in reasons[outcome]
            or value.get('outcome') != outcome or session.get('outcome') != outcome):
        return dict(PENDING)
    cleanup = value.get('cleanup')
    if (value.get('cleanup_confirmed') is not True or value.get('natural_exit_zero') is not True or
            type(cleanup) is not dict or any(cleanup.get(k) is not True for k in
                ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete', 'io_threads_stopped'))):
        return dict(PENDING)
    content = turn.get('text')
    if type(content) is not str:
        return dict(PENDING)
    try:
        if len(content.encode('utf8')) > MAX_RESULT_BYTES:
            return dict(PENDING)
    except UnicodeError:
        return dict(PENDING)
    if outcome == 'succeeded' and not content.strip():
        return dict(PENDING)
    result = {'outcome': outcome, 'liveness': 'stopped_confirmed',
              'output_blocks': [{'type': 'refusal' if turn['terminal_reason'] == 'refusal' else 'text', 'text': content}] if outcome == 'succeeded' else [],
              'native_completion': {'confirmed': True},
              'model_evidence': {'selected': model, 'reported': model}, 'usage': {'state': 'unknown'}}
    if outcome == 'failed': result['code'] = 'NATIVE_FAILED'
    return result


class PublicSfeAdapter:
    synthetic = False

    def __init__(self, root, *, binding=None, bindings=None, invoke):
        if (binding is None) == (bindings is None):
            raise ValueError('Explicit binding or bindings required')
        approved = (binding,) if bindings is None else bindings
        if (type(approved) is not tuple or not 1 <= len(approved) <= 32 or
                any(type(b) is not Binding for b in approved) or not callable(invoke) or
                len({b.model_alias for b in approved}) != len(approved) or
                len({b.binding_id for b in approved}) != len(approved)):
            raise ValueError('Explicit unique bindings and invocation owner required')
        self.root = Path(root)
        if not self.root.is_absolute():
            raise ValueError('Existing dedicated absolute evidence directory required')
        self.db = self.root / 'sfe-evidence.sqlite'
        self._check_storage()
        self.bindings, self.invoke = approved, invoke
        with self._connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS evidence (grant_id TEXT PRIMARY KEY, task_id TEXT UNIQUE NOT NULL, request_hash TEXT NOT NULL, result TEXT)')

    def _check_storage(self):
        for path in (self.root, *self.root.parents):
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
                raise ValueError('Unsafe evidence directory')
        for suffix in ('', '-journal', '-wal', '-shm'):
            path = Path(str(self.db) + suffix)
            try: info = path.lstat()
            except FileNotFoundError: continue
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                    getattr(info, 'st_file_attributes', 0) & 0x400):
                raise ValueError('Unsafe evidence file')

    @contextmanager
    def _connect(self):
        self._check_storage()
        con = sqlite3.connect(self.db, timeout=5)
        con.execute('PRAGMA synchronous=FULL')
        try:
            with con: yield con
        finally: con.close()

    def _identity(self, task, grant, *, require_current=True):
        task_id = identifier(task['remote_task_id'])
        grant_id = identifier(grant['start_grant_id'])
        frozen = task['request']
        request = normalize_request(frozen['client_request'])
        binding = Binding(**frozen['binding'])
        if ((require_current and binding not in self.bindings) or request['model'] != binding.model_alias or
                frozen['messages'] != request['messages'] or digest(request) != task['request_digest']):
            raise ValueError('Task binding/request mismatch')
        if len(request['messages']) != 1 or request['messages'][0]['role'] != 'user':
            raise ValueError('Native local trial supports exactly one user message')
        # Include owner/ledger routing in the immutable execution identity.
        request_hash = digest({'request': request, 'binding': binding.wire(),
                               'identity': task['identity'], 'ledger': task['ledger_instance']})
        return task_id, grant_id, request_hash, request

    def start(self, task, grant):
        task, grant = copy.deepcopy(task), copy.deepcopy(grant)
        task_id, grant_id, fingerprint, request = self._identity(task, grant)
        with self._connect() as con:
            # BEGIN IMMEDIATE serializes cross-process admission before native IO.
            con.execute('BEGIN IMMEDIATE')
            row = con.execute('SELECT task_id,request_hash,result FROM evidence WHERE grant_id=?', (grant_id,)).fetchone()
            if row is not None:
                if row[:2] != (task_id, fingerprint): raise ValueError('Execution identity conflict')
                return json.loads(row[2]) if row[2] is not None else dict(PENDING)
            con.execute('INSERT INTO evidence VALUES(?,?,?,NULL)', (grant_id, task_id, fingerprint))
        # Committed intent survives every exception, interruption or process loss.
        # No exception message or raw vendor evidence is persisted or forwarded.
        try:
            model = task['request']['binding']['native_model']
            evidence = self.invoke(model_id=model, messages=request['messages'])
            result = project_result(evidence, model)
        except Exception:
            result = dict(PENDING)
        with self._connect() as con:
            con.execute('UPDATE evidence SET result=? WHERE grant_id=? AND result IS NULL', (canonical(result), grant_id))
        return result

    def reconcile(self, task, grant):
        task_id, grant_id, fingerprint, _ = self._identity(task, grant, require_current=False)
        with self._connect() as con:
            row = con.execute('SELECT task_id,request_hash,result FROM evidence WHERE grant_id=?', (grant_id,)).fetchone()
        if row is None: return None
        if row[:2] != (task_id, fingerprint): raise ValueError('Execution identity conflict')
        return json.loads(row[2]) if row[2] is not None else dict(PENDING)
