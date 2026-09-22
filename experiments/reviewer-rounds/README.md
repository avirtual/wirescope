# reviewer-rounds — where a clodex reviewer seat's API round trips go

Offline analysis over the live capture corpus (`~/Library/Application Support/clodex/wirescope/logs`), 2026-09-01.
Pipeline: `rv_sessions.py <out.json>` indexes `clodex.tNNN.review-rR` sessions by filename glob (never parses every capture);
`rv_extract.py <sessions.json> <out.json>` pulls tool calls, dispatch, verdict text and billing per session;
`rv_calls.py <extract.json>` prints the grep/read shape statistics.
Method: count batching on the WIRE (assistant messages by tool_use count in the last forwarded body) — the CLI's session JSONL splits one parallel batch into N entries and reads as "never batches".
Findings are in HANDOFF.local.md (2026-09-01) and the session write-up; verdict taxonomy was done by a sonnet subagent over the REWORK verdict texts.
