#!/usr/bin/env python3
"""Measure Read -> Edit locality across captured sessions.

For every Edit (and Write) tool_use we find the most-recent prior access of the
same file and classify the gap:
  - same assistant message (Read+Edit emitted in one step)
  - same turn  (between two genuine user prompts)
  - later turn (the edit lands N turns after the read -> "a long time after")
  - no prior Read at all (blind edit / freshly Written file)

A "turn" boundary = a user message carrying a real text block (a genuine or
injected prompt), not merely tool_result continuations.

Per (session, agent) we analyze only the LARGEST snapshot (most complete
transcript) so each tool_use is counted once.
"""
import json, glob, os, sys, collections, re

# Injected/fleet user messages (workbench traffic): peer DMs, system relays,
# response receipts, cli/ws directives. A session dominated by these is a
# workbench/fleet agent, not a person coding.
INJ = re.compile(r'^\s*\[(response|#?system|broadcast|from|logproxy|cli:|agent:|ws:|wirescope:)')

def _texts(content):
    if isinstance(content, str):
        return [content]
    return [b.get('text', '') for b in content if isinstance(b, dict) and b.get('type') == 'text']

def inj_ratio(msgs):
    inj = tot = 0
    for m in msgs:
        if m['role'] != 'user':
            continue
        for t in _texts(m['content']):
            if t.strip():
                tot += 1
                if INJ.match(t):
                    inj += 1
    return (inj / tot) if tot else 0.0

def iter_blocks(content):
    if isinstance(content, str):
        yield 'text', None
        return
    for b in content:
        yield b.get('type'), b

def load_largest_per_agent(session_dir):
    """Return {agent: messages} keeping the request with most messages."""
    best = {}
    for f in glob.glob(os.path.join(session_dir, '*.request.json')):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        body = d.get('body') or {}
        msgs = body.get('messages')
        if not msgs:
            continue
        agent = d.get('agent') or '?'
        if agent not in best or len(msgs) > len(best[agent][1]):
            best[agent] = (f, msgs)
    return best

def analyze_messages(msgs):
    """Walk one transcript. Return list of edit records + counts."""
    turn_idx = 0
    asst_idx = 0
    last_read = {}      # file -> (msg_i, turn_idx, asst_idx)
    last_access = {}    # file -> (msg_i, turn_idx, asst_idx, kind)
    edits = []          # records for Edit
    writes = []
    reads = 0

    for mi, m in enumerate(msgs):
        role = m['role']
        content = m['content']
        # turn boundary detection: user message with a genuine text block
        if role == 'user':
            has_text = False
            if isinstance(content, str):
                has_text = bool(content.strip())
            else:
                for t, b in iter_blocks(content):
                    if t == 'text' and (b.get('text') if b else '').strip():
                        has_text = True
            if has_text:
                turn_idx += 1
        if role == 'assistant':
            asst_idx += 1
            for t, b in iter_blocks(content):
                if t != 'tool_use':
                    continue
                name = b.get('name')
                fp = (b.get('input') or {}).get('file_path')
                same_asst = b  # marker
                if name == 'Read' and fp:
                    reads += 1
                    last_read[fp] = (mi, turn_idx, asst_idx)
                    last_access[fp] = (mi, turn_idx, asst_idx, 'Read')
                elif name in ('Edit', 'MultiEdit') and fp:
                    rec = {'file': fp, 'mi': mi, 'turn': turn_idx, 'asst': asst_idx}
                    r = last_read.get(fp)
                    a = last_access.get(fp)
                    if r is None:
                        rec['cat'] = 'no_read'
                    else:
                        d_turn = turn_idx - r[1]
                        d_asst = asst_idx - r[2]
                        # same assistant message? edit emitted alongside read
                        if r[0] == mi:
                            rec['cat'] = 'same_msg'
                        elif d_turn == 0:
                            rec['cat'] = 'same_turn'
                        else:
                            rec['cat'] = 'later_turn'
                        rec['d_turn'] = d_turn
                        rec['d_asst'] = d_asst
                        rec['d_msg'] = mi - r[0]
                        # closer anchor (Edit/Write) since the Read?
                        rec['anchor_kind'] = a[3] if a else None
                        rec['anchor_d_turn'] = (turn_idx - a[1]) if a else None
                    edits.append(rec)
                    last_access[fp] = (mi, turn_idx, asst_idx, 'Edit')
                elif name == 'Write' and fp:
                    rec = {'file': fp, 'mi': mi, 'turn': turn_idx, 'asst': asst_idx}
                    r = last_read.get(fp)
                    rec['cat'] = 'no_read' if r is None else (
                        'same_turn' if turn_idx - r[1] == 0 else 'later_turn')
                    writes.append(rec)
                    last_access[fp] = (mi, turn_idx, asst_idx, 'Write')
    return edits, writes, reads, turn_idx

class Cohort:
    def __init__(self, name):
        self.name = name
        self.cat = collections.Counter()
        self.wcat = collections.Counter()
        self.d_turns = []; self.d_assts = []; self.d_msgs = []
        self.anchor_closer = 0
        self.edits = self.writes = self.reads = 0
        self.sessions = 0; self.sessions_with_edits = 0

    def add_session(self, best):
        self.sessions += 1
        sess_edits = 0
        for agent, (f, msgs) in best.items():
            edits, writes, reads, _ = analyze_messages(msgs)
            self.reads += reads
            for e in edits:
                self.cat[e['cat']] += 1; self.edits += 1; sess_edits += 1
                if e['cat'] == 'later_turn':
                    self.d_turns.append(e['d_turn']); self.d_assts.append(e['d_asst'])
                    self.d_msgs.append(e['d_msg'])
                    if e.get('anchor_kind') in ('Edit', 'Write') and e.get('anchor_d_turn') == 0:
                        self.anchor_closer += 1
            for w in writes:
                self.wcat[w['cat']] += 1; self.writes += 1
        if sess_edits:
            self.sessions_with_edits += 1

    def report(self):
        def pct(n, d): return f'{100.0*n/d:.1f}%' if d else 'n/a'
        print(f'=== {self.name} ===')
        print(f'sessions: {self.sessions}  (with >=1 Edit: {self.sessions_with_edits})   '
              f'Reads: {self.reads}  Edits: {self.edits}  Writes: {self.writes}')
        order = ['same_msg', 'same_turn', 'later_turn', 'no_read']
        labels = {'same_msg':'Read+Edit in SAME assistant message',
                  'same_turn':'Edit in SAME turn as its Read',
                  'later_turn':'Edit in a LATER turn (long after Read)',
                  'no_read':'Edit with NO prior Read of file'}
        for k in order:
            print(f'  {labels[k]:38s}: {self.cat[k]:5d}  ({pct(self.cat[k],self.edits)})')
        if self.d_turns:
            dt = sorted(self.d_turns); da = sorted(self.d_assts); dm = sorted(self.d_msgs)
            n = len(dt)
            def stat(a): return f'p50={a[n//2]} p90={a[int(n*0.9)]} max={a[-1]} mean={sum(a)/n:.1f}'
            print(f'  later-turn gap: turns[{stat(dt)}]')
            print(f'                  msgs [{stat(dm)}]')
            print(f'                  re-anchored same-turn by a Write/Edit: '
                  f'{self.anchor_closer} ({pct(self.anchor_closer,n)})')
        print(f'  Writes: no_read={self.wcat["no_read"]} same_turn={self.wcat["same_turn"]} '
              f'later_turn={self.wcat["later_turn"]}')
        print()

def main():
    root = sys.argv[1] if len(sys.argv) > 1 else 'logs_main'
    thresh = float(sys.argv[2]) if len(sys.argv) > 2 else 0.20
    sessions = [d for d in glob.glob(os.path.join(root, '*')) if os.path.isdir(d)
                and not os.path.basename(d).startswith('_')]
    allc = Cohort('ALL sessions'); wb = Cohort(f'WORKBENCH (inj>={thresh:.0%})')
    clean = Cohort(f'CLEAN coding (inj<{thresh:.0%})')

    for s in sessions:
        best = load_largest_per_agent(s)
        if not best:
            continue
        # session inj-ratio = max over its agent lines (any fleet line marks it)
        r = max(inj_ratio(msgs) for _, msgs in best.values())
        allc.add_session(best)
        (wb if r >= thresh else clean).add_session(best)

    print(f'### Read->Edit locality :: {root}  (workbench cutoff inj>= {thresh:.0%})\n')
    for c in (allc, wb, clean):
        c.report()

if __name__ == '__main__':
    main()
