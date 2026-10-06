# Run ledger

File: `~/.bhramastra/ledger.jsonl` (override: `BHRAMASTRA_LEDGER`). Append-only,
one event per line, written **only** through `scripts/ledger.py` — never by
hand-editing the file.

```
{"ts": "2026-09-30T12:00:00Z", "run_id": "AVX-123-20260930T120000Z", "ticket": "AVX-123",
 "event": "gate_decision", "stage": "gate1", "actor": "human",
 "data": {"gate": "gate1", "decision": "revise", "feedback": "..."}}
```

## Events

| event | actor | stage | data |
|---|---|---|---|
| `run_started` | human | — | `cwd` (written by `start`) |
| `stage_started` / `stage_finished` | agent | intake·logs·rca·plan·fix | `iteration`, `artifact`, `result` (ok / halt); `interrupted`, `error` when an agent died; `resumed: <fix_probe case>` on a resumed start |
| `context_loaded` → recorded as `stage_finished.data.context` | agent | | `required`, `missing` |
| `eligibility` | agent | intake | full `eligibility.py` output (`verdict`, `criteria`) |
| `override` | human | intake | `criteria` overridden, `reason` |
| `logs_downloaded` | agent | logs | `prefix`, `bundles`, `dir` |
| `repro_test` | agent | rca·fix | `name`, `command`, `result` red/green, `verified_by: orchestrator` (re-run by `fix_guard.py repro`) |
| `gate_opened` | agent | gate1·gate2·gate3 | `gate` |
| `gate_decision` | human | gate1·gate2·gate3 | `gate`, `decision` approve/revise/reject, `feedback`; gate3 also `fingerprint` (the diff that was approved) |
| `tests_run` | agent | fix | `targets[]`, `passed`, `failed[]` |
| `manual_edit` | human | any | `files[]`, `note` (user changed code by hand — mid-run or as a PR follow-up) |
| `human_feedback` | human | — | **written by `lessons.py teach`**: `text`, `stage` (optional) — your own feedback on a finished run (`/fix teach`) |
| `halted` | agent | any | `reason` (HALT reason code), `detail` |
| `pr_opened` | agent | fix | `url`, `number`, `branch`, `files`, `added`, `deleted` (from `fix_guard.py ship`) |
| `pr_state` | agent | — | `gh pr view` snapshot (via `refresh-prs`) |
| `run_started` extras | human | — | `cwd`, `hint` (user's free-text pointer, if given) |
| `run_finished` | agent | — | `outcome`: pr_opened · ineligible · checked · rejected_gate1 · rejected_gate2 · rejected_gate3 · halted · dry_run · aborted |
| `usage` | agent | intake·rca·plan·fix·other·orchestrator | **written by `harvest` only**: `scope` agent/orchestrator, `agent_id`, `agent_type`, token fields (`input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `total_tokens`), `requests`, `models{}`, `efforts{}`, `tool_calls{}`, `tool_errors{}`, `duration_s`, `transcript` |
| `skill_used` | agent | any | `skill`, `outcome` ok·failed·fallback·partial (reported) or ok·error (transcript), `detail`/`error`, `source` reported·transcript, `invoked_by` |
| `ai_rating` | human | — | `rating` good·ok·poor, `note` — post-merge verdict via `/fix rate` |
| `lessons_injected` | agent | intake·rca·plan·fix | `stage`, `ids` injected, `applied`, `not_applicable` (from `lessons.py verify`) |
| `lessons_distilled` | agent | — | **written by `lessons.py propose`**: `phase` run_end·review·post_merge·manual, `proposed`, `reinforced`, `errors`, `consumed` (signal ids distilled: `rc-`/`rv-`/`ic-` review comments, `cm-` follow-up commits, `hf-` feedback, `me-` hand edits, `gd-` gate decisions, `ov-` overrides, `sf-` skill failures, `ar-` ratings) |
| `review_round` | agent | — | `/fix review-handle`: `round`, `stage` started·dry_run·shipped, `items`, `head_before`, `shipped`, `commit`, `replies` (on the run that opened the PR) |
| `review_reply` | agent | — | `round`, `id` (th-/rv-/ic- item), `kind`, `url` of the posted reply |
| `lesson_review` | human | — | **written by `lessons.py review-set`**: `id`, `decision` approve·reject·retire, `edited` |

Extra fields on existing events, used for AI-performance analytics:

- `gate_decision`: `rating` good·ok·poor (quality of the RCA / plan), `reason` on
  revise/reject — gate1 `wrong_root_cause · weak_evidence · test_not_reproducing ·
  test_wrong_place`, gate2 `wrong_approach · too_broad · missing_tests · risk_unaddressed`, or free text.
- `stage_finished`: `confidence` and `hint_check` confirmed·partly·refuted·not_used (rca), `context {required, missing, reprompted}`.
- `tests_run`: `attempts` (red→green rounds).
- `pr_opened`: `commits`, `head_sha` — baseline for human follow-up commits.
- `pr_state`: also `commits`, `additions`, `deletions`.
- `gate_opened` / `gate_decision` also use `review_r1` (what to do per review item) and `review_r2` (publish: commit, push, replies).

## Where the numbers come from

| Data | Source | Trust |
|---|---|---|
| tokens, model, effort, tool errors, skill load errors | Claude Code transcripts (`~/.claude/projects/*/<session>.jsonl` + `<session>/subagents/agent-*.jsonl`) via `harvest` | objective |
| skill did its job? | `SKILLS_USED` block in each artifact; orchestrator for tracelog | self-reported |
| gate rating / reasons, `ai_rating` | you, at the gates and after merge | human judgement |
| PR state, review comments, follow-up commits | `gh` via `refresh-prs` | objective |

`harvest` finds every transcript mentioning the run_id (so resumed runs across
sessions are covered), is idempotent per agent, and bounds the orchestrator
window by `run_started` … the run's last event after `run_finished` (so the
`fix-lessons` agent that Learn spawns after `run_finished` is counted). Tokens are raw counts (no $ — pricing
changes); `tokens_cache_read` dominates and is cheap, `tokens_output` is the costly part.

## Derived report (`ledger.py report [--ticket T] [--csv]`)

Per run: ticket, started, status/outcome, eligibility verdict + failed
criteria, gate decisions (`gate1:revise/approve`), **interventions**
(revise + reject + override + manual_edit), human touchpoints (all human
events), run time, gate wait (gate_opened→gate_decision), agent time (run −
wait), PR raised, PR url/state (DRAFT/OPEN/MERGED/CLOSED), time to merge,
rework ratio (audit-ai `metrics-<T>.json` if present).

AI performance columns: `rca_confidence`, `hint`, `hint_check`, `gate1_first_pass`, `gate2_first_pass`,
`gate1_rating`, `gate2_rating`, `revise_reasons`, `rca_iterations`,
`plan_iterations`, `fix_attempts`, `context_reprompts`, `post_pr_commits` (human
follow-ups only; `review-handle` commits are excluded), `review_rounds`, `review_replies`,
`review_comments`, `review_decision`, `ai_rating`, `ai_rating_note`,
`autonomous_merge` (merged, 0 interventions, 0 follow-up commits).
Cost / compliance columns: `tokens_total`, `tokens_output`, `tokens_cache_read`,
`tokens_by_stage`, `models`, `efforts`, `model_ok` (all opus + high),
`tool_errors`, `skills`, `skills_failed`.
Lessons columns: `lessons_injected`, `lessons_proposed`, `lessons_approved`.

PR state changes after the run → `/fix status --refresh` (`ledger.py refresh-prs`);
it reminds you to `/fix rate` runs whose PR merged or closed.

## Analytics (`ledger.py analytics [--json]`)

Across all runs: outcome counts; funnel (started → eligible → gate1 approved →
gate2 approved → PR → merged, + closed-unmerged, autonomous merges);
ineligibility and halt reasons; gate first-pass rates; revise/reject reasons;
gate ratings; RCA confidence vs gate-1 first-pass (calibration); `ai_rating`
distribution; medians for interventions, follow-up commits, review comments,
run time, gate wait, time to merge, tokens per run and per stage; tokens per
merged PR; model/effort compliance; per-skill failure rate; lessons proposed/approved; gate first-pass **with vs
without** injected lessons; gate-1 first-pass by month; `hint_check` distribution and gate-1 first-pass
with vs without a hint. Per-lesson
effectiveness is `lessons.py stats` (`references/lessons.md`). For anything else,
`report --csv` is one flat row per run.

## Commands

```bash
L=~/.claude/skills/fix/scripts/ledger.py
RUN_ID=$(python3 $L start --ticket AVX-123)
python3 $L append --run-id "$RUN_ID" --event stage_started --stage rca --data '{"iteration":1}'
python3 $L append --run-id "$RUN_ID" --event eligibility --stage intake --data-file ~/.bhramastra/runs/AVX-123/eligibility.json
python3 $L append --run-id "$RUN_ID" --event gate_decision --stage gate1 --actor human --data '{"gate":"gate1","decision":"approve"}'
python3 $L harvest --run-id "$RUN_ID"
python3 $L rate --ticket AVX-123 --rating ok --note "right fix, reviewer asked for a helper"
python3 $L report --csv
python3 $L analytics
python3 $L refresh-prs
```
