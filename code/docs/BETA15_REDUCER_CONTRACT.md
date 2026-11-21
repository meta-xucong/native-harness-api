# Offline beta.15 reducer foundation (not connected)

`beta15_reducer.py` is new self-authored, vendor-free Python. It performs no SDK
import, authentication, real session/model selection, prompt, networking or files.
No production adapter/route/ledger or previously published diagnostic is modified.
It is not a replacement for process ownership, authorization or native readiness.

Pinned source basis: root @genie/agent-harness beta.15 inside official WorkBuddy
5.9.3. ACP experimental/v2 schema SHA256
75b2aa359dd26cd9d0468674be96482b14a2d181bc8e3ea8c91a8e598f63e3b5;
server-events 67d246760a07f53e5ccc8a2307b7ecf36fbc5e968532dbedb4693013b849c8cc;
subagent projection 7ede853142d3c1322a19c078c5c43a9e89ed6ebcc8c22c90249fd49fabbfdb62;
error constants 06f6ea7eeb15009f41a13779f7e81e3cbad25f8d287e521b3bcfa43c905d9a80.
Only facts/contracts were referenced; no external implementation copied.

## Inputs and evidence

One reducer represents one submitted prompt attempt and root session. Register
updates before submitting; bounded immutable copies buffer updates until the prompt
response binds `_meta['codebuddy.ai/requestId']`. Response messageId is insertion
identity, not run identity or completion. Match envelope sessionId and update-level
requestId; ignore different runs/child envelopes, subagent_update and any private
subagent marker. rootRequestId, if present, must match the root run.

Monotonic deadline is checked before each response/update; timeout/disconnect/
overflow/schema fault is sticky indeterminate, never success or permission to retry.
Only matching idle with a recognized reason can terminate. end_turn/refusal require
nonempty observed text; max_tokens/max_turn_requests/future reasons remain
indeterminate in this minimal complete-text contract. Cancellation notification
params are only intent, not sent/accepted confirmation; still await terminal.
Conflicting terminal evidence invalidates this uncommitted observation; this module
must never overwrite an already immutable Bridge result.

Chunks append per messageId; agent_message full content replaces that message,
absent content retains it, null/[] clears it. Thought/user/tool events never become
assistant text or remote tool_calls. Unknown content fails closed. Error event is
not terminal; only correlated idle/error ends as failed. Raw error text is omitted.
Usage remains unknown; liveness remains unknown even on terminal because the reducer
cannot prove tools/descendants stopped. No automatic retry is ever permitted.

ModelReadback validates a bounded flat OR grouped model catalog. It produces a
selection intent only for an advertised ID, confirms only matching response snapshot,
and permanently invalidates this single attempt on a changed post-confirmation
model, malformed input or duplicate/out-of-order response. Later matching updates
cannot revive old response evidence. Confirmation is cleared before validating any
update/response envelope. A pre-response matching update still does not confirm;
the one response may confirm only when the caller has already correlated it to
this pending selection RPC and routed updates from this same session, in order. This is not model entitlement, actual selection
or execution evidence. A future caller must serialize config updates, match the RPC
response to its selection, preserve binding ownership and enforce readiness gates.
Permission response is always cancelled. No authorization/UI exists in this module.

## Remaining boundaries

Transport must reject duplicate JSON keys and enforce framing before passing decoded
objects. Callers must serialize reducer access; no concurrent/production integration
is implemented. This is a deliberately narrow projection, not the full v2 validator.
No usage accounting, multimodal output, tools/approval UI, reconnect/replay protocol,
SDK loader, authenticated identity, API route or model execution has been verified.
The prior actual cloud library trial failed at import before harness/client creation;
these synthetic fixtures do not turn that result into readiness. Product remains
NOT_READY_FOR_DIRECT_REAL_USE. No paid model action or deployment occurred.
