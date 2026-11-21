"""Synthetic subprocess ACP peer; no model, network or real tool execution."""
import json
from pathlib import Path
import sys
import time

mode, count_path = sys.argv[1:]
def send(value):
    print(json.dumps({'jsonrpc':'2.0', **value}), flush=True)
def update(kind, **fields):
    send({'method':'session/update','params':{'sessionId':'foreign' if mode == 'wrong_session' else 's1', 'update':{'sessionUpdate':kind, **fields}}})
def options(current='fake-a'):
    return [{'id':'model','category':'model','type':'select','currentValue':current,'options':[{'value':'fake-a','name':'A'},{'value':'fake-b','name':'B'}]}, {'id':'mode','category':'mode','type':'select','currentValue':'default','options':[{'value':'default','name':'Default'}]}]
for line in sys.stdin:
    req = json.loads(line); method=req.get('method'); rid=req.get('id'); params=req.get('params',{})
    if method == 'initialize':
        if mode == 'badjson':
            print('INVALID',flush=True);continue
        if mode == 'flood':
            print('x'*300000,flush=True);continue
        if mode == 'stderr':
            sys.stderr.write('x'*70000);sys.stderr.flush();time.sleep(.1)
        send({'id':rid,'result':{'protocolVersion':1,'agentInfo':{'name':'synthetic-acp-peer','version':'test'},'agentCapabilities':{},'authMethods':[]}})
    elif method == 'session/new':
        if mode == 'auth':send({'id':rid,'error':{'code':-32000,'message':'secret must not escape'}});continue
        update('available_commands_update',availableCommands=[])
        result={'sessionId':'s1','modes':{'currentModeId':'fullAccess' if mode=='unsafe' else 'default'},'models':{'currentModelId':'fake-a','availableModels':[{'modelId':'fake-a','name':'A'},{'modelId':'fake-b','name':'B'}]}}
        if mode.startswith('config'):result['configOptions']=options()
        send({'id':rid,'result':result})
    elif method in ('session/set_model','session/set_config_option'):
        selected=params.get('modelId',params.get('value'))
        if mode in ('ack_config_after','ack_config_before'):
            if mode == 'ack_config_before':update('config_option_update',configOptions=options(selected))
            send({'id':rid,'result':{'modelId':selected}})
            if mode == 'ack_config_after':update('config_option_update',configOptions=options(selected))
            continue
        if mode=='late':update('agent_message_chunk',content={'type':'text','text':'old'})
        if mode=='mismatch':selected='wrong'
        if mode in ('config','config_removed','config_ambiguous','config_mode_removed','config_mode_ambiguous','config_mode_changed'):send({'id':rid,'result':{'configOptions':options(selected)}})
        elif mode in ('config_empty','empty_model'):send({'id':rid,'result':{}})
        else:
            update('current_model_update',currentModelId=selected)
            send({'id':rid,'result':{}})
    elif method=='session/prompt':
        p=Path(count_path);p.write_text(str(int(p.read_text())+1) if p.exists() else '1')
        if mode=='timeout':time.sleep(4);continue
        if mode=='eof':sys.exit(0)
        if mode=='permission':
            send({'id':'permission1','method':'session/request_permission','params':{'sessionId':'s1','toolCall':{'title':'Synthetic tool'},'options':[{'optionId':'always','kind':'allow_always','name':'Always'},{'optionId':'once','kind':'allow_once','name':'Once'},{'optionId':'no','kind':'reject_once','name':'No'}]}})
            answer=json.loads(next(sys.stdin));Path(count_path+'.approval').write_text(json.dumps(answer))
        if mode=='config_mode_removed':update('config_option_update',configOptions=[options()[0]])
        if mode=='config_mode_ambiguous':update('config_option_update',configOptions=options()+[options()[1]])
        if mode=='config_mode_changed':
            changed=options();changed[1]['id']='replacement-mode';update('config_option_update',configOptions=changed)
        if mode=='config_removed':update('config_option_update',configOptions=[options()[1]])
        if mode=='config_ambiguous':update('config_option_update',configOptions=options()+[options()[0]])
        if mode=='changed':update('current_model_update',currentModelId='other')
        if mode != 'empty':update('agent_message_chunk',content={'type':'text','text':'synthetic answer'})
        send({'id':rid,'result':{'stopReason':'cancelled' if mode=='cancelled' else 'end_turn'}})
        if mode=='after_final':update('agent_message_chunk',content={'type':'text','text':'must not append'})
