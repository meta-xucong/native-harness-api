"""Self-authored JSON-RPC child, never a vendor executable or model."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

mode = os.environ.get('CODEX_FIXTURE_MODE', 'natural')
workspace = None

def send(value):
    print(json.dumps(value), flush=True)

def note(method, **params):
    send(dict(method=method, params=dict(threadId='thread', **params)))

for line in sys.stdin:
    request = json.loads(line)
    method, number = request['method'], request.get('id')
    if method == 'initialize':
        result = dict(userAgent='fixture', codexHome='fixture', platformFamily='fixture', platformOs='fixture')
    elif method == 'initialized':
        continue
    elif method == 'account/read':
        result = dict(account=dict(type='chatgpt', email='fixture@example.test', workspaceName=None, planType='plus'), requiresOpenaiAuth=True)
    elif method == 'thread/start':
        workspace = request['params']['cwd']
        result = dict(thread={'id': 'thread'}, model=request['params']['model'], modelProvider='openai', cwd=workspace, approvalPolicy='on-request', sandbox={'type': 'readOnly', 'networkAccess': False})
    elif method == 'turn/start':
        if mode == 'descendant':
            marker = Path.cwd() / 'codex-descendant.ready'
            subprocess.Popen([sys.executable, '-c', 'import pathlib,time,sys;pathlib.Path(sys.argv[1]).write_text("ready");time.sleep(60)', str(marker)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             creationflags=getattr(subprocess, 'DETACHED_PROCESS', 0))
            until = time.monotonic() + 5
            while not marker.exists() and time.monotonic() < until:
                time.sleep(.01)
            if not marker.exists():
                raise RuntimeError('descendant failed to become ready')
        send(dict(id=number, result={'turn': {'id': 'turn', 'status': 'inProgress'}}))
        if mode != 'preterminal_hang':
            note('item/started', turnId='turn', item=dict(type='agentMessage', id='answer', text='', phase='final_answer'))
            note('item/agentMessage/delta', turnId='turn', itemId='answer', delta='fixture answer')
            note('item/completed', turnId='turn', item=dict(type='agentMessage', id='answer', text='fixture answer', phase='final_answer'))
            note('turn/completed', turn=dict(id='turn', status='completed', items=[], itemsView='partial', error=None))
        continue
    elif method == 'turn/interrupt':
        result = {}  # acknowledgement deliberately does not prove termination
    else:
        raise RuntimeError('unexpected fixture method')
    send(dict(id=number, result=result))

if mode == 'late_conflict':
    note('turn/completed', turn=dict(id='turn', status='failed', items=[], error=None))
if mode in ('idle_hang', 'preterminal_hang'):
    time.sleep(60)
