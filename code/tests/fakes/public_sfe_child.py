"""Self-authored outer wire peer. Never imports vendor or uses account services."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

MODE = os.environ.get('SFE_FIXTURE_MODE', 'success')
CURRENT = 'synthetic-a'
SESSION = 'synthetic-root'
META = {'codebuddy.ai/requestId': 'synthetic-run'}
FRAME_ERRORS = {
    'node_invalid_utf8': b'PRIVATE\xff',
    'node_invalid_json': b'{"PRIVATE":',
    'node_invalid_frame_structure': b'[{"jsonrpc":"2.0","result":"PRIVATE"}]',
    'node_invalid_jsonrpc_version': b'{"jsonrpc":"PRIVATE","result":{}}',
}

def emit(value):
    sys.stdout.write(json.dumps({'jsonrpc': '2.0', **value}, separators=(',', ':')) + '\n')
    sys.stdout.flush()

def catalog():
    return [{'configId': 'model', 'name': 'Model', 'type': 'select', 'currentValue': CURRENT,
             'options': [{'value': 'synthetic-a', 'name': 'A'}, {'value': 'synthetic-b', 'name': 'B'}]}]

def update(kind, **extra):
    emit({'method': 'session/update', 'params': {'sessionId': SESSION,
          'update': {'sessionUpdate': kind, '_meta': META, **extra}}})

ACCOUNT = {'state': 'authenticated', 'mode': 'managed',
           'account': {'id': 'synthetic-account', 'authMethodId': 'workbuddy-desktop'}}
PRODUCT = {'state': 'ready', 'mode': 'managed', 'generation': 1, 'revision': 'fixture',
           'productName': 'WorkBuddy', 'applicationName': 'WorkBuddy', 'productVersion': '5.9.3',
           'networkEnvironment': None, 'deploymentType': None, 'defaultFolderName': None,
           'freshness': 'fresh', 'links': {}, 'productFeatures': {}, 'featureToggles': {}}
NODE = MODE.startswith('node_')
if NODE:
    PRODUCT['generation'] = 2

prompt_seen = False
for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    if MODE == 'node_late_session' and method in ('session/set_config_option', 'session/prompt'):
        raise RuntimeError('EXECUTION_AFTER_DEADLINE')
    result = {}
    if method == 'initialize':
        if MODE == 'init_hang':
            time.sleep(30)
        result = {'protocolVersion': 1, 'serverInfo': {'name': '@genie/agent-harness', 'version': '0.2.0-beta.16'},
                  'capabilities': {}, 'authMethods': [{'description': None, 'methodId': 'workbuddy-desktop', 'name': 'TencentCloud', 'type': 'agent'}]}
    elif method == 'initialized':
        if NODE:
            emit({'method': 'account/updated', 'params': ACCOUNT})
            if MODE != 'node_product_timeout':
                emit({'method': 'product/updated', 'params': PRODUCT})
        continue
    elif method == 'product/read':
        result = PRODUCT
    elif method == 'account/read':
        result = ACCOUNT
        if MODE == 'anonymous':
            result = {'state': 'unauthenticated', 'mode': None, 'account': None}
    elif method == 'auth/status':
        result = {'authenticated': True}
    elif method == 'session/new':
        if MODE == 'node_session_timeout':
            continue
        if MODE == 'node_late_session':
            time.sleep(1.6)
        result = {'sessionId': SESSION, 'configOptions': catalog()}
    elif method == 'session/set_config_option':
        if MODE == 'node_model_timeout':
            continue
        if MODE != 'wrong_model':
            CURRENT = request['params']['value']
        result = {'configOptions': catalog()}
    elif method == 'session/prompt':
        if prompt_seen:
            raise RuntimeError('DUPLICATE_PROMPT')
        prompt_seen = True
        if MODE in FRAME_ERRORS:
            sys.stdout.buffer.write(FRAME_ERRORS[MODE] + b'\n')
            sys.stdout.buffer.flush()
            continue
        if MODE == 'node_rpc_error':
            emit({'id': request['id'], 'error': {'code': -32603, 'message': 'PRIVATE message', 'data': 'PRIVATE data'}})
            continue
        if MODE == 'node_missing_ack_fields':
            emit({'id': request['id'], 'result': {'messageId': 'PRIVATE input', '_meta': {}}})
            continue
        if MODE == 'node_control_notification':
            # Same number as our peer's prompt, in the opposite RPC direction.
            emit({'method': '$/cancel_request', 'params': {'requestId': request['id']}})
        if MODE == 'orphan':
            marker = Path.cwd() / 'synthetic-orphan.ready'
            subprocess.Popen([sys.executable, '-c', 'import pathlib,time,sys;pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(30)', str(marker)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
            until = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.01)
        if MODE == 'bad_frame':
            sys.stdout.write('{"jsonrpc":"2.0","id":1,"id":2}\n'); sys.stdout.flush()
        if MODE == 'oversized':
            sys.stdout.write('x' * 300000 + '\n'); sys.stdout.flush()
        if MODE == 'stderr_overflow':
            sys.stderr.write('PRIVATE' * 12000); sys.stderr.flush()
        emit({'method': 'session/update', 'params': {'sessionId': SESSION, 'update': {'sessionUpdate': 'usage_update', 'used': 0, 'size': 100}}})
        if MODE != 'ack_only':
            for _ in range(4000 if MODE == 'long_burst' else 1):
                update('agent_message_chunk', messageId='synthetic-answer', content={'type': 'text', 'text': 'x'*16 if MODE == 'long_burst' else 'synthetic answer'})
            update('state_update', state='idle', stopReason='end_turn')
        result = {'messageId': 'synthetic-input', '_meta': META}
    elif method == 'session/cancel':
        update('state_update', state='idle', stopReason='cancelled')
        continue
    elif method == 'session/close':
        if MODE == 'node_late_session' and request['params']['sessionId'] != SESSION:
            raise RuntimeError('CLOSED_WRONG_SESSION')
        if MODE == 'close_hang':
            continue
    else:
        raise RuntimeError('UNEXPECTED_METHOD')
    emit({'id': request['id'], 'result': result})
# No shim/close: actual stdin EOF is the only normal process shutdown signal.
if MODE == 'late_conflict':
    update('state_update', state='idle', stopReason='cancelled')
if MODE == 'shutdown_hang':
    time.sleep(30)
if MODE == 'exit_nonzero':
    sys.exit(7)
