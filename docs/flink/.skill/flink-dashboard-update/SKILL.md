---
name: flink-dashboard-update
description: Update the Flink contributor dashboard hosted under docs/flink/. Covers three coupled workflows — (A) refreshing todo-data.json (open PRs, unreplied JIRA issues, daily contribution graph, mailing-list discussions, and CR review targets in SQL/Table & AI/Model areas) and (B) regenerating offline mailing-list thread archives under dissuss/ that the dashboard's Discuss tab links to via the "📖 解读" button and (C) wiring new visualizations into index.html. Trigger when the user asks to "更新看板数据"/"刷新 flink 看板"/"补抓 disscuss"/"重抓 thread 解读"/"刷新 CR 列表"/"刷新 review targets"/"更新 review targets"/"补全 review targets"/"找 SQL/AI 相关 PR 去 review"/"批量评审 PR"/"add a new discussion to the dashboard"/"refresh CR targets"/"refresh review targets"/"regenerate dissuss reports", or whenever todo-data.json or dissuss/*.html need to be modified.
---

# Flink Contributor Dashboard — Update Skill

This skill keeps two artifacts in sync inside `docs/flink/`:

- `todo-data.json` — single source of truth for the dashboard (PRs, KPIs, daily graph, unreplied issues, mailing-list discussions).
- `dissuss/<thread_id>.html` — one offline HTML report per mailing-list thread referenced from `discussions[]`, linked from the Discuss tab via a "📖 解读" button on each card.

`index.html` is rendered entirely from `todo-data.json` at load time (no build step). After updating data, just refresh the browser.

---

## 1. File layout (must not be renamed)

```
docs/flink/
├── index.html                       # the dashboard (vanilla JS, no bundler)
├── todo-data.json                   # data contract — see §2
├── mail-thread-archiver.prompt.md   # spec for thread→HTML conversion (do not edit)
└── dissuss/                         # one HTML per mailing-list thread
    ├── _build.py                    # batch fetch + render script (idempotent)
    └── <thread_id>.html             # generated, filename == lists.apache.org thread id
```

Important: the directory is spelled `dissuss/` (intentional, matches existing links in `index.html`). Do NOT rename to `discuss/`.

---

## 2. todo-data.json contract

### 2.0 Data scope rule (CRITICAL)

The dashboard is a **single-user** dashboard for `githubUser` (currently `FeatZhang`). **Every section except `crTargets` must contain only data authored / participated in by that user.** When refreshing any section:

| Section | Scope | Filter |
|---|---|---|
| `repositories[].pullRequests` | **mine only** | `gh pr list ... --author "@me"` or `--author <login>`; never use community-wide queries |
| `stats.merged` | **mine only** | `gh search prs --author=<login> --merged ...` |
| `dailyContributions` | **mine only** | `contributionsCollection` is per-user by definition |
| `discussions[]` | **threads I started or replied to** | filter the dev@ archive by `From: <my email>` or by my message-id appearing under the thread |
| `unrepliedIssues[]` (legacy, unused) | global | only set if you re-enable the Help Wanted tab |
| **`crTargets[]`** | **community-wide** | the only field that intentionally lists *other* contributors' PRs (`author != me`) |

If a refresh script or downstream agent ever returns community-wide PRs into `repositories[].pullRequests`, that is a bug — re-filter by author before splicing.

### 2.1 Top-level keys

The dashboard reads these top-level keys. **All sections except `repositories` are optional** — missing fields fall back to inferred or empty state without breaking the page.

| Key | Purpose |
|---|---|
| `lastUpdated` | ISO datetime, drives the daily graph reference date and footer timestamp. |
| `user` | `{login, name, ...}` — shown in the hero. |
| `stats` | `{merged, ...}` — feeds the Committer Path progress score. |
| `repositories[]` | Each has `name` + `pullRequests[]`. PR fields used: `number, title, url, state, isDraft, ciStatus, reviewState, updatedAt, module, issueId, discussions, flips[]`. The `flips[]` array carries inline tags (`ci-failure`, `changes-requested`, `gha-fail:<job>`, `discussions:<n>`). |
| `dailyContributions` | Optional map `YYYY-MM-DD -> count`. Drives the GitHub-style heatmap. If only `_comment` is present (no real date keys), the dashboard falls back to inferring activity from `pullRequests[].updatedAt` + `discussions[].date`. |
| `unrepliedIssues[]` | Open JIRA issues with 0 comments. Fields: `key, summary, url, priority, components[], created, updated, isStarter`. The first entry may be a `_comment`-only stub — it is filtered out by `it.key||it.id` in render. |
| `discussions[]` | Mailing-list threads. Required fields: `subject, url, date, replies, role` (`starter`\|`participant`). The dashboard derives `thread_id` from `url` regex `\/thread\/([a-z0-9]+)` to link to `dissuss/<thread_id>.html`. |
| `crTargets[]` | Open PRs (NOT authored by you) worth actively reviewing. Drives the **🔍 Review Targets** tab. Required: `number, title, url`. Optional: `repo, author, module, topic` (`sql`\|`ai`\|`other` — auto-inferred from title/module/labels keywords if omitted), `updatedAt, reviewState, ciStatus, isDraft, comments, additions, deletions, labels[]`. |

When adding a new entry, **preserve existing `_comment` fields** — they embed the shell snippet used to refresh that section (e.g. `gh api graphql ...` for `dailyContributions`, `curl ... jq` for `unrepliedIssues`).

---

## 3. Workflow A — update `todo-data.json`

Pick the right path based on what the user asks:

### 3.1 Refresh PRs (`repositories[].pullRequests`)
Use the existing `flink-pr-todo` user skill in dashboard mode (it writes `todo-data.json` directly). Do not hand-edit PR entries unless the user asks for a specific tweak.

### 3.2 Refresh `dailyContributions`
```bash
gh api graphql -f query='{user(login:"<login>"){contributionsCollection{contributionCalendar{weeks{contributionDays{date contributionCount}}}}}}' \
  | jq '.data.user.contributionsCollection.contributionCalendar.weeks
        | map(.contributionDays) | add
        | map({(.date): .contributionCount}) | add'
```
Splice the resulting object into `todo-data.json` under `dailyContributions`, keeping `_comment`. Empty/zero days may be omitted.

### 3.3 Refresh `unrepliedIssues`
```bash
JQL='project = FLINK AND resolution = Unresolved AND status = Open AND comments = 0 ORDER BY priority DESC, created ASC'
curl -s -G 'https://issues.apache.org/jira/rest/api/2/search' \
  --data-urlencode "jql=$JQL" --data-urlencode 'maxResults=80' \
  --data-urlencode 'fields=summary,priority,components,created,updated,labels' \
  | jq '[.issues[] | {key, summary:.fields.summary, url:("https://issues.apache.org/jira/browse/"+.key),
        priority:(.fields.priority.name|ascii_downcase),
        components:[.fields.components[].name],
        created:(.fields.created|.[0:10]), updated:(.fields.updated|.[0:10]),
        isStarter:((.fields.labels // []) | map(ascii_downcase) | any(.=="starter"))}]'
```
Replace the `unrepliedIssues` array (keep the `_comment` stub at index 0).

### 3.4 Add or refresh a `discussions[]` entry
1. Append `{subject, url, date, replies, role}` to `discussions[]`. `date` = ISO `YYYY-MM-DD` of the first message; `replies` excludes the OP; `role` = `starter` if the user authored the OP, else `participant`.
2. Immediately run Workflow B for that thread so the "📖 解读" button works.

### 3.5 Refresh `crTargets` (active code-review queue)
The **🔍 Review Targets** tab surfaces other contributors' open PRs in domains where active CR is high-leverage for committer candidacy: **SQL/Table planner work** and **AI/Model integrations**. Refresh whenever the user asks to "刷新 CR 列表" / "找 SQL/AI 相关的 PR 去 review" / "update review targets":

```bash
gh pr list --repo apache/flink --state open --limit 200 \
  --json number,title,url,author,updatedAt,labels,isDraft,additions,deletions,reviewDecision \
  | jq --arg me FeatZhang '[.[]
      | select(.author.login != $me and .isDraft==false)
      | select((.title|test("(?i)\\b(sql|table|planner|calcite|hive|catalog|udf|view|format|connector|ai|model|llm|inference|embedding|vector|mcp)\\b")))
      | {number,title,url,repo:"apache/flink",author:.author.login,updatedAt,
         labels:[.labels[].name],isDraft,additions,deletions,
         reviewState:((.reviewDecision//"")|ascii_downcase)}]' > /tmp/cr_targets.json
```

Then splice into `todo-data.json`, **preserving the `_comment` stub at index 0**:

```bash
python3 - <<'PY'
import json,pathlib
p=pathlib.Path('docs/flink/todo-data.json')
data=json.loads(p.read_text())
items=json.loads(open('/tmp/cr_targets.json').read())
keep=[x for x in data.get('crTargets',[]) if isinstance(x,dict) and '_comment' in x]
data['crTargets']=keep+items
p.write_text(json.dumps(data,indent=2,ensure_ascii=False)+'\n')
PY
```

Notes:
- `gh search prs` with `-author:` exclusion currently returns empty results across `apache/*`. Use the `gh pr list --repo` + `jq` pipeline above instead.
- The keyword regex covers both **SQL/Table** (sql, table, planner, calcite, hive, catalog, udf, view, format, connector) and **AI/Model** (ai, model, llm, inference, embedding, vector, mcp) families. Tweak the regex to broaden/narrow scope.
- `topic` is auto-inferred client-side in `index.html` (`computeCrTargets`) — the AI heuristic wins over SQL when both keywords appear, e.g. "AI Model SQL Function". Override by setting `topic` explicitly on the entry.
- The dashboard renders each card with a `🔍 Review` button that deep-links to `<pr_url>/files`, ready for inline comments.

#### 3.5.1 Multi-repo one-shot (apache/flink + flink-connector-*)

When the user wants the **full** review queue across the umbrella repo and connector repos, fan out:

```bash
REPOS=(apache/flink \
       apache/flink-connector-kafka \
       apache/flink-connector-jdbc \
       apache/flink-connector-mongodb \
       apache/flink-connector-elasticsearch \
       apache/flink-connector-aws \
       apache/flink-connector-gcp-pubsub \
       apache/flink-connector-cassandra \
       apache/flink-connector-hbase \
       apache/flink-connector-hive \
       apache/flink-connector-pulsar)

: > /tmp/cr_targets.json
echo '[' > /tmp/cr_targets.json
first=1
for r in "${REPOS[@]}"; do
  gh pr list --repo "$r" --state open --limit 200 \
    --json number,title,url,author,updatedAt,labels,isDraft,additions,deletions,reviewDecision 2>/dev/null \
  | jq --arg me FeatZhang --arg repo "$r" '[.[]
      | select(.author.login != $me and .isDraft==false)
      | select((.title|test("(?i)\\b(sql|table|planner|calcite|hive|catalog|udf|view|format|connector|ai|model|llm|inference|embedding|vector|mcp)\\b")))
      | {number,title,url,repo:$repo,author:.author.login,updatedAt,
         labels:[.labels[].name],isDraft,additions,deletions,
         reviewState:((.reviewDecision//"")|ascii_downcase)}]' \
  | jq -c '.[]' >> /tmp/cr_targets.ndjson || true
done

jq -s '.' /tmp/cr_targets.ndjson > /tmp/cr_targets.json
rm /tmp/cr_targets.ndjson
jq 'length' /tmp/cr_targets.json
```

Then run the same Python splice script as §3.5 to merge into `todo-data.json` (still preserving `_comment` at index 0). Connector PRs surface with `repo: apache/flink-connector-<name>` so cards remain disambiguated.

#### 3.5.2 Trim aged-out entries

If the queue grows past ~120 entries, drop PRs not updated in the last 90 days before splicing:

```bash
jq --argjson cutoff "$(date -v-90d -u +%s 2>/dev/null || date -d '90 days ago' -u +%s)" \
  '[.[] | select((.updatedAt|fromdateiso8601) >= $cutoff)]' \
  /tmp/cr_targets.json > /tmp/cr_targets.fresh.json && mv /tmp/cr_targets.fresh.json /tmp/cr_targets.json
```

After any edit: validate with `python3 -c 'import json;json.load(open("todo-data.json"))'`.

---

## 4. Workflow B — regenerate `dissuss/<thread_id>.html`

### 4.1 Tool
`dissuss/_build.py` is idempotent: it reads `todo-data.json`, walks each `discussions[].url`, extracts the thread id, and skips files that already exist. It implements the spec in `mail-thread-archiver.prompt.md` (no 中文翻译栏 — batch variant).

### 4.2 Commands
```bash
cd docs/flink/dissuss

# Generate any missing reports (skip ones already on disk)
python3 _build.py

# Force-rebuild a specific thread (e.g. after new replies came in)
rm -f <thread_id>.html && python3 _build.py <thread_id>

# Force-rebuild all (rare — only when _build.py spec changes)
rm -f [a-z0-9]*.html && python3 _build.py
```

### 4.3 What `_build.py` does (do not duplicate this logic elsewhere)
1. Reads `discussions[]` from `../todo-data.json`.
2. For each entry, extracts `thread_id` via regex `/thread/([a-z0-9]+)`.
3. Fetches `https://lists.apache.org/api/thread.lua?id=<tid>` to walk the thread tree, then `https://lists.apache.org/api/source.lua?id=<mid>` for each message's RFC-822 source.
4. Parses with Python's `email` stdlib (`policy.default`); prefers `text/plain`, falls back to a simple HTML→text strip.
5. Splits each body into alternating `text-block` / `quote-block`s by counting leading `>` characters per line (depth 1–5).
6. Sorts emails by `epoch` ascending and renders one self-contained HTML file `<thread_id>.html` with:
   - sticky toolbar (`展开/折叠引用` · `深色/浅色` · `↗ lists.apache.org` · `← 返回看板`),
   - a stub "📖 AI 解读" summary card,
   - pre-wrap text blocks (no `<br>` injection — that would double space),
   - color-coded `.qd-1`..`.qd-5` quote borders,
   - URL auto-linkification with `html.escape` first.

### 4.4 Quality bar (verify after each rebuild)
- `<br>` count must be `0` inside generated files.
- Number of `<div class="email">` matches `n_emails` in toolbar.
- Open one file in the browser: no double blank lines, deepest quote level renders, "← 返回看板" works (relative path `../index.html`).

### 4.5 Producing a richer 中文 AI 解读
The default report only embeds raw emails + a stub summary card. To produce the full bilingual variant described in `mail-thread-archiver.prompt.md` §1.3.5–§1.6, hand the prompt file plus the thread URL to a downstream agent. That agent overwrites the same `<thread_id>.html` so the dashboard link still resolves.

---

## 5. Workflow C — wire a new field into `index.html`

When the user asks for a brand-new visualization (rare, but follows a fixed pattern):

1. **CSS** — add a scoped class block near the closest existing section (e.g. `.discuss-card` for discussion features).
2. **Data contract** — extend `todo-data.json` with an optional field; embed a `_comment` stub showing how to populate it.
3. **`computeStats(data)`** — compute the new metric, return it on the stats object.
4. **Render function** — add `renderXxx(s)`; integrate into `renderTabNav`, `renderKpi`, or `renderOverview` as appropriate.
5. **`activeFilter`** — if it adds filters, extend the global state object and the `bindEvents` switch.
6. **Fallback** — when data is missing, render a `renderXxxHowto()` block with the populate command, never a blank pane.
7. **Lint** — `read_lints` must stay clean; the file is plain HTML+JS, no transpile.

---

## 6. Preview locally

The dashboard is a static page. Reuse the running server on port 8765 if any:
```bash
lsof -ti:8765 || (cd docs/flink && python3 -m http.server 8765 &) && sleep 1
open http://localhost:8765/index.html
```
Hard-refresh the Discuss tab and click "📖 解读" on a card to confirm the report opens in a new tab.

---

## 7. Bundled scripts

- `scripts/_build.py` — canonical copy of `dissuss/_build.py`. If `dissuss/_build.py` is missing in a fresh checkout, copy this one over before running Workflow B.

---

## 8. Common pitfalls

- **Spelling**: directory is `dissuss/`, not `discuss/`. Both `index.html` and `_build.py` rely on this exact name.
- **JSON `_comment` keys** look like real entries — `unrepliedIssues[0]._comment` survives in the array because the renderer filters by `it.key||it.id`. Keep them.
- **Inferred daily graph**: if `dailyContributions` only contains `_comment` (no `YYYY-MM-DD` keys), `computeDailyContributions` falls back to PR `updatedAt` + discussion `date`. That fallback is correct behavior — do not "fix" it by deleting the comment.
- **`dissuss/_build.py` is rate-limited** by `lists.apache.org`. For a full rebuild of all 12 threads expect ~30–60s; do not parallelize.
- **`mbxfwyrk4*.html` is ~1.7 MB** (long thread). Do not commit a force-prettified version — keep the compact inline-CSS layout produced by `_build.py`.
- **Thread id extraction** must use the same regex on both sides: `/thread/([a-z0-9]+)`. The `index.html` uses it inside `renderDiscussCard` to build the `dissuss/<tid>.html` link.
