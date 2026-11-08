"""IO-free fixed Codex App Server protocol; no credentials, processes or retries."""
import hashlib
import json
import time
from .beta15_session import decode_frame
from .contracts import MAX_RESULT_BYTES


def account_identity_digest(account):
    if not isinstance(account, dict) or account.get('type') != 'chatgpt':
        raise ValueError('CODEX_ACCOUNT_REQUIRED')
    identity = {k: account.get(k) for k in ('type', 'email', 'workspaceName')}
    if any(v is not None and not isinstance(v, str) for v in identity.values()):
        raise ValueError('CODEX_ACCOUNT_INVALID')
    return hashlib.sha256(json.dumps(identity, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode()).hexdigest()


class CodexSession:
    def __init__(self, *, deadline, expected_account_digest, on_event=None, clock=time.monotonic):
        self.deadline, self.clock = deadline, clock
        self.expected_account_digest, self.on_event = expected_account_digest, on_event
        self.phase = 'new'
        self.thread_id = self.turn_id = self.model = None
        self.model_provider = None
        self.fault = None
        self.protocol_ready = self.account_verified = self.model_confirmed = False
        self.terminal = False
        self.outcome = None
        self.text = ''
        self._pending = {}
        self._next = 1
        self._items = {}
        self._completed_items = set()
        self.workspace = None
        self._early = []
        self._count = self._bytes = 0
        self._cancelled = False
        self._requires_action = False

    @property
    def terminal_observed(self):
        return self.terminal

    @property
    def ready(self):
        return self.protocol_ready and self.account_verified and self.fault is None

    def fail(self, reason='transport'):
        self.fault = self.fault or reason
        self.phase = 'indeterminate'
        return ()

    def tick(self):
        if not self.terminal and self.clock() >= self.deadline:
            self.fail('timeout')
        return self.fault is None

    def _frame(self, value):
        raw = json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(',', ':')).encode() + b'\n'
        if len(raw) > 262144:
            raise ValueError('frame_limit')
        return raw

    def _request(self, method, params):
        number = self._next
        self._next += 1
        self._pending[number] = method
        return self._frame(dict(id=number, method=method, params=params))

    def start(self):
        if self.phase != 'new' or not self.tick():
            return self.fail('protocol')
        self.phase = 'initializing'
        return (self._request('initialize', {'clientInfo': {'name': 'native-harness-api', 'title': 'Native Harness API', 'version': '0.3'}}),)

    def new_session(self, workspace, model_id):
        if not self.ready or self.phase != 'ready' or not self.tick():
            return self.fail('protocol')
        self.model = model_id
        self.workspace = workspace
        self.phase = 'thread_starting'
        return (self._request('thread/start', dict(cwd=workspace, model=model_id, modelProvider='openai', sandbox='read-only', approvalPolicy='on-request')),)

    def prompt(self, text):
        if not self.ready or self.phase != 'thread_ready' or not self.tick():
            return self.fail('protocol')
        self.phase = 'turn_starting'
        return (self._request('turn/start', {'threadId': self.thread_id, 'input': [{'type': 'text', 'text': text, 'text_elements': []}]}),)

    def cancel(self):
        self._cancelled = True
        if self.turn_id and not self.terminal and 'turn/interrupt' not in self._pending.values():
            return (self._request('turn/interrupt', dict(threadId=self.thread_id, turnId=self.turn_id)),)
        return ()

    def _emit(self, event):
        if self.on_event is not None and self.on_event(event) is False:
            raise ValueError('output_fault')

    def _snapshot(self):
        items = list(self._items.values())
        final = [x for x in items if x['phase'] == 'final_answer']
        selected = final or [x for x in items if x['phase'] != 'commentary']
        text = '\n\n'.join(x['text'] for x in selected)
        if len(text.encode('utf8')) > MAX_RESULT_BYTES:
            raise ValueError('output_limit')
        if text != self.text:
            self.text = text
            self._emit({'type': 'text_snapshot', 'text': text})

    def _item(self, item):
        if item.get('type') != 'agentMessage':
            return
        key, text, phase = item['id'], item['text'], item.get('phase')
        if not isinstance(key, str) or not key or not isinstance(text, str) or phase not in (None, 'commentary', 'partial_answer', 'final_answer'):
            raise ValueError('item_invalid')
        self._items[key] = {'text': text, 'phase': phase}
        self._snapshot()
        if phase == 'commentary':
            self._emit({'type': 'native_status', 'state': 'running'})

    def _notification(self, method, p):
        if method == 'account/updated':
            raise ValueError('account_changed')
        relevant = method in ('item/agentMessage/delta', 'item/started', 'item/completed', 'turn/started', 'turn/completed', 'model/rerouted')
        if not relevant:
            return
        if p.get('threadId') != self.thread_id or not self.thread_id:
            raise ValueError('thread_mismatch')
        if self.phase == 'turn_starting':
            if len(self._early) >= 64:
                raise ValueError('early_limit')
            self._early.append((method, p))
            return
        turn = p.get('turn', {})
        turn_id = turn.get('id') if method in ('turn/started', 'turn/completed') else p.get('turnId')
        if not self.turn_id or turn_id != self.turn_id:
            raise ValueError('turn_mismatch')
        if self.terminal:
            raise ValueError('post_terminal_event')
        if method == 'model/rerouted':
            raise ValueError('model_rerouted')
        if method == 'item/agentMessage/delta':
            key, delta = p['itemId'], p['delta']
            if not isinstance(key, str) or not key or not isinstance(delta, str):
                raise ValueError('delta_invalid')
            if key in self._completed_items:
                raise ValueError('completed_item_delta')
            item = self._items.setdefault(key, {'text': '', 'phase': None})
            item['text'] += delta
            self._snapshot()
        elif method in ('item/started', 'item/completed'):
            if p['item'].get('type') == 'agentMessage':
                key = p['item']['id']
                if key in self._completed_items:
                    raise ValueError('completed_item_event')
                self._item(p['item'])
                if method == 'item/completed':
                    self._completed_items.add(key)
        elif method == 'turn/completed':
            status = turn['status']
            if status not in ('completed', 'failed', 'interrupted'):
                raise ValueError('terminal_invalid')
            for item in turn.get('items', []):
                self._item(item)
            self.terminal = True
            self.phase = 'terminal'
            self.outcome = {'completed': 'succeeded', 'failed': 'failed', 'interrupted': 'cancelled'}[status]
            if status == 'completed' and (not self.text.strip() or self._requires_action or turn.get('error') is not None):
                raise ValueError('completion_invalid')
            self._emit({'type': 'native_status', 'state': 'idle'})
        else:
            self._emit({'type': 'native_status', 'state': 'running'})

    def receive(self, raw):
        try:
            if self.fault or not self.tick():
                return ()
            self._count += 1
            self._bytes += len(raw)
            if self._count > 4096 or self._bytes > 16 * 1024 * 1024:
                raise ValueError('evidence_limit')
            v = decode_frame(raw)
            if 'method' in v:
                method, p = v['method'], v.get('params', {})
                if 'id' in v:
                    if self.terminal or p.get('threadId') != self.thread_id or p.get('turnId') != self.turn_id or not self.turn_id:
                        raise ValueError('interaction_ownership')
                    self._requires_action = True
                    self._emit({'type': 'native_status', 'state': 'requires_action'})
                    if method in ('item/commandExecution/requestApproval', 'item/fileChange/requestApproval'):
                        reply = self._frame({'id': v['id'], 'result': {'decision': 'cancel'}})
                    else:
                        reply = self._frame({'id': v['id'], 'error': {'code': -32601, 'message': 'Interactive method unsupported'}})
                    return (reply, *self.cancel())
                self._notification(method, p)
                return ()
            number = v.get('id')
            if type(number) is not int or number not in self._pending or 'error' in v:
                raise ValueError('response_invalid')
            method = self._pending.pop(number)
            r = v['result']
            if method == 'initialize':
                if not isinstance(r, dict) or not all(isinstance(r.get(k), str) for k in ('userAgent', 'codexHome', 'platformFamily', 'platformOs')):
                    raise ValueError('initialize_invalid')
                self.protocol_ready = True
                self.phase = 'account_checking'
                return (self._frame({'method': 'initialized'}), self._request('account/read', {'refreshToken': False}))
            if method == 'account/read':
                if account_identity_digest(r['account']) != self.expected_account_digest:
                    raise ValueError('account_mismatch')
                self.account_verified = True
                self.phase = 'ready'
            elif method == 'thread/start':
                if r['cwd'] != self.workspace or r['model'] != self.model or r['modelProvider'] != 'openai' or r['approvalPolicy'] != 'on-request' or r['sandbox']['type'] != 'readOnly':
                    raise ValueError('thread_configuration_mismatch')
                self.thread_id = r['thread']['id']
                if not isinstance(self.thread_id, str) or not self.thread_id:
                    raise ValueError('thread_invalid')
                self.model_provider = r['modelProvider']
                self.model_confirmed = True
                self.phase = 'thread_ready'
            elif method == 'turn/start':
                self.turn_id = r['turn']['id']
                if not isinstance(self.turn_id, str) or not self.turn_id:
                    raise ValueError('turn_invalid')
                self.phase = 'running'
                for event in self._early:
                    self._notification(*event)
                self._early.clear()
                if self._cancelled:
                    return self.cancel()
            return ()
        except Exception as exc:
            return self.fail(str(exc) if isinstance(exc, ValueError) else 'protocol')

    def evidence(self):
        return dict(protocol_ready=self.protocol_ready, account_verified=self.account_verified,
                    model_confirmed=self.model_confirmed, model=self.model, model_provider=self.model_provider,
                    thread_id=self.thread_id, turn_id=self.turn_id, terminal_observed=self.terminal,
                    outcome=self.outcome, text=self.text, fault=self.fault, phase=self.phase,
                    requires_action=self._requires_action)


def project_result(value, model):
    """Codex completion requires owned cleanup, not a natural exit code."""
    pending = {'outcome': 'pending', 'liveness': 'unknown', 'code': 'EXECUTION_UNCERTAIN'}
    if not isinstance(value, dict) or value.get('fault') is not None:
        return pending
    s, cleanup = value.get('session'), value.get('cleanup')
    if not isinstance(s, dict) or not isinstance(cleanup, dict):
        return pending
    if (s.get('fault') is not None or any(s.get(k) is not True for k in ('protocol_ready', 'account_verified', 'model_confirmed', 'terminal_observed'))
            or s.get('model') != model or s.get('model_provider') != 'openai'
            or value.get('cleanup_confirmed') is not True or value.get('forced_before_terminal') is not False
            or any(cleanup.get(k) is not True for k in ('host_exited', 'owned_job_empty', 'handles_closed', 'cleanup_complete', 'io_threads_stopped'))):
        return pending
    outcome, text = s.get('outcome'), s.get('text')
    if outcome not in ('succeeded', 'failed', 'cancelled') or not isinstance(text, str):
        return pending
    if len(text.encode('utf8')) > MAX_RESULT_BYTES or outcome == 'succeeded' and (not text.strip() or s.get('requires_action')):
        return pending
    result = dict(outcome=outcome, liveness='stopped_confirmed', output_blocks=[{'type': 'text', 'text': text}] if outcome == 'succeeded' else [],
                  native_completion={'confirmed': True}, model_evidence={'selected': model, 'reported': model}, usage={'state': 'unknown'})
    if outcome == 'failed':
        result['code'] = 'NATIVE_FAILED'
    return result
