# BhramASTRA `/fix` — Jira bug → failing test → draft PR, with human gates

`/fix AVX-N` runs a gated pipeline of Claude Code agents on the cloudn repo:

```
Jira → Intake → eligibility → branch (latest master) → logs → RCA + red unit test
     → GATE 1 (you) → Plan → GATE 2 (you) → Fix + targeted tests → GATE 3 (you, the diff)
     → commit · push · draft PR → you review/merge
     → your feedback → proposed lessons → you approve → injected into future runs
```

- **Test-first**: no plan or code until a unit test reproduces the bug (red on master).
- **Human-gated**: you approve the root cause, the plan, and then the actual diff — nothing is committed or pushed before Gate 3.
- **Checked, not trusted**: red/green, plan drift and comment rules are re-checked by scripts, not taken from the agents.
- **Sequential agents** on Opus / high effort, each with a fresh context; handoff via files.
- **Everything is recorded** in a ledger (decisions, time, tokens, skills, PR outcome).
- **Learns from you**: corrections become reviewed lessons for similar future bugs.

How to use it step by step: [WORKFLOW.md](WORKFLOW.md). Team deck:
[docs/presentation.md](docs/presentation.md).

## Quick start

```
/fix check AVX-12345              # is it a good candidate?
/fix AVX-12345                    # run it; answer Gates 1, 2 and 3
/fix AVX-12345 check bgp_translator.go peer matching   # same, with a hint on where to look
/fix status --refresh             # later: pull PR state
/fix review-handle AVX-12345      # review comments came in → answer them (two gates, then push + replies)
/fix learn AVX-12345              # review comments came in → lessons (repeatable)
/fix teach AVX-12345 "tests should assert on routes, not log lines"   # your own lesson
/fix rate AVX-12345 ok "reviewer asked for a helper"   # after merge
/fix analytics                    # how is the pipeline doing overall?
```

All subcommands: [WORKFLOW.md § Command reference](WORKFLOW.md#command-reference)
or the repo [README](../../../README.md#use).

## Contents

| Path | Role |
|---|---|
| `SKILL.md` | orchestrator — dispatch, stages, gates, ledger calls |
| `~/.claude/agents/fix-intake.md` | reads Jira, extracts eligibility facts + candidate dirs |
| `~/.claude/agents/fix-rca.md` | root cause + failing Go/Python unit test |
| `~/.claude/agents/fix-planner.md` | minimal change within guardrails |
| `~/.claude/agents/fix-coder.md` | implement, green, targeted tests, commit message + PR body (never commits) |
| `~/.claude/agents/fix-lessons.md` | turns human feedback into proposed lessons |
| `~/.claude/agents/fix-responder.md` | `review-handle`: triages review comments, makes approved changes, drafts replies (never commits) |
| `references/` | handoff formats, guardrails, testing, eligibility rules, component map, ledger, lessons |
| `scripts/eligibility.py` | deterministic eligibility verdict from `facts.json` |
| `scripts/context_docs.py` | which repo docs each stage must read (+ verification) |
| `scripts/ledger.py` | append-only run ledger, transcript harvest, report, analytics |
| `scripts/fix_probe.py` | read-only branch/PR probe that tells `resume` where an interrupted stage left off |
| `scripts/fix_review.py` | `review-handle`: find the PR, sync its branch, fetch open review items, build the round plan, post replies |
| `scripts/fix_guard.py` | orchestrator's own checks: tree snapshots, plan-drift/comment checks, repro re-runs, existing-branch guard, commit/push/PR after Gate 3 |
| `scripts/lessons.py` | lesson store: feedback, propose, review, select, verify, stats |

---

## Example run (illustrative — ticket, code and numbers are made up)

**Ticket AVX-12345** — *"Spoke loses BGP-learned routes after transit HA
failover; routes come back only after spoke restart."* Component BGP,
severity S2, desired version 10.2.0, tracelog bundle prefix in the description.

### 1. Eligibility

```
> /fix check AVX-12345

TICKET_FACTS:
  KEY:         AVX-12345
  TYPE:        Bug            STATUS: Open         SEVERITY: S2
  COMPONENTS:  BGP            DESIRED: 10.2.0
  REPRO:       yes — "1. fail over transit-1 to HA  2. show routes on spoke-3"
  LOGS:        prefix=cust-acme/2026-09-20/spoke-3
  CANDIDATE_DIRS:
    - go/aviatrix.com/conduit/v2/controller-conduit — BGP translator
    - go/aviatrix.com/conduit/v2/gateway-conduit/routing — route programming

criterion          status  detail
issuetype          pass    Bug
status             pass    Open
component          pass    BGP
desired_version    pass    10.2.0 ≥ 9.2
repro_or_logs      pass    repro + bundle prefix
severity           warn    S2 (informational)

VERDICT: ELIGIBLE
```

### 2. Run → Gate 1

```
> /fix AVX-12345 suspect controller-side filtering of learned routes
  hint    → go/aviatrix.com/conduit/v2/controller-conduit (bgp_translator.go)
  branch  vkhare/AVX-12345-spoke-loses-bgp-learned-routes   (from origin/master)
  logs    3 bundles → ~/.bhramastra/runs/AVX-12345/logs/
  lessons injected for rca: L-0004

ROOT_CAUSE_ANALYSIS:
  WHAT:       Spoke drops transit-learned BGP routes after transit HA failover.
  WHY:        The translator keys learned routes by the primary transit's IP;
              after failover the HA IP doesn't match, so they are filtered as stale.
  CONFIDENCE: high
  EVIDENCE:
    - "filtering 42 stale learned routes from 10.1.0.12" (logs/spoke-3/avx-gw-state-sync.log:8812)
    - bgp_translator.go:311 compares peer.PrimaryIP only
  RULED_OUT:
    - gateway route programming — gateway received an empty desired set (log line 8815)
  SUSPECT_SITES:
    - go/aviatrix.com/conduit/v2/controller-conduit/bgp_translator.go:311
HINT_CHECK: confirmed — filtering happens in bgp_translator.go:311 (peer IP match)

REPRO_TEST:
  LANG: go   NAME: TestLearnedRoutes_KeptAfterTransitHAFailover
  COMMAND: bazel test //go/aviatrix.com/conduit/v2/controller-conduit:controller-conduit_test \
           --test_filter='^TestLearnedRoutes_KeptAfterTransitHAFailover$' --test_output=errors
  RESULT:  fail
  FAILURE: "expected 42 learned routes, got 0"

? Decision:     [Approve]  Revise  Reject
? RCA quality:  Good  [OK]  Poor
```

(If you had picked **Revise** — e.g. *"The HA IP is already in the peer
struct; check why it's not populated at failover time"* — RCA re-runs with
that feedback, and that sentence becomes evidence for a future lesson.)

### 3. Plan → Gate 2

```
## Summary
Match learned routes against both primary and HA transit IPs.

## Files to Change
- controller-conduit/bgp_translator.go — compare against peer.PrimaryIP and peer.HAIP

BLAST_RADIUS: FILES_NONTEST 1/5 · LINES_NONTEST ~12/150 · PACKAGES 1/2 · VERDICT PASS
Tests to run: repro test + //go/aviatrix.com/conduit/v2/controller-conduit:controller-conduit_test

Approving lets the fix agent edit code on vkhare/AVX-12345-… and run the targeted
tests. Nothing is committed or pushed until you approve the diff at Gate 3.
? Decision:      [Approve]  Revise  Reject
? Plan quality:  [Good]  OK  Poor
```

### 4. Fix → Gate 3 → draft PR

```
TEST_REPORT: controller-conduit_test PASS (148) · ATTEMPTS 1
check: PASS — 1 non-test file, 11 lines (limit 32), no unplanned files, no comment hits
repro (re-run by /fix): green — --- PASS: TestLearnedRoutes_KeptAfterTransitHAFailover

  controller-conduit/bgp_translator.go       +9 -2   (prod)
  controller-conduit/bgp_translator_test.go  +41 -0  (test)
  commit: AVX-12345: Match learned routes against primary and HA peer IPs

? Commit these files, push vkhare/AVX-12345-… and open a draft PR?
  [Commit, push and open draft PR]  Stop

PR (draft): https://github.com/AviatrixDev/cloudn/pull/60123   Run: r-20260930-a1b2
Repro: bazel test …controller-conduit_test --test_filter='^TestLearnedRoutes_KeptAfterTransitHAFailover$'
Tokens: 1.9M (intake 0.2M, rca 1.1M, plan 0.2M, fix 0.4M)   Model/effort: opus/high
Skills: net-download-tracelog   Failed: none
```

### 5. Learn

```
Run end: Gate 1 rated OK → fix-lessons proposed 1 lesson
  L-0007 (proposed, rca, comp=BGP)
    WHEN  Routes are missing after a transit/HA failover
    DO    Check which peer IPs the controller translator matches on (primary and HA)
          before looking at gateway route programming; the gateway only programs
          what the translator sends.
    evidence: gate1 rating ok — "right cause, but spent a long time in gateway-conduit first"
? L-0007:  [Approve]  Approve, but narrow or reword  Reject

… a week later, PR merged after one reviewer follow-up commit …
> /fix rate AVX-12345 ok "needed a nil check on HAIP for non-HA transits"
  post_merge: 1 follow-up commit, 2 review comments → proposed L-0008 (fix, comp=BGP)
    WHEN  Changing code that reads peer HA fields
    DO    Handle non-HA peers (HAIP empty) explicitly and add a test case for it.
```

The next BGP ticket's RCA prompt gets L-0007 and its fix prompt gets L-0008.
Each agent must say whether it applied them. `/fix lessons stats` shows whether
the same gate complaint stops recurring.

### 6. What the ledger shows

```
> /fix status AVX-12345
run_id          ticket     status     gates                          interventions  pr_state  time_to_merge  ai_rating
r-20260930-a1b2 AVX-12345  pr_opened  gate1:approve gate2:approve    0              MERGED    6d 3h          ok
```

`/fix analytics` aggregates across runs: funnel (started → eligible → gate 1
→ gate 2 → PR → merged), first-pass rates, revise reasons, tokens per merged
PR, model/effort compliance, skill failure rates, and first-pass rate with vs
without lessons.

---

## Not in scope (yet)

e2e tests; `make test-branch` runs automatically; auto-merge; backport labels;
Jira transitions. Fixes bigger than ~5 files / 150 lines are allowed only if
you approve them at Gate 2 (shown as ⚠).
