"""Experimental single-turn stdio ACP v1 client, not a model API adapter.

Launch identity/provenance belongs to the caller. No credentials are discovered,
no authentication is attempted, and host exit never proves descendant exit.
"""
from __future__ import annotations

import json
import math
from pathlib import Path
import queue
import subprocess
import threading
import time


class ACPError(RuntimeError):
    def __init__(self, code, message=None):
        self.code = code
        super().__init__(message or code)


class ACPClient:
    MAX_FRAME = 262144
    MAX_STDOUT = 4 * 1024 * 1024
    MAX_STDERR = 65536
    MAX_TEXT = 262144
    SAFE_MODES = frozenset({'default'})

    def __init__(self, command, cwd, env, timeout=120.0, permission_handler=None, *, supervised=False):
        if (not isinstance(command, list) or not command or
                any(not isinstance(x, str) or not x or '\x00' in x for x in command)):
            raise ValueError('Explicit argv required')
        if not isinstance(env, dict) or any(not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()):
            raise ValueError('Explicit environment required')
        if isinstance(timeout, bool) or not isinstance(timeout, (float, int)) or not math.isfinite(timeout) or not 0 < timeout <= 3600:
            raise ValueError('Invalid timeout')
        if type(supervised) is not bool:
            raise ValueError('Explicit supervision choice required')
        self.command, self.cwd, self.env = list(command), Path(cwd).resolve(strict=True), dict(env)
        if not self.cwd.is_dir():
            raise ValueError('Working directory required')
        self.timeout, self.permission_handler = timeout, permission_handler
        self._selection = None
        self._observed_model_config_id = None
        self._selection_lock = threading.Lock()
        self.process = None
        # The existing experimental probe retains its original host-only path.
        # Explicit supervision has no Popen or platform fallback and is not a
        # production admission switch. It never proves external work stopped.
        self.supervised = supervised
        self._transport = None
        self._start_attempted = False
        self._closing = False
        self._lifecycle_lock = threading.RLock()
        self._io_lock = threading.Lock()
        self._io_threads = []
        self.deadline = None
        self.frames = queue.Queue(maxsize=256)
        self.fault = None
        self.identity = None
        self.session_id = None
        self.session = None
        self.model_id = None
        self._model_source = None
        self._mode_sources = set()
        self.prompt_attempted = False
        self._counter = 0
        self._active_prompt = False
        self._text = []
        self._text_bytes = 0
        self._permissions = []
        self._permission_ids = set()
        self._pending_events = []
        self._creating_session = False
        self._prompt_lock = threading.Lock()

    def _remaining(self):
        if self.fault:
            raise ACPError(self.fault)
        if self._closing:
            raise ACPError('ACP_CLOSED')
        remaining = self.deadline - time.monotonic()
        if remaining <= 0:
            self.fault = 'ACP_TIMEOUT'
            raise ACPError(self.fault)
        return remaining

    def _reader(self, stream, stderr=False):
        total = 0
        try:
            while True:
                line = stream.readline(self.MAX_FRAME + 1) if not stderr else stream.read(4096)
                if not line:
                    if not stderr:
                        self.frames.put_nowait(None)
                    return
                total += len(line)
                if total > (self.MAX_STDERR if stderr else self.MAX_STDOUT):
                    self.fault = 'ACP_OUTPUT_LIMIT'
                    return
                if stderr:
                    continue  # Never persist or expose runtime stderr (may contain secrets).
                if len(line) > self.MAX_FRAME or not line.endswith(b'\n'):
                    self.fault = 'ACP_FRAME_LIMIT'
                    return
                self.frames.put_nowait(line)
        except queue.Full:
            self.fault = 'ACP_OUTPUT_LIMIT'
        except Exception:
            if not self._closing:
                self.fault = 'ACP_IO_ERROR'

    def _start_io(self, target, args=()):
        # Closing seals registration before taking its thread snapshot. There
        # must not be a late writer after cleanup was reported complete.
        with self._io_lock:
            if self._closing:
                raise ACPError('ACP_CLOSED')
            thread = threading.Thread(target=target, args=args, daemon=True)
            # Register BEFORE starting. An asynchronous exception can interrupt
            # Thread.start or the following confirmation; retain that ambiguity.
            record = {'thread': thread, 'start_confirmed': False}
            self._io_threads.append(record)
            thread.start()
            record['start_confirmed'] = True
        return thread

    def start(self):
        with self._lifecycle_lock:
            if self._start_attempted or self._closing:
                raise ACPError('ACP_ALREADY_STARTED')
            self._start_attempted = True
            self.deadline = time.monotonic() + self.timeout
            try:
                if self.supervised:
                    from .supervised_acp_transport import SupervisedACPTransport
                    self._transport = SupervisedACPTransport()
                    started = self._transport.start(self.command, self.cwd, self.env)
                    streams = self._transport
                else:
                    self.process = subprocess.Popen(self.command, cwd=self.cwd, env=self.env,
                        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        shell=False, bufsize=0)
                    streams = self.process
                    started = {'pid': self.process.pid, 'host_started': True}
                for stream, stderr in ((streams.stdout, False), (streams.stderr, True)):
                    self._start_io(self._reader, (stream, stderr))
            except BaseException:
                self.fault = 'ACP_LAUNCH_FAILED'
                self.close()
                raise ACPError(self.fault) from None
            return started

    def _bounded_call(self, callback, *, io=False):
        result = queue.Queue(maxsize=1)
        def run():
            try:
                result.put((True, callback()))
            except BaseException:
                result.put((False, None))
        if io:
            self._start_io(run)
        else:
            threading.Thread(target=run, daemon=True).start()
        try:
            ok, value = result.get(timeout=self._remaining())
        except queue.Empty:
            self.fault = 'ACP_TIMEOUT'
            raise ACPError(self.fault) from None
        self._remaining()
        if not ok:
            raise ACPError('ACP_CALLBACK_FAILED')
        return value

    def _send(self, frame):
        data = (json.dumps(frame, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()
        if len(data) > self.MAX_FRAME:
            raise ACPError('ACP_FRAME_LIMIT')
        def write():
            stream = self._transport.stdin if self.supervised else self.process.stdin
            view = memoryview(data)
            while view:
                count = stream.write(view)
                if not count:
                    raise OSError('closed')
                view = view[count:]
            stream.flush()
        self._bounded_call(write, io=True)

    def _next(self):
        while True:
            try:
                frame = self.frames.get(timeout=min(0.05, self._remaining()))
                break
            except queue.Empty:
                continue
        return self._decode(frame)

    def _decode(self, frame):
        if frame is None:
            raise ACPError('ACP_EOF')
        try:
            def pairs(items):
                obj = {}
                for key, value in items:
                    if key in obj:
                        raise ValueError()
                    obj[key] = value
                return obj
            value = json.loads(frame, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (ValueError, UnicodeError, RecursionError):
            raise ACPError('ACP_INVALID_JSON') from None
        if not isinstance(value, dict) or value.get('jsonrpc') != '2.0':
            raise ACPError('ACP_INVALID_FRAME')
        return value

    def _drain_controls(self):
        # Drain only frames already observed by our reader. This is NOT an atomic
        # barrier against agent updates still in flight; stdio has no such fence.
        while True:
            self._remaining()
            try:
                raw = self.frames.get_nowait()
            except queue.Empty:
                return
            frame = self._decode(raw)
            if 'method' not in frame:
                raise ACPError('ACP_UNEXPECTED_RESPONSE')
            self._event(frame)

    def _rpc(self, method, params, before_send=None):
        if self._closing or (self.process is None and self._transport is None) or self.prompt_attempted and method != 'session/prompt':
            raise ACPError('ACP_INVALID_STATE')
        self._counter += 1
        request_id = self._counter
        try:
            if before_send is not None:
                # Durable intent must commit synchronously under the caller's
                # OS lock/thread ownership. fsync cannot safely be canceled:
                # retain the lock until it returns, then enforce the deadline
                # before any prompt bytes. Never leave a late journal writer.
                try:
                    before_send()
                except Exception:
                    raise ACPError('ACP_CALLBACK_FAILED') from None
            self._remaining()
            if method == 'session/prompt':
                self._drain_controls()
                self._safe_mode()
                self._check_model()
                self._active_prompt = True
            self._send({'jsonrpc':'2.0', 'id':request_id, 'method':method, 'params':params})
            while True:
                frame = self._next()
                if 'method' in frame:
                    self._event(frame)
                    continue
                if type(frame.get('id')) is not int or frame['id'] != request_id:
                    raise ACPError('ACP_UNEXPECTED_RESPONSE')
                if 'error' in frame:
                    error = frame['error']
                    raise ACPError('ACP_AUTH_REQUIRED' if isinstance(error, dict) and error.get('code') == -32000 else 'ACP_REMOTE_ERROR')
                result = frame.get('result')
                if not isinstance(result, dict):
                    raise ACPError('ACP_INVALID_RESULT')
                return result
        except BaseException:
            self.fault = self.fault or 'ACP_SESSION_UNCERTAIN'
            self.close()  # Stop this owned host on ambiguity; never claim descendants stopped.
            raise

    def initialize(self):
        if self.identity is not None:
            raise ACPError('ACP_INVALID_STATE')
        value = self._rpc('initialize', {'protocolVersion':1,
            'clientCapabilities':{'fs':{'readTextFile':False,'writeTextFile':False},'terminal':False},
            'clientInfo':{'name':'experimental-workbuddy-bundled-runtime','version':'0.1'}})
        info = value.get('agentInfo')
        if (type(value.get('protocolVersion')) is not int or value['protocolVersion'] != 1 or
            not isinstance(info, dict) or not all(isinstance(info.get(k), str) and 0 < len(info[k]) <= 256 for k in ('name','version')) or
            not isinstance(value.get('agentCapabilities'), dict)):
            raise ACPError('ACP_IDENTITY_UNVERIFIED')
        self.identity = value
        return value

    def new_session(self, cwd=None):
        if self.identity is None or self.session_id is not None:
            raise ACPError('ACP_INVALID_STATE')
        path = Path(cwd or self.cwd).resolve(strict=True)
        if path != self.cwd:
            raise ACPError('ACP_CWD_MISMATCH')
        self._creating_session = True
        try:
            value = self._rpc('session/new', {'cwd':str(path),'mcpServers':[]})
        finally:
            self._creating_session = False
        sid = value.get('sessionId')
        if not isinstance(sid, str) or not sid or len(sid) > 512:
            raise ACPError('ACP_INVALID_SESSION')
        if (not isinstance(value.get('models', {}), dict) or
                not isinstance(value.get('modes', {}), dict) or
                not isinstance(value.get('configOptions', []), list) or
                any(not isinstance(o, dict) for o in value.get('configOptions', [])) or
                not isinstance(value.get('models', {}).get('availableModels', []), list)):
            raise ACPError('ACP_INVALID_SESSION')
        self.session_id, self.session = sid, value
        # A present unsafe initial value must not be erased by queued updates.
        # Missing initial mode remains subject to the existing admission gate.
        try:
            observed_modes = self._pin_mode_sources()
            if any(mode not in self.SAFE_MODES for mode in observed_modes):
                raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
            if 'configOptions' in self.session:
                self._observe_config_snapshot()
        except ACPError as exc:
            self.fault = exc.code
            raise
        for event in self._pending_events:
            self._event(event)
        self._pending_events.clear()
        return value

    def _model_option(self):
        options = self.session.get('configOptions', [])
        if not isinstance(options, list) or any(not isinstance(o, dict) for o in options):
            raise ACPError('ACP_INVALID_OPTIONS')
        ids = [o.get('id') for o in options]
        if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)):
            raise ACPError('ACP_INVALID_OPTIONS')
        found = [o for o in options if isinstance(o, dict) and o.get('category') == 'model']
        if len(found) > 1:
            raise ACPError('ACP_AMBIGUOUS_MODEL')
        if found:
            item = found[0]
            values = self._values(item.get('options'))
            if (item.get('type') != 'select' or not isinstance(item.get('currentValue'), str) or
                    item['currentValue'] not in values or len(values) != len(set(values))):
                raise ACPError('ACP_INVALID_OPTIONS')
        return found[0] if found else None

    def _observe_config_snapshot(self):
        # Pin an observed source even before selection. Full snapshots cannot
        # delete, replace or repair previously observed malformed evidence.
        option = self._model_option()
        if option is None:
            raise ACPError('ACP_MODEL_UNVERIFIED')
        identifier = option['id']
        if self._observed_model_config_id not in (None, identifier):
            raise ACPError('ACP_MODEL_CHANGED')
        self._observed_model_config_id = identifier
        return option

    @staticmethod
    def _values(options):
        values = []
        if not isinstance(options, list):
            raise ACPError('ACP_INVALID_OPTIONS')
        for option in options:
            if not isinstance(option, dict):
                raise ACPError('ACP_INVALID_OPTIONS')
            if 'group' in option:
                values.extend(ACPClient._values(option.get('options')))
            elif isinstance(option.get('value'), str) and option['value']:
                values.append(option['value'])
            else:
                raise ACPError('ACP_INVALID_OPTIONS')
        return values

    @staticmethod
    def _legacy_catalog(models):
        catalog = models.get('availableModels') if isinstance(models, dict) else None
        if (not isinstance(catalog, list) or any(not isinstance(m, dict) or
                not isinstance(m.get('modelId'), str) or not m['modelId'] for m in catalog)):
            raise ACPError('ACP_INVALID_OPTIONS')
        identifiers = [m['modelId'] for m in catalog]
        if len(identifiers) != len(set(identifiers)):
            raise ACPError('ACP_AMBIGUOUS_MODEL')
        return identifiers

    def _selection_observation(self, config=False):
        pending = self._selection
        if pending is None:
            return
        option = self._model_option()
        if config or pending['source'][0] == 'config':
            if option is None:
                raise ACPError('ACP_MODEL_UNVERIFIED')
            identifier = option.get('id')
            values = self._values(option.get('options'))
            if (not isinstance(identifier, str) or not identifier or
                    option.get('type') != 'select' or
                    not isinstance(option.get('currentValue'), str) or
                    len(values) != len(set(values)) or
                    pending['target'] not in values):
                raise ACPError('ACP_MODEL_UNVERIFIED')
            if pending['source'][0] == 'config' and identifier != pending['source'][1]:
                raise ACPError('ACP_MODEL_CHANGED')
            pending['source'] = ('config', identifier)
            current = option['currentValue']
        else:
            current = self.session.get('models', {}).get('currentModelId')
        pending['fresh'] = current == pending['target']
        if not pending['fresh'] and pending['acked']:
            raise ACPError('ACP_MODEL_UNVERIFIED')

    def set_model(self, model_id):
        # Do not hold the lifecycle lock while waiting for native state: close()
        # remains able to interrupt. A concurrent caller cannot send a setter.
        if not self._selection_lock.acquire(blocking=False):
            raise ACPError('ACP_INVALID_STATE')
        try:
            return self._select_model(model_id)
        finally:
            self._selection_lock.release()

    def _select_model(self, model_id):
        if (self.fault or self._selection is not None or self.model_id is not None or
                not self.session_id or not isinstance(model_id, str) or not model_id):
            raise ACPError('ACP_INVALID_STATE')
        try:
            if 'configOptions' in self.session:
                self._observe_config_snapshot()
            self._drain_controls()
            self._safe_mode()
            option = self._model_option()
            if option is None and 'configOptions' in self.session:
                raise ACPError('ACP_MODEL_UNVERIFIED')
            source = ('config', option.get('id')) if option is not None else ('legacy', None)
            self._selection = {'source':source, 'target':model_id, 'fresh':False, 'acked':False}
            if option is not None:
                self._selection_observation(config=True)
                method = 'session/set_config_option'
                params = {'sessionId':self.session_id, 'configId':option['id'], 'value':model_id}
            else:
                models = self.session.get('models', {})
                identifiers = self._legacy_catalog(models)
                if model_id not in identifiers:
                    raise ACPError('ACP_MODEL_UNAVAILABLE')
                method = 'session/set_model'
                params = {'sessionId':self.session_id, 'modelId':model_id}
            self._selection['fresh'] = False
            value = self._rpc(method, params)
            # Acknowledgment echoes are never state evidence; contradictions in
            # either supported typed model field cannot be silently ignored.
            for key in ('modelId', 'currentModelId'):
                if key in value and (not isinstance(value[key], str) or value[key] != model_id):
                    raise ACPError('ACP_MODEL_UNVERIFIED')
            if method == 'session/set_config_option' and 'configOptions' not in value:
                raise ACPError('ACP_MODEL_UNVERIFIED')
            self._selection['acked'] = True
            if 'configOptions' in value:
                self.session['configOptions'] = value['configOptions']
                self._observe_config_snapshot()
                self._safe_mode()
                self._selection_observation(config=True)
            elif 'currentModelId' in value and self._selection['source'][0] == 'legacy':
                self.session.setdefault('models', {})['currentModelId'] = value['currentModelId']
                self._selection_observation()
            while not self._selection['fresh']:
                frame = self._next()  # Existing absolute deadline, strict parser.
                if 'method' not in frame:
                    raise ACPError('ACP_UNEXPECTED_RESPONSE')
                self._event(frame)
            self._drain_controls()
            self._safe_mode()
            self.model_id = model_id
            self._model_source = self._selection['source']
            self._check_model()
            return {'model_id':model_id, 'readback_confirmed':True}
        except BaseException:
            self.fault = self.fault or 'ACP_MODEL_UNVERIFIED'
            self.close()
            raise
        finally:
            self._selection = None

    def _check_model(self):
        if self.model_id is None or self._model_source is None:
            raise ACPError('ACP_MODEL_UNVERIFIED')
        source, identifier = self._model_source
        option = self._model_option()
        if source == 'config':
            if (option is None or option.get('id') != identifier or
                    option.get('currentValue') != self.model_id or
                    self.model_id not in self._values(option.get('options'))):
                raise ACPError('ACP_MODEL_CHANGED')
        else:
            if self.session.get('models', {}).get('currentModelId') != self.model_id:
                raise ACPError('ACP_MODEL_CHANGED')
            if option is not None and option.get('currentValue') != self.model_id:
                raise ACPError('ACP_MODEL_CHANGED')

    def _pin_mode_sources(self):
        """Mode evidence is monotonic: no removed selector falls back to cache."""
        sources = set()
        values = []
        legacy = self.session.get('modes', {})
        if not isinstance(legacy, dict):
            raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
        if 'currentModeId' in legacy:
            sources.add(('legacy', 'currentModeId'))
            values.append(legacy['currentModeId'])
        options = self.session.get('configOptions', [])
        if not isinstance(options, list) or any(not isinstance(o, dict) for o in options):
            raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
        configured = [o for o in options if o.get('category') == 'mode']
        if len(configured) > 1:
            raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
        for option in configured:
            identifier = option.get('id')
            if (not isinstance(identifier, str) or not identifier or
                    option.get('type') != 'select' or
                    not isinstance(option.get('currentValue'), str)):
                raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
            if option['currentValue'] not in self._values(option.get('options')):
                raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
            sources.add(('config', identifier))
            values.append(option['currentValue'])
        if not self._mode_sources <= sources:
            raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
        self._mode_sources.update(sources)
        if any(not isinstance(value, str) for value in values) or len(set(values)) > 1:
            raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')
        return values

    def _safe_mode(self):
        values = self._pin_mode_sources()
        if not values or any(value not in self.SAFE_MODES for value in values):
            raise ACPError('ACP_PERMISSION_MODE_UNVERIFIED')

    def _event(self, frame):
        if self.fault:
            raise ACPError(self.fault)
        try:
            return self._apply_event(frame)
        except ACPError as exc:
            self.fault = exc.code
            raise

    def _apply_event(self, frame):
        method, params = frame.get('method'), frame.get('params')
        if not isinstance(params, dict):
            raise ACPError('ACP_INVALID_EVENT')
        if method == 'session/request_permission':
            if not self._active_prompt or params.get('sessionId') != self.session_id:
                raise ACPError('ACP_WRONG_SESSION')
            rid = frame.get('id')
            if isinstance(rid, bool) or not isinstance(rid, (str, int)) or rid in self._permission_ids:
                raise ACPError('ACP_INVALID_PERMISSION')
            self._permission_ids.add(rid)
            options = params.get('options')
            if not isinstance(options, list) or len(options) > 32:
                raise ACPError('ACP_INVALID_PERMISSION')
            choice = self._bounded_call(lambda:self.permission_handler(params)) if self.permission_handler else 'reject_once'
            if choice not in ('allow_once','reject_once'):
                raise ACPError('ACP_UNSAFE_PERMISSION')
            matches = [o for o in options if isinstance(o, dict) and o.get('kind') == choice and isinstance(o.get('optionId'),str)]
            outcome = {'outcome':'selected','optionId':matches[0]['optionId']} if len(matches) == 1 else {'outcome':'cancelled'}
            self._send({'jsonrpc':'2.0','id':rid,'result':{'outcome':outcome}})
            self._permissions.append(choice if len(matches) == 1 else 'cancelled')
            return
        if method != 'session/update' or 'id' in frame:
            raise ACPError('ACP_UNSUPPORTED_EVENT')
        if self._creating_session and self.session_id is None:
            if len(self._pending_events) >= 32:
                raise ACPError('ACP_OUTPUT_LIMIT')
            self._pending_events.append(frame)
            return
        if params.get('sessionId') != self.session_id:
            raise ACPError('ACP_WRONG_SESSION')
        update = params.get('update')
        if not isinstance(update, dict):
            raise ACPError('ACP_INVALID_EVENT')
        kind = update.get('sessionUpdate')
        if kind == 'current_mode_update':
            self.session.setdefault('modes', {})['currentModeId'] = update.get('currentModeId')
            self._pin_mode_sources()
            self._safe_mode()
        elif kind == 'model_update':
            # Fixed native catalog notification: a complete legacy snapshot.
            identifiers = self._legacy_catalog(update)
            current = update.get('currentModelId')
            if not isinstance(current, str) or not current or current not in identifiers:
                raise ACPError('ACP_MODEL_UNVERIFIED')
            self.session['models'] = {'availableModels':update['availableModels'],
                                      'currentModelId':current}
            if self._selection is not None:
                pending = self._selection
                if pending['target'] not in identifiers:
                    raise ACPError('ACP_MODEL_CHANGED')
                if pending['source'][0] == 'config':
                    if current != pending['target']:
                        if pending['acked']:
                            raise ACPError('ACP_MODEL_CHANGED')
                        pending['fresh'] = False
                    # Matching legacy state cannot certify a config source.
                else:
                    self._selection_observation()
            if self.model_id is not None:
                if current != self.model_id or self.model_id not in identifiers:
                    raise ACPError('ACP_MODEL_CHANGED')
                self._check_model()
        elif kind == 'current_model_update':
            current = update.get('currentModelId')
            identifiers = self._legacy_catalog(self.session.get('models'))
            if not isinstance(current, str) or not current or current not in identifiers:
                raise ACPError('ACP_MODEL_UNVERIFIED')
            self.session['models']['currentModelId'] = current
            if self._selection is not None:
                if self._selection['source'][0] == 'config':
                    if current != self._selection['target']:
                        if self._selection['acked']:
                            raise ACPError('ACP_MODEL_CHANGED')
                        self._selection['fresh'] = False
                else:
                    self._selection_observation()
            if self.model_id is not None:
                if update.get('currentModelId') != self.model_id:
                    raise ACPError('ACP_MODEL_CHANGED')
                self._check_model()
        elif kind == 'config_option_update':
            if 'configOptions' not in update:
                raise ACPError('ACP_INVALID_OPTIONS')
            self.session['configOptions'] = update['configOptions']
            self._safe_mode()
            self._observe_config_snapshot()
            if self._selection is not None:
                self._selection_observation(config=True)
            elif self.model_id is not None and self._model_source[0] == 'legacy':
                option = self._model_option()
                if option is None:
                    raise ACPError('ACP_MODEL_CHANGED')
                self._model_source = ('config', option.get('id'))
            self._pin_mode_sources()
            if self.model_id is not None:
                self._check_model()
            self._safe_mode()
        elif kind == 'agent_message_chunk':
            if not self._active_prompt:
                raise ACPError('ACP_LATE_MESSAGE')
            content = update.get('content', {})
            if not isinstance(content, dict):
                raise ACPError('ACP_INVALID_EVENT')
            if content.get('type') == 'text' and isinstance(content.get('text'), str):
                self._text_bytes += len(content['text'].encode())
                if self._text_bytes > self.MAX_TEXT:
                    raise ACPError('ACP_OUTPUT_LIMIT')
                self._text.append(content['text'])
        # Other bounded session notifications are observation-only, not completion.

    def prompt(self, text, before_send):
        if self.prompt_attempted or not self.session_id or not self.model_id:
            raise ACPError('ACP_INVALID_STATE')
        if not isinstance(text, str) or not text.strip() or len(text.encode()) > 65536 or not callable(before_send):
            raise ValueError('Bounded prompt and durable intent callback required')
        with self._prompt_lock:
            if self.prompt_attempted:
                raise ACPError('ACP_INVALID_STATE')
            self._drain_controls()
            self._safe_mode()
            self._check_model()
            self.prompt_attempted = True
        try:
            result = self._rpc('session/prompt', {'sessionId':self.session_id,'prompt':[{'type':'text','text':text}]}, before_send)
            stop = result.get('stopReason')
            if stop not in {'end_turn','max_tokens','max_turn_requests','refusal','cancelled'}:
                raise ACPError('ACP_COMPLETION_UNVERIFIED')
            self._remaining()
            self._safe_mode()
            self._check_model()
            if stop == 'end_turn' and not ''.join(self._text).strip():
                raise ACPError('ACP_EMPTY_OUTPUT')
            return {'session_id':self.session_id,'text':''.join(self._text),'stop_reason':stop,
                    'model_id':self.model_id,'native_completion':True,'permissions':list(self._permissions)}
        finally:
            self._active_prompt = False

    def close(self):
        with self._lifecycle_lock:
            with self._io_lock:
                self._closing = True
                threads = tuple(self._io_threads)
            if self.supervised:
                deadline = time.monotonic() + 5
                if self._transport is None:
                    evidence = {'host_exited': None, 'owned_job_empty': None,
                                'handles_closed': True, 'cleanup_complete': True}
                else:
                    evidence = self._transport.close(timeout=max(0, deadline-time.monotonic()))
                stopped = True
                for record in threads:
                    thread = record['thread']
                    if not record['start_confirmed'] and thread.ident is None:
                        # A not-yet-visible native thread may still start. Lack
                        # of ident/is_alive is not proof it never existed.
                        stopped = False
                        continue
                    try:
                        thread.join(timeout=max(0, deadline-time.monotonic()))
                        if thread.is_alive():
                            stopped = False
                    except RuntimeError:
                        # Includes an interrupted/unconfirmed start and current
                        # thread joins. Retain the record for later observation.
                        stopped = False
                return {**evidence, 'io_threads_stopped': stopped,
                        'cleanup_complete': evidence['cleanup_complete'] is True and stopped,
                        'descendants_stopped': False}
            return self._close_host()

    def _close_host(self):
        process = self.process
        if process is None:
            return {'host_exited':True,'descendants_stopped':False}
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1)
            except subprocess.TimeoutExpired:
                process.kill()
                try:
                    process.wait(timeout=1)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (process.stdin, process.stdout, process.stderr):
            try:
                stream.close()
            except OSError:
                pass
        return {'host_exited':process.poll() is not None,'descendants_stopped':False}
