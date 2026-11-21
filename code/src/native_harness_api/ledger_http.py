"""The existing Native source and node routes served by the Native ledger."""
import hmac
import re

from .errors import ChannelError
from .http_server import bearer
from .ledger_events import MAX_EVENT_WIRE_BYTES

ID = r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}'
SOURCE = '/bridge/v1'
NODE = '/bridge/node/v1'
EVENTS = re.compile(SOURCE + rf'/tasks/({ID})/harness-events\?after=(0|[1-9][0-9]{{0,15}})&limit=([1-8])')


class LedgerHTTP:
    max_body_bytes = MAX_EVENT_WIRE_BYTES + 4096

    def __init__(self, store, source_token, node_token):
        if (source_token == node_token or any(type(token) is not str or not 16 <= len(token) <= 4096
                or any(not 33 <= ord(char) <= 126 for char in token) for token in (source_token, node_token))):
            raise ValueError('Distinct ledger credentials required')
        self.store = store
        self.source_token = source_token
        self.node_token = node_token

    def accepts_query(self, method, path):
        return method == 'GET' and EVENTS.fullmatch(path) is not None

    def handle(self, method, path, headers, payload):
        token = bearer(headers)
        if path.startswith(NODE+'/'):
            if not hmac.compare_digest(token, self.node_token):
                raise ChannelError('unauthorized', 401)
            value = self._node(method, path[len(NODE):], payload)
        elif path.startswith(SOURCE+'/'):
            if not hmac.compare_digest(token, self.source_token):
                raise ChannelError('unauthorized', 401)
            value = self._source(method, path, payload)
        else:
            raise ChannelError('not_found', 404)
        return 200, value, {}

    def _source(self, method, path, payload):
        store = self.store
        scope = store.scope
        if method == 'GET':
            if path == SOURCE+'/capabilities':
                return store.capabilities(scope)
            match = EVENTS.fullmatch(path)
            if match:
                task, after, limit = match.groups()
                return store.harness_events(scope, task, after=int(after), limit=int(limit))
            match = re.fullmatch(SOURCE+rf'/tasks/by-origin/({ID})', path)
            if match:
                return store.get_task_by_origin(scope, match[1])
            match = re.fullmatch(SOURCE+rf'/tasks/({ID})', path)
            if match:
                return store.get_task(scope, match[1])
        elif method == 'POST':
            if path == SOURCE+'/tasks':
                return store.create_harness_task(scope, payload)
        raise ChannelError('not_found', 404)

    def _node(self, method, path, payload):
        store = self.store
        scope = store.scope
        node, executor = store.harness.node_id, store.harness.executor_instance
        if method == 'GET':
            if path == '/tasks/next':
                return store.next_task(scope, node, executor)
            match = re.fullmatch(rf'/tasks/({ID})', path)
            if match:
                return store.get_task(scope, match[1])
        elif method == 'POST':
            if path == '/harness-heartbeat':
                return store.report_harness_heartbeat(scope, node, executor, payload)
            match = re.fullmatch(rf'/tasks/({ID})/([a-z-]+)', path)
            if match:
                task, action = match.groups()
                if action == 'claim':
                    if payload != {'mode':'execute'}:
                        raise ChannelError('invalid_claim_mode', 422)
                    return store.claim(scope, task, node, executor)
                if action == 'start':
                    if type(payload) is not dict or set(payload) != {'claim_id'} or not isinstance(payload['claim_id'], str):
                        raise ChannelError('invalid_claim', 422)
                    return store.start(scope, task, payload['claim_id'], node, executor)
                if action == 'harness-event':
                    return store.report_harness_event(scope, task, node, executor, payload)
                if action == 'harness-observation':
                    return store.report_harness_observation(scope, task, node, executor, payload)
                if action == 'harness-result':
                    return store.report_harness_result(scope, task, node, executor, payload)
        raise ChannelError('not_found', 404)
