#!/usr/bin/env python3
"""Extract all text blocks from every dissuss thread, output to _pending_translations.json.

Schema:
{
  "<tid>": {
     "subject": "...",
     "emails": [
        {"mid": "...", "from": "...", "blocks": [
            {"idx": 0, "text": "english paragraph..."},
            ...
        ]}
     ]
  }
}

Only kind=='text' blocks with non-empty stripped content are exported.
The block "idx" matches the order among kind=='text' blocks within that email
(this is exactly what _build.py's render_email iterates over via tb_iter).
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _build import collect_emails, split_blocks, thread_id_from_url, DATA  # type: ignore

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    with open(DATA, 'r', encoding='utf-8') as f:
        data = json.load(f)
    discs = data.get('discussions', [])
    out = {}
    for d in discs:
        tid = thread_id_from_url(d['url'])
        if not tid:
            continue
        print(f'[fetch] {tid} - {d["subject"][:60]}')
        try:
            mails = collect_emails(tid)
        except Exception as e:
            print(f'  [error] {e}')
            continue
        out[tid] = {
            'subject': d['subject'],
            'url': d['url'],
            'emails': [],
        }
        for m in mails:
            blocks = split_blocks(m.get('body') or '')
            text_blocks = []
            t_idx = 0
            for b in blocks:
                if b['kind'] != 'text':
                    continue
                text = '\n'.join(b['lines']).strip('\n')
                if not text.strip():
                    t_idx += 1
                    continue
                text_blocks.append({'idx': t_idx, 'text': text})
                t_idx += 1
            out[tid]['emails'].append({
                'mid': m.get('mid', ''),
                'from': m.get('from', ''),
                'subject': m.get('subject', ''),
                'time': m.get('time', ''),
                'blocks': text_blocks,
            })
    dest = os.path.join(ROOT, '_pending_translations.json')
    with open(dest, 'w', encoding='utf-8') as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    n_threads = len(out)
    n_emails = sum(len(v['emails']) for v in out.values())
    n_blocks = sum(len(e['blocks']) for v in out.values() for e in v['emails'])
    print(f'[done] {n_threads} threads, {n_emails} emails, {n_blocks} text blocks -> {dest}')


if __name__ == '__main__':
    main()
