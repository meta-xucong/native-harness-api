"""Ordinary provider protocol fixtures, not upstream or native-model evidence."""
import copy
from dataclasses import replace
from email.message import Message
import http.client
import json
import unittest
from urllib.parse import urlsplit
from native_harness_api.contracts import Binding
from native_harness_api.errors import ChannelError
from native_harness_api.facade import Service
from native_harness_api.openai_provider import OpenAIProvider
from tests.fakes.server import running
from tests.test_contracts import request, task_result

TOKEN = "fixture-provider-token"

class Bridge:
    ledger_instance = "ledger"
    def __init__(self):
        self.records = {}; self.submissions = self.lookups = 0
        self.ready = True; self.pages = []; self.lost_submit = False
    def lookup(self, origin):
        self.lookups += 1
        return copy.deepcopy(self.records.get(origin))
    def submit(self, value):
        self.submissions += 1
        task = dict(value, remote_task_id="task-" + str(self.submissions), created=1,
                    outcome="pending", liveness="running", result=None, result_expired=False)
        if self.ready: task["result"] = task_result()["result"]
        self.records[value["origin_key"]] = copy.deepcopy(task)
        if self.lost_submit: raise ChannelError("BRIDGE_TRANSPORT_UNCERTAIN")
        return task
    def task(self, task_id):
        return copy.deepcopy(next(v for v in self.records.values() if v["remote_task_id"] == task_id))
    def events(self, task_id, cursor):
        if self.pages:
            page = self.pages.pop(0)
            if any(e["type"] == "task_terminal" for e in page["events"]):
                for task in self.records.values(): task["result"] = task_result()["result"]
            return dict(page, remote_task_id=task_id, schema_version="native-events-v1")
        return {"schema_version":"native-events-v1", "remote_task_id":task_id, "events":[],
                "next_cursor":cursor, "has_more":False, "terminal_seq":None}

def page(*events, start=1, terminal=None, more=False):
    return {"events":[{"seq":i,"type":kind,"data":data} for i,(kind,data) in enumerate(events,start)],
            "next_cursor":start+len(events)-1,"terminal_seq":terminal,"has_more":more}

class ProviderTests(unittest.TestCase):
    def setUp(self):
        self.bridge = Bridge(); self.binding = Binding("binding",1,"synthetic-model","fake-v1")
        self.service = Service("source",self.bridge,{self.binding.model_alias:self.binding},True,("provider","resource"))
        self.provider = OpenAIProvider({TOKEN:self.service},mode="p1b_integration",wait_seconds=.01,poll_seconds=.001)
    def headers(self, key="operation", **extra):
        result = Message(); result["Authorization"] = "Bearer " + TOKEN
        if key is not None: result["Idempotency-Key"] = key
        for name,value in extra.items(): result[name] = value
        return result
    def call(self, body=None, key="operation", headers=None):
        return self.provider.handle("POST","/v1/chat/completions",self.headers(key) if headers is None else headers,request() if body is None else body)
    def stream(self, body=None, **kwargs):
        status,stream,_ = self.call(dict(request(),stream=True) if body is None else body,**kwargs)
        self.assertEqual(status,200)
        return b"".join(stream.frames).decode()
    def error(self, code, callback):
        with self.assertRaises(ChannelError) as caught: callback()
        self.assertEqual(caught.exception.code,code)
        return caught.exception
    def test_standard_bearer_only_and_models(self):
        status,value,_ = self.provider.handle("GET","/v1/models",self.headers(None),None)
        self.assertEqual(status,200)
        self.assertEqual(value["data"][0],{"id":"synthetic-model","object":"model","created":0,"owned_by":"native-harness"})
        self.assertEqual(self.call(key=None)[1]["choices"][0]["message"]["content"],"real synthetic output")
        self.assertNotIn("usage",self.call(key=None)[1])
    def test_key_required_before_bridge_io(self):
        self.error("UNAUTHORIZED",lambda:self.call(headers=Message()))
        bad=self.headers();bad.replace_header("Authorization","Bearer wrong")
        self.error("UNAUTHORIZED",lambda:self.call(headers=bad));self.assertEqual(self.bridge.lookups,0)
    def test_provider_owner_not_body_or_internal_headers(self):
        self.call(dict(request(),user="attacker"),headers=self.headers(**{"X-Native-Tenant":"attacker","X-Native-Principal":"attacker","X-Native-Operation":"attacker","X-Native-Source":"attacker","X-Native-Admission-Allowed":"false"}))
        task=next(iter(self.bridge.records.values()))
        self.assertEqual((task["identity"]["tenant"],task["identity"]["principal"]),self.service.owner)
        self.assertEqual(task["identity"]["source"],"source");self.assertNotIn("user",task["request"]["client_request"])
    def test_semantic_validation_before_bridge_io(self):
        for patch in ({"tools":[]},{"temperature":1},{"stream":1},{"n":True},
                      {"stream":True,"stream_options":{"include_usage":1}},
                      {"stream":True,"stream_options":{"unknown":True}},
                      {"stream_options":{"include_usage":True}},
                      {"messages":[{"role":"system","content":"hello"}]},
                      {"messages":[{"role":"user","content":"a"},{"role":"user","content":"b"}]}):
            with self.subTest(patch=patch),self.assertRaises(ChannelError): self.call(dict(request(),**patch))
        self.assertEqual(self.bridge.lookups,0)
    def test_unknown_model_and_routes_do_not_submit(self):
        self.error("MODEL_NOT_AVAILABLE",lambda:self.call(dict(request(),model="synthetic-missing")))
        for path in ("/v1/responses","/v1/native-harness/events"):
            self.error("NOT_FOUND",lambda:self.provider.handle("POST",path,self.headers(),request()))
        self.assertEqual(self.bridge.submissions,0)
    def test_same_key_replays_across_observation_preferences(self):
        value=self.call()[1]
        wire=self.stream(dict(request(),stream=True,stream_options={"include_usage":True},user="other"))
        self.assertIn(value["id"],wire);self.assertNotIn('"usage"',wire);self.assertIn("data: [DONE]",wire)
        self.assertEqual(self.bridge.submissions,1);self.assertEqual(self.call()[1],value)
    def test_same_key_changed_semantics_conflicts(self):
        self.call();changed=request();changed["messages"][0]["content"]="changed"
        self.error("IDEMPOTENCY_CONFLICT",lambda:self.call(changed));self.assertEqual(self.bridge.submissions,1)
    def test_no_key_same_prompt_two_operations(self):
        first,second=self.call(key=None)[1],self.call(key=None)[1]
        self.assertNotEqual(first["id"],second["id"]);self.assertEqual(self.bridge.submissions,2)
    def test_replay_before_binding_or_admission(self):
        value=self.call()[1];self.provider.services[TOKEN]=replace(self.service,bindings={},accepting_new=False)
        self.assertEqual(self.call()[1],value);self.assertIn("data: [DONE]",self.stream())
        self.error("NEW_ADMISSION_DISABLED",lambda:self.call(key="new"));self.assertEqual(self.bridge.submissions,1)
    def test_lost_submit_ack_never_retries(self):
        self.bridge.lost_submit=True
        self.assertEqual(self.error("EXECUTION_UNCERTAIN",self.call).status,409)
        self.assertEqual(self.call()[0],200);self.assertEqual(self.bridge.submissions,1)
    def test_wait_timeout_does_not_resubmit(self):
        self.bridge.ready=False
        self.assertEqual(self.error("TASK_WAIT_TIMEOUT",self.call).status,409)
        self.error("TASK_WAIT_TIMEOUT",self.call);self.assertEqual(self.bridge.submissions,1)
    def test_bad_duplicate_keys_precede_io(self):
        for key in ("","two words","é","x"*257):
            with self.subTest(key=key),self.assertRaises(ChannelError): self.call(key=key)
        headers=self.headers();headers["Idempotency-Key"]="second"
        self.error("INVALID_HEADER",lambda:self.call(headers=headers));self.assertEqual(self.bridge.lookups,0)
    def test_cursor_zero_readonly_positive_rejected(self):
        self.error("TASK_NOT_FOUND",lambda:self.call(dict(request(),stream=True),headers=self.headers(**{"Last-Event-ID":"0"})))
        self.error("STREAM_RESUME_NOT_SUPPORTED",lambda:self.call(dict(request(),stream=True),headers=self.headers(**{"Last-Event-ID":"1"})))
        self.assertEqual(self.bridge.submissions,0);self.call()
        self.assertIn("data: [DONE]",self.stream(headers=self.headers(**{"Last-Event-ID":"0"})))
    def test_append_pages_and_terminal_only_finish(self):
        self.bridge.ready=False
        self.bridge.pages=[page(("text_snapshot",{"text":"real "}),("text_snapshot",{"text":"real synthetic output"}),more=True),page(("task_terminal",{"outcome":"succeeded","liveness":"stopped_confirmed"}),start=3,terminal=3)]
        wire=self.stream();self.assertIn('"content":"real "',wire);self.assertIn('"content":"synthetic output"',wire)
        self.assertEqual(wire.count('"finish_reason":"stop"'),1);self.assertEqual(wire.count("data: [DONE]"),1)
        self.assertNotIn('"usage"',wire)
    def test_rewrite_errors_without_success(self):
        self.bridge.ready=False
        self.bridge.pages=[page(("text_snapshot",{"text":"original"}),("text_snapshot",{"text":"revision"}))]
        wire=self.stream();self.assertIn("NATIVE_STREAM_REWRITE_REQUIRED",wire)
        self.assertNotIn('"content":"revision"',wire);self.assertNotIn('"finish_reason":"stop"',wire)
        self.assertNotIn("[DONE]",wire);self.assertEqual(self.bridge.submissions,1)
    def test_final_rewrite_and_nonstream_authority(self):
        self.bridge.ready=False
        self.bridge.pages=[page(("text_snapshot",{"text":"wrong draft"}),more=True),page(("task_terminal",{"outcome":"succeeded","liveness":"stopped_confirmed"}),start=2,terminal=2)]
        wire=self.stream();self.assertIn("NATIVE_STREAM_REWRITE_REQUIRED",wire);self.assertNotIn("[DONE]",wire)
        self.assertEqual(self.call()[1]["choices"][0]["message"]["content"],"real synthetic output");self.assertEqual(self.bridge.submissions,1)
    def test_refusal_not_content_or_tool_call(self):
        self.call();next(iter(self.bridge.records.values()))["result"]["output_blocks"]=[{"type":"refusal","text":"Cannot do that."}]
        wire=self.stream();self.assertIn('"refusal":"Cannot do that."',wire);self.assertNotIn('"content"',wire)
        self.assertNotIn("tool_calls",wire);self.assertIn("[DONE]",wire)
    def test_invalid_terminal_or_usage_never_success(self):
        for change in ({"output_blocks":[]},{"native_completion":{"confirmed":False}},{"usage":{"state":"measured","total_tokens":0}}):
            with self.subTest(change=change):
                self.setUp();self.call();next(iter(self.bridge.records.values()))["result"].update(change)
                wire=self.stream();self.assertIn('"error"',wire);self.assertNotIn('"finish_reason":"stop"',wire);self.assertNotIn("[DONE]",wire)
    def test_idle_without_terminal_times_out(self):
        self.bridge.ready=False;self.bridge.pages=[page(("native_status",{"state":"idle","stop_reason":"end_turn"}))]
        wire=self.stream();self.assertIn("TASK_WAIT_TIMEOUT",wire);self.assertNotIn("[DONE]",wire);self.assertEqual(self.bridge.submissions,1)
    def test_changed_binding_during_observation_rejected(self):
        self.bridge.ready=False
        def mutated(task_id):
            value=Bridge.task(self.bridge,task_id);value["request"]["binding"]["native_model"]="fake-changed";return value
        self.bridge.task=mutated
        self.error("BRIDGE_BINDING_MISMATCH",self.call);self.assertEqual(self.bridge.submissions,1)
    def test_real_http_json_sse_headers(self):
        with running(self.provider) as url:
            address=urlsplit(url)
            for stream in (False,True):
                conn=http.client.HTTPConnection(address.hostname,address.port,timeout=2)
                conn.request("POST","/v1/chat/completions",json.dumps(dict(request(),stream=stream)),{"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json"})
                response=conn.getresponse();wire=response.read();self.assertEqual(response.status,200)
                if stream:
                    self.assertEqual(response.getheader("Content-Type"),"text/event-stream");self.assertIsNone(response.getheader("Content-Length"));self.assertIn(b"data: [DONE]",wire)
                else:self.assertEqual(json.loads(wire)["object"],"chat.completion")
                conn.close()
    def test_real_http_disconnect_does_not_resubmit(self):
        self.bridge.ready=False;self.provider.wait_seconds=1
        with running(self.provider) as url:
            address=urlsplit(url);conn=http.client.HTTPConnection(address.hostname,address.port,timeout=2)
            conn.request("POST","/v1/chat/completions",json.dumps(dict(request(),stream=True)),{"Authorization":"Bearer "+TOKEN,"Content-Type":"application/json","Idempotency-Key":"operation"})
            response=conn.getresponse();self.assertIn(b'"role":"assistant"',response.readline());response.close();conn.close()
            self.assertEqual(self.bridge.submissions,1)
            next(iter(self.bridge.records.values()))["result"]=task_result()["result"]
            self.assertEqual(self.call()[0],200);self.assertEqual(self.bridge.submissions,1)
