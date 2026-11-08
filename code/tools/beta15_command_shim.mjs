/** Vendor-free dispatcher. The caller supplies an already-authorized connected client.
 * No vendor import, loader, process launcher, account access, or production admission.
 */
const MAX_FRAME = 262144;
const MAX_FRAMES = 512;
const MAX_BYTES = 4 * 1024 * 1024;
const BAD = 'BETA15_SHIM_CONTRACT';
const METHODS = new Set(['session/new', 'session/set_config_option', 'session/prompt', 'session/cancel', 'session/close', 'shim/close']);
const FAULTS = new Set(['INVALID_COMMAND', 'INVALID_RESULT', 'INVALID_UPDATE', 'MODEL_EVIDENCE_LOST', 'NATIVE_RPC_FAILED', 'OUTPUT_LIMIT', 'TRANSPORT_FAILED', 'STOPPED', 'POST_SHUTDOWN_ACTIVITY']);
function requireValue(value) { if (!value) throw new TypeError(BAD); }
function validString(value, max = 512) {
  requireValue(typeof value === 'string' && value.length > 0 && value.length <= max);
  for (let i = 0; i < value.length; i++) {
    const code = value.charCodeAt(i);
    if (code >= 0xD800 && code <= 0xDBFF) {
      const next = value.charCodeAt(++i);
      requireValue(next >= 0xDC00 && next <= 0xDFFF);
    } else requireValue(code < 0xDC00 || code > 0xDFFF);
  }
  return value;
}
function plain(value) {
  return value !== null && typeof value === 'object' && !Array.isArray(value) &&
    (Object.getPrototypeOf(value) === Object.prototype || Object.getPrototypeOf(value) === null);
}
function keys(value, allowed) {
  requireValue(plain(value) && Object.keys(value).every(key => allowed.includes(key)));
}
/** Copy JSON data without invoking accessors/toJSON; fail before emitting dynamic errors. */
export function boundedCopy(value, maxBytes = MAX_FRAME) {
  requireValue(Number.isSafeInteger(maxBytes) && maxBytes > 0 && maxBytes <= MAX_BYTES);
  let nodes = 0;
  const seen = new Set();
  const visit = (item, depth) => {
    requireValue(++nodes <= 20000 && depth <= 64);
    if (item === null || typeof item === 'boolean') return item;
    if (typeof item === 'string') { if (item.length) validString(item, maxBytes); return item; }
    if (typeof item === 'number') { requireValue(Number.isFinite(item)); return item; }
    requireValue(typeof item === 'object' && (Array.isArray(item) || plain(item)) && !seen.has(item));
    seen.add(item);
    const result = Array.isArray(item) ? [] : Object.create(null);
    const descriptors = Object.getOwnPropertyDescriptors(item);
    requireValue(Object.getOwnPropertySymbols(item).length === 0);
    if (Array.isArray(item)) {
      requireValue(item.length <= 20000 && Object.keys(item).length === item.length);
      for (let i = 0; i < item.length; i++) {
        const d = descriptors[String(i)];
        requireValue(d && Object.hasOwn(d, 'value') && d.enumerable);
        result.push(visit(d.value, depth + 1));
      }
    } else {
      for (const [key, d] of Object.entries(descriptors)) {
        requireValue(d.enumerable && Object.hasOwn(d, 'value'));
        if (key.length) validString(key, maxBytes);
        result[key] = visit(d.value, depth + 1);
      }
    }
    seen.delete(item);
    return result;
  };
  const copy = visit(value, 0);
  requireValue(Buffer.byteLength(JSON.stringify(copy), 'utf8') + 1 <= maxBytes);
  return copy;
}
function modelSnapshot(options) {
  requireValue(Array.isArray(options) && options.length <= 128);
  const entries = options.filter(x => plain(x) && x.configId === 'model');
  requireValue(entries.length === 1 && entries[0].type === 'select');
  const entry = entries[0];
  requireValue(Array.isArray(entry.options) && entry.options.length > 0 && entry.options.length <= 1024);
  let values;
  if (entry.options.every(x => plain(x) && Object.hasOwn(x, 'groupId'))) {
    const groups = new Set(); values = [];
    for (const group of entry.options) {
      const id = validString(group.groupId); validString(group.name);
      requireValue(!groups.has(id) && Array.isArray(group.options) && group.options.length <= 1024);
      groups.add(id); values.push(...group.options);
    }
  } else {
    requireValue(entry.options.every(x => plain(x) && !Object.hasOwn(x, 'groupId')));
    values = entry.options;
  }
  requireValue(values.length > 0 && values.length <= 1024);
  const admitted = new Set();
  for (const option of values) {
    requireValue(plain(option) && !Object.hasOwn(option, 'groupId'));
    const value = validString(option.value); validString(option.name);
    requireValue(!admitted.has(value)); admitted.add(value);
  }
  const current = validString(entry.currentValue); requireValue(admitted.has(current));
  return { admitted, current };
}

/**
 * emit receives a complete bounded JSONL string synchronously. Throw on output
 * failure. The owner supplies framing/duplicate-key rejection and backpressure.
 * dispatch promises indicate RPC handling only, never model completion or cleanup.
 */
export function createCommandShim({ client, emit, expectedCwd, closeRuntime }) {
  requireValue(client && typeof client.on === 'function' && client.session && typeof emit === 'function' && typeof closeRuntime === 'function');
  for (const method of ['new', 'setConfigOption', 'prompt', 'cancel', 'close']) requireValue(typeof client.session[method] === 'function');
  validString(expectedCwd, 4096);
  let sessionId, catalog, selected, modelConfirmed = false;
  let newIssued = false, selectionIssued = false, promptIssued = false, cancelIssued = false, closeIssued = false;
  let frames = 0, bytes = 0, fault = null, outputDead = false, shutdownIssued = false, shutdownSettled = false;
  const ids = new Set();
  const nativePending = new Set();
  function invoke(operation) {
    // Register synchronously, before dispatch yields. Shutdown fences new native
    // work and drains these promises after asking the runtime to close.
    let resolve, reject;
    const pending = new Promise((a, b) => { resolve = a; reject = b; });
    nativePending.add(pending);
    pending.then(() => nativePending.delete(pending), () => nativePending.delete(pending));
    try { Promise.resolve(operation()).then(resolve, reject); }
    catch (error) { reject(error); }
    return pending;
  }
  function output(value, emergency = false) {
    if (outputDead) return false;
    let line;
    try { line = JSON.stringify(boundedCopy(value)) + '\n'; }
    catch {
      if (emergency) outputDead = true;
      else fail('INVALID_RESULT');
      return false;
    }
    const size = Buffer.byteLength(line, 'utf8');
    // Keep room for one fixed fault frame, including after a limit failure.
    if (!emergency && (frames >= MAX_FRAMES - 1 || bytes + size > MAX_BYTES - 128)) {
      fail('OUTPUT_LIMIT');
      return false;
    }
    frames++; bytes += size;
    try {
      const returned = emit(line);
      if (returned instanceof Promise) returned.catch(() => {});
      requireValue(returned === undefined || returned === true);
      return true;
    } catch {
      // A failed writer cannot reliably carry another error frame. Never retry it.
      outputDead = true;
      if (fault === null) fault = 'TRANSPORT_FAILED';
      modelConfirmed = false;
      return false;
    }
  }
  function fail(code) {
    requireValue(FAULTS.has(code));
    if (fault !== null) return;
    fault = code; modelConfirmed = false;
    output({ method: 'shim/fault', params: { code } }, true);
  }
  function response(id, result) { return output({ id, result }); }
  function reject(id, code) {
    if (Number.isSafeInteger(id) && id > 0) output({ id, error: { code } });
    fail(code);
  }
  client.on('session/request_permission', () => {
    if (shutdownSettled) fail('POST_SHUTDOWN_ACTIVITY');
    return { outcome: { outcome: 'cancelled' } };
  });
  client.on('elicitation/create', () => {
    if (shutdownSettled) fail('POST_SHUTDOWN_ACTIVITY');
    return { action: 'cancel' };
  });
  client.on('session/update', envelope => {
    if (outputDead || fault !== null) return;
    if (shutdownSettled) { fail('POST_SHUTDOWN_ACTIVITY'); return; }
    try {
      const value = boundedCopy(envelope);
      requireValue(plain(value) && typeof value.sessionId === 'string' && plain(value.update));
      if (!sessionId || value.sessionId !== sessionId) return;
      if (value.update.sessionUpdate === 'config_option_update') {
        catalog = modelSnapshot(value.update.configOptions);
        if (modelConfirmed && catalog.current !== selected) { fail('MODEL_EVIDENCE_LOST'); return; }
      }
      output({ method: 'session/update', params: value });
    } catch { fail('INVALID_UPDATE'); }
  });
  return Object.freeze({
    async dispatch(input) {
      let command, id, method;
      try {
        command = boundedCopy(input); requireValue(plain(command));
        method = command.method; requireValue(METHODS.has(method));
        const notification = method === 'session/cancel';
        keys(command, notification ? ['method', 'params'] : ['id', 'method', 'params']);
        if (notification) requireValue(!Object.hasOwn(command, 'id'));
        else {
          id = command.id; requireValue(Number.isSafeInteger(id) && id > 0 && !ids.has(id));
          requireValue(method === 'shim/close' ? id === 2147483647 : id < 2147483647);
          requireValue(method === 'shim/close' || ids.size < 4); ids.add(id);
        }
        const p = command.params; requireValue(plain(p));
        // Failure never permits new work. Bound cleanup remains available once.
        requireValue(!outputDead && (fault === null || method === 'session/cancel' || method === 'session/close' || method === 'shim/close'));
        if (method === 'shim/close') {
          keys(p, []); requireValue(!shutdownIssued);
          shutdownIssued = true; modelConfirmed = false;
          requireValue(await closeRuntime() === true);
          await Promise.allSettled([...nativePending]);
          shutdownSettled = true;
          response(id, { closed: true });
          return;
        }
        requireValue(!shutdownIssued);
        if (method === 'session/new') {
          keys(p, ['cwd', 'mcpServers']);
          requireValue(!newIssued && !closeIssued && p.cwd === expectedCwd && Array.isArray(p.mcpServers) && p.mcpServers.length === 0);
          newIssued = true;
          const result = boundedCopy(await invoke(() => client.session.new(p)));
          requireValue(plain(result)); sessionId = validString(result.sessionId);
          catalog = modelSnapshot(result.configOptions);
          response(id, result);
        } else {
          requireValue(sessionId && p.sessionId === sessionId);
          if (method === 'session/set_config_option') {
            keys(p, ['sessionId', 'configId', 'type', 'value']);
            requireValue(!selectionIssued && !closeIssued && p.configId === 'model' && p.type === 'id' && catalog.admitted.has(p.value));
            selectionIssued = true; selected = p.value;
            const result = boundedCopy(await invoke(() => client.session.setConfigOption(p)));
            requireValue(plain(result)); catalog = modelSnapshot(result.configOptions);
            requireValue(catalog.current === selected);
            if (fault === null && !closeIssued && !shutdownIssued) modelConfirmed = true;
            response(id, result);
          } else if (method === 'session/prompt') {
            keys(p, ['sessionId', 'prompt']);
            requireValue(modelConfirmed && !promptIssued && !closeIssued && Array.isArray(p.prompt) && p.prompt.length === 1);
            keys(p.prompt[0], ['type', 'text']);
            requireValue(p.prompt[0].type === 'text' && validString(p.prompt[0].text, 131072).trim().length > 0);
            promptIssued = true;
            const result = boundedCopy(await invoke(() => client.session.prompt(p)));
            requireValue(plain(result)); validString(result.messageId);
            requireValue(plain(result._meta)); validString(result._meta['codebuddy.ai/requestId']);
            response(id, result);
          } else if (method === 'session/cancel') {
            keys(p, ['sessionId']); requireValue(promptIssued && !cancelIssued);
            cancelIssued = true;
            await invoke(() => client.session.cancel(p)); // Notification has no response envelope.
          } else if (method === 'session/close') {
            keys(p, ['sessionId']); requireValue(!closeIssued);
            closeIssued = true; modelConfirmed = false;
            const result = boundedCopy(await invoke(() => client.session.close(p)));
            requireValue(plain(result)); keys(result, ['_meta']);
            requireValue(!Object.hasOwn(result, '_meta') || result._meta === null || plain(result._meta));
            response(id, result);
          }
        }
      } catch { reject(id, command === undefined || !METHODS.has(method) ? 'INVALID_COMMAND' : 'NATIVE_RPC_FAILED'); }
    },
    stop() { fail('STOPPED'); },
    state() { return { sessionBound: Boolean(sessionId), promptIssued, closeIssued, shutdownIssued, shutdownSettled, modelConfirmed, fault, frames, bytes, retrySafe: false }; }
  });
}
