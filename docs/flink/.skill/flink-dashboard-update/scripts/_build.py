#!/usr/bin/env python3
"""
Batch generator: download Apache mailing-list threads listed in todo-data.json
and produce a self-contained HTML report per thread under dissuss/.

Implements the spec in mail-thread-archiver.prompt.md (English-only variant
without 中文翻译栏 — kept simple for batch run).
"""
import json
import re
import html
import urllib.request
import os
import sys
from email import policy, message_from_string

ROOT = os.path.dirname(os.path.abspath(__file__))
PARENT = os.path.dirname(ROOT)
DATA = os.path.join(PARENT, 'todo-data.json')

API_THREAD = 'https://lists.apache.org/api/thread.lua?id={tid}'
API_SOURCE = 'https://lists.apache.org/api/source.lua?id={mid}'


def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': 'flink-dashboard-archiver/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', 'replace')


def collect_emails(thread_id):
    """Walk the thread tree and return a flat list of {from,time,subject,mid,body}."""
    raw = fetch(API_THREAD.format(tid=thread_id))
    root = json.loads(raw)
    out = []

    def walk(node):
        # node is the tree-shaped thread; the API returns either {thread:{...}} or
        # a top-level dict with `children`. Cope with both.
        if not isinstance(node, dict):
            return
        mid = node.get('mid') or node.get('message-id-hash')
        if mid:
            try:
                src = fetch(API_SOURCE.format(mid=mid))
                msg = message_from_string(src, policy=policy.default)
                body_part = msg.get_body(preferencelist=('plain', 'html'))
                body = body_part.get_content() if body_part else ''
                if body_part is not None and body_part.get_content_type() == 'text/html':
                    # very light HTML to text fallback
                    body = re.sub(r'<br\s*/?>', '\n', body, flags=re.I)
                    body = re.sub(r'<[^>]+>', '', body)
                    body = html.unescape(body)
                out.append({
                    'from': str(msg['From'] or node.get('from') or ''),
                    'time': str(msg['Date'] or ''),
                    'epoch': node.get('epoch') or 0,
                    'subject': str(msg['Subject'] or node.get('subject') or ''),
                    'mid': mid,
                    'body': body,
                })
            except Exception as e:
                # Fallback to thread metadata only
                out.append({
                    'from': node.get('from', ''),
                    'time': '',
                    'epoch': node.get('epoch') or 0,
                    'subject': node.get('subject', ''),
                    'mid': mid,
                    'body': f'[failed to fetch source: {e}]',
                })
        # Recurse children
        for ch in node.get('children', []) or []:
            walk(ch)

    # Root response shape: {"thread": {...}, "emails": [...]}
    if 'thread' in root:
        walk(root['thread'])
        # Also walk 'emails' list (siblings) if present
        for e in root.get('emails', []) or []:
            if isinstance(e, dict) and e.get('mid') and not any(o['mid'] == e['mid'] for o in out):
                walk(e)
    elif 'children' in root:
        walk(root)

    out.sort(key=lambda m: m.get('epoch') or 0)
    return out


# ---------- block splitting ----------
def split_blocks(body):
    blocks, buf, cur_kind, cur_depth = [], [], None, 0

    def flush():
        nonlocal buf, cur_kind, cur_depth
        if buf:
            blocks.append({'kind': cur_kind, 'depth': cur_depth, 'lines': list(buf)})
            buf = []

    for line in body.splitlines():
        m = re.match(r'^((?:\s*>)+)\s?(.*)$', line)
        if m:
            depth = m.group(1).count('>')
            text = m.group(2)
            kind = 'quote'
        else:
            depth, text, kind = 0, line, 'text'
        if kind != cur_kind or (kind == 'quote' and depth != cur_depth):
            flush()
            cur_kind, cur_depth = kind, depth
        buf.append(text)
    flush()
    return blocks


# ---------- HTML render ----------
URL_RE = re.compile(r'https?://[^\s<>"\']+', re.I)


def linkify(text):
    """Escape HTML, then turn URLs into <a> tags."""
    escaped = html.escape(text)
    def repl(m):
        u = m.group(0)
        return f'<a href="{u}" target="_blank" rel="noopener">{u}</a>'
    return URL_RE.sub(repl, escaped)


def render_block(b):
    if b['kind'] == 'text':
        # join with \n; rely on white-space:pre-wrap
        text = '\n'.join(b['lines']).strip('\n')
        if not text.strip():
            return ''
        return f'<div class="text-block">{linkify(text)}</div>'
    # quote
    depth = min(max(b['depth'], 1), 5)
    inner = []
    for ln in b['lines']:
        if ln.strip() == '':
            inner.append('<span class="qd qd-empty"></span>')
        else:
            inner.append(f'<span class="qd qd-{depth}">{linkify(ln)}</span>')
    nlines = len(b['lines'])
    return (f'<details class="quote-block"><summary>引用 ({nlines} lines, depth {depth})</summary>'
            f'<div class="quoted">{"".join(inner)}</div></details>')


PAGE_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#57606a;--card:#f6f8fa;--accent:#0969da;--border:#d0d7de;--quote-bg:#fbfbfd}}
body.dark{{--bg:#0d1117;--fg:#c9d1d9;--muted:#8b949e;--card:#161b22;--accent:#58a6ff;--border:#30363d;--quote-bg:#0b0e13}}
*{{box-sizing:border-box}}
html,body{{background:var(--bg);color:var(--fg)}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55;max-width:none;margin:0;padding:0 32px 64px}}
.toolbar{{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:14px 0;margin-bottom:18px;display:flex;flex-wrap:wrap;align-items:center;gap:12px}}
.toolbar h1{{font-size:18px;margin:0;flex:1 1 auto;min-width:240px}}
.toolbar .status{{color:var(--muted);font-size:12px}}
.toolbar button, .toolbar a.btn{{font-size:12px;padding:6px 12px;border:1px solid var(--border);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer;text-decoration:none;display:inline-block}}
.toolbar button:hover, .toolbar a.btn:hover{{border-color:var(--accent);color:var(--accent)}}
.email{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:16px}}
.email header{{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:10px;font-size:12px;color:var(--muted)}}
.email .from{{font-weight:600;color:var(--fg)}}
.email .subj{{flex:1 1 100%;font-weight:500;color:var(--fg);font-size:13px}}
.body-font{{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:13px}}
.text-block{{white-space:pre-wrap;word-wrap:break-word;margin:6px 0}}
details.quote-block{{margin:6px 0;border-left:3px solid var(--border);background:var(--quote-bg);padding:4px 8px;border-radius:0 4px 4px 0}}
details.quote-block summary{{cursor:pointer;color:var(--muted);font-size:11px;user-select:none;padding:2px 0}}
.quoted{{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12px;margin-top:6px}}
.qd{{display:block;white-space:pre-wrap;padding-left:8px;border-left:2px solid;margin:1px 0}}
.qd-1{{border-color:#0969da;color:var(--fg)}}
.qd-2{{border-color:#1a7f37;color:var(--fg);opacity:.95}}
.qd-3{{border-color:#bf8700;color:var(--fg);opacity:.9}}
.qd-4{{border-color:#cf222e;color:var(--fg);opacity:.85}}
.qd-5{{border-color:#8250df;color:var(--fg);opacity:.8}}
.qd-empty{{display:block;min-height:.5em;border-left:none;padding:0}}
a{{color:var(--accent);text-decoration:none}}
a:hover{{text-decoration:underline}}
.summary-card{{background:linear-gradient(135deg,rgba(9,105,218,.08),rgba(130,80,223,.05));border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:18px}}
.summary-card h2{{font-size:14px;margin:0 0 6px;color:var(--accent)}}
.summary-card p{{margin:6px 0;color:var(--muted);font-size:13px}}
.summary-card ul{{margin:6px 0 0 18px;color:var(--fg);font-size:13px}}
</style>
</head>
<body>
<div class="toolbar">
  <h1>{title}</h1>
  <span class="status">{n_emails} emails · {n_quotes} quote blocks</span>
  <button onclick="document.querySelectorAll('details.quote-block').forEach(d=>d.open=!d.open)">展开/折叠引用</button>
  <button onclick="document.body.classList.toggle('dark')">深色/浅色</button>
  <a class="btn" href="{thread_url}" target="_blank">↗ 在 lists.apache.org 打开</a>
  <a class="btn" href="../index.html">← 返回看板</a>
</div>
<div class="summary-card">
  <h2>📖 AI 解读 (auto-generated stub)</h2>
  <p>本报告由 <code>mail-thread-archiver.prompt.md</code> 流程批量归档生成，包含完整邮件原文（按时间升序），用于离线阅读和后续翻译/批注。</p>
  <ul>
    <li><b>主题</b>: {title}</li>
    <li><b>归档</b>: {n_emails} 封邮件，{n_quotes} 个引用块</li>
    <li><b>原始链接</b>: <a href="{thread_url}" target="_blank">{thread_url}</a></li>
  </ul>
  <p style="margin-top:10px;font-size:12px">提示：要生成中文翻译栏 / 深度技术解读，请把本目录的 <code>../mail-thread-archiver.prompt.md</code> 与本 thread URL 一起喂给具备 Python 与 LLM 能力的 agent，按 §1.7 骨架重跑即可覆盖本文件。</p>
</div>
{emails_html}
</body>
</html>
"""

EMAIL_TPL = """<div class="email" id="m{idx}">
  <header>
    <span class="from">{frm}</span>
    <span class="time">{time}</span>
    <span class="subj">{subj}</span>
  </header>
  <div class="orig body-font">{blocks_html}</div>
</div>"""


def render_email(idx, m):
    blocks = split_blocks(m['body'] or '')
    blocks_html = ''.join(render_block(b) for b in blocks)
    return EMAIL_TPL.format(
        idx=idx,
        frm=html.escape(m['from'] or '(unknown)'),
        time=html.escape(m['time'] or ''),
        subj=html.escape(m['subject'] or ''),
        blocks_html=blocks_html or '<div class="text-block">(empty body)</div>'
    ), sum(1 for b in blocks if b['kind'] == 'quote')


def render_thread_html(disc, emails):
    title = disc['subject']
    parts, total_quotes = [], 0
    for i, m in enumerate(emails, 1):
        h, q = render_email(i, m)
        parts.append(h)
        total_quotes += q
    return PAGE_TPL.format(
        title=html.escape(title),
        n_emails=len(emails),
        n_quotes=total_quotes,
        thread_url=html.escape(disc['url']),
        emails_html='\n'.join(parts),
    )


def thread_id_from_url(url):
    m = re.search(r'/thread/([a-z0-9]+)', url)
    return m.group(1) if m else None


def main():
    with open(DATA, 'r', encoding='utf-8') as f:
        data = json.load(f)
    discs = data.get('discussions', [])
    only = sys.argv[1] if len(sys.argv) > 1 else None
    print(f'[i] {len(discs)} discussions in todo-data.json')
    for d in discs:
        tid = thread_id_from_url(d['url'])
        if not tid:
            print(f'  [skip] no thread id: {d["url"]}')
            continue
        if only and tid != only:
            continue
        out = os.path.join(ROOT, f'{tid}.html')
        if os.path.exists(out) and not only:
            print(f'  [skip exists] {tid}')
            continue
        print(f'  [fetch] {tid} — {d["subject"][:60]}')
        try:
            mails = collect_emails(tid)
            html_doc = render_thread_html(d, mails)
            with open(out, 'w', encoding='utf-8') as f:
                f.write(html_doc)
            print(f'    -> {out}  ({len(mails)} emails)')
        except Exception as e:
            print(f'    [error] {e}')


if __name__ == '__main__':
    main()
