# Lessons — learning from human feedback

The model does not learn between runs. `/fix` learns by **writing down** what
humans corrected, having a human approve it, and **injecting** the relevant
lessons into future agent prompts.

```
feedback (review comments, your text, ledger) → fix-lessons proposes → human approves → select → inject → verify → measure
```

Store: `~/.bhramastra/lessons.jsonl` (override `BHRAMASTRA_LESSONS`), managed
only through `scripts/lessons.py`.

## Lesson record

| field | meaning |
|---|---|
| `id` | `L-0001`… |
| `status` | `proposed` → `approved` · `rejected` · `retired`. Only `approved` is injected. Rejected stays so it isn't re-proposed. |
| `kind` | `lesson` (behaviour) or `doc_gap` (repo docs should say this — also injected until the doc is fixed) |
| `stage` | intake · rca · plan · fix · any — whose prompt it goes into |
| `components`, `paths` | scope. Path prefixes beat components; neither = global |
| `reason` | gate reason code it should prevent — used to measure it |
| `trigger` | observable situation ("when …") |
| `lesson` | the check/rule ("do …"), with the why |
| `evidence[]` | `{source, quote, run_id, ticket}` — every lesson cites real feedback |

## Where feedback comes from

| Signal | Source | Value |
|---|---|---|
| **PR review comments** — inline (with `path:line`, code hunk, thread replies), review bodies, PR comments | GitHub, fetched by `review` / `post_merge` | highest: a reviewer saying what the AI got wrong |
| **Your own feedback** (`/fix teach T "…"`) | `human_feedback` | high |
| Hand edits / follow-up commits + their diff | `manual_edit`, GitHub | high: what a human had to change |
| Gate revise/reject + reason + verbatim feedback; `ok`/`poor` ratings | `gate_decision` | high |
| Overrides, reported skill failures | `override`, `skill_used` | low |
| Final verdict | `ai_rating` (`/fix rate`) | context |

Bot comments are ignored.

## Phases

| Phase | Trigger | Sees |
|---|---|---|
| `run_end` | end of every run (once) | gate signals, overrides, skill failures |
| `review` | `/fix learn T` — any time review comments exist; repeatable | new review comments + follow-up commits |
| `manual` | `/fix teach T "<text>"` | your text |
| `post_merge` | `/fix rate T …` | rating + review comments not learned yet |

Every signal has an id; `propose --feedback` records the ids it distilled
(`lessons_distilled.consumed`), so each comment/feedback is learned **once**
however many passes run. `hf-` / `me-` signals are picked up by whichever
pass comes next. `refresh-prs` prints a `/fix learn` hint on new review activity.

**Writing a lesson yourself.** `/fix teach T "<text>"` distils your text in
the context of that run (stage and scope inferred, you approve). `/fix teach
"<text>"` without a ticket writes it directly (`lessons.py add`, approved on
entry) — for rules you already know, e.g. team conventions.

## Selection (injection)

`lessons.py select --stage S --component C… --path P…` returns approved
lessons for that stage (or `any`):

- lesson has `components` → injected only if the ticket shares one; ranked 3
  if a suspect path also matches its `paths`, else 2;
- lesson has only `paths` → suspect-path match 3, component-seed match 2;
- neither → global, 1.

Ties broken by evidence count. Max 8 per stage.

Each agent must answer every injected lesson in a `LESSONS_APPLIED` block:
`- L-0003 — applied — <how>` or `- L-0003 — not_applicable — <why>`.
`lessons.py verify` enforces it; the result goes into `lessons_injected`.

## Measuring

- `lessons.py stats` per lesson: injected, applied, first-pass at the stage's
  gate when applied, **recurred** (same gate revised/rejected for the lesson's
  own `reason` despite it being applied). `suggest_retire` when recurred ≥ half
  of ≥2 applications — the lesson isn't working; rewrite or retire it.
- `ledger.py analytics`: gate first-pass **with vs without** lessons, and
  gate-1 first-pass by month (is the pipeline getting better?).

## Hygiene

- Review in small batches; edit wording freely (`review-set --edit`).
- Prefer narrow scope. A global lesson costs every future run context.
- `doc_gap` lessons: periodically `lessons.py list --kind doc_gap` and turn
  them into a cloudn docs PR, then retire them.
