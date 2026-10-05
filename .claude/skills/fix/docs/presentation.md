---
title: "BhramASTRA /fix — from Jira bug to draft PR, test-first, human-gated"
audience: cloudn engineering team
format: one slide per "---" section; "Notes:" = speaker notes
revision: 2026-09-30 r4
---

<!--
Changes in r4 (vs r3), after the first real pilot run (AVX-81794):
- Slide 11 "It learns from us": review comments are the main source; /fix learn and /fix teach added
- Slide 10 "Guardrails": BUILD-dependency drift and ticket references in comments
- Slide 14 "What runs automatically": learn / teach rows
- Slide 17 "How to use it": learn / teach commands
- Slide 18 "Pilot status": first run results

Changes in r3 (vs r2):
- Slide 3  diagram: learning line now shows who does each step (script / AI / you)
- Slide 13 NEW "The loop in action": one lesson followed from ticket A to ticket B
- Slides after it shift by one (What runs automatically = 14 … Pilot status = 18)

Changes in r2 (vs the first deck):
- Slide 3  "The idea in one picture": optional hint; override path for tickets without repro/logs
- Slide 4  "Design principles": #4 reworded (size = warning at Gate 2, plan drift = stop)
- Slide 5  "Intake and eligibility": Evidence row is now an override, not a hard stop
- Slide 6  "Root cause and a red test": hint + HINT_CHECK
- Slide 7  "Gate 1": shows HINT_CHECK
- Slide 8  "Plan and Gate 2": over budget is a ⚠ at Gate 2 (was: halts)
- Slide 9  "Fix and draft PR": plan-drift check, orchestrator re-verifies
- Slide 10 "Guardrails": split into "warns you" vs "stops the run"
- Slide 11 REPLACED by two slides: "It learns from us: who does what" + "Which lessons reach which ticket"
- Slide 13 NEW "What runs automatically — and what you run"
- Slide 14 "Example": uses a hint
- Slide 15 "What we measure": hint row
- Slide 16 "How to use it": hint + check examples
- Slide 17 "Pilot status": large fixes moved out of "out of scope"
-->

# BhramASTRA `/fix`
### Jira bug → failing test → draft PR — with engineers in control

- A Claude Code pipeline for cloudn bug fixes
- Test-first, two human gates, every run measured
- Learns from our corrections

Notes: This is a pilot. The goal isn't "AI fixes bugs alone". The goal is that
an engineer spends minutes on judgement instead of hours on digging, and we
can measure whether that's actually true.

---

## The problem

- Many cloudn bugs are small fixes (median real fix: 1–2 files, <35 lines)…
- …but finding them costs hours: ticket, logs, bundles, code, history
- Unstructured AI help is hard to trust: it guesses causes, skips tests and can't be audited
- We have no data on where AI helps and where it doesn't

Notes: The numbers come from 1,779 merged master PRs (Apr–Aug 2026).

---

## The idea in one picture

```
/fix AVX-N [hint]
Jira ─► Intake ─► Eligible? ─(no repro/logs? override 👤)─► Branch ─► Logs
     ─► RCA + RED TEST ─► GATE 1 👤 ─► Plan ─► GATE 2 👤
     ─► Fix + targeted tests ─► GATE 3 👤 (the diff) ─► DRAFT PR ─► Review / merge 👤
     ─► feedback (script) ─► proposed lessons (AI) ─► approve 👤
     ─► next similar ticket: matching lessons injected (script) ─► agent follows them
```

- 👤 = a human decision. The AI never merges, never marks the PR ready, and nothing is committed or pushed before you see the diff at Gate 3.
- Optional **hint**: "look at bgp_translator.go". It's a place to start, not evidence.
- The learning step runs automatically at the end of every run. Only the approval is yours.

Notes: Walk the diagram left to right. The core touchpoints are Gate 1,
Gate 2, lesson approval and the normal PR review. The override only appears
for tickets that have no repro steps and no logs.

---

## Design principles

1. **Prove it before fixing it.** A root cause counts only when a unit test reproduces it (red on master).
2. **Humans decide; agents work.** The gates happen in the main session. Agents can't talk to you.
3. **One agent at a time, fresh context.** Handoff happens through files, so runs are resumable and auditable.
4. **Small by default, but you decide.** Over the size budget is a ⚠ at Gate 2. Denied paths and drift from the approved plan stop the run.
5. **Measure everything.** A ledger records every decision, minute, token and outcome.
6. **Learn only what humans approve.**

---

## Step 1 — Intake and eligibility

- The agent reads the Jira ticket, comments and attachments, and turns any hint into real code paths.
- A **deterministic script** (not the LLM) gives the verdict:

| Criterion | Rule |
|---|---|
| Type / status | Bug, Task or Story, not Closed / Done / Resolved |
| Component | Routing, BGP, Gateway-Platform, Controller Infra, API, DCF, Micro-seg |
| Desired version | ≥ 9.2 |
| Evidence | repro steps **or** logs. Missing both → ⚠ **you may override** by describing how to reproduce it |
| Severity | shown, never blocks |

- `/fix check AVX-N` = eligibility only: no branch, no logs, no code changes.

Notes: The rules live in `eligibility.json`, not in prompts. When you
override a ticket with no evidence, your repro description becomes the hint,
and RCA must back everything with code. Otherwise it stops with
AMBIGUOUS_RCA instead of guessing. Every override is recorded.

---

## Step 2 — Root cause and a red test

- Branches from the latest `origin/master` and downloads tracelog bundles (SSO-aware; waits for you).
- The RCA agent works through ticket → logs → topology → code (starting at your hint) → git history and prior PRs.
- It reports **HINT_CHECK**: was the hint confirmed, partly right, refuted or not used?
- It writes **one unit test** where the bug starts:
  - Go (`go_test`, testify, table-driven)
  - Python (`pytest`, existing fixtures)
- The test must fail **on the assertion that encodes the bug**. A compile error or timeout doesn't count.
- If no unit test can reproduce it, the run halts with `REPRO_FAILED` / `NEEDS_E2E`. That is a useful answer, not a failure.

---

## Gate 1 — "Is this the real bug?"

You see:
- **What / why / confidence**, with quoted evidence (log line, file:line, PR)
- **HINT_CHECK**, if you gave a hint
- The test diff and its failing output
- The alternatives that were ruled out

You choose **Approve / Revise (with feedback) / Reject**, rate it **Good / OK / Poor**, and give a reason code if you don't approve.

- Revising re-runs RCA with your words (max 2 revisions).

Notes: This is the most valuable 5 minutes of the pipeline. A wrong root cause
caught here costs nothing.

---

## Step 3 and Gate 2 — "Is this the right change?"

- The planner designs the **minimal** change that turns the red test green.
- The plan shows the files, approach, risks, acceptance criteria, blast radius and the exact tests to run.
- **Over the size budget** (5 files / 150 lines / 2 new files / 2 packages):
  - shown **first** at Gate 2 as ⚠, with the planner's reason it can't be smaller;
  - **you decide**; it isn't an automatic stop.
- A denied path, or a file the RCA didn't point to, stops the run before any code is written.
- **Approving Gate 2 authorizes code edits and targeted tests only.** The commit and push wait for Gate 3.

Notes: We already have a human looking at the plan, so stopping on size
before the gate would take the decision away from them.

---

## Step 4 — Fix and draft PR

- Implements only the approved plan.
- Checks red → green, then runs **only the targeted unit tests** (fast; no e2e).
- **Plan-drift check** by a script, before anything is committed. The run stops (`PLAN_DRIFT`) if:
  - a non-test file was changed (or created) that isn't in the approved plan;
  - the non-test lines exceed 2× the plan's estimate.
- The orchestrator re-runs the repro test itself: it must pass, and the test must actually have run.
- **Gate 3**: you see the diff stat, commit message and PR title → *Commit, push and open draft PR?*
- Only then a script commits `AVX-N: …`, pushes, and opens a **draft** PR labelled `bhramastra` — exactly the approved diff.
  - The PR body includes the root cause, the exact test commands and provenance.
- From here it's our normal flow: `make test-branch`, `/pr-review`, `/pr-comments`, merge.

Notes: The plan-drift check guards "does the code match the plan I approved?",
not an absolute line count. Gate 3 is one extra click so that a commit and
push only ever happen after a human has seen the real diff (per-commit,
per-push consent, as cloudn's CLAUDE.md asks).

---

## Guardrails

**Warns you (you decide at a gate):**
- over the size budget: 5 files / 150 lines / 2 new files / 2 packages;
- a ticket with no repro and no logs (override at intake).

**Stops the run, with a reason and what a human needs to do next:**
- touching `test-scripts/`, Terraform, CI, migrations, charts, CLAUDE / AGENTS docs;
- hand-editing generated code;
- plan drift: files or new BUILD dependencies not in the approved plan, or >2× the estimated lines;
- code comments that cite the ticket or PR (they belong in the PR description);
- 2 failed red → green attempts;
- logs exist but AWS auth failed (it waits; it never continues "without logs").

**Never does:** add backport labels, move Jira tickets, mark the PR ready, or merge.

---

## It learns from us: who does what

**The best teacher is code review.** A reviewer's comment on the AI's PR says exactly what it got wrong.

**Learning:**

| Step | When | Who |
|---|---|---|
| 1. Collect feedback, each item only once | end of run: gate revisions, ratings, overrides · **`/fix learn`**: new PR review comments (with the code they point at) and follow-up commits, any time and repeatable · **`/fix teach`**: whatever you type · **`/fix rate`**: verdict after merge | script |
| 2. Propose at most 5 lessons (WHEN / DO, stage, scope, quoted evidence) | right after step 1 | **AI agent `fix-lessons`** |
| 3. Approve, reword or reject each | right after step 2 | **you** |

`/fix teach "<rule>"` with no ticket writes a lesson directly, for conventions you already know.

**Using** happens at the start of every stage in later runs:

| Step | Who |
|---|---|
| 4. Pick approved lessons that match this ticket and stage | script (no AI) |
| 5. Follow them; answer each "applied" or "not applicable" | stage agent (intake / RCA / plan / fix) |
| 6. Check that every lesson was answered, and record it | script |
| 7. Measure: did the same gate complaint come back? | script: `/fix lessons stats`, `/fix analytics` |

Notes: There are only two AI parts: one proposes, the stage agent follows.
Picking, checking and measuring are scripts. Nothing reaches a future run
without a human approving it. The model isn't retrained; a lesson is a
short text block in the right agent's prompt. A misleading hint counts as a
human mistake, not an AI one.

---

## Which lessons reach which ticket

Each lesson is tagged with a **stage** (which agent gets it) and a **scope** (which tickets):

```
L-0007  stage=rca  components=[BGP]  paths=[controller-conduit]
  WHEN  routes are missing after a transit/HA failover
  DO    check translator peer matching (primary + HA) before gateway route programming
```

| Lesson scope | Injected when | Example |
|---|---|---|
| **Component** (± paths) | the Jira ticket has that component | L-0007 goes to the RCA agent of **BGP tickets only**. A DCF ticket in the same directory never sees it. |
| **Paths only** | the ticket's candidate or suspect code paths match | "in `gateway-conduit/routing`, check the HA sync path" works for any component |
| **Neither** (global) | every ticket at that stage | rare, only for cross-cutting habits |

- At most 8 per stage, most specific first.
- **Doc gaps:** some lessons really mean our docs are missing something. `/fix lessons gaps` → cloudn docs PR → retire the lesson.

Notes: Where the tags come from. Component is the Jira Components field.
Paths are intake's candidate dirs for RCA, and the RCA's suspect code
locations for plan and fix. A lesson that stops working (applied, but the
same complaint recurs) is flagged for retirement.

---

## The loop in action (illustrative)

**Run 1: AVX-12345 (BGP)**, spoke loses routes after transit HA failover
1. **Gate 1:** you approve, rate **OK**, and write *"right cause, but it spent a long time in gateway-conduit first"*.
2. **End of run:** the script collects that feedback, and `fix-lessons` (AI) proposes:
   ```
   L-0007  stage=rca  components=[BGP]
     WHEN  routes are missing after a transit/HA failover
     DO    check translator peer matching (primary + HA) before gateway route programming
   ```
3. **You** approve it, and it's stored.

**Run 2: AVX-23456 (BGP)**, routes missing after a gateway replace
4. **Before RCA:** the script finds L-0007 (stage rca + component BGP) and adds it to the RCA agent's prompt.
5. **The RCA agent** writes `L-0007 — applied — checked bgp_translator peer matching first; it was fine, the cause is in …`
6. **The script** checks that the answer is there and records it. Gate 1 passes first time.

**Run 3: AVX-34567 (DCF)** doesn't get L-0007: wrong component.

Over time, `/fix lessons stats` shows L-0007: *injected 6, applied 5, first-pass 4, recurred 0*. It's working. If the same complaint kept coming back, it would suggest retiring the lesson.

Notes: This is the whole loop on one slide. Your one sentence at Gate 1
became a rule the next BGP RCA agent had to consider and answer. Nobody
retrained anything, and no lesson reached a ticket it wasn't scoped to.

---

## What runs automatically, and what you run

| Automatic, in order, inside `/fix AVX-N` | You run |
|---|---|
| intake → eligibility → branch → logs → RCA | Gate 1, Gate 2 and Gate 3 decisions |
| plan (after Gate 1) | the override, if the ticket has no repro and no logs |
| fix → tests → checks (after Gate 2); commit, push, draft PR (after Gate 3) | the AWS SSO code, if it has expired |
| token / model / skill recording after every agent | approving proposed lessons |
| lessons proposed at the end of the run | `/fix status --refresh` — PR state (not polled yet) |
| | `/fix learn AVX-N`: lessons from review comments, during review |
| | `/fix teach AVX-N "…"`: your own lesson, any time |
| | `/fix rate AVX-N`: verdict after merge |
| | `/fix analytics`, `/fix lessons stats` |

Notes: Planned: a nightly PR-state refresh, and a reminder to rate
merged runs whenever you start `/fix`.

---

## Example (illustrative)

**`/fix AVX-12345 suspect controller-side filtering of learned routes`**
Spoke loses BGP routes after transit HA failover.

1. **Eligible:** BGP, 10.2.0, repro + bundle. The hint resolves to `controller-conduit/bgp_translator.go`.
2. **RCA:** the translator matches the primary transit IP only.
   - HINT_CHECK: confirmed.
   - Red test: `expected 42 routes, got 0`.
   - Gate 1: Approve, rated OK ("spent too long in gateway-conduit first").
3. **Plan:** 1 file, ~12 lines, within budget. Gate 2: Approve.
4. **Draft PR:** repro green, package tests pass, 1 attempt, no drift.
5. **Lessons:**
   - L-0007: "for failover route loss, check translator peer matching before gateway programming"
   - After merge, L-0008: "handle non-HA peers explicitly"

---

## What we measure

| Question | Metric |
|---|---|
| Which tickets fit? | funnel: started → eligible → gate 1 → gate 2 → PR → merged; ineligibility and override reasons |
| Is the RCA right? | gate 1 first-pass rate, revise reasons, RCA confidence vs outcome |
| Do hints help? | hint confirmed / refuted; gate 1 first-pass with vs without a hint |
| Is the plan right? | gate 2 first-pass rate, reasons, plan-drift halts |
| How much human work is left? | interventions, follow-up commits, review comments, gate wait time |
| What does it cost? | tokens per stage, per merged PR; model / effort compliance |
| Is it improving? | first-pass with vs without lessons; gate 1 first-pass by month |
| Are the tools reliable? | per-skill failure rate |

- `/fix status`, `/fix analytics`, `ledger.py report --csv`
- Tokens and models come from the session transcripts, not from the AI's own account.

---

## How to use it

```
/fix check AVX-N                  # candidate? (no side effects)
/fix AVX-N                        # run; answer Gates 1, 2 and 3
/fix AVX-N look at bgp_translator.go peer matching   # with a hint
/fix resume AVX-N                 # continue an interrupted run
/fix status --refresh             # PR state
/fix learn AVX-N                  # review comments came in → lessons (repeatable)
/fix teach AVX-N "…"              # your own lesson for that run
/fix rate AVX-N ok "…"            # after merge: verdict + lessons
/fix lessons review               # approve proposed lessons
/fix analytics                    # team view
```

**Tips:**
- Know roughly where it is? Add a hint. RCA starts there, checks it, and reports whether it was right.
- Give specific gate feedback. It drives both the retry and future lessons.
- Commit or park your work before starting; tracked changes get stashed.
- A HALT is data. Take over the branch or the plan.

---

## Pilot status and asks

- **First real run (AVX-81794, Story, BGP):** draft PR #59914 in about 1h45m.
  - Both gates were approved first time and rated Good; the hint was confirmed.
  - The two red tests (Go and Python) turned green; there was no plan drift.
  - Review found an unneeded test dependency and ticket numbers in comments. Both are now guardrails and lessons.
- **Out of scope for now:**
  - e2e repro
  - automatic `make test-branch`
  - automatic PR-state polling (planned)
- Large fixes are allowed, but only with a ⚠ you approve at Gate 2.
- **Asks for the team:**
  1. Nominate eligible tickets (small, reproducible bugs in the components above).
  2. Run the gates honestly: reject and rate Poor when it's wrong.
  3. Rate every run after merge (`/fix rate`). This is what makes it learn.
- **Review in 4–6 weeks** using `/fix analytics`: continue, widen scope, or stop.
