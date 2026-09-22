#!/usr/bin/env python3
"""What ENTERS the coordinator seat's window per request, by source, priced at
the write rate plus the re-reads until the next compaction.

Diffs consecutive request bodies of one agent hash: new = messages appended
since the previous request (plus text blocks the CLI appended to the previously
last message). A shrink >40% = post-compact rebuild, classified separately.

usage: inbound.py <session_dir> <agent_hash> <model_tag> <out.json>
  e.g. inbound.py .../5383fbbc-... 5653f2b5 fable-5-1 fable.json
"""
import json, os, re, sys, glob, statistics as st
from collections import Counter, defaultdict
sys.path.insert(0, '/Users/bogdan/projects/proxy-lab')
from proxylab import billing

CH = 3.0   # message JSON density measured 2.98 ch/tok (tokest)
COMPACT_NEEDLE = "Your task is to create a detailed summary of the conversation so far"

def classify_text(t):
    s = t.lstrip('\x15 \n')
    if COMPACT_NEEDLE in s: return 'compact_instruction'
    if s.startswith('This session is being continued from a previous conversation'): return 'compact_summary'
    if s.startswith('[agent:from user]'): return 'operator'
    if s.startswith('[agent:from reminder]') or s.startswith('[agent:remind]'): return 'reminder'
    if s.startswith('[agent:from ticket-loop]') or s.startswith('[agent:task]'): return 'task_board'
    if s.startswith('[terminal]'): return 'terminal'
    if s.startswith('[agent:from clodex-reviewer'): return 'dm_reviewer'
    if s.startswith('[agent:from clodex-hand'): return 'dm_hand'
    if s.startswith('[agent:from '): return 'dm_peer'
    if s.startswith('[agent:'): return 'intent_ack'
    if s.startswith('<system-reminder>'):
        if 'memory store' in s[:200]: return 'cli_memory_attach'
        if '# Environment' in s[:200] or 'currentDate' in s[:300]: return 'cli_env_reminder'
        return 'cli_system_reminder'
    if s.startswith('<command-name>') or s.startswith('<local-command'): return 'cli_command'
    if s.startswith('[Image:'): return 'image'
    if re.match(r'^Clodex lead', s): return 'boot_prompt'
    return 'operator'

def classify_system(t):
    m = re.match(r'Called the (\w+) tool with the following input: (\{.*?\})\n', t, re.S)
    if m:
        inp = m.group(2)
        if '/.clodex/messages/' in inp:
            fm = re.search(r'\nFrom: ([^\n]+)', t[:600])
            who = (fm.group(1).strip() if fm else '?')
            if who.startswith('clodex-reviewer'): return 'dm_reviewer_body'
            if who.startswith('clodex-hand'): return 'dm_hand_body'
            if who == 'reminder': return 'reminder_body'
            return 'dm_peer_body'
        return 'cli_autoread'
    return 'cli_roster_system'

def tool_result_cat(name, inp):
    s = json.dumps(inp or {})
    if '/.clodex/messages/' in s: return 'tool:read_dm_attachment'
    if name in ('Bash', 'Read') and re.search(r'/tasks/[^"\s]*\.(md|txt|json)|JOURNAL|live\.md', s): return 'tool:read_task_file'
    return f'tool:{name or "?"}'

def block_text(b):
    if isinstance(b, str): return b
    if b.get('type') == 'text': return b.get('text') or ''
    if b.get('type') == 'tool_result':
        c = b.get('content')
        if isinstance(c, str): return c
        return ''.join((y.get('text') or '') for y in (c or []) if isinstance(y, dict) and y.get('type') == 'text')
    if b.get('type') == 'thinking': return b.get('thinking') or ''
    if b.get('type') == 'tool_use': return json.dumps(b.get('input') or {})
    return ''

HINT_HEAD = 'First privately list what you need next'
def strip_proxy_tail(msgs):
    # captured bodies are POST-transform: drop the proxy-injected trailing system hint
    out = list(msgs)
    while out and out[-1].get('role') == 'system' and HINT_HEAD in block_text(blocks(out[-1])[0] if blocks(out[-1]) else ''):
        out.pop()
    return out

def canon(m):
    def cb(x):
        if isinstance(x, str): return {'type': 'text', 'text': x}
        x = dict(x); x.pop('cache_control', None); return x
    c = m.get('content'); bl = [cb(c)] if isinstance(c, str) else [cb(x) for x in c if isinstance(x, dict)]
    bl = [x for x in bl if x.get('type') not in ('thinking', 'redacted_thinking')]
    return bl

def same_msg(a, b):
    if a['role'] != b['role']: return False
    ca, cb_ = canon(a), canon(b)
    ia = [x.get('tool_use_id') or x.get('id') for x in ca if x.get('type') in ('tool_result', 'tool_use')]
    ib = [x.get('tool_use_id') or x.get('id') for x in cb_ if x.get('type') in ('tool_result', 'tool_use')]
    if ia or ib: return ia == ib
    ta = ''.join(block_text(x) for x in ca)[:200]; tb = ''.join(block_text(x) for x in cb_)[:200]
    return ta == tb

def blocks(m):
    c = m.get('content')
    if isinstance(c, str): return [{'type': 'text', 'text': c}]
    return [x for x in c if isinstance(x, dict)]

def main():
    sdir, ahash, mtag, out = sys.argv[1:5]
    fs = sorted(glob.glob(f'{sdir}/*-{ahash}-parent-{mtag}-*.request.json'), key=lambda f: int(os.path.basename(f).split('-')[0]))
    print(f'{len(fs)} requests', file=sys.stderr)
    id2tool = {}
    prev = None
    recs = []
    for f in fs:
        rf = f.replace('.request.json', '.response.json')
        if not os.path.exists(rf): continue
        r = json.load(open(f)); o = json.load(open(rf))
        if o.get('status_code') != 200: continue
        b = r['body']; msgs = strip_proxy_tail(b.get('messages') or [])
        tk = {k: (v or 0) for k, v in ((o.get('billing') or {}).get('tokens') or {}).items()}
        if not (b.get('tools')): continue
        for m in msgs:
            if m['role'] == 'assistant':
                for x in blocks(m):
                    if x.get('type') == 'tool_use': id2tool[x['id']] = (x.get('name'), x.get('input'))
        mode = 'delta'
        if prev is None or len(msgs) < 0.6 * len(prev):
            mode = 'rebuild' if prev is not None else 'first'
            new = list(enumerate(msgs)); extra_blocks = []
        elif len(msgs) >= len(prev) and same_msg(msgs[len(prev) - 1], prev[-1]):
            new = list(enumerate(msgs))[len(prev):]
            # blocks the CLI appended to the previously-last message
            extra_blocks = []
            pb = [json.dumps(x, sort_keys=True) for x in canon(prev[-1])]
            for x in canon(msgs[len(prev) - 1]):
                if json.dumps(x, sort_keys=True) not in pb: extra_blocks.append((msgs[len(prev) - 1]['role'], x))
        else:
            mode = 'realign'; new = list(enumerate(msgs)); extra_blocks = []
        cat = Counter(); items = []
        def add(c, txt, note=''):
            cat[c] += len(txt)
            if len(txt) >= 1500: items.append((len(txt), c, note or txt[:90].replace('\n', ' | ')))
        for i, m in new:
            role = m['role']
            for x in blocks(m):
                txt = block_text(x)
                if role == 'assistant':
                    t = x.get('type')
                    add('out_thinking' if t == 'thinking' else 'out_tool_input' if t == 'tool_use' else 'out_text', txt)
                elif role == 'system':
                    add(classify_system(txt), txt)
                elif x.get('type') == 'tool_result':
                    name, inp = id2tool.get(x.get('tool_use_id'), (None, None))
                    add(tool_result_cat(name, inp), txt, (name or '?') + ' ' + json.dumps(inp or {})[:80])
                elif x.get('type') == 'image':
                    cat['image'] += 0
                else:
                    add(classify_text(txt), txt)
        for role, x in extra_blocks:
            txt = block_text(x)
            add(('extra_' + classify_text(txt)) if role == 'user' else 'extra_other', txt)
        if mode != 'delta':
            cat = Counter({f'REBUILD:{k}': v for k, v in cat.items()})
        n_user_new = sum(1 for i, m in new if m['role'] == 'user')
        recs.append(dict(seq=int(os.path.basename(f).split('-')[0]), ts=r.get('ts'), mode=mode, n_msgs=len(msgs),
                         new_msgs=len(new), new_user_msgs=n_user_new, cat=dict(cat), items=items,
                         w=tk.get('cache_write_1h_tokens', 0) + tk.get('cache_write_5m_tokens', 0), inp=tk.get('input_tokens', 0),
                         read=tk.get('cache_read_input_tokens', 0), out=tk.get('output_tokens', 0), think=tk.get('thinking_tokens', 0),
                         compact=COMPACT_NEEDLE in json.dumps(blocks(msgs[-1]))[:0] or any(COMPACT_NEEDLE in block_text(x) for x in blocks(msgs[-1]) if x.get('type') == 'text'),
                         model=o.get('model')))
        prev = msgs
    json.dump(recs, open(out, 'w'))
    print(f'wrote {len(recs)} to {out}', file=sys.stderr)

if __name__ == '__main__':
    main()
