"""Synthetic, local-only adapter for P1a tests. This is not WorkBuddy."""
from __future__ import annotations

from contextlib import contextmanager
import json
from pathlib import Path
import sqlite3
import time


class FakeHarness:
    """A durable synthetic native executor, never a production fallback.

    Its SQLite file represents native execution evidence, not a task queue.
    Reconcile only reads records. Even directly reusing a grant cannot execute
    its simulated side effect twice.
    """
    synthetic = True
    MODES = {'success', 'failure', 'approval', 'observation_error',
             'model_mismatch', 'empty', 'partial', 'unknown_liveness'}

    def __init__(self, root: Path, *, mode='success', delay=0,
                 text='Synthetic FakeHarness result'):
        if mode not in self.MODES or type(delay) not in (int, float) or not 0 <= delay <= 60:
            raise ValueError('Invalid synthetic adapter configuration')
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.db = self.root / 'fake-native.sqlite'
        self.mode, self.delay, self.text = mode, delay, text
        with self._connect() as con:
            con.execute('CREATE TABLE IF NOT EXISTS executions (grant_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, result TEXT)')

    @contextmanager
    def _connect(self):
        con = sqlite3.connect(self.db, timeout=5)
        con.execute('PRAGMA synchronous=FULL')
        try:
            with con:
                yield con
        finally:
            con.close()

    @property
    def start_count(self):
        with self._connect() as con:
            return con.execute('SELECT count(*) FROM executions').fetchone()[0]

    def start(self, task, grant):
        binding = task['request']['binding']
        if not binding['model_alias'].startswith('synthetic-') or not binding['native_model'].startswith('fake-'):
            raise ValueError('FakeHarness only accepts explicitly synthetic bindings')
        task_id = task['remote_task_id']
        grant_id = grant['start_grant_id']
        with self._connect() as con:
            # A uniqueness failure deliberately cannot cause another execution.
            con.execute('INSERT INTO executions VALUES(?,?,NULL)', (grant_id, task_id))
        if self.delay:
            time.sleep(self.delay)
        model = task['request']['binding']['native_model']
        if self.mode in {'approval', 'observation_error', 'partial'}:
            if self.mode == 'approval':
                return {'outcome': 'pending', 'liveness': 'waiting_approval',
                        'code': 'APPROVAL_PENDING'}
            # Neither partial text nor an exception proves a failed native turn.
            raise RuntimeError('Synthetic observation interruption')
        result = {
            'outcome': 'failed' if self.mode == 'failure' else 'succeeded',
            'liveness': 'unknown' if self.mode == 'unknown_liveness' else 'stopped_confirmed',
            'output_blocks': [] if self.mode in {'failure', 'empty'} else [
                {'type': 'text', 'text': self.text}],
            'native_completion': {'confirmed': True},
            'model_evidence': {'selected': model, 'reported':
                               'synthetic-wrong-model' if self.mode == 'model_mismatch' else model},
            'usage': {'state': 'unknown'},
        }
        if self.mode == 'failure':
            result['code'] = 'NATIVE_FAILED'
        with self._connect() as con:
            con.execute('UPDATE executions SET result=? WHERE grant_id=?',
                        (json.dumps(result, allow_nan=False), grant_id))
        return result

    def reconcile(self, task, grant):
        """Read existing evidence only; never start or resume inference."""
        with self._connect() as con:
            row = con.execute('SELECT task_id,result FROM executions WHERE grant_id=?',
                              (grant['start_grant_id'],)).fetchone()
        if row is None or row[0] != task['remote_task_id'] or row[1] is None:
            return None
        return json.loads(row[1])
