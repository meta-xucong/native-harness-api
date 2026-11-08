# Native live events, not buffered final output

The public SFE emits `session/update` during an active native prompt. Text chunks
append to their message; `agent_message` replaces that message's text, including
shortening or clearing it. The observation contract therefore publishes the
current root text as `text_snapshot`, not invented append-only token deltas.
Clients replace their displayed draft on each snapshot. A draft is provisional.

Only notifications bound to the accepted prompt's run are observable. Updates
received before the insertion acknowledgement remain bounded and are released in
order only after that acknowledgement identifies the run. Foreign session/run,
subagent, native tool contents, reasoning, raw errors and metadata are not exposed.
`native_status` carries only `running`, `requires_action`, `idle`, or `unknown`,
and a validated stop reason for idle. Native idle is **not** proof of process
cleanup or a successful API completion.

The reducer calls a nonblocking bounded enqueue, not an HTTP request. One worker
sends each event in order to the Bridge using the original task, claim and start
grant plus an increasing `event_seq`. The Bridge must durably acknowledge exactly
that identity and sequence. Its durable event cursor is separate from the native
sequence. Task admission and inference are never repeated by event reading or
reconnection. Terminal completion still requires the original model, prompt,
terminal and whole-owned-process cleanup evidence and result submission.

Limits: 64 queued events, 512 native events, 16 MiB cumulative escaped JSON
payload, 262144 decoded UTF-8 text bytes per snapshot, and a bounded single-event
wire envelope. Existing prompt and input-event budgets are unchanged. Backpressure,
malformed acknowledgement, transport error or an unfinished publisher fails the
attempt's delivery proof; a cancellation signal reaches the active owner. The
result stays pending/uncertain, no inference retries occur, and a still-running
publisher is retained. A dropped connection must not turn partial text into a
successful final completion. Network publication uses the existing bounded
socket-inactivity timeout, not a guaranteed total HTTP deadline. The final join
is bounded; a peer that dribbles bytes may keep the publisher alive, in which
case its reference and uncertain execution slot remain retained. The protocol
loop itself does not wait for the network.

Fixtures verify delivery before native invocation returns, snapshot replacement,
ACK-delayed event ordering, private-event filtering, errors, limits and no replay
on duplicate starts. Synthetic proof is not evidence of a real logged-in account
or model. No vendor execution is needed for these regressions.
