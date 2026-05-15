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
    """Walk the thread tree and return a flat list of {from,time,subject,mid,body,in_reply_to}."""
    raw = fetch(API_THREAD.format(tid=thread_id))
    root = json.loads(raw)
    out = []

    def walk(node, parent_mid=None):
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
                    'in_reply_to': parent_mid,
                })
            except Exception as e:
                out.append({
                    'from': node.get('from', ''),
                    'time': '',
                    'epoch': node.get('epoch') or 0,
                    'subject': node.get('subject', ''),
                    'mid': mid,
                    'body': f'[failed to fetch source: {e}]',
                    'in_reply_to': parent_mid,
                })
        for ch in node.get('children', []) or []:
            walk(ch, mid)

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
    # preview: first non-empty line, truncated
    preview = ''
    for ln in b['lines']:
        if ln.strip():
            preview = ln.strip()
            break
    if len(preview) > 80:
        preview = preview[:78] + '…'
    preview_html = f' <span class="qpreview">{html.escape(preview)}</span>' if preview else ''
    summary = f'📎 Quoted · {nlines} lines · depth {depth}{preview_html}'
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


def render_email(idx, m, trans_for_mid, idx_by_mid):
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

    parent_mid = m.get('in_reply_to')
    parent_idx = idx_by_mid.get(parent_mid) if parent_mid else None
    if parent_idx:
        reply_to_html = (
            f'<div class="reply-to">↩ In reply to '
            f'<a href="#m{parent_idx}">#{parent_idx}</a></div>'
        )
    else:
        reply_to_html = ''

    email_html = EMAIL_TPL.format(
        idx=idx,
        frm=html.escape(m['from'] or '(unknown)'),
        time=html.escape(m['time'] or ''),
        subj=html.escape(m['subject'] or ''),
        mid=html.escape(m['mid'] or ''),
        reply_to_html=reply_to_html,
        orig_html=''.join(orig_html_parts) or '<div class="text-block">(empty body)</div>',
        trans_html=''.join(trans_html_parts) or '<div class="text-block trans-pending"><em>pending translation</em></div>',
    )
    return email_html, n_quote


PAGE_TPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<title>{title}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{{
  --bg:#fff;--fg:#1f2328;--muted:#57606a;--border:#d0d7de;--border-soft:#e4e7eb;
  --accent:#1f6feb;--accent-soft:#ddf4ff;--card:#fff;--header-bg:#f6f8fa;
  --quote-bg:#f6f8fa;--quote-fg:#6e7781;--trans-bg:#fbfdff;--tag-bg:#f3f4f6;--hover:#f0f3f6;
}}
body.dark{{
  --bg:#0d1117;--fg:#c9d1d9;--muted:#8b949e;--border:#30363d;--border-soft:#21262d;
  --accent:#58a6ff;--accent-soft:#1f3a5f;--card:#0d1117;--header-bg:#161b22;
  --quote-bg:#0b0e13;--quote-fg:#8b949e;--trans-bg:#0f1622;--tag-bg:#161b22;--hover:#161b22;
}}
*{{box-sizing:border-box}}
html,body{{background:var(--bg);color:var(--fg)}}
body{{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;font-size:14px;line-height:1.6;margin:0;padding:24px 32px 64px;max-width:none}}
h1{{border-bottom:2px solid var(--accent);padding-bottom:8px;font-size:22px;margin:0 0 8px}}
.meta{{color:var(--muted);font-size:13px;margin-bottom:18px}}
.meta code{{background:var(--tag-bg);padding:2px 6px;border-radius:3px}}
.meta a{{color:var(--accent);text-decoration:none}}
.meta a:hover{{text-decoration:underline}}

.toolbar{{position:sticky;top:0;z-index:10;background:var(--bg);border-bottom:1px solid var(--border);padding:10px 0;margin-bottom:18px;display:flex;flex-wrap:wrap;gap:10px;align-items:center;font-size:13px}}
.toolbar button,.toolbar a.btn{{background:var(--header-bg);border:1px solid var(--border);padding:5px 12px;border-radius:5px;cursor:pointer;color:var(--fg);font-size:12px;text-decoration:none;display:inline-block}}
.toolbar button:hover,.toolbar a.btn:hover{{background:var(--hover);border-color:var(--accent);color:var(--accent)}}
.toolbar .stat{{color:var(--muted);font-size:12px;margin-left:auto}}

.toc{{background:var(--header-bg);border:1px solid var(--border);border-radius:6px;padding:12px 18px;margin-bottom:24px}}
.toc strong{{display:block;margin-bottom:8px;font-size:13px}}
.toc-list{{list-style:none;padding-left:0;margin:0}}
.toc-list li{{padding:3px 0;border-bottom:1px dashed var(--border-soft);font-size:13px}}
.toc-list li:last-child{{border-bottom:none}}
.toc-list a{{color:var(--accent);text-decoration:none}}
.toc-list a:hover{{text-decoration:underline}}
.toc-list .lvl{{display:inline-block;color:var(--muted);font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12px}}

.summary-card{{background:linear-gradient(135deg,rgba(31,111,235,.06),rgba(130,80,223,.04));border:1px solid var(--border);border-radius:6px;padding:12px 16px;margin-bottom:18px;font-size:13px}}
.summary-card h2{{font-size:13px;margin:0 0 6px;color:var(--accent)}}
.summary-card ul{{margin:6px 0 0 18px;padding:0;color:var(--fg)}}
.summary-card li{{margin:2px 0}}
.summary-card a{{color:var(--accent)}}

.email{{border:1px solid var(--border);border-left:4px solid var(--accent);border-radius:6px;margin:18px 0;padding:0;background:var(--card);scroll-margin-top:64px;transition:box-shadow .35s ease}}
.email.flash{{box-shadow:0 0 0 3px var(--accent-soft)}}
.email-header{{background:var(--header-bg);padding:12px 18px;border-bottom:1px solid var(--border);border-radius:5px 5px 0 0;position:relative}}
.email-header .top-line{{display:flex;flex-wrap:wrap;align-items:center;gap:8px}}
.email-header .num{{display:inline-flex;align-items:center;justify-content:center;background:var(--accent);color:#fff;border-radius:50%;width:26px;height:26px;font-weight:bold;font-size:13px;flex-shrink:0}}
.email-header .from{{font-weight:600;color:var(--fg)}}
.email-header .time{{color:var(--muted);font-size:12px}}
.email-header .reply-to{{margin-top:6px;font-size:12px;color:var(--muted)}}
.email-header .reply-to a{{color:var(--accent);text-decoration:none}}
.email-header .reply-to a:hover{{text-decoration:underline}}
.email-header .subject{{margin-top:6px;font-size:13px;color:var(--fg);font-weight:500}}
.email-header .mid{{color:var(--muted);font-size:11px;font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;word-break:break-all;margin-top:4px}}
.anchor-link{{position:absolute;top:10px;right:14px;color:var(--muted);font-size:14px;text-decoration:none}}
.anchor-link:hover{{color:var(--accent)}}

.body-grid{{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:18px;padding:14px 20px}}
.orig{{min-width:0}}
.trans{{min-width:0;border-left:1px dashed var(--border);padding-left:18px;position:relative;background:var(--trans-bg);border-radius:0 4px 4px 0;padding:8px 12px 8px 18px}}
.trans::before{{content:"中文翻译";position:absolute;top:-9px;left:12px;background:var(--card);color:var(--accent);font-size:10px;font-weight:600;padding:0 6px;letter-spacing:.08em;border-radius:3px}}
body.hide-trans .body-grid{{grid-template-columns:1fr}}
body.hide-trans .trans{{display:none}}
@media (max-width:860px){{.body-grid{{grid-template-columns:1fr}}.trans{{border-left:none;border-top:1px dashed var(--border);padding-left:0;padding-top:18px;margin-top:6px}}.trans::before{{top:2px;left:0}}}}

.body-font{{font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:13px}}
.trans .body-font{{font-family:-apple-system,BlinkMacSystemFont,'PingFang SC','Hiragino Sans GB','Microsoft YaHei',sans-serif;font-size:13.5px;line-height:1.7}}
.text-block{{margin:0 0 8px;white-space:pre-wrap;word-wrap:break-word;line-height:1.55}}
.text-block:last-child{{margin-bottom:0}}
.text-block a{{color:var(--accent)}}
.trans-pending{{color:var(--muted);font-style:italic;font-size:12px}}
.trans-quote-placeholder{{margin:6px 0;min-height:1.4em;opacity:0}}

details.quote-block{{margin:8px 0;border:1px solid var(--border-soft);border-radius:5px;background:var(--quote-bg)}}
details.quote-block>summary{{cursor:pointer;padding:6px 12px;font-size:12px;color:var(--muted);list-style:none;user-select:none;outline:none;border-radius:5px}}
details.quote-block>summary::-webkit-details-marker{{display:none}}
details.quote-block>summary::before{{content:"▶";display:inline-block;margin-right:6px;transition:transform .15s ease;font-size:10px}}
details.quote-block[open]>summary::before{{transform:rotate(90deg)}}
details.quote-block>summary:hover{{background:var(--hover)}}
details.quote-block .qpreview{{font-style:italic;opacity:.75;margin-left:8px}}
details.quote-block .quoted{{padding:8px 12px;border-top:1px solid var(--border-soft);font-family:ui-monospace,'SF Mono',Menlo,Consolas,monospace;font-size:12.5px;line-height:1.5;color:var(--quote-fg);word-wrap:break-word}}
.qd{{display:block;white-space:pre-wrap;word-wrap:break-word;min-height:1em}}
.qd-empty{{min-height:.5em;border-left-color:transparent !important}}
.qd-1{{border-left:3px solid #c8d1da;padding-left:8px}}
.qd-2{{border-left:3px solid #d8b4fe;padding-left:8px;margin-left:12px}}
.qd-3{{border-left:3px solid #fdba74;padding-left:8px;margin-left:24px}}
.qd-4{{border-left:3px solid #86efac;padding-left:8px;margin-left:36px}}
.qd-5{{border-left:3px solid #fda4af;padding-left:8px;margin-left:48px}}

a{{color:var(--accent)}}
a:hover{{text-decoration:underline}}
.footer{{margin-top:40px;padding-top:16px;border-top:1px solid var(--border-soft);color:var(--muted);font-size:12px;text-align:center}}
</style>
</head>
<body>
<h1>{title}</h1>
<div class="meta">
  Mailing list: <code>dev@flink.apache.org</code> &nbsp;|&nbsp;
  Thread ID: <code>{tid}</code> &nbsp;|&nbsp;
  Source: <a href="{thread_url}" target="_blank" rel="noreferrer">lists.apache.org</a> &nbsp;|&nbsp;
  Total messages: {n_emails}
</div>
<div class="toolbar">
  <button onclick="toggleAll(true)">展开所有引用</button>
  <button onclick="toggleAll(false)">折叠所有引用</button>
  <button onclick="document.body.classList.toggle('hide-trans')">显示/隐藏中文翻译</button>
  <button onclick="document.body.classList.toggle('dark')">深色 / 浅色</button>
  <a class="btn" href="{thread_url}" target="_blank" rel="noreferrer">↗ lists.apache.org</a>
  <a class="btn" href="../index.html">← Back to dashboard</a>
  <span class="stat" id="stat">{n_emails} emails · {n_quotes} quotes · {trans_status}</span>
</div>
<div class="toc">
  <strong>📋 Index（按时间顺序，缩进表示回复层级）</strong>
  {toc_html}
</div>
<div class="summary-card">
  <h2>Mailing-list thread archive</h2>
  <ul>
    <li><b>Subject</b>: {title}</li>
    <li><b>Archive</b>: {n_emails} emails · {n_quotes} quote blocks · {trans_status}</li>
    <li><b>Source</b>: <a href="{thread_url}" target="_blank" rel="noreferrer">{thread_url}</a></li>
    <li><b>Translation file</b>: <code>{tid}.translation.json</code></li>
  </ul>
</div>
{emails_html}
<div class="footer">
  Generated by <code>dissuss/_build.py</code> from
  <a href="{thread_url}" target="_blank" rel="noreferrer">lists.apache.org</a>
  · Quoted content collapsed by default · Reply relationships shown via "↩ In reply to" links
</div>
<script>
function toggleAll(open){{document.querySelectorAll('details.quote-block').forEach(function(d){{d.open=open}})}}
window.addEventListener('hashchange',function(){{
  var id=location.hash.slice(1);if(!id)return;
  var el=document.getElementById(id);if(!el)return;
  el.classList.add('flash');setTimeout(function(){{el.classList.remove('flash')}},1200);
}});
// flash on initial load if URL has hash
window.addEventListener('DOMContentLoaded',function(){{
  if(location.hash){{
    var el=document.getElementById(location.hash.slice(1));
    if(el){{el.classList.add('flash');setTimeout(function(){{el.classList.remove('flash')}},1200)}}
  }}
}});
</script>
</body>
</html>
"""

EMAIL_TPL = """<div class="email" id="m{idx}">
  <div class="email-header">
    <a class="anchor-link" href="#m{idx}" title="permalink">#</a>
    <div class="top-line">
      <span class="num">{idx}</span>
      <span class="from">{frm}</span>
      <span class="time">{time}</span>
    </div>
    {reply_to_html}
    <div class="subject">Subject: {subj}</div>
    <div class="mid">Message-ID: &lt;{mid}&gt;</div>
  </div>
  <div class="body-grid">
    <div class="orig body-font">{orig_html}</div>
    <div class="trans"><div class="body-font">{trans_html}</div></div>
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


def short_addr(s):
    """Convert 'Name <user@host.com>' -> 'Name <us...@host.com>' (privacy + width)."""
    if not s:
        return '(unknown)'
    m = re.match(r'^(.*?)<\s*([^@\s>]+)@([^\s>]+)\s*>\s*$', s)
    if not m:
        return s
    name, local, host = m.group(1).strip(), m.group(2), m.group(3)
    if len(local) > 2:
        local = local[:2] + '...'
    addr = f'{local}@{host}'
    return f'{name} <{addr}>' if name else f'<{addr}>'


def build_toc(emails, idx_by_mid):
    """Return HTML <ul> of TOC ordered by epoch, indented by reply depth."""
    # depth = chain length from root
    depth_by_mid = {}
    for m in emails:
        parent = m.get('in_reply_to')
        if parent and parent in depth_by_mid:
            depth_by_mid[m['mid']] = depth_by_mid[parent] + 1
        else:
            depth_by_mid[m['mid']] = 0
    items = []
    for i, m in enumerate(emails, 1):
        d = depth_by_mid.get(m['mid'], 0)
        d = min(d, 8)  # cap visual indent
        indent = '&nbsp;' * (d * 4) + ('└─ ' if d > 0 else '')
        items.append(
            f'<li><span class="lvl">{indent}</span>'
            f'<a href="#m{i}"><strong>#{i}</strong> {html.escape(short_addr(m["from"]))} '
            f'&mdash; {html.escape(m["time"] or "")}</a></li>'
        )
    return '<ul class="toc-list">' + ''.join(items) + '</ul>'


def render_thread_html(disc, emails, tid):
    title = disc['subject']
    translations, trans_status = load_translations(tid)
    idx_by_mid = {m['mid']: i for i, m in enumerate(emails, 1)}
    parts, total_quotes = [], 0
    for i, m in enumerate(emails, 1):
        t_for_mid = (translations or {}).get(m.get('mid')) if translations else None
        h, q = render_email(i, m, t_for_mid, idx_by_mid)
        parts.append(h)
        total_quotes += q
    toc_html = build_toc(emails, idx_by_mid)
    return PAGE_TPL.format(
        title=html.escape(title),
        n_emails=len(emails),
        n_quotes=total_quotes,
        thread_url=html.escape(disc['url']),
        tid=html.escape(tid),
        trans_status=html.escape(trans_status),
        toc_html=toc_html,
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
