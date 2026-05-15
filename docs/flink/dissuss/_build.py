#!/usr/bin/env python3
"""
Batch generator: download Apache mailing-list threads listed in todo-data.json
and produce a self-contained HTML report per thread under dissuss/.

Implements the spec in mail-thread-archiver.prompt.md:
  - Two-column grid (left = English original, right = Chinese translation)
  - Quote blocks rendered as collapsible <details>
  - Toolbar: expand/collapse all quotes, show/hide translation, dark/light theme
  - Translations sourced from optional <tid>.translation.json
        {
          "<message-id>": {
              "blocks": [
                  {"type": "text", "lines": ["译文段 1", "译文段 2"]},
                  ...
              ]
          },
          ...
        }
    Block index must align with the original split_blocks() output.
    Quote blocks are kept English (per spec §3.5) so the translation file
    only needs to provide entries for kind=="text" blocks (others ignored).
  - Pending blocks are rendered as `<em>pending translation</em>` so the
    grid layout is preserved and a follow-up agent / LLM run can fill them.

Usage:
    python3 _build.py                # build all missing threads
    python3 _build.py <thread_id>    # rebuild a single thread (overwrites)
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
                out.append({
                    'from': node.get('from', ''),
                    'time': '',
                    'epoch': node.get('epoch') or 0,
                    'subject': node.get('subject', ''),
                    'mid': mid,
                    'body': f'[failed to fetch source: {e}]',
                })
        for ch in node.get('children', []) or []:
            walk(ch)

    if 'thread' in root:
        walk(root['thread'])
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


def render_orig_block(b):
    """Render one original-side block (English)."""
    if b['kind'] == 'text':
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
    summary = f'Quoted ({nlines} lines, depth {depth})'
    return (f'<details class="quote-block"><summary>{summary}</summary>'
            f'<div class="quoted">{"".join(inner)}</div></details>')


def render_trans_block(orig_block, trans_block):
    """Render right-side translation block. Quote blocks reuse English (per spec §3.5)."""
    if orig_block['kind'] == 'quote':
        # spec §3.5: 引用块保持英文（已在对应邮件翻译过）
        return '<div class="trans-quote-placeholder" aria-hidden="true">&nbsp;</div>'
    # text block
    if trans_block and trans_block.get('lines'):
        text = '\n'.join(trans_block['lines']).strip('\n')
        if text.strip():
            return f'<div class="text-block">{linkify(text)}</div>'
    # original text was non-empty? show pending
    text = '\n'.join(orig_block['lines']).strip('\n')
    if not text.strip():
        return ''
    return '<div class="text-block trans-pending"><em>pending translation</em></div>'


def render_email(idx, m, trans_for_mid):
    blocks = split_blocks(m['body'] or '')
    if not blocks:
        blocks = [{'kind': 'text', 'depth': 0, 'lines': ['(empty body)']}]

    # Align translation entries to text-block index inside this email
    tblocks_in = (trans_for_mid or {}).get('blocks') or []
    tb_iter = iter([b for b in tblocks_in if b.get('type') == 'text'])

    orig_html_parts, trans_html_parts = [], []
    n_quote = 0
    for b in blocks:
        if b['kind'] == 'quote':
            n_quote += 1
            orig_html_parts.append(render_orig_block(b))
            trans_html_parts.append(render_trans_block(b, None))
        else:
            tb = next(tb_iter, None)
            orig_html_parts.append(render_orig_block(b))
            trans_html_parts.append(render_trans_block(b, tb))

    email_html = EMAIL_TPL.format(
        idx=idx,
        frm=html.escape(m['from'] or '(unknown)'),
        time=html.escape(m['time'] or ''),
        subj=html.escape(m['subject'] or ''),
        mid=html.escape(m['mid'] or ''),
        orig_html=''.join(orig_html_parts) or '<div class="text-block">(empty body)</div>',
        trans_html=''.join(trans_html_parts) or '<div class="text-block trans-pending"><em>pending translation</em></div>',
    )
    return email_html, n_quote


PAGE_TPL = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{--bg:#fff;--fg:#1f2328;--muted:#57606a;--card:#f6f8fa;--accent:#0969da;--border:#d0d7de;--quote-bg:#fbfbfd;--trans-bg:#fafbff}}
body.dark{{--bg:#0d1117;--fg:#c9d1d9;--muted:#8b949e;--card:#161b22;--accent:#58a6ff;--border:#30363d;--quote-bg:#0b0e13;--trans-bg:#0f1320}}
*{{box-sizing:border-box}}
html,body{{background:var(--bg);color:var(--fg)}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;font-size:14px;line-height:1.55;max-width:none;margin:0;padding:0 32px 64px}}
.toolbar{{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:14px 0;margin-bottom:18px;display:flex;flex-wrap:wrap;align-items:center;gap:12px}}
.toolbar h1{{font-size:18px;margin:0;flex:1 1 auto;min-width:240px}}
.toolbar .status{{color:var(--muted);font-size:12px}}
.toolbar button, .toolbar a.btn{{font-size:12px;padding:6px 12px;border:1px solid var(--border);background:var(--card);color:var(--fg);border-radius:6px;cursor:pointer;text-decoration:none;display:inline-block}}
.toolbar button:hover, .toolbar a.btn:hover{{border-color:var(--accent);color:var(--accent)}}
.email{{background:var(--card);border:1px solid var(--border);border-radius:8px;padding:14px 18px;margin-bottom:16px}}
.email>header{{display:flex;flex-wrap:wrap;gap:8px 16px;align-items:baseline;border-bottom:1px solid var(--border);padding-bottom:8px;margin-bottom:10px;font-size:12px;color:var(--muted)}}
.email .from{{font-weight:600;color:var(--fg)}}
.email .subj{{flex:1 1 100%;font-weight:500;color:var(--fg);font-size:13px}}
.email .mid{{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:11px;color:var(--muted);word-break:break-all}}
.body-grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px}}
.orig{{min-width:0}}
.trans{{min-width:0;background:var(--trans-bg);border-left:3px solid var(--accent);border-radius:0 6px 6px 0;padding:8px 12px}}
.trans-label{{font-size:10px;color:var(--accent);font-weight:600;letter-spacing:.08em;text-transform:uppercase;margin-bottom:6px;opacity:.85}}
body.hide-trans .body-grid{{grid-template-columns:1fr}}
body.hide-trans .trans{{display:none}}
@media (max-width:860px){{.body-grid{{grid-template-columns:1fr}}.trans{{border-left:none;border-top:3px solid var(--accent);border-radius:0 0 6px 6px}}}}
.body-font{{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:13px}}
.trans .body-font, .trans-body{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;font-size:13px}}
.text-block{{white-space:pre-wrap;word-wrap:break-word;margin:6px 0}}
.trans-pending{{color:var(--muted);font-style:italic;font-size:12px}}
.trans-quote-placeholder{{margin:6px 0;min-height:1.4em;opacity:0}}
details.quote-block{{margin:6px 0;border-left:3px solid var(--border);background:var(--quote-bg);padding:4px 8px;border-radius:0 4px 4px 0}}
details.quote-block summary{{cursor:pointer;color:var(--muted);font-size:11px;user-select:none;padding:2px 0;list-style:none}}
details.quote-block summary::-webkit-details-marker{{display:none}}
details.quote-block summary::before{{content:'▶ ';display:inline-block;transition:transform .12s;margin-right:4px}}
details.quote-block[open]>summary::before{{transform:rotate(90deg)}}
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
  <span class="status">{n_emails} emails · {n_quotes} quote blocks · {trans_status}</span>
  <button onclick="document.querySelectorAll('details.quote-block').forEach(d=>d.open=!d.open)">Expand / collapse quotes</button>
  <button onclick="document.body.classList.toggle('hide-trans')">Show / hide translation</button>
  <button onclick="document.body.classList.toggle('dark')">Dark / light</button>
  <a class="btn" href="{thread_url}" target="_blank">↗ Open on lists.apache.org</a>
  <a class="btn" href="../index.html">← Back to dashboard</a>
</div>
<div class="summary-card">
  <h2>Mailing-list thread archive</h2>
  <p>Generated by <code>dissuss/_build.py</code> following <code>mail-thread-archiver.prompt.md</code>. Layout: left = English original (with collapsible quotes), right = Chinese translation (per spec §3.5). Use the <em>Show / hide translation</em> button to toggle the right column.</p>
  <ul>
    <li><b>Subject</b>: {title}</li>
    <li><b>Archive</b>: {n_emails} emails, {n_quotes} quote blocks</li>
    <li><b>Source</b>: <a href="{thread_url}" target="_blank">{thread_url}</a></li>
    <li><b>Translation</b>: {trans_status} (drop a <code>{tid}.translation.json</code> beside this file and rebuild to fill it in)</li>
  </ul>
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
    <span class="mid">&lt;{mid}&gt;</span>
  </header>
  <div class="body-grid">
    <div class="orig body-font">{orig_html}</div>
    <div class="trans">
      <div class="trans-label">中文翻译 · Chinese translation</div>
      <div class="trans-body body-font">{trans_html}</div>
    </div>
  </div>
</div>"""


def load_translations(tid):
    """Return dict {mid: {blocks: [...]}} from <tid>.translation.json (if exists)."""
    path = os.path.join(ROOT, f'{tid}.translation.json')
    if not os.path.exists(path):
        return None, 'pending (no translation file)'
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        n = len(data) if isinstance(data, dict) else 0
        return data, f'translated ({n} emails)'
    except Exception as e:
        return None, f'pending (failed to load translation file: {e})'


def render_thread_html(disc, emails, tid):
    title = disc['subject']
    translations, trans_status = load_translations(tid)
    parts, total_quotes = [], 0
    for i, m in enumerate(emails, 1):
        t_for_mid = (translations or {}).get(m.get('mid')) if translations else None
        h, q = render_email(i, m, t_for_mid)
        parts.append(h)
        total_quotes += q
    return PAGE_TPL.format(
        title=html.escape(title),
        n_emails=len(emails),
        n_quotes=total_quotes,
        thread_url=html.escape(disc['url']),
        tid=html.escape(tid),
        trans_status=html.escape(trans_status),
        emails_html='\n'.join(parts),
    )


def thread_id_from_url(url):
    m = re.search(r'/thread/([a-z0-9]+)', url)
    return m.group(1) if m else None


def main():
    with open(DATA, 'r', encoding='utf-8') as f:
        data = json.load(f)
    discs = data.get('discussions', [])
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    force = '--force' in sys.argv
    only = args[0] if args else None
    print(f'[i] {len(discs)} discussions in todo-data.json')
    for d in discs:
        tid = thread_id_from_url(d['url'])
        if not tid:
            print(f'  [skip] no thread id: {d["url"]}')
            continue
        if only and tid != only:
            continue
        out = os.path.join(ROOT, f'{tid}.html')
        # Always regenerate when explicitly requested OR when forcing rebuild;
        # in batch mode skip existing files only if no `only` filter.
        if os.path.exists(out) and not only and not force:
            print(f'  [skip exists] {tid}')
            continue
        print(f'  [fetch] {tid} — {d["subject"][:60]}')
        try:
            mails = collect_emails(tid)
            html_doc = render_thread_html(d, mails, tid)
            with open(out, 'w', encoding='utf-8') as f:
                f.write(html_doc)
            print(f'    -> {out}  ({len(mails)} emails)')
        except Exception as e:
            print(f'    [error] {e}')


if __name__ == '__main__':
    main()
