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

def emit(value):
    sys.stdout.write(json.dumps({'jsonrpc': '2.0', **value}, separators=(',', ':')) + '\n')
    sys.stdout.flush()

def catalog():
    return [{'configId': 'model', 'name': 'Model', 'type': 'select', 'currentValue': CURRENT,
             'options': [{'value': 'synthetic-a', 'name': 'A'}, {'value': 'synthetic-b', 'name': 'B'}]}]

def update(kind, **extra):
    emit({'method': 'session/update', 'params': {'sessionId': SESSION,
          'update': {'sessionUpdate': kind, '_meta': META, **extra}}})

for line in sys.stdin:
    request = json.loads(line)
    method = request.get('method')
    result = {}
    if method == 'initialize':
        if MODE == 'init_hang':
            time.sleep(30)
        result = {'protocolVersion': 1, 'serverInfo': {'name': '@genie/agent-harness', 'version': '0.2.0-beta.16'},
                  'capabilities': {}, 'authMethods': [{'description': None, 'methodId': 'workbuddy-desktop', 'name': 'TencentCloud', 'type': 'agent'}]}
    elif method == 'initialized':
        continue
    elif method == 'product/read':
        result = {'state': 'ready', 'mode': 'managed', 'generation': 1, 'revision': 'fixture',
                  'productName': 'WorkBuddy', 'applicationName': 'WorkBuddy', 'productVersion': '5.9.3',
                  'networkEnvironment': None, 'deploymentType': None, 'defaultFolderName': None,
                  'freshness': 'fresh', 'links': {}, 'productFeatures': {}, 'featureToggles': {}}
    elif method == 'account/read':
        result = {'state': 'authenticated', 'mode': 'managed', 'account': {'id': 'synthetic-account', 'authMethodId': 'workbuddy-desktop'}}
        if MODE == 'anonymous':
            result = {'state': 'unauthenticated', 'mode': None, 'account': None}
    elif method == 'auth/status':
        result = {'authenticated': True}
    elif method == 'session/new':
        result = {'sessionId': SESSION, 'configOptions': catalog()}
    elif method == 'session/set_config_option':
        if MODE != 'wrong_model':
            CURRENT = request['params']['value']
        result = {'configOptions': catalog()}
    elif method == 'session/prompt':
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
            update('agent_message_chunk', messageId='synthetic-answer', content={'type': 'text', 'text': 'synthetic answer'})
            update('state_update', state='idle', stopReason='end_turn')
        result = {'messageId': 'synthetic-input', '_meta': META}
    elif method == 'session/cancel':
        update('state_update', state='idle', stopReason='cancelled')
        continue
    elif method == 'session/close':
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
