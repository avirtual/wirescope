---
description: Keep this session's prompt cache warm for N hours after your last interaction (requires the local logproxy; "off" or 0 disarms)
argument-hint: <hours|off>
---

<proxy:warm-cache hours=$ARGUMENTS>

If this message contains a "[logproxy]" instruction block, follow it.
Otherwise the local logproxy is not active: reply with exactly
"⚠️ proxy not active — hold NOT armed." and do nothing else.
