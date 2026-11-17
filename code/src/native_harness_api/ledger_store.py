"""Single-slot task store for the Native Harness HTTP contract."""
from contextlib import closing, contextmanager
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import time

from .contracts import identifier
from .ledger_contract import CONTRACT, DDL, HarnessMixin, SourceScope, _fail, _id, digest


TASK_DDL = '''
CREATE TABLE tasks (
 id TEXT PRIMARY KEY, source TEXT NOT NULL, workspace TEXT NOT NULL,
 origin_key TEXT NOT NULL, digest TEXT NOT NULL, payload TEXT NOT NULL,
 state TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
 cancel_requested INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, deadline REAL NOT NULL,
 UNIQUE(source,workspace,origin_key));
CREATE TABLE attempts (
 id TEXT PRIMARY KEY, task_id TEXT NOT NULL UNIQUE REFERENCES tasks(id),
 start_grant_id TEXT UNIQUE, original_node TEXT, original_executor TEXT, started REAL);
CREATE TABLE claims (
 task_id TEXT PRIMARY KEY REFERENCES tasks(id), id TEXT NOT NULL UNIQUE,
 epoch INTEGER NOT NULL, node TEXT NOT NULL, executor TEXT NOT NULL, mode TEXT NOT NULL, expires REAL NOT NULL);
CREATE TABLE audit_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT, source TEXT NOT NULL, workspace TEXT NOT NULL, occurred REAL NOT NULL,
 kind TEXT NOT NULL, task_id TEXT, attempt_id TEXT, receipt_id TEXT, code TEXT);
'''

VERSION = 1
APPLICATION_ID = 0x4E484C44


def ledger_identity(config):
    return (config.ledger_instance, config.source_id, config.workspace_id, config.node_id, config.executor_instance)


class NativeLedger(HarnessMixin):
    def __init__(self, path, config, *, clock=time.time):
        self.path = Path(path).resolve(strict=True)
        self.harness = config
        self.clock = clock
        self.scope = SourceScope(config.source_id, config.workspace_id)
        with self._connection() as con:
            if (con.execute('PRAGMA application_id').fetchone()[0] != APPLICATION_ID
                    or con.execute('PRAGMA user_version').fetchone()[0] != VERSION):
                raise ValueError('Native ledger schema required')
            row = con.execute('SELECT ledger_instance,source,workspace,node,executor FROM harness_instance WHERE singleton=1').fetchone()
            if row is None or tuple(row) != ledger_identity(config):
                raise ValueError('Native ledger deployment identity mismatch')

    @classmethod
    def initialize(cls, path, config, *, populate=None, clock=time.time):
        path = Path(path)
        with path.open('xb'):
            pass
        with closing(sqlite3.connect(path)) as con, con:
            con.execute('PRAGMA foreign_keys=ON')
            con.executescript('BEGIN IMMEDIATE;'+TASK_DDL+DDL)
            con.execute('INSERT INTO harness_instance VALUES(1,?,?,?,?,?)', ledger_identity(config))
            if populate is not None:
                populate(con)
            con.execute(f'PRAGMA application_id={APPLICATION_ID}')
            con.execute(f'PRAGMA user_version={VERSION}')
        return cls(path, config, clock=clock)

    @property
    def accepting_new_tasks(self):
        return self.harness.accepting_new

    @contextmanager
    def _connection(self):
        with closing(sqlite3.connect(self.path.as_uri()+'?mode=rw', uri=True, timeout=2)) as con:
            con.row_factory = sqlite3.Row
            con.execute('PRAGMA foreign_keys=ON')
            yield con

    @contextmanager
    def _transaction(self):
        with self._connection() as con, con:
            con.execute('BEGIN IMMEDIATE')
            yield con

    @staticmethod
    def _scope(scope):
        return scope.source_id, scope.workspace_id

    def _scoped(self, con, table, task_id, scope):
        assert table == 'tasks'
        row = con.execute('SELECT * FROM tasks WHERE id=? AND source=? AND workspace=?',
                          (identifier(task_id), *self._scope(scope))).fetchone()
        if row is None:
            _fail('not_found', 404)
        return row

    def _check_node_active(self, con, scope, node, executor, *, required=True):
        cfg = self.harness
        if scope != self.scope or (node, executor) != (cfg.node_id, cfg.executor_instance):
            _fail('harness_node_mismatch', 403)

    def _active_task_count(self, con):
        return con.execute('''SELECT count(*) FROM tasks t JOIN attempts a ON a.task_id=t.id
            WHERE t.state NOT IN ('expired','harness_terminal') AND (t.deadline>? OR a.start_grant_id IS NOT NULL)''',
                           (self.clock(),)).fetchone()[0]

    def _audit(self, con, scope, kind, *, task_id, attempt_id):
        con.execute('INSERT INTO audit_events(source,workspace,occurred,kind,task_id,attempt_id) VALUES(?,?,?,?,?,?)',
                    (*self._scope(scope), self.clock(), kind, task_id, attempt_id))
        con.execute('DELETE FROM audit_events WHERE id NOT IN (SELECT id FROM audit_events ORDER BY id DESC LIMIT 1000)')

    def capabilities(self, scope):
        cfg = self._harness_scope(scope)
        with self._connection() as con, con:
            con.execute('BEGIN')
            active = self._active_task_count(con)
            presence = self._harness_presence(con, *self._scope(scope), cfg.node_id, cfg.executor_instance)
        accepting = bool(cfg.accepting_new and active == 0 and presence and presence['state']=='available')
        return {'schema_version': '1', 'harness_contract': CONTRACT, 'ledger_instance': cfg.ledger_instance,
                'harness_admission': {'accepting_new_tasks': accepting, 'max_physical_slots': 1,
                                      'synthetic_only': cfg.execution_mode=='synthetic'},
                'queue': {'active_tasks': active, 'max_queued_tasks': 1}}

    def get_task(self, scope, task_id):
        self._harness_scope(scope)
        with self._transaction() as con:
            row = self._scoped(con, 'tasks', task_id, scope)
            self._expire_harness_before_start(con, scope, row)
            return self._harness_view(con, row)

    def get_task_by_origin(self, scope, origin_key):
        self._harness_scope(scope)
        with self._transaction() as con:
            row = con.execute('SELECT * FROM tasks WHERE source=? AND workspace=? AND origin_key=?',
                              (*self._scope(scope), identifier(origin_key))).fetchone()
            if row is None:
                _fail('not_found', 404)
            self._expire_harness_before_start(con, scope, row)
            return self._harness_view(con, row)

    def next_task(self, scope, node, executor):
        with self._connection() as con, con:
            con.execute('BEGIN')
            self._harness_node(con, scope, node, executor)
            presence = self._harness_presence(con, *self._scope(scope), node, executor)
            if presence is not None and presence['state']=='available':
                bindings = set(json.loads(presence['bindings']))
                rows = con.execute("SELECT id,payload FROM tasks WHERE source=? AND workspace=? AND state='harness_queued' AND deadline>? ORDER BY created,id",
                                   (*self._scope(scope), self.clock()))
                for row in rows:
                    payload = json.loads(row['payload'])
                    self._harness_owner(scope, payload)
                    binding = payload['request']['binding']
                    if digest(binding) in bindings and binding in [item.wire() for item in self.harness.bindings]:
                        return {'task_id': row['id']}
        return {'task_id': None}

    def claim(self, scope, task_id, node, executor):
        with self._transaction() as con:
            self._harness_node(con, scope, node, executor)
            task = self._scoped(con, 'tasks', task_id, scope)
            self._harness_owner(scope, json.loads(task['payload']))
            attempt = con.execute('SELECT start_grant_id FROM attempts WHERE task_id=?', (task_id,)).fetchone()
            if attempt['start_grant_id']:
                _fail('execution_already_started', 409)
            now = self.clock()
            if task['deadline'] <= now:
                _fail('task_expired', 409)
            if task['state'] != 'harness_queued' or task['cancel_requested']:
                _fail('task_not_executable', 409)
            old = con.execute('SELECT * FROM claims WHERE task_id=?', (task_id,)).fetchone()
            if old is not None and old['expires'] > now:
                if (old['node'], old['executor'], old['mode']) != (node, executor, 'execute'):
                    _fail('claim_busy', 409)
                claim_id, epoch, expires = old['id'], old['epoch'], old['expires']
            else:
                claim_id, epoch, expires = _id('claim'), 1 if old is None else old['epoch']+1, now+3600
                con.execute('INSERT OR REPLACE INTO claims VALUES(?,?,?,?,?,?,?)',
                            (task_id, claim_id, epoch, node, executor, 'execute', expires))
            return {'claim_id': claim_id, 'epoch': epoch, 'mode': 'execute', 'attempt_id': task['attempt_id'],
                    'expires_at': datetime.fromtimestamp(expires, tz=timezone.utc).isoformat()}

    def start(self, scope, task_id, claim_id, node, executor):
        with self._transaction() as con:
            self._harness_node(con, scope, node, executor)
            task = self._scoped(con, 'tasks', task_id, scope)
            self._harness_owner(scope, json.loads(task['payload']))
            claim = con.execute('SELECT * FROM claims WHERE task_id=?', (task_id,)).fetchone()
            if claim is None or (claim['id'], claim['node'], claim['executor']) != (claim_id, node, executor):
                _fail('stale_claim', 409)
            now = self.clock()
            if claim['expires'] <= now:
                _fail('claim_expired', 409)
            attempt = con.execute('SELECT id,start_grant_id FROM attempts WHERE task_id=?', (task_id,)).fetchone()
            if attempt['start_grant_id']:
                return {'start_grant_id': attempt['start_grant_id'], 'attempt_id': attempt['id'], 'may_execute': False}
            if task['state'] != 'harness_queued' or task['cancel_requested'] or claim['mode'] != 'execute':
                _fail('task_not_executable', 409)
            if task['deadline'] <= now:
                _fail('task_expired', 409)
            if con.execute("""SELECT 1 FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.id!=?
                    AND a.start_grant_id IS NOT NULL AND t.state NOT IN ('expired','harness_terminal') LIMIT 1""",
                           (task_id,)).fetchone():
                _fail('physical_slot_busy', 409)
            grant = _id('grant')
            con.execute('UPDATE attempts SET start_grant_id=?,original_node=?,original_executor=?,started=? WHERE id=?',
                        (grant, node, executor, now, attempt['id']))
            con.execute("UPDATE tasks SET state='running' WHERE id=?", (task_id,))
            con.execute("UPDATE harness_tasks SET liveness='may_be_running' WHERE task_id=?", (task_id,))
            self._append_harness_status(con, task_id, code='START_GRANTED')
            self._audit(con, scope, 'start_granted', task_id=task_id, attempt_id=attempt['id'])
            return {'start_grant_id': grant, 'attempt_id': attempt['id'], 'may_execute': True}

    def cancel_task(self, scope, task_id):
        self._harness_scope(scope)
        with self._transaction() as con:
            return self._cancel_harness(con, scope, self._scoped(con, 'tasks', task_id, scope))
