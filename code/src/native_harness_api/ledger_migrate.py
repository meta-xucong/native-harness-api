"""Import settled Harness history from local Bridge and Node SQLite snapshots."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from pydantic import ValidationError

from .ledger_contract import HarnessCreate, digest
from .ledger_store import NativeLedger, ledger_identity


def read_snapshot(path):
    return closing(sqlite3.connect(Path(path).resolve(strict=True).as_uri()+'?mode=ro', uri=True))


def _copy_rows(source, target, table, key, values):
    # Table and column names are fixed by the target schema. Fetch from the same
    # snapshot for every table and keep source row values byte-for-byte.
    columns = [row[1] for row in target.execute(f'PRAGMA table_info({table})')]
    fields = ','.join(columns)
    marks = ','.join('?' for _ in columns)
    for value in values:
        rows = source.execute(f'SELECT {fields} FROM {table} WHERE {key}=?', (value,))
        target.executemany(f'INSERT INTO {table}({fields}) VALUES({marks})', rows)


def migrate_settled(bridge_snapshot, journal_snapshot, destination, config):
    if config.accepting_new:
        raise ValueError('Import requires closed admission')
    expected = ledger_identity(config)
    with read_snapshot(bridge_snapshot) as source, read_snapshot(journal_snapshot) as journal:
        source.row_factory = sqlite3.Row
        source.execute('BEGIN')
        journal.execute('BEGIN')
        if any(con.execute('PRAGMA quick_check').fetchone()[0]!='ok' for con in (source, journal)):
            raise ValueError('Source snapshot integrity check failed')
        row = source.execute('SELECT ledger_instance,source,workspace,node,executor FROM harness_instance WHERE singleton=1').fetchone()
        if row is None or tuple(row)!=expected:
            raise ValueError('Bridge deployment identity mismatch')
        node = source.execute('SELECT active FROM node_registry WHERE source=? AND workspace=? AND node=? AND executor=?',
                              expected[1:]).fetchone()
        if node is None or node['active']!=1:
            raise ValueError('Original Node is not provisioned and active')
        tasks = source.execute('''SELECT t.*,h.outcome,h.liveness,h.result,h.result_digest,h.result_expired,
                   a.id AS actual_attempt,a.start_grant_id,a.original_node,a.original_executor,
                   c.id AS claim_id,c.epoch,e.terminal_seq,e.expired AS events_expired
            FROM tasks t LEFT JOIN harness_tasks h ON h.task_id=t.id LEFT JOIN attempts a ON a.task_id=t.id
            LEFT JOIN claims c ON c.task_id=t.id LEFT JOIN harness_event_state e ON e.task_id=t.id
            WHERE t.source=? AND t.workspace=? ORDER BY t.id''', expected[1:3])
        records = {task_id: json.loads(record) for task_id,record in journal.execute('SELECT task_id,record FROM records')}
        task_ids = []
        for task in tasks:
            try:
                payload = HarnessCreate.model_validate_json(task['payload']).model_dump()
            except ValidationError:
                raise ValueError('Source scope contains an incompatible Harness payload') from None
            identity = payload['identity']
            task_id = task['id']
            if (payload['ledger_instance']!=expected[0] or identity['source']!=expected[1]
                    or (identity['tenant'],identity['principal']) not in config.allowed_owners
                    or payload['origin_key']!=task['origin_key'] or payload['request_digest']!=task['digest']
                    or task['attempt_id']!=task['actual_attempt'] or task['state'] not in ('expired','harness_terminal')
                    or task['outcome'] not in ('succeeded','failed','cancelled','expired')
                    or task['liveness'] not in ('stopped_confirmed','not_started') or task['terminal_seq'] is None):
                raise ValueError('Bridge scope has an unsettled or incompatible task')
            result = None if task['result'] is None else json.loads(task['result'])
            if (result is None and not (task['result_expired'] and task['events_expired'] and task['result_digest'])
                    or result is not None and (task['result_expired'] or digest(result)!=task['result_digest'])):
                raise ValueError('Original terminal result digest mismatch')
            record = records.pop(task_id, None)
            if task['start_grant_id']:
                if (not record or record.get('acknowledged') is not True or record.get('liveness')!='stopped_confirmed'
                        or record.get('task_id')!=task_id or record.get('attempt_id')!=task['attempt_id']
                        or record.get('claim_id')!=task['claim_id'] or record.get('epoch')!=task['epoch']
                        or (task['original_node'],task['original_executor'])!=expected[3:]
                        or (record.get('grant') or {}).get('start_grant_id')!=task['start_grant_id']
                        or digest(record.get('result'))!=task['result_digest']
                        or any(record.get('task',{}).get(key)!=value for key,value in payload.items())):
                    raise ValueError('Original journal does not agree with the settled grant')
            elif record is not None:
                raise ValueError('Original journal has an unresolved pre-start record')
            task_ids.append(task_id)
        if records:
            raise ValueError('Original journal has tasks outside the imported scope')

        def populate(target):
            for table,key in (('tasks','id'),('attempts','task_id'),('claims','task_id'),('harness_tasks','task_id'),
                              ('harness_event_state','task_id'),('harness_events','task_id'),('audit_events','task_id')):
                _copy_rows(source,target,table,key,task_ids)

        return NativeLedger.initialize(destination,config,populate=populate)
