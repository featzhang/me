#!/usr/bin/env python3
"""Helper: read a single combined translations file and split into <tid>.translation.json.

Input file: _all_translations.json
Schema:
{
  "<tid>": {
     "<mid>": {
        "blocks": [
           {"type":"text","lines":["译文段1","译文段2"]},
           ...
        ]
     }
  }
}

For each tid, writes a sibling <tid>.translation.json containing only the
inner mid->blocks mapping (matching the format _build.py expects).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def main():
    src = os.path.join(ROOT, '_all_translations.json')
    if not os.path.exists(src):
        print(f'[error] {src} not found', file=sys.stderr)
        sys.exit(1)
    with open(src, 'r', encoding='utf-8') as f:
        data = json.load(f)
    for tid, mid_map in data.items():
        out_path = os.path.join(ROOT, f'{tid}.translation.json')
        with open(out_path, 'w', encoding='utf-8') as f:
            json.dump(mid_map, f, ensure_ascii=False, indent=2)
        n_emails = len(mid_map)
        n_blocks = sum(len(v.get('blocks', [])) for v in mid_map.values())
        print(f'  wrote {out_path}  ({n_emails} emails, {n_blocks} text blocks)')


if __name__ == '__main__':
    main()
