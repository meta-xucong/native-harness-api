"""Self-authored credential-free peer; forbidden execution methods are fatal."""
import json
import os
import sys
MODE=os.environ.get('MODELS_FIXTURE','success')
def send(value):
    sys.stdout.write(json.dumps({'jsonrpc':'2.0',**value})+'\n');sys.stdout.flush()
for line in sys.stdin:
    v=json.loads(line);m=v.get('method')
    if m=='initialize':r={'protocolVersion':1,'serverInfo':{'name':'@genie/agent-harness','version':'synthetic'},'capabilities':{},'authMethods':[{'type':'agent','methodId':'workbuddy-desktop','name':'TencentCloud','description':None}]}
    elif m=='initialized':continue
    elif m=='product/read':r={'state':'ready','mode':'managed','generation':1,'revision':'synthetic','productName':'WorkBuddy','applicationName':'WorkBuddy','productVersion':'5.9.3','networkEnvironment':None,'deploymentType':None,'defaultFolderName':None,'freshness':'fresh','links':{},'productFeatures':{},'featureToggles':{}}
    elif m=='account/read':r={'state':'authenticated','mode':'managed','account':{'id':'synthetic-account','authMethodId':'workbuddy-desktop'}}
    elif m=='auth/status':r={'authenticated':True}
    elif m=='session/new':
        options=[{'value':'synthetic-a','name':'Synthetic A'},{'value':'synthetic-b','name':'Synthetic B'}]
        if MODE=='malformed':options[0]['credential']='PRIVATE_SECRET'
        r={'sessionId':'synthetic-session','configOptions':[{'configId':'model','name':'Model','type':'select','currentValue':'synthetic-a','options':options}]}
        if MODE=='interaction':send({'id':80,'method':'elicitation/create','params':{'url':'PRIVATE_SECRET'}})
    elif m=='session/close':
        if MODE=='close_hang':continue
        r={}
    elif 'result' in v:continue
    else:raise RuntimeError('FORBIDDEN_METHOD')
    send({'id':v['id'],'result':r})
if MODE=='late':send({'method':'account/updated','params':{'state':'unauthenticated','mode':None,'account':None}})
if MODE=='nonzero':sys.exit(7)
