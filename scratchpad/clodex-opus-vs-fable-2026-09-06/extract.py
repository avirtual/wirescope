#!/usr/bin/env python3
"""One pass over a wirescope capture root -> per-request receipts as JSONL.

Reads response.json (small) + the head/tail of request.json only; a request
body is json-loaded ONLY when the compact needle appears in its last 64 KB
(then the strict walk-back detector from compaction-2026-09-04/scan.py decides,
so agents merely READING compaction code don't count as compactions).

usage: extract.py <capture_root> <out.jsonl> [--since YYYY-MM-DD] [--agent-prefix P]
"""
import json, os, re, sys, glob, time
from pathlib import Path
sys.path.insert(0, '/Users/bogdan/projects/proxy-lab')
from proxylab import billing

NEEDLE = "Your task is to create a detailed summary of the conversation so far"
TS_RE = re.compile(r'"ts":\s*"([^"]+)"')
AGENT_RE = re.compile(r'"agent":\s*"([^"]*)"')

def last_user_instruction(b):
    msgs = b.get("messages") or []
    m = None
    for cand in reversed(msgs):
        if cand.get("role") == "user":
            m = cand; break
        if cand.get("role") == "assistant":
            return None
    if m is None: return None
    c = m.get("content")
    blocks = [c] if isinstance(c, str) else [
        x.get("text") for x in c if isinstance(x, dict) and x.get("type") == "text"]
    for t in blocks:
        if t and NEEDLE in t:
            return t
    return None

def reprice(model, t, ts_epoch):
    p = billing._price_for(model, now=ts_epoch, speed=t.get("speed"))
    if not p: return None, True
    w5, w1 = t.get("cache_write_5m_tokens"), t.get("cache_write_1h_tokens")
    if w5 is None and w1 is None and t.get("cache_write_flat_tokens"):
        w5 = t["cache_write_flat_tokens"]
    u = lambda n, r: (n or 0) * r / 1e6
    parts = dict(
        usd_in=u(t.get("input_tokens"), p["in"]),
        usd_out=u(t.get("output_tokens"), p["out"]),
        usd_read=u(t.get("cache_read_input_tokens"), p["cache_read"]),
        usd_w5=u(w5, p["cache_write_5m"]),
        usd_w1=u(w1, p["cache_write_1h"]),
    )
    parts["usd"] = sum(parts.values())
    return parts, False

INTENT_RE = re.compile(r'^\s*\[agent:(dm|task (?:add|accept|reject|respec|cancel|assign|start|park|done)|notify-user|spawn|remind|memory remember)\b', re.M)
def intent_counts(text):
    c = {}
    for m in INTENT_RE.finditer(text):
        k = m.group(1).replace(' ', '_'); c[k] = c.get(k, 0) + 1
    return c

def head_meta(path):
    with open(path, 'rb') as fh:
        head = fh.read(1500).decode('utf-8', 'replace')
    ts = TS_RE.search(head); ag = AGENT_RE.search(head)
    return (ts.group(1) if ts else None), (ag.group(1) if ag else None)

def tail_has_needle(path):
    sz = os.path.getsize(path)
    with open(path, 'rb') as fh:
        fh.seek(max(0, sz - 1048576))
        return NEEDLE.encode() in fh.read()

def main():
    root, out = sys.argv[1], sys.argv[2]
    since = None; prefix = None
    a = sys.argv[3:]
    while a:
        k = a.pop(0)
        if k == '--since': since = a.pop(0)
        elif k == '--agent-prefix': prefix = a.pop(0)
    n = 0; skipped = 0; t0 = time.time()
    with open(out, 'w') as fo:
        for sdir in sorted(Path(root).iterdir()):
            if not sdir.is_dir(): continue
            if since and time.strftime('%Y-%m-%d', time.localtime(sdir.stat().st_mtime)) < since:
                continue
            reqs = sorted(sdir.glob('*.request.json'), key=lambda p: int(p.name.split('-')[0]))
            for req in reqs:
                base = req.name[:-len('.request.json')]
                if prefix and not base.split('-', 1)[1].startswith(prefix):
                    continue
                resp = req.with_name(base + '.response.json')
                if not resp.exists(): skipped += 1; continue
                ts, agent = head_meta(req)
                if since and ts and ts[:10] < since: continue
                try:
                    o = json.load(open(resp))
                except Exception:
                    skipped += 1; continue
                b = o.get('billing') or {}
                t = {k: v for k, v in (b.get('tokens') or {}).items()}
                meta = o.get('meta') or {}
                ts_epoch = None
                if ts:
                    try: ts_epoch = time.mktime(time.strptime(ts[:19], '%Y-%m-%dT%H:%M:%S'))
                    except Exception: pass
                parts, unpriced = reprice(o.get('model'), t, ts_epoch)
                rec = dict(
                    session=sdir.name, seq=o.get('seq'), ts=ts, ts_epoch=ts_epoch,
                    agent=agent or o.get('agent'), role=o.get('role'), model=o.get('model'),
                    status=o.get('status_code'), endpoint=o.get('endpoint'),
                    inp=t.get('input_tokens') or 0, out=t.get('output_tokens') or 0,
                    read=t.get('cache_read_input_tokens') or 0,
                    w5=t.get('cache_write_5m_tokens') or 0, w1=t.get('cache_write_1h_tokens') or 0,
                    wflat=t.get('cache_write_flat_tokens') or 0,
                    think=t.get('thinking_tokens') or 0,
                    est_usd_proxy=b.get('est_usd'), unpriced=unpriced,
                    stop=meta.get('stop_reason'),
                    n_tool_uses=len(meta.get('tool_uses') or []),
                    tool_uses=meta.get('tool_uses') or [],
                    text_chars=len(meta.get('text') or ''),
                    intents=intent_counts(meta.get('text') or ''),
                    compact=False,
                )
                if parts: rec.update(parts)
                # compact detection: cheap tail scan, strict confirm on hit
                if rec['endpoint'] == 'messages' and tail_has_needle(req):
                    try:
                        body = json.load(open(req)).get('body') or {}
                        if body.get('max_tokens') != 1 and last_user_instruction(body) is not None:
                            rec['compact'] = True
                            rec['compact_n_msgs'] = len(body.get('messages') or [])
                    except Exception:
                        pass
                # request-side proxy annotations (cheap: head of file has them? no -> summary is at the END)
                sz = req.stat().st_size
                with open(req, 'rb') as fh:
                    fh.seek(max(0, sz - 4096)); tail = fh.read().decode('utf-8', 'replace')
                m = re.search(r'"summary":\s*(\{.*\})\s*\}\s*$', tail, re.S)
                if m:
                    try:
                        s = json.loads(m.group(1))
                        rec.update(n_messages=s.get('n_messages'), n_tools=s.get('n_tools'),
                                   system_chars=s.get('system_chars'), messages_chars=s.get('messages_chars'),
                                   tool_names=s.get('tool_names'), agent_id=s.get('agent_id'))
                    except Exception:
                        pass
                w = req.with_name(base + '.warmth.json')
                if w.exists():
                    try:
                        wj = json.load(open(w))
                        rec.update(ttl=wj.get('ttl'), ping=wj.get('ping'), warm_on_arrival=wj.get('warm_on_arrival'),
                                   cold_resume=wj.get('cold_resume'), bust_class=wj.get('bust_class'))
                    except Exception:
                        pass
                fo.write(json.dumps(rec) + '\n'); n += 1
            print(f'{sdir.name} done, total {n} ({time.time()-t0:.0f}s)', file=sys.stderr, flush=True)
    print(f'wrote {n} records, skipped {skipped}, {time.time()-t0:.0f}s', file=sys.stderr)

if __name__ == '__main__':
    main()
