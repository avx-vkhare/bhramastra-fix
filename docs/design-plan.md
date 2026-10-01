# /fix redesign: a Jira-to-PR bug-fix pipeline with two human gates and a failing test first

## Context

The current `/fix` ([SKILL.md](/home/vkhare/cloudn/.claude/skills/fix/SKILL.md)) doesn't work as written:
- Its `trap` deletes the downloaded logs as soon as the download command finishes.
- The Jira tool names it uses don't exist here.
- The Terraform it writes is invalid (HCL2 doesn't accept `;`).
- It commits and pushes without asking.
- It runs its two subagents in parallel.
- It never writes a failing test first.
- It never decides whether a ticket is a good candidate for automation.

The target flow is the pilot diagram:

Jira → **Intake** → **RCA** → **Gate 1** → **Planner** → **Gate 2** → **Fix** (opens a draft PR) → human review → human merge

Additional requirements:
- Decide eligibility at intake (repro, logs, desired version > 9.1, component, severity).
- Write a failing test before planning or fixing.
- Run stages one after another, never in parallel.
- Run on Opus 5.5 at high effort.
- Run only the specific tests the fix affects.
- Start each run from the latest master.
- Keep a shared ledger of every run.

Most of the parts already exist in `~/.claude/skills/s4-autofix`:
- fixed handoff formats (`references/handoff-formats.md`);
- limits on diff size and scope (`references/guardrails.md`);
- the rule that the repro test must fail before the fix;
- RCA "lenses" (logs, code, history, topology, prior-art).

s4-autofix, however, has no human gates and runs its lenses in parallel. The plan reuses its content and replaces how it is orchestrated.

## Design decisions (my reasoning)

1. **The gates live in the orchestrator; the work lives in subagents.**
   - Subagents can't prompt the user, so both gates (and any eligibility override) run in the main `/fix` session through `AskUserQuestion`.
   - Each work stage is a named subagent, started in the foreground (`run_in_background: false`) and strictly one at a time.
   - Each stage starts with a fresh context, so logs read during RCA don't crowd out the fix stage.
   - Stages hand off through files in `.bhramastra/<T>/`, not through conversation text. That is what makes a run resumable and auditable.
2. **The failing test is part of RCA, not a separate step.** A root cause only counts as proven when a test reproduces it: red on master, and failing for the stated reason.
   - Gate 1 therefore approves "RCA + red test" together. The human sees the evidence and the test before any fix is planned.
   - The planner then only designs the change that turns that test green.
   - If no test can reproduce the bug, the run halts with `REPRO_FAILED`. That outcome is useful data, not a failure of the pipeline.
3. **Eligibility is a scored checklist with hard stops.** Each criterion is either a hard stop or a warning, configured in `references/eligibility.yaml` rather than fixed in the prose.
   - Test-first needs something to reproduce from, so "repro steps OR logs" is a hard requirement.
   - Intake always writes a verdict with reasons, which gives the ledger data even for rejected tickets.
   - `/fix check AVX-X` runs intake alone as a dry run.
4. **Log download goes through `net-download-tracelog`, run in the orchestrator.** It is interactive (it shows an SSO device code, asks "all or specific file", and asks for an output directory), so it can't run inside a subagent.
   - The orchestrator passes it the prefix found by intake and answers "all" and `.bhramastra/<T>/logs`.
   - No `rm` and no `trap`: the settings deny `rm`, and artifacts should persist for the ledger and for resume.
   - The plugin name is `net-download-tracelog:net-download-tracelog`, with the bare name as fallback.
5. **Each stage has a limited set of tests, and they are unit tests only.** The test language follows the buggy code (Go or Python). There are no e2e tests in this version.
   - RCA: runs only the new repro test (`--test_filter='^TestX$'` for Go, `--test_arg=-k` for pytest).
   - Fix: runs the repro test plus the `go_test` or `pytest_test` targets of the changed packages.
   - `make test-branch` (blastradius) is optional, offered at the end and never automatic.
   - The exact commands go into `TEST_REPORT` and the PR body.
6. **Model and effort are set at every level.**
   - Frontmatter: `model: opus` and `effort: high` on the skill and on each of the four agents.
   - Belt-and-braces: pass `model: "opus"` on every `Agent` call too.
   - Caveat: `~/.claude/settings.json` has `modelSettings.claude-opus-5-5.effortLevel: medium`. That may override frontmatter, so implementation must check which one wins (claude-code-guide) and say so in the report.
7. **The ledger is an append-only JSONL of events, written by a small script, with a report command.**
   - A script is used because the model shouldn't hand-write JSON that is meant to be permanent.
   - Events can record things that happen after the run (PR merged or closed, review-comment counts) through `/fix status --refresh`, which polls `gh`.
8. **Branching.** `git fetch origin master && git stash push -u -m "fix-<T>-autostash" || true && git switch -f -C vkhare/<T>-<slug> origin/master`.
   - This overrides local changes as you allowed. The stash is a free safety net.
   - The branch name follows your `vkhare/AVX-…` convention.
9. **Drop the dev-environment modes (B/D/R/T/G).** `rebuild` already covers them and describes itself as mirroring them. `/fix` stays focused on the pipeline.
10. **Use the repo's docs for routing.** Intake and RCA load, in order:
    - root `CLAUDE.md` and `AGENTS.md`;
    - `agents/architecture.md` and `agents/feature-map.md`;
    - the nearest `AGENTS.md` for each suspect directory;
    - `agents/conventions/{go,python}.md` (for the test and fix stages).

    A component→directory table (from the merge history of 1,779 PRs) goes into `references/component-map.md` as a starting point for search.

## Decisions confirmed by the user

- **Severity:** any severity is eligible. Severity is recorded in the ledger as a warning only, never a hard stop.
- **Components in scope:**
  - Routing
  - BGP
  - Gateway-Platform
  - Controller Infrastructure
  - API
  - DCF
  - Micro-segmentation

  A ticket with none of these components is INELIGIBLE (hard stop).
- **Hard stops:**
  - repro OR logs available;
  - Desired Version (`customfield_10238`, falling back to `fixVersions`) is 9.2 or later;
  - component is in scope;
  - `issuetype` = Bug;
  - status is not Closed or Done.
- **PR authority:** Gate 2 approval authorizes exactly one commit series, a push, and a draft PR for this run. Any later commit or push, for example to address review comments, needs its own confirmation per CLAUDE.md.
- **Ledger location:** `~/.bhramastra/ledger.jsonl`. Override it with `BHRAMASTRA_LEDGER`.

## Pipeline (what SKILL.md will describe)

Subcommands:
- `/fix <AVX-#>` runs the full pipeline.
- `/fix check <AVX-#>` runs intake only.
- `/fix resume <AVX-#>` continues a saved run.
- `/fix status [AVX-#] [--refresh]` prints the ledger report or a single run, optionally refreshing PR states.
- `/fix help`.

Stages. Each writes `.bhramastra/<T>/state.json` and ledger events when it starts and ends.

**Stage 0: preflight** (orchestrator)
- Checks `gh auth status`, `bazel`, the Jira MCP tools and the ledger script.
- Creates `.bhramastra/<T>/` and adds `.bhramastra/` to `.git/info/exclude`, since `.gitignore` is tracked.
- Offers to resume if a saved state exists.

**Stage 1: intake** (subagent `fix-intake`, read-only)
- Fetches the ticket with `mcp__jira__jira_issues` (`get`), comments and attachments.
- Fields used:
  - Severity: `customfield_10033`.
  - Desired Version: `customfield_10238`, falling back to `fixVersions`.
  - Plus `components`, `issuetype`, `status`, `versions`, and `customfield_10046` (defect finder).
- Checks for a repro in the description and comments: a "steps to reproduce" section or a numbered procedure. Jira has no repro label.
- Checks for logs: a bundle prefix in the text, or `.tgz` attachments.
- Maps components to candidate directories.
- Writes `intake.md` containing an `ELIGIBILITY` block:
  - `VERDICT: ELIGIBLE | INELIGIBLE | NEEDS_OVERRIDE`
  - one line per criterion
  - `TICKET_FACTS`
  - log prefix
- The orchestrator stops if the ticket is INELIGIBLE. On NEEDS_OVERRIDE it asks the user. Any override is recorded as a human intervention.

**Stage 1b: logs** (orchestrator)
- If a prefix or attachments exist, runs `net-download-tracelog` into `.bhramastra/<T>/logs/`.
- Attachments are fetched with `mcp__jira__jira_attachments`.
- Keeps the existing strict rule: if AWS access fails, stop and wait. Never "proceed without logs".

**Stage 2: RCA and red test** (subagent `fix-rca`)
- Works through the lenses in sequence: logs (using the existing file-priority table), code, history (`git log -S`), topology (`net-topology` on `etcd_data.txt`), and prior-art (similar merged PRs).
- Writes a `ROOT_CAUSE_ANALYSIS` block with `SUSPECT_SITES` to `rca.md`.
- Writes the repro test as a **unit test in the same language as the buggy code**:
  - **Go** when a suspect site is a `.go` file (gateway, conduit, appserver, avxapi, and so on).
    - Put it in that package's `*_test.go` and match the local style: testify, table-driven, `logging.InitTestCtx(t)`.
    - Run it with `bazel test //<pkg>:<pkg>_test --test_filter='^TestX$' --test_output=errors`.
  - **Python** when a suspect site is a `.py` file (`cloudx-local`, `cloudx-gateway`, `cloudx-common`, `python/aviatrix`).
    - Use pytest with the existing fixtures from `conftest.py` and mongomock.
    - Run it with `bazel test //cloudx-local:cloudxd_tests --test_arg=-k --test_arg='test_x'`, or the nearest `pytest_test` target.
  - If the bug spans both languages, write the test on the side where the wrong behaviour starts (the root-cause site), not where it shows up.
  - **No e2e tests** (`test-scripts/end-to-end/` is out of scope; e2e support is a later enhancement). If only an e2e test could reproduce the bug, halt with `REPRO_FAILED`, reason `needs_e2e`.
  - Run only that one test.
- The test must fail, and for the stated reason (the assertion message is checked).
- Writes `REPRO_TEST: file, name, command, observed failure`.
- Halts with `REPRO_FAILED` or `AMBIGUOUS_RCA` when appropriate.

**GATE 1** (orchestrator)
- Shows the RCA, the test diff and the failing output.
- Options: approve / revise (with feedback, which reruns Stage 2 at most twice) / reject.
- If rejected, the run stops and the ledger records the reason.

**Stage 3: plan** (subagent `fix-planner`, read-only)
- Uses the s4-autofix plan format (Summary, Files, Approach, Risks, Acceptance Criteria) plus an estimated size.
- The plan must be checked against `guardrails.md` (at most 5 non-test files and 150 lines, and paths outside the denylist).

**GATE 2** (orchestrator): approve / revise / reject, same as Gate 1.

**Stage 4: fix** (subagent `fix-coder`)
1. Implements only the approved plan.
2. Runs `gazelle` and `gofmt` or `ruff` where relevant.
3. Checks the repro test is now green and runs the targeted package tests. Up to 2 attempts to fix a red result.
4. Checks the diff against the guardrails.
5. Writes `TEST_REPORT`.
6. On success: commits each file individually with the message `AVX-#: <summary>`, then `git push -u`, then `gh pr create --draft --label bhramastra`.
   - The PR body uses the dcf-fix template sections: Description / Root cause / Tests (exact commands) / Jira / Upgrade / GW rollback / Provenance.
   - It doesn't add backport labels; it only lists the Jira fixVersions for the reviewer.
   - Whether commit and push are automatic depends on the open question below.

**Stage 5: handoff**
- Prints the PR URL.
- Suggests the `/pr-review <N>` (pr-reviewer agent) and `/pr-comments <N>` loop.
- Writes the final ledger event.

## Ledger (`references/ledger.md` + `scripts/ledger.py`)

Events are written to one JSONL file, one line per event:

```
{ts, run_id, ticket, event, stage, actor: agent|human, data{...}}
```

`event` is one of:
- `run_started`
- `eligibility` (verdict and per-criterion results)
- `logs_downloaded` (bundles, bytes)
- `stage_started` / `stage_finished` (duration, iteration)
- `gate_decision` (gate, decision, feedback, wait_seconds)
- `override`
- `repro_test` (red/green, command)
- `tests_run` (targets, pass/fail)
- `halted` (reason code)
- `pr_opened` (url, number, files, +/-)
- `pr_state` (draft/open/merged/closed, review threads, comment passes)
- `run_finished`

`ledger.py report` rolls these up per ticket:
- eligible? yes/no, and why;
- PR raised? yes/no;
- human interventions = gate revisions + gate rejections + overrides + manual edits;
- total time and time spent waiting at gates;
- PR outcome (merged, closed or still open) and time to merge;
- rework, taken from audit-ai `metrics-<KEY>.json` when present.

`report` can print a table or `--csv`.

## Files

- **Rewrite:** `.claude/skills/fix/SKILL.md` (the orchestrator; `model: opus`, `effort: high`, fixed `allowed-tools` names).
- **New:** `.claude/skills/fix/references/`
  - `eligibility.yaml`
  - `handoff-formats.md` (adapted from s4-autofix)
  - `guardrails.md` (from s4-autofix)
  - `component-map.md`
  - `testing.md` (targeted test commands for Go and Python, taken from `agents/build.md` and `.bazelrc`)
  - `ledger.md`
- **New:** `.claude/skills/fix/scripts/ledger.py` (stdlib only: `append`, `report`, `refresh-prs`).
- **New:** `.claude/agents/fix-intake.md`, `fix-rca.md`, `fix-planner.md`, `fix-coder.md`. Each is `model: opus`, `effort: high`, with a limited tool list; intake and planner get no Edit or Write except their artifact file.
- **Remove from `/fix`:** the L/B/D/R/T/G branches. They point to `rebuild` and `net-download-tracelog` instead.

## Verification

1. `/fix help` prints the subcommands.
2. `/fix check` on three tickets:
   - one that should be eligible;
   - one below 9.1 or with an excluded component, which should be INELIGIBLE with reasons;
   - one with no repro and no logs, which should hit the hard stop.

   Confirm the ledger has an `eligibility` event for each.
3. `/fix AVX-<known small bug>` end to end:
   - logs land in `.bhramastra/<T>/logs`;
   - the red test is shown at Gate 1;
   - reject at Gate 2 once to check that the run stops and the ledger records it;
   - rerun and approve, then check the green test output, the draft PR, and that its body contains the test commands.
4. `/fix status --refresh` shows PR state. `ledger.py report --csv` gives the interventions and timeline columns.
5. Check the model and effort that actually ran, using the session transcript `model` field and any effort indicator.
