# Text versus JSON response budgets

The decoded UTF-8 text budget remains 262144 bytes **in aggregate** across text
and refusal blocks. The request budget remains 65536 bytes. Neither changed.
A maximum-sized valid result must not be rejected merely because its JSON object
adds metadata or escapes control characters.

Native completion and Sub2API completion responses therefore allow at most
`6 * 262144 + 4096 = 1576960` wire bytes. Six is the maximum JSON escaping
expansion per decoded UTF-8 byte (for example byte U+0001 becomes six ASCII bytes
`\u0001`). The extra 4096 covers the strictly bounded completion metadata.
Content plus refusal still cannot exceed 262144 decoded UTF-8 bytes, even when
the wire envelope is below its separate limit. Unknown fields and malformed
Unicode remain rejected.

Bridge result validation uses the same text and result-wire budgets; its node
result submission adds 4096 bytes for the submission envelope. Capacity
reservation accounts for the maximum stored **wire** result, not just the text.
The native Bridge HTTP client permits an additional 128 KiB around a result for
the task/request/routing envelope (1708032 bytes total), remains finite and
requires exact explicit Content-Length. It does not loosen incoming requests.

Old stored results and replay are unaffected. No request digest, task identity,
execution grant or retry permission changes. Oversize results fail closed rather
than truncate output or repeat generation. These are serialization/HTTP fixture
checks, not model execution tests.
