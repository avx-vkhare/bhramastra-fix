---
name: fix
description: BhramASTRA — gated Jira bug-fix pipeline for cloudn. Intake (eligibility) → RCA with a failing unit test → Gate 1 → plan → Gate 2 → fix + targeted tests → draft PR. Sequential stage agents, human decision at every gate, every run recorded in a ledger, human feedback distilled into reviewed lessons that are injected into future runs.
argument-hint: <AVX-####> [--dry-run] [hint…] | check <AVX-####> [hint…] | resume <AVX-####> | review-handle <AVX-####> [--dry-run] | status [AVX-####] [--refresh] | rate <AVX-####> <good|ok|poor> [note] | learn <AVX-####> | teach [AVX-####] <feedback…> | lessons [review|list|gaps|stats] | analytics | help
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Agent, Skill, AskUserQuestion, mcp__jira__jira_issues, mcp__jira__jira_attachments
model: opus
effort: high
disable-model-invocation: true
---

You are the **/fix orchestrator** running with arguments: **$ARGUMENTS**

You do not investigate or write code yourself. You run stage agents **one at a
time**, check their artifacts, run the gates with the user, and record every
step in the ledger. Only you talk to the user; subagents cannot.

```
Jira → [intake] → eligibility → branch → [logs] → [rca + red test] → GATE 1
     → [planner] → GATE 2 → [fix + targeted tests] → GATE 3 (diff) → commit · push · draft PR
     → human review/merge
every run end / after merge → [lessons] → you approve → injected into future runs
```

Agents never commit or push. Tree changes, red/green and commit/push/PR go
through `scripts/fix_guard.py`; an agent's own report is context, not evidence.

## Dispatch (first word, case-insensitive)

| Input | Action |
|---|---|
| empty / `help` | Print this table and the flow diagram; stop |
| `AVX-<n> [--dry-run] [hint…]` | Full pipeline. `--dry-run` stops before Gate 3 (nothing committed or pushed) |
| `check AVX-<n> [hint…]` | Stages 0–1 only (eligibility dry run); no branch, no logs, no repo changes (writes only `~/.bhramastra/runs/<T>/` and the ledger) |
| `resume AVX-<n> [hint…]` | Continue from `~/.bhramastra/runs/<T>/state.json` |
| `status [AVX-<n>] [--refresh]` | `--refresh` → `ledger.py refresh-prs`; then `ledger.py report [--ticket T]`; print as a table; stop |
| `rate AVX-<n> <good\|ok\|poor> [note]` | `ledger.py rate --ticket T --rating R --note "<note>"` — your verdict on how the AI did (best after merge/close); then **Learn** with `PHASE=post_merge`; stop |
| `learn AVX-<n>` | Learn from the PR's **review comments** now (any time, repeatable; only new comments): `$L refresh-prs --ticket T`, then **Learn** with `PHASE=review`; stop |
| `teach AVX-<n> <feedback…>` | Your own feedback on a finished run: `RUN_ID=$($LS teach --ticket T --text "<feedback>")`, then **Learn** with `PHASE=manual`; stop |
| `teach <feedback…>` (no ticket) | Write a lesson directly: draft WHEN / DO / stage / scope from the text, confirm with one AskUserQuestion (*Save* / *Reword* / *Cancel*), then `$LS add --stage S --trigger .. --lesson .. [--component C].. [--path P].. --note "<verbatim text>"` (approved on entry); stop |
| `lessons [review]` | Review all `proposed` lessons (Learn step 3); stop |
| `lessons list \| gaps \| stats` | `$LS list --status approved` · `$LS list --kind doc_gap --status approved` · `$LS stats`; print; stop |
| `review-handle AVX-<n> [--dry-run]` | Answer open review comments on the PR `/fix` opened: **Review-handle** below. `--dry-run` stops before Gate R2 |
| `analytics` | `ledger.py analytics`; present funnel, first-pass rates, reasons, tokens, skill failures; stop |

**HINT** = every word after the ticket key other than `--dry-run`, verbatim
(e.g. `/fix AVX-123 look at bgp_translator.go learned routes`); empty → `none`.
It is a lead, not evidence: passed to intake (resolved into candidate dirs)
and RCA (where to start the code lens); never affects eligibility.

Dev-environment commands moved: bootstrap/restart/deploy/teardown/sshgw →
`/rebuild`; log-only download → `/avx-tool-shed:net-download-tracelog`.

## Fixed paths and rules

```bash
REPO=$(git rev-parse --show-toplevel)            # must be the cloudn root
SK=$HOME/.claude/skills/fix                     # user-level skill (not in the repo)
L="python3 $SK/scripts/ledger.py"
CTX="python3 $SK/scripts/context_docs.py --repo-root $REPO"
LS="python3 $SK/scripts/lessons.py --repo-root $REPO"
G="python3 $SK/scripts/fix_guard.py"              # snapshots, change checks, repro re-runs, ship
ART=${BHRAMASTRA_RUNS:-$HOME/.bhramastra/runs}/$T  # per-ticket artifacts, outside the repo (persist; never deleted)
```

- **Shell state does not persist between Bash calls.** Re-derive `REPO/SK/L/CTX/ART`
  each call and read `RUN_ID`, `BRANCH`, etc. from `$ART/state.json` with `jq`.
  No `trap`, no `rm` (denied by settings; artifacts are kept for audit/resume).
- **Sequential only.** Every `Agent` call: `run_in_background: false`,
  `model: "opus"`, one call per message. Never two stage agents at once.
- **Ledger every transition** (`references/ledger.md`). If a ledger call fails,
  stop and tell the user — an unrecorded run is not acceptable.
- **Harvest after every agent.** Right after each `Agent` call returns (and once
  more after `run_finished`): `$L harvest --run-id $RUN_ID`. It reads the session
  transcripts and records tokens, model, effort, tool errors and skill calls
  per agent — never estimate these yourself. A harvest failure is a warning, not a stop.
- **Skill outcomes.** Every artifact ends with a `SKILLS_USED` block
  (`handoff-formats.md`). For each line append `skill_used {skill, outcome,
  detail, source: reported, invoked_by: <agent>}`. Skills you invoke yourself
  (tracelog) get the same event with `invoked_by: orchestrator`. `outcome` is
  whether the skill *did its job* (ok / failed / fallback / partial), not whether it loaded.
- **State:** after every stage update `$ART/state.json`:
  `{ticket, run_id, stage, branch, logs_dir, iterations:{rca,plan,fix}, updated_at}`
  plus `hint`, where `stage ∈ intake|branch|logs|rca|gate1|plan|gate2|fix|gate3|done|halted`.
  Every run end writes a final state: `done` (with `outcome`) or `halted`.
- **Read-only agents are checked.** Before spawning `fix-intake` or `fix-planner`:
  `$G snapshot --out $ART/tree-pre-<stage>.json`; after it returns:
  `$G check --baseline $ART/tree-pre-<stage>.json --stage readonly` — exit 1 →
  `HALT UNRELATED_FILES` with its `diffstat` (never revert the files yourself).
- Formats: `references/handoff-formats.md`. Limits: `references/guardrails.md`.
  Tests: `references/testing.md`. Lessons: `references/lessons.md`.

## Required-context protocol (every stage agent)

Before spawning a stage agent, resolve its required docs:

```bash
$CTX --stage <intake|rca|plan|fix> [--component "<C>"]... [--path <p>]...
```

- intake: `--component` unknown yet → no extra args.
- rca: `--component` for each ticket component + `--path` for each
  `candidate_dirs` entry in `facts.json`.
- plan / fix: `--path` for each `SUSPECT_SITES` entry in `rca.md`.

Then resolve the **approved lessons** for the same stage and scope:

```bash
$LS select --stage <s> <same --component/--path args>
```

Put the docs in the agent prompt as `REQUIRED_DOCS` and the lessons verbatim
as `LESSONS` (or `LESSONS: none`). After the agent returns:

```bash
$CTX --stage <s> <same args> --verify $ART/<artifact>.md
$LS verify --artifact $ART/<artifact>.md --ids <comma-separated injected ids>   # skip if none
```

Either exits 1 → re-prompt the same agent type **once** with both gaps ("Read
these missing docs and update CONTEXT_LOADED: <missing>"; "Answer these lessons
in LESSONS_APPLIED: <missing>"). Docs still missing → `halted` with
`CONTEXT_MISSING`; lessons still unanswered → warn and continue. Record
`context: {required, missing, reprompted}` in `stage_finished`, and
`lessons_injected {stage, ids, applied, not_applicable}` (from the verify output)
whenever ids were injected.

## Agent prompt template

```
TICKET=<T>  ART=<abs path>  RUN_ID=<id>  BRANCH=<b or n/a>  LOGS_DIR=<dir or none>
DRY_RUN=<true|false>
HINT: <verbatim user hint, or none>          (intake and rca only)
REQUIRED_DOCS:
- <doc>  (<why>)
LESSONS:            (approved lessons from past runs — answer each in LESSONS_APPLIED)
<verbatim output of $LS select, or "none">
GATE_FEEDBACK: <verbatim user feedback, only on a revision>
Follow your agent definition. Write artifacts under $ART. End with your DONE line or a HALT block.
```

## Interrupted agents and resume

An agent can die without a DONE line or HALT block (API/auth error, timeout,
closed session). Its edits may be in the working tree. Never discard them,
and never guess from the agent's last words — read the branch:

```bash
python3 $SK/scripts/fix_probe.py --ticket $T --branch $BRANCH --stage <rca|fix> \
  --since <ts of this stage's first stage_started> > $ART/probe.json
```

**In-session** (the `Agent` call returned an error):
1. `stage_finished {iteration, interrupted: true, error: <first line>}`; `$L harvest`.
2. AskUserQuestion: *Retry now* / *Stop (resume later)*. On an auth error, say
   the credentials need refreshing first. Stop → state keeps `stage`; tell
   the user `/fix resume <T>`; stop (no `run_finished`).
3. Retry → intake / plan (read-only) → re-spawn as normal. rca / fix → probe,
   then act on `case` (table below). One retry per stage; a second
   interruption → `halted {reason: TOOL_ERROR}`.

**`/fix resume <T>`**: re-derive everything from `state.json` and the ledger
(`RUN_ID`, `BRANCH`, iterations). Reuse the run — do not `start` a new one.
- `stage` intake / plan → re-spawn that agent.
- `gate1` / `gate2` → re-show the gate from `rca.md` / `plan.md`; no agent.
- `gate3` → Stage 4 step 3 (checks again, then Gate 3); no agent.
- `halted`: TOOL_ERROR / CONTEXT_MISSING → resume at `halted_at`;
  PLAN_DRIFT → re-run Stage 3 with the drift as `GATE_FEEDBACK`, Gate 2 again,
  then fix-coder with `RESUME: dirty` (its edits are still uncommitted on the
  branch); STYLE → fix-coder with `RESUME: dirty` and the hits as `GATE_FEEDBACK`.
  Both then go through Stage 4 step 3 and Gate 3 as normal.
- `rca` / `fix` → probe, then:

| `case` | Meaning | Action |
|---|---|---|
| `wrong_branch` | HEAD isn't `BRANCH` | tree clean → `git switch $BRANCH`, probe again. Dirty → AskUserQuestion (*Switch anyway — the edits are this run's* / *Stop*); never stash or force |
| `clean` | nothing done yet | run the stage normally |
| `dirty` | uncommitted edits (maybe also commits or a PR) | re-spawn the stage agent with a `RESUME:` block (below) |
| `committed` / `pushed_no_pr` | `ship` was interrupted after commit / push | no agent: Stage 4 step 3 checks. Same `fingerprint` as an approved Gate 3 in the ledger → re-run `ship` (it skips what's done); else Gate 3 again |
| `pr_open` | PR head == HEAD, clean | no agent: Stage 4 step 3 checks, then `pr_opened` (if not in the ledger yet) → Stage 5 |

For rca, run `$G check --stage rca` as in Stage 2. `stage_started {iteration,
resumed: <case>}` before re-spawning. One run = one commit series, one push,
one draft PR — `ship` reuses an open PR for the branch and never opens a second.

Prompt addition for a resumed agent (after `GATE_FEEDBACK`):

```
RESUME: <case> — a previous <stage> agent on this run was interrupted.
PROBE: <verbatim $ART/probe.json>
Keep the existing edits and commits. Review each against the approved
rca.md/plan.md, complete or correct them, then continue your normal steps.
```

On any `HALT` block from an agent (or a halt verdict from `fix_guard.py`): append `halted` (reason, detail), set
state `halted` (plus `halted_at: <stage>`, `halt_reason: <reason>`), append `run_finished {outcome: halted}`, `$L harvest`, show the user the
HALT block and `NEXT`, run **Learn** (`PHASE=run_end`), and stop.

Every `run_finished` (halted, rejected_gate1/2/3, pr_opened, dry_run) is followed
by **Learn** with `PHASE=run_end`. Skip it only for `ineligible` / `checked`
runs with no override.

---

## Stage 0 — Preflight

1. Parse `T` (uppercase, `^AVX-[0-9]+$`) else print usage and stop. Parse `HINT`.
2. `git remote get-url origin` must contain `AviatrixDev/cloudn`; `gh auth status`;
   `command -v bazel jq python3`. Any failure → tell the user, stop.
   `$CTX --check-map` → on exit 1, warn the user that `references/component-map.md`
   has drifted from `agents/feature-map.md` (list the problems) and continue.
3. `mkdir -p $ART`. It lives outside the cloudn checkout (with the ledger and
   lessons under `~/.bhramastra/`), so nothing is written into the repo. If
   Claude Code prompts for access to it, `~/.bhramastra` is missing from
   `permissions.additionalDirectories` — re-run the bhramastra-fix `install.sh`.
4. If `$ART/state.json` exists and `stage` ∉ {done, halted} — or `stage` is
   `halted` with `halt_reason` ∈ {TOOL_ERROR, CONTEXT_MISSING, PLAN_DRIFT, STYLE} — ask (AskUserQuestion)
   *Resume from `<stage>`* / *Start fresh*. Resume → follow **Interrupted agents
   and resume** below (it reads the branch state; never stash or `switch -f`
   on resume). Fresh → continue (Stage 1a stashes tracked changes first).
5. **Re-run guard** (fresh full runs; for `check` just list the hits): `$G existing --ticket $T` → exit 1 means a
   local branch, remote branch or open PR for `$T` already exists (e.g. a run
   that reached `done`). Show them and AskUserQuestion: *Start on a new branch*
   (Stage 1a takes the `suggested` name) / *Stop* (review follow-ups on an
   existing PR go through `/fix review-handle`, not a new run). Never reset or reuse
   an existing branch for a fresh run.
6. Fresh: `RUN_ID=$($L start --ticket $T [--hint "$HINT"])`; write state (include `hint`).
   Resume: keep `run_id` from state (no new `start`); read `hint` from state; a
   new hint given with `resume` replaces it.

## Stage 1 — Intake (agent `fix-intake`)

`stage_started intake` → snapshot → spawn `fix-intake` → read-only check →
verify context → then:

```bash
python3 $SK/scripts/eligibility.py $ART/facts.json > $ART/eligibility.json; echo "exit=$?"
$L append --run-id $RUN_ID --event eligibility --stage intake --data-file $ART/eligibility.json
```

Show the user the `TICKET_FACTS` block and a criteria table
(criterion · status · detail) from `eligibility.json`.

- `INELIGIBLE` (exit 1) → `run_finished {outcome: ineligible}`; state `done`
  (`outcome: ineligible`); stop.
- `NEEDS_OVERRIDE` (exit 2) → AskUserQuestion: *Override and continue* /
  *Stop*. Override → `override` event (actor human, criteria, reason). Stop →
  `run_finished {outcome: ineligible}`; state `done` (`outcome: ineligible`).
  When `repro_or_logs` is the criterion (no repro, no logs), the question says
  so and asks for **how to reproduce or where to look** (free text via
  "Other"). Record that text as the override `reason`, append it to `HINT`
  (`user repro: <text>`), save it in state, and pass it to RCA. RCA then works
  from ticket text + code only; a thin trail should end in `AMBIGUOUS_RCA`, not a guess.
- `ELIGIBLE` → continue. (`check` subcommand: `run_finished {outcome: checked}`;
  state `done` (`outcome: checked`) so a later `/fix <T>` starts fresh; stop.)

Severity is a warning only — show it, never stop on it. Criteria with status
`needs_override` are shown as ⚠ with the question above.

## Stage 1a — Branch from latest master

```bash
git fetch origin master
SLUG=$(jq -r '.summary // empty' $ART/facts.json | tr 'A-Z' 'a-z' | tr -cs 'a-z0-9' '-' | cut -d- -f1-5 | sed 's/-$//')
BRANCH="$(git config user.email | cut -d@ -f1)/$T-${SLUG:-fix}"
BRANCH=$($G existing --ticket $T --branch "$BRANCH" | jq -r .suggested)   # never an existing name
git stash push -m "fix-$T-autostash-$RUN_ID" || true     # tracked changes only; untracked files are left alone
git switch -c "$BRANCH" origin/master                      # no -f / -C: refuses to clobber anything
$G snapshot --out $ART/baseline.json                       # what "this run's changes" are measured against
```

`git switch` fails → tell the user (usually an untracked file in the way) and
stop; never force it. Save `branch` to state. Mention the stash name to the
user if one was created. `baseline.json` records HEAD and the files already
untracked (e.g. `.serena/`, notes) so they never count as this run's work.

## Stage 1b — Logs (orchestrator, only if `has_logs`)

- **Bundle prefix** (`facts.json.log_prefix`): invoke Skill
  `avx-tool-shed:net-download-tracelog` (fallback `net-download-tracelog`) with
  args `<prefix> — download ALL matches and extract into <ART>/logs`. When it
  asks which files → all; output dir → `$ART/logs`.
  **If any AWS call fails** (expired token, AccessDenied, no creds): follow that
  skill's SSO device-code flow, show the user the URL + code, and **wait**.
  Never proceed to RCA "without logs" while a prefix exists and auth is
  failing. If creds are valid but nothing matches → AskUserQuestion *Proceed
  without logs* / *Abort* (bundles age out; never broaden the prefix).
- **Attachments** (`log_attachments`): text logs ≤2 MB → `mcp__jira__jira_attachments
  {action: get_content}` and Write into `$ART/logs/`. Binary `.tgz`
  attachments → ask the user to drop them into `$ART/logs/` (*Done* / *Skip*),
  then extract with `gzip -dc f | tar x -C <dir>` (multi-member gzip — never `tar xzf`).
- `logs_downloaded {prefix, bundles, dir}`; save `logs_dir`.
- `skill_used {skill: <name actually used>, outcome, detail, source: reported,
  invoked_by: orchestrator}` — `fallback` if only the bare name worked,
  `failed` if auth or download failed, `partial` if some bundles were missing.

## Stage 2 — RCA + red test (agent `fix-rca`)

`stage_started rca {iteration}` → spawn `fix-rca` (with `LOGS_DIR`) → verify
context → checks below → `stage_finished rca {iteration, confidence: <CONFIDENCE from rca.md>,
hint_check: <confirmed|partly|refuted|not_used from HINT_CHECK, omit if no hint>, context}`.

Checks before the gate — yours, not the agent's:
- `$ART/rca.md` has `ROOT_CAUSE_ANALYSIS` and `REPRO_TEST`.
- **Tree:** `$G check --baseline $ART/baseline.json --stage rca > $ART/check-rca.json`.
  `HALT:UNRELATED_FILES` (a production file changed) → treat as `HALT`.
  `STYLE` (ticket/PR refs in added comments, new files included) → re-prompt
  `fix-rca` once ("remove ticket/PR references from comments; keep comments to
  a non-obvious why, ≤2 lines"); still hits → show them at Gate 1 as ⚠.
  `noise` (e.g. `MODULE.bazel.lock` rewritten by bazel) is shown, never a halt.
- **Red, re-run by you:** `$G repro --rca $ART/rca.md --expect red --out $ART/repro-red.out`
  (runs `REPRO_TEST.COMMAND` with `--nocache_test_results`; Bash timeout 600000).
  `red` = bazel exit 3 **and** the `FAILURE` text is in the output. Anything
  else (`red_unmatched`, `build_error`, `green`, `no_tests`, `bad_command`) →
  re-prompt `fix-rca` once with the result and `repro-red.out` tail; still not
  `red` → `HALT REPRO_FAILED`. Then `repro_test {name, command, result: red,
  verified_by: orchestrator}`.

## GATE 1 — Approve root cause + failing test

`gate_opened {gate: gate1}`. Show the user, in this order:
1. `ROOT_CAUSE_ANALYSIS` block (verbatim), and `HINT_CHECK` if a hint was given.
2. The repro test diff (`$ART/repro.diff`) and the failing output tail from
   **your** run (`$ART/repro-red.out`, not the agent's `repro.out`).
3. `RULED_OUT` alternatives.

One AskUserQuestion call with two questions:
1. **Decision** — *Approve* / *Revise (give feedback)* / *Reject*. Free text
   via "Other" counts as Revise with that text as feedback.
2. **RCA quality** — *Good* (would have written it myself) / *OK* (right,
   needed polish) / *Poor* (wrong or unsupported).

On Revise/Reject, ask once more for the **reason**: *Wrong root cause* /
*Weak evidence* / *Test doesn't reproduce the bug* / *Wrong test level or place*.

`gate_decision {gate: gate1, decision, rating: good|ok|poor, reason:
wrong_root_cause|weak_evidence|test_not_reproducing|test_wrong_place|<other text>,
feedback}` (actor human).
- Approve → Stage 3.
- Revise → re-run Stage 2 with `GATE_FEEDBACK` (max 2 revisions; after that
  only Approve/Reject are offered).
- Reject → `run_finished {outcome: rejected_gate1, reason}`; stop.

## Stage 3 — Plan (agent `fix-planner`)

`stage_started plan` → snapshot → spawn `fix-planner` → read-only check →
verify context → `stage_finished plan`.
If `BLAST_RADIUS.VERDICT` is `HALT:*` (out of scope / unrelated files) → treat
as HALT (the plan is still shown so a human can take it over).
`WARN:OVER_BUDGET` is **not** a halt — it goes to Gate 2.

## GATE 2 — Approve plan (authorizes code edits + targeted tests only)

`gate_opened {gate: gate2}`. Show `plan.md` (Summary, Files, Approach, Risks,
Acceptance Criteria, BLAST_RADIUS, Tests to run). If the verdict is
`WARN:OVER_BUDGET`, put it first: **"⚠ Over the size budget: <metrics> —
<planner's why>. Approve only if this size is acceptable for a bugfix."**
State plainly:
**"Approving lets the fix agent edit code on `<BRANCH>` and run the targeted
tests. Nothing is committed or pushed until you approve the diff at Gate 3."**

Same two questions as Gate 1 (Decision; **Plan quality** Good/OK/Poor), same
handling (revise → re-run Stage 3 with feedback, max 2). Revise/Reject reasons:
*Wrong approach* / *Too broad* / *Missing tests* / *Risk not addressed*
(`wrong_approach|too_broad|missing_tests|risk_unaddressed|<other>`).
`gate_decision {gate: gate2, decision, rating, reason, feedback}`. Reject → `run_finished {outcome: rejected_gate2}`.

## Stage 4 — Fix (agent `fix-coder`), then GATE 3 — approve the diff

1. `stage_started fix` → spawn `fix-coder` (`BRANCH`, `DRY_RUN`) → verify
   context. The agent edits, runs the targeted tests, writes `fix.md`,
   `$ART/commit-msg.txt` and `$ART/pr-body.md`, and stops with `FIX_READY` —
   it never runs `git add/commit/push` or `gh pr`.
2. From `fix.md`: `tests_run {targets, passed, failed, attempts}` (`attempts` =
   red→green rounds, from `TEST_REPORT.ATTEMPTS`).
3. **Checks — yours, before anything is committed:**
   - `$G check --baseline $ART/baseline.json --stage fix --plan $ART/plan.md > $ART/check-fix.json`.
     It measures the worktree against the branch point (new untracked files
     included), so upstream master changes never count.
     `HALT:PLAN_DRIFT` (unplanned non-test file, new `BUILD.bazel` dep not in
     the plan, or non-test lines added+deleted > max(2 × estimate, estimate + 20))
     · `HALT:OUT_OF_SCOPE` · `HALT:FIX_INCOMPLETE` (no changes) → treat as
     `HALT`; nothing was committed or pushed. `STYLE` → re-prompt `fix-coder`
     once with `RESUME: dirty` and the `comment_hits` as `GATE_FEEDBACK`, then
     re-check; still `STYLE` → `HALT STYLE`.
   - **Green, re-run by you:** `$G repro --rca $ART/rca.md --expect green --out $ART/repro-green.out`.
     `green` = bazel exit 0 **and** proof the test ran (Go `--- PASS: <NAME>`,
     pytest `N passed`); a filter that matches nothing gives `no_tests`, not
     green. Not `green` → `HALT TESTS_FAILING` with the output tail.
     `repro_test {name, command, result: green, verified_by: orchestrator}`.
4. DRY_RUN → show the check summary and the `ship` command you would run;
   `run_finished {outcome: dry_run}`; stop.
5. **GATE 3.** `gate_opened {gate: gate3}`; state `gate3`. Show: the
   `diffstat` from `check-fix.json` (plus `git diff` of the non-test files if
   ≤150 lines, else offer it), `noise` files (left uncommitted), the commit
   message, the PR title, and the green evidence. One AskUserQuestion:
   **"Commit these files, push `<BRANCH>` and open a draft PR?"** —
   *Commit, push and open draft PR* / *Stop (edits stay uncommitted on the
   branch)*. Free text via "Other" = change request → `fix-coder` with
   `RESUME: dirty` and that text as `GATE_FEEDBACK`, then back to step 3 (max 2).
   `gate_decision {gate: gate3, decision: approve|revise|reject, fingerprint,
   feedback}` (actor human). Stop → `run_finished {outcome: rejected_gate3}`; state `done`.
6. **Ship** (approve only):
   ```bash
   $G ship --baseline $ART/baseline.json --ticket $T --branch $BRANCH --plan $ART/plan.md \
     --fingerprint <fingerprint from check-fix.json> \
     --message $ART/commit-msg.txt --body $ART/pr-body.md > $ART/ship.json
   ```
   It re-runs the fix check, refuses if the diff no longer matches the
   approved fingerprint, stages exactly the changed files (never noise or
   pre-existing untracked files), commits, pushes (never forced) and opens
   the draft PR — or reuses the open PR for the branch. Exit 3 on a failed
   commit (usually a pre-commit hook) → `fix-coder` with `RESUME: dirty` and
   the hook output, then step 3 and Gate 3 again (new fingerprint). Exit 3 on
   push / PR → show the error and stop; `resume` picks it up (`committed` /
   `pushed_no_pr`).
7. `pr_opened {url, number, branch, files, added, deleted, commits, head_sha}`
   from `ship.json` `.pr` → `stage_finished fix`. (`commits` is the baseline
   for counting human follow-up commits later.)

If the user edits code by hand at any point during the run, append
`manual_edit {files, note}` (actor human) before continuing.

## Stage 5 — Handoff

`run_finished {outcome: pr_opened}`; `$L harvest --run-id $RUN_ID`; state `done`.
Print (tokens / skills from `$L report --ticket $T --csv`):

```
PR (draft): <url>        Branch: <BRANCH>        Run: <RUN_ID>
Repro: <command>  (red on master → green)
Tokens: <tokens_total> (<tokens_by_stage>)   Model/effort: <models>/<efforts>
Skills: <skills>   Failed: <skills_failed or none>
Next (optional):
  make test-branch        — full blast-radius tests for the branch (not run by /fix)
  /pr-review <N>          — AI review posting inline comments
  /fix review-handle <T>  — answer review comments: triage → your OK → fix → your OK → push + replies
  /fix status --refresh   — record PR state changes in the ledger
  /fix learn <T>          — after review comments arrive: turn them into lessons (repeatable)
  /fix teach <T> "<feedback>"  — your own lesson for this run, any time
  /fix rate <T> <good|ok|poor> [note]  — after merge/close: how did the AI do?
```

Then run **Learn** (`PHASE=run_end`).

After this point, any further commit or push (e.g. review follow-ups) needs a
fresh confirmation from the user — Gate 3 covered only the initial PR.
`/fix review-handle` asks for it at Gate R2 of every round.

## Review-handle — answer PR review comments (agent `fix-responder`)

`/fix review-handle <T> [--dry-run]`. One **round** = everything open on the PR
right now. Max 3 shipped rounds per PR; after that tell the user it needs a
human conversation and stop. `R="python3 $SK/scripts/fix_review.py"`.

1. **Locate.** `$R locate --ticket $T > $ART/review-locate.json` — the PR `/fix`
   opened (from the ledger), its branch and `RUN_ID`. Exit 1 (no PR, or not
   OPEN) → tell the user; stop. Reuse that `RUN_ID`: every event of the round
   goes to the run that opened the PR. `k = rounds_shipped + 1`; `RD=$ART/review-<k>`;
   `mkdir -p $RD`. `rounds_shipped ≥ 3` → stop as above.
2. **Sync the branch.** `$R sync --branch $BRANCH`. It needs a clean tree:
   tracked edits → exit 1 with the files → tell the user "commit or stash
   these first" and stop (no auto-stash; untracked files are fine).
   `MODULE.bazel.lock` alone is reverted (your rule, lesson on bazel noise).
   It switches to the PR branch (no `-f`) and fast-forwards to `origin`; a
   diverged branch (unpushed local commits) → exit 1 → show ahead/behind; stop.
   Merge conflicts with master are not handled — GitHub keeps showing them.
   Then `$G snapshot --out $RD/baseline.json`.
3. **Items.** `$R items --ticket $T --out $RD/items.json`: unresolved,
   non-outdated review threads, review bodies and PR comments — bots, our own
   replies, and items answered in an earlier round (unless the reviewer wrote
   again) are dropped. `items: 0` → "nothing to answer"; stop.
   `review_round {round: k, stage: started, items: <n>, head_before}`.
4. **Triage.** Required docs: `$CTX --stage fix --path <each thread path>`;
   lessons: `$LS select --stage fix <same --path args>`. Spawn `fix-responder`
   with `MODE=triage`, `ROUND=k`, `RD` → verify context/lessons on
   `$RD/triage.md` → `$L harvest` → read-only check (`$G check --baseline
   $RD/baseline.json --stage readonly` must pass).
5. **GATE R1 — what to do per item.** `gate_opened {gate: review_r1}`. Show a
   table: id · where · reviewer's words (first 2 lines) · kind · proposal or
   draft reply. AskUserQuestion, one question per item (4 per call):
   *Approve* / *Reply only, no code change* / *Skip this round*; free text via
   "Other" = your wording for the change or the reply. Write `$RD/approved.json`
   (the triage entries with `decision: approve`, your edits applied; *Reply
   only* sets `kind: question`). `gate_decision {gate: review_r1, approved,
   skipped, edited}` (actor human). Nothing approved → stop.
6. **Plan.** `$R plan --approved $RD/approved.json --out $RD/review-plan.md`
   — the files of the approved changes and their line estimate; this is what
   the diff is held to.
7. **Implement** (only if any approved item is `kind: change`). Required docs
   / lessons as in step 4 plus `--path` for each planned file. Spawn
   `fix-responder` with `MODE=implement` → verify context/lessons on
   `$RD/respond.md` → `$L harvest` → `tests_run` from its `TEST_REPORT`. Then
   your checks, as in Stage 4 step 3:
   `$G check --baseline $RD/baseline.json --stage fix --plan $RD/review-plan.md > $RD/check.json`
   (`HALT:*` → treat as HALT; `STYLE` → one re-prompt, then `HALT STYLE`) and
   `$G repro --rca $ART/rca.md --expect green --out $RD/repro-green.out`
   (not `green` → `HALT TESTS_FAILING`). No approved changes → the agent only
   writes `replies.json`; skip the checks.
8. `--dry-run` → show the diff stat and every reply; `review_round {round: k,
   stage: dry_run}`; stop.
9. **GATE R2 — publish.** `gate_opened {gate: review_r2}`. Show the
   `diffstat` from `check.json` (and the non-test diff if ≤150 lines), the
   commit message, and every reply as it will be posted. One question:
   **"Commit, push `<BRANCH>` and post <n> replies?"** — *Commit, push and
   reply* / *Stop (edits stay uncommitted)*; free text = change request →
   implement again with it as `GATE_FEEDBACK` (max 2), back to step 7.
   Replies only (no code change): *Post <n> replies* / *Stop*.
   `gate_decision {gate: review_r2, decision, fingerprint, feedback}`.
10. **Ship + reply.**
    ```bash
    $G ship --baseline $RD/baseline.json --ticket $T --branch $BRANCH --plan $RD/review-plan.md \
      --fingerprint <from check.json> --message $RD/commit-msg.txt --body $ART/pr-body.md > $RD/ship.json
    $R reply --ticket $T --items $RD/items.json --replies $RD/replies.json \
      --sha <ship.json .committed> --posted $RD/posted.json
    ```
    (skip `ship` when there are no code changes; replies then must not use
    `{sha}`). `ship` reuses the open PR — it never opens a second one. A push
    failure (e.g. the pre-push hook) → show it and stop; nothing is replied
    yet, and re-running step 10 resumes (`reply` skips what's in `posted.json`).
    For each posted reply `review_reply {round: k, id, kind, url}`; then
    `review_round {round: k, stage: shipped, shipped: true, commit, replies: <n>}`.
    Threads are never resolved by `/fix` — the reviewer does that.
11. `$L harvest`; then **Learn** with `PHASE=review` (the reviewer's comments
    from this round; our replies and commits are excluded automatically).
    Tell the user they are now on `<BRANCH>` (switched from `switched_from`
    in the sync output, if any).

## Learn — feedback → lessons (agent `fix-lessons`)

| PHASE | When | Signals it sees |
|---|---|---|
| `run_end` | after every `run_finished` (once per run) | gate revise/reject/ok/poor + feedback, overrides, skill failures |
| `review` | `/fix learn T` — whenever review comments arrive; repeatable | **human PR review comments** (inline + threads + review bodies + PR comments), follow-up commits |
| `manual` | `/fix teach T "<text>"` — any time after the run | your text |
| `post_merge` | `/fix rate` (merged/closed; `$L refresh-prs --ticket $T` first) | your rating, plus any review comments not yet learned |

Every phase also picks up `human_feedback` and `manual_edit` not yet distilled.
Signals carry ids; each is distilled **once** (bots are ignored). Review
comments are the most valuable signal — prefer `/fix learn` during review over
waiting for merge. `refresh-prs` prints a `/fix learn` hint when new review activity appears.

1. **Collect.** `$LS feedback --run-id $RUN_ID --phase $PHASE > $ART/feedback-$PHASE.json`.
   `has_signal: false` → tell the user "nothing new to learn from this run" and stop.
2. **Distil.** Spawn `fix-lessons` (`TICKET, ART, RUN_ID, PHASE`,
   `FEEDBACK=$ART/feedback-$PHASE.json`, `LESSONS=$SK/scripts/lessons.py`) →
   `$L harvest`. It proposes via `$LS propose --feedback $FEEDBACK` (writes
   `lessons_distilled` and marks the bundle's `signal_ids` consumed).
3. **Review** (also `/fix lessons review`). `$LS list --status proposed --json`.
   For each proposed lesson, show `WHEN <trigger> / DO <lesson>`, scope, and
   the evidence quotes, then AskUserQuestion (up to 4 lessons per call, one
   question each): *Approve* / *Approve, but narrow or reword it* / *Reject*.
   - Approve → `$LS review-set --id L --decision approve --run-id $RUN_ID`.
   - Reword/narrow (or free text via "Other") → apply the user's wording as
     `--edit '{"lesson": ..., "trigger": ..., "paths": [...]}'` with `--decision approve`.
   - Reject → `--decision reject` (kept so it is never re-proposed).
   Nothing is injected into future runs until approved.
4. Print the approved ids and, if any `kind: doc_gap` was approved, a hint:
   "doc gaps pending: `/fix lessons gaps` — worth a cloudn docs PR".
