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

## Long replies and slow observers

Public SFE has separate budgets: at most 4096 complete input frames and 4 MiB
cumulative native input, with a 256 KiB frame cap. The root text, aggregated
across messages, separately remains limited to 256 KiB UTF-8. Enlarging the
input budget does not enlarge the output or prompt limit. The 512-position
stdio queue applies bounded pipe backpressure rather than dropping a valid
burst; the owner deadline and closing flag still bound its lifetime. Legacy beta15 callers
retain their previous defaults.

The Node keeps at most 64 pending positions. Consecutive **unsent** text snapshots
are replaced by the newest complete snapshot. A native status is an ordering
barrier: text before and after it never merges across it, and statuses are never
discarded. Already-sent snapshots are immutable. This deliberately samples draft
versions, not text content: a shortened or cleared latest snapshot must still
arrive faithfully. It is not an append-token transcript.

Only actual publication receives an event sequence and consumes the Bridge's
512-event / 16 MiB escaped-JSON quota. Up to 4096 bounded source observations may
be coalesced. The publication worker paces snapshots using the configured native
execution timeout, available byte/event budget and snapshot size, with an initial
100 ms collection window. It reserves pacing capacity for boundary/final events;
status boundaries and final shutdown flush immediately in FIFO order. The
coalescing delay is capped at one second even for a long timeout or large
snapshot: quota pressure never silently delays draft updates for minutes. This
is an internal scheduling bound, not an end-to-end network latency promise.
There is no promise to show every token or every intermediate draft.
The final complete snapshot must be durably acknowledged before the ordinary
terminal result can be accepted. A very long, heavily escaped, or state-heavy stream can still exhaust the hard
quota; that fails explicitly rather than silently omitting states or text.

A slow external SSE reader does not slow or restart native inference: it reads
the Bridge's durable cursor log. If its cursor expires or storage is exhausted,
the reader receives an explicit error, never a fabricated successful ending.
Slow Node-to-Bridge publication keeps the newest unsent draft in bounded memory.
Backpressure, malformed acknowledgement, transport error or an unfinished
publisher fails delivery proof and signals cancellation to the active owner.
The result stays pending/uncertain, no inference retries occur, and a still-running
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
