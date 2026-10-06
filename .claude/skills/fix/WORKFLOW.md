# /fix workflow — how to use it

`/fix` (BhramASTRA) takes a Jira bug to a **draft PR** with a failing test
written first. You make the decisions; the agents do the digging and typing.
This page covers what you do at each step.

```
 you                      /fix                                   you
 ───                      ────                                   ───
 /fix check AVX-N  ──►  intake + eligibility ──► ELIGIBLE?
 /fix AVX-N        ──►  branch · logs · RCA + red test ──►  GATE 1  approve / revise / reject
                        plan                           ──►  GATE 2  approve / revise / reject
                        fix · targeted tests · checks  ──►  GATE 3  see the diff → commit · push · draft PR
                                                       ──►  review + merge (normal PR flow)
                        lessons from your feedback     ──►  approve / reword / reject lessons
 /fix learn AVX-N  ──►  lessons from PR review comments ──►  approve / reword / reject lessons
 /fix teach AVX-N  ──►  lessons from your own feedback  ──►  approve / reword / reject lessons
 /fix rate AVX-N   ──►  post-merge lessons              ──►  approve / reword / reject lessons
```

## 0. One-time setup

| Check | Why |
|---|---|
| Run Claude Code from the **cloudn root** | agents read `CLAUDE.md`, `AGENTS.md`, `agents/*.md` |
| `gh auth status` is OK | draft PR, PR state, review comments |
| Jira MCP connected (`/mcp`) | intake reads the ticket, comments, attachments |
| AWS SSO works for tracelog bundles | log download (it will show you a device code if expired) |
| Model **Opus**, effort **high** | set by the skill; `/fix analytics` flags runs that weren't |

## 1. Pick a ticket

Good candidates: a **Bug/Task/Story** in Routing, BGP, Gateway-Platform, Controller
Infrastructure, API, DCF or Micro-segmentation; desired version **≥ 9.2**;
**repro steps or logs** attached; not Closed/Done/Resolved. Severity is shown
but never blocks. No repro and no logs → **NEEDS_OVERRIDE**: you can continue
if you describe how to reproduce it or where to look (that text becomes the hint).

Not sure? Dry-run the eligibility check — no branch, no logs, no code
changes (it only writes `~/.bhramastra/runs/AVX-N/` and a ledger entry):

```
/fix check AVX-N
```

## 2. Start the run

```
/fix AVX-N                                   # full pipeline
/fix AVX-N --dry-run                         # everything up to Gate 3; nothing committed or pushed
/fix AVX-N look at bgp_translator.go learned-route filtering   # with a hint
```

**Hint (optional)** — anything after the ticket key: a file, package,
function, feature or suspicion. Intake resolves it to real dirs and RCA starts
there, but it is a lead, not evidence — RCA still checks every lens and
reports `HINT_CHECK: confirmed | partly | refuted | not_used` at Gate 1. It
never makes an ineligible ticket eligible. `/fix analytics` shows whether
hinted runs pass Gate 1 more often.

`/fix` will:
1. **Intake** — read the ticket and show `TICKET_FACTS` + a criteria table.
   `NEEDS_OVERRIDE` → you choose *Override* or *Stop* (overrides are recorded).
2. **Branch** — `git switch -c <you>/AVX-N-<slug> origin/master`. It never
   resets an existing branch: if a branch or open PR for the ticket already
   exists, `/fix` asks whether to start on a new branch name or stop.
   ⚠️ Tracked local changes are stashed first (`fix-AVX-N-autostash-…`) —
   commit or park your work before starting. Untracked files are left alone
   and never count as the run's changes.
3. **Logs** — download tracelog bundles / Jira attachments into
   `~/.bhramastra/runs/AVX-N/logs/`. If AWS auth fails it shows the SSO URL + code
   and **waits for you**. It never silently continues without logs.
4. **RCA + red test** — the agent finds the root cause and writes **one unit
   test** (Go or Python, where the bug starts) that fails on master for that reason.
   `/fix` then re-runs that test itself and only shows Gate 1 if it really
   fails with the failure the RCA claims.

## 3. GATE 1 — is this the real bug?

You see: the `ROOT_CAUSE_ANALYSIS` (what, why, evidence, confidence), the test
diff, the failing output, and ruled-out alternatives. Answer two questions:

| Decision | When |
|---|---|
| **Approve** | cause is right *and* the test fails for that cause |
| **Revise** + feedback | close but wrong; say what's wrong — it re-runs RCA with your words (max 2) |
| **Reject** | not worth continuing — run ends, reason recorded |

**Quality**: Good / OK / Poor. On Revise/Reject pick a reason
(*wrong root cause · weak evidence · test doesn't reproduce · wrong test place*).

> Be specific in feedback — it is used both for the retry **and** to create
> lessons for future runs. "Check the controller translator, not the gateway"
> beats "wrong".

## 4. GATE 2 — is this the right change?

You see `plan.md`: files, approach, risks, acceptance criteria, blast radius,
and the tests it will run. If the plan is over the size budget (5 non-test
files, 150 lines, 2 new files, 2 packages) it is shown **first as a ⚠ with the
planner's reason** — it's your call, not an automatic stop. Once you approve,
the code is held to *that plan*: new files or >2× the estimated lines halt
with `PLAN_DRIFT` before the commit.

**Approving Gate 2 lets the fix agent edit code and run the targeted tests —
nothing is committed or pushed yet.** Same Approve / Revise / Reject +
quality + reason (*wrong approach · too broad · missing tests · risk not addressed*).

## 5. Fix → GATE 3 → draft PR

The fix agent implements only the plan, turns the red test green and runs
**only the targeted unit tests** (no e2e, no `make test-branch`). It does not
commit. `/fix` then checks the real diff itself (plan drift, denied paths,
ticket refs in comments), re-runs the repro test to see it pass, and shows you
the diff stat, commit message and PR title:

> **GATE 3** — *Commit these files, push `<branch>` and open a draft PR?*
> *Commit, push and open draft PR* / *Stop* / or type a change request.

Only on your yes does a script commit (`AVX-N: …`), push and open the **draft
PR** labelled `bhramastra` — exactly the diff you saw. The handoff prints the
PR, the repro command, tokens, and next steps.

From here it's a normal PR: `make test-branch` if you want the full blast
radius, `/pr-review <N>`, mark ready, merge.

## 5b. Review comments → `/fix review-handle AVX-N`

When reviewers comment, run `/fix review-handle AVX-N` (`--dry-run` to stop
before anything is published). Your tree must have no uncommitted edits to
tracked files — it tells you which ones and stops; nothing is stashed. Then:

1. It switches to the PR branch and fast-forwards it to GitHub (it stops if
   the branch has local commits that were never pushed).
2. The responder agent reads every open thread / review / PR comment and
   proposes: *change* · *question* · *disagree* · *out of scope* · *already done*.
3. **GATE R1** — per item: *Approve* / *Reply only* / *Skip*, or type your own wording.
4. It makes only the approved changes, runs the targeted tests; `/fix` re-checks
   the diff against the approved items and re-runs the repro test.
5. **GATE R2** — you see the diff and every reply → *Commit, push and reply*.
6. One commit `AVX-N: Address review comments (round k)`, a normal push, and
   in-thread replies ("Fixed in `<sha>`: …"). Threads are left for the
   reviewer to resolve. Then the comments are turned into proposed lessons.

At most 3 rounds per PR; after that it's a conversation for humans.
Any further commit/push `/fix` makes needs your fresh OK.

## 6. Teach it

Review comments are the most valuable feedback — a reviewer telling you what
the AI got wrong. Feed them in while the PR is in review, not only after merge.

| When | Command | Learns from |
|---|---|---|
| Run end (automatic) | — | gate revisions/ratings, overrides, skill failures |
| Review comments came in (repeat as often as you like) | `/fix learn AVX-N` | **new** human review comments (inline + threads + review bodies), follow-up commits |
| You spotted something yourself | `/fix teach AVX-N "the test asserted on a log line; assert on routes"` | your text, in the context of that run |
| A rule you already know, no ticket | `/fix teach "comments never cite ticket numbers"` | written directly as an approved lesson |
| After merge/close | `/fix status --refresh` then `/fix rate AVX-N good\|ok\|poor "note"` | your verdict + any comments not learned yet |

Each comment or piece of feedback is learned once, however many times you run
`learn`. `/fix status --refresh` tells you when new review activity appears.
Every proposed lesson waits for your Approve / reword / Reject.

Only lessons **you approve** are ever injected into future runs, and only
for matching stage + component/code path.

## 7. When it stops (HALT)

A HALT is a normal outcome, not a crash. It tells you what it needs:

| Reason | Typical cause | What you do |
|---|---|---|
| `REPRO_FAILED` / `NEEDS_E2E` | no unit test can reproduce it | fix by hand, or add repro detail and re-run |
| `AMBIGUOUS_RCA` | evidence points several ways | add logs / narrow the ticket |
| `OUT_OF_SCOPE` / `UNRELATED_FILES` | plan touches denied paths, generated code, or files the RCA didn't implicate | take the plan and do it manually |
| `PLAN_DRIFT` | the code grew beyond the plan you approved (new files, new BUILD deps, or >2× lines); caught before any commit | revise the plan (`/fix resume`, Gate 2 again) or take over the branch |
| `STYLE` | added comments still cite the ticket / PR after one automatic retry (nothing committed) | `/fix resume AVX-N`, or take over |
| `TESTS_FAILING` / `FIX_INCOMPLETE` | 2 red→green attempts failed | take over the branch |
| `CONTEXT_MISSING` / `TOOL_ERROR` | docs/tools unavailable | fix env, `/fix resume AVX-N` |

**Interrupted** (API/auth error, closed terminal, lost session)? If an agent
dies mid-stage, `/fix` asks *Retry now* / *Stop*. Later, `/fix resume AVX-N`
continues the same run. For RCA and fix it first reads the branch
(`scripts/fix_probe.py`): uncommitted edits, commits, push and PR. Then it
picks up from there. The existing edits are never stashed or discarded, and
no second PR is opened. `resume` also works after `TOOL_ERROR`,
`CONTEXT_MISSING`, `PLAN_DRIFT` (back to planning and Gate 2) and `STYLE` halts.

## Command reference

| Command | Does |
|---|---|
| `/fix help` | usage + flow |
| `/fix check AVX-N [hint…]` | eligibility only |
| `/fix AVX-N [--dry-run] [hint…]` | full pipeline |
| `/fix resume AVX-N [hint…]` | continue a saved run (a new hint replaces the old one) |
| `/fix review-handle AVX-N [--dry-run]` | answer review comments on the PR: triage → Gate R1 → fix → Gate R2 → push + replies |
| `/fix status [AVX-N] [--refresh]` | ledger report; `--refresh` pulls PR state from GitHub |
| `/fix learn AVX-N` | lessons from new PR review comments (repeatable) |
| `/fix teach AVX-N <feedback…>` | lessons from your own feedback on that run |
| `/fix teach <feedback…>` | write a lesson directly (no ticket) |
| `/fix rate AVX-N good\|ok\|poor [note]` | your verdict + post-merge lessons |
| `/fix lessons [review]` | review proposed lessons |
| `/fix lessons list \| gaps \| stats` | approved lessons · doc-gap lessons · per-lesson effectiveness |
| `/fix analytics` | funnel, first-pass rates, reasons, tokens, skill failures, lessons impact |

## Where things live

| What | Where |
|---|---|
| Per-ticket artifacts (intake, rca, plan, fix, logs, state) | `~/.bhramastra/runs/AVX-N/` (outside the repo, kept) |
| Run ledger (every event, every run) | `~/.bhramastra/ledger.jsonl` |
| Lessons | `~/.bhramastra/lessons.jsonl` |
| Skill + agents | `~/.claude/skills/fix/`, `~/.claude/agents/fix-*.md` |
| Review rounds (items, triage, approved, replies, posted) | `~/.bhramastra/runs/AVX-N/review-<k>/` |
