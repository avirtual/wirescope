#!/usr/bin/env python3
"""Find compaction REQUESTS on the wire and price them.

Detector (deliberately narrow -- a substring grep over the corpus would match
agents READING compaction code, cf. the 611-hits/0-firings trap):
  * last message is role=user
  * its LAST text block contains the compact task sentence
  -> that block is the CLI's appended compact instruction, not history.
"""
import json, os, sys, re
from pathlib import Path

NEEDLE = "Your task is to create a detailed summary of the conversation so far"
NEEDLE2 = "detailed summary of the conversation"

def last_user_instruction(b):
    """The compact instruction is appended to the FINAL USER message.

    That is not always messages[-1]: on the opus-4.8 wire shape a trailing
    role:"system" roster message rides behind it (mid-conversation-system).
    Scanning only messages[-1] silently classified 76 real compactions as
    mere mentions -- so walk back over trailing non-user messages instead.
    """
    msgs = b.get("messages") or []
    m = None
    for cand in reversed(msgs):
        if cand.get("role") == "user":
            m = cand; break
        if cand.get("role") == "assistant":
            return None      # a real assistant turn = we are past the tail
    if m is None: return None
    c = m.get("content")
    blocks = [c] if isinstance(c, str) else [
        x.get("text") for x in c if isinstance(x, dict) and x.get("type") == "text"]
    for t in blocks:
        if t and NEEDLE in t:
            return t
    return None

def usage_of(resp_path):
    try:
        d = json.load(open(resp_path))
    except Exception:
        return None
    for cand in (d, d.get("body") if isinstance(d, dict) else None):
        if isinstance(cand, dict) and isinstance(cand.get("usage"), dict):
            return cand["usage"]
    # SSE-shaped capture
    if isinstance(d, dict):
        for k in ("message", "response", "final"):
            v = d.get(k)
            if isinstance(v, dict) and isinstance(v.get("usage"), dict):
                return v["usage"]
    return None

def main(root):
    root = Path(root)
    out = []
    for req in root.rglob("*.request.json"):
        try:
            raw = req.read_text(errors="replace")
        except Exception:
            continue
        if NEEDLE2 not in raw:
            continue
        try:
            d = json.loads(raw)
        except Exception:
            continue
        b = d.get("body", d)
        instr = last_user_instruction(b)
        if instr is None:
            out.append({"file": str(req), "kind": "mention_only"})
            continue
        msgs = b.get("messages") or []
        # manual /compact leaves the slash command in the turn that triggered it
        recent = json.dumps(msgs[-4:])
        manual = "<command-name>/compact</command-name>" in recent or "/compact" in instr[:0]
        addl = "Additional Instructions:" in instr
        rec = {
            "file": str(req.relative_to(root)),
            "session": req.parent.name,
            "kind": "compact",
            "model": b.get("model"),
            "max_tokens": b.get("max_tokens"),
            "n_msgs": len(msgs),
            "n_tools": len(b.get("tools") or []),
            "instr_chars": len(instr),
            "manual_marker": manual,
            "addl_instructions": addl,
            "ts": d.get("ts"),
            "seq": d.get("seq"),
            "agent": d.get("agent"),
        }
        u = usage_of(str(req).replace(".request.json", ".response.json"))
        if u:
            rec["usage"] = {
                "input": u.get("input_tokens"),
                "cache_read": u.get("cache_read_input_tokens"),
                "cache_write": u.get("cache_creation_input_tokens"),
                "output": u.get("output_tokens"),
            }
        out.append(rec)
    json.dump(out, sys.stdout)

if __name__ == "__main__":
    main(sys.argv[1])
