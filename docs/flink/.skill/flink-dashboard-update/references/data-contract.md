# `todo-data.json` — full data-contract reference

Load this file with `read_file` only when adding **new top-level fields** or debugging why a field renders unexpectedly. Day-to-day data refresh tasks do not need this reference — SKILL.md §2 is enough.

## Top-level keys (May 2026 schema)

```jsonc
{
  "user":           { "login": "FeatZhang", "name": "Avery Zhang" },
  "lastUpdated":    "2026-05-15T13:24:00+08:00",
  "stats":          { "merged": <int>, ... },           // committer-path inputs
  "repositories":   [ { "name": "apache/flink", "pullRequests": [ ... ] } ],
  "dailyContributions": { "_comment": "...", "YYYY-MM-DD": <int>, ... },
  "unrepliedIssues":    [ { "_comment": "..." }, { ...issue... }, ... ],
  "discussions":        [ { "subject", "url", "date", "replies", "role" } ],
  "crTargets":          [ { "_comment": "..." }, { ...pr... }, ... ]
}
```

## `repositories[].pullRequests[]` — fields actually consumed by index.html

| Field | Type | Used in |
|---|---|---|
| `number` | int | card title, links |
| `title` | string | card title |
| `url` | string | open in new tab |
| `state` | `"open"` \| `"closed"` \| `"merged"` | KPI counters |
| `isDraft` | bool | "drafts" KPI |
| `ciStatus` | `"success"` \| `"failure"` \| `"pending"` | tag rendering |
| `reviewState` | `"approved"` \| `"changes_requested"` \| `null` | tag rendering |
| `updatedAt` | ISO datetime | inferred daily graph |
| `module` | string | module distribution |
| `issueId` | string (e.g. `"FLINK-12345"`) | distinct-issues KPI |
| `discussions` | int | summed into `totalDiscussions` |
| `flips` | string[] | inline tag tokens (see below) |

### `flips[]` token grammar
- `discussions:<n>` → 💬 `<n>` (informational only)
- `ci-failure` → red CI tag, marks PR as needing follow-up
- `gha-fail:<job>` → ⚠️ `<job>` chip, marks follow-up
- `changes-requested` → 🔄 CHANGES chip, marks follow-up
- *anything else* → grey neutral chip (kept verbatim)

`needFollowUp(p)` returns true iff any of `ci-failure`, `changes-requested`, or `gha-fail:*` appear in `flips[]`.

## `unrepliedIssues[]` — minimum viable issue object

```jsonc
{
  "key":        "FLINK-12345",
  "summary":    "Title text",
  "url":        "https://issues.apache.org/jira/browse/FLINK-12345",
  "priority":   "blocker" | "critical" | "major" | "minor" | "trivial",
  "components": ["table-planner", "..."],
  "created":    "2024-08-20",
  "updated":    "2024-08-20",
  "isStarter":  true     // optional; set when "starter" appears in JIRA labels
}
```

`computeUnrepliedIssues` derives:
- `ageDays` = days between `created` and `lastUpdated`
- `aged` count (>365 days)
- `starter` count (`isStarter==true`)

The leading `{ "_comment": "..." }` stub is filtered out by the `it.key||it.id` truthy check in `renderHelpWanted`. Do not remove the stub — it is the populate hint for future maintainers.

## `discussions[]`

```jsonc
{
  "subject":  "[DISCUSS] FLIP-XXX: ...",
  "url":      "https://lists.apache.org/thread/<thread_id>",
  "date":     "YYYY-MM-DD",
  "replies":  <int>,                // excludes the OP
  "role":     "starter" | "participant"
}
```

The dashboard derives `thread_id` via `url.match(/\/thread\/([a-z0-9]+)/i)` and links to `dissuss/<thread_id>.html` via the "📖 解读" button. **The thread_id is the only identifier — do not introduce slugs.**

## `crTargets[]` — review-targets queue (SQL/Table & AI/Model)

```jsonc
{
  "number":      28162,
  "title":       "[FLINK-39421][table] Fix metadata filter contract",
  "url":         "https://github.com/apache/flink/pull/28162",
  "repo":        "apache/flink",
  "author":      "jnh5y",
  "module":      "table",                // optional
  "topic":       "sql" | "ai" | "other", // optional — auto-inferred from title/module/labels
  "updatedAt":   "2026-05-15T02:58:30Z",
  "reviewState": "" | "approved" | "changes_requested" | "review_required",
  "ciStatus":    "" | "success" | "failure",
  "isDraft":     false,
  "comments":    0,
  "additions":   878,
  "deletions":   57,
  "labels":      ["component:table"]
}
```

`computeCrTargets` derives:
- `topic` (when missing): regex `\b(ai|model|llm|inference|embedding|vector|mcp)\b` → `ai`; else `\b(sql|table|planner|catalog|calcite|hive|view|udf|connector|format)\b` → `sql`; else `other`.
- `ageDays` = days since `updatedAt`.
- KPI counts: `total`, `sql`, `ai`, `fresh` (`ageDays<=7`).

The leading `{ "_comment": "..." }` stub is filtered by the `it.number||it.url` truthy check in `computeCrTargets`. Keep it — it carries the `gh pr list` populate hint.

## `dailyContributions`

Sparse map. Two acceptance modes:

1. **Explicit** — at least one `YYYY-MM-DD` key present → use as-is.
2. **Inferred fallback** — only `_comment` present → dashboard derives counts from `pullRequests[].updatedAt` + `discussions[].date`.

Detection happens via `Object.keys(...).some(k => /^\d{4}-\d{2}-\d{2}$/.test(k))`. Do not break this contract by adding non-date metadata keys other than `_comment`.

The grid renders the **last 53 weeks ending on `lastUpdated` (or today)**. Streak math:
- `curStreak` keeps counting backwards from today; if today's count is 0, the algorithm steps to yesterday and continues, so the streak does not snap to 0 at midnight.
- `maxStreak` is the longest consecutive run anywhere in the 53-week window.
