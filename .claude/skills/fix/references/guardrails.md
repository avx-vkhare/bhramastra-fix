# Guardrails

Two kinds of rule:

- **Size budget → warning at Gate 2.** The planner estimates; the human
  sees ⚠ on anything over budget and decides. Never a halt on its own.
- **Hard stops → `HALT`, never "proceed anyway".** Denied paths, generated
  files, and code that drifts from the plan the human approved (after Gate 2
  nobody reviews the code before the push, so it must match what was approved).

## Size budget (planner estimate → ⚠ at Gate 2)

| Metric | Budget | Note |
|---|---|---|
| Non-test files changed | 5 (warn from 4) | median real fix is 1–2 files |
| Net non-test lines | 150 (warn from 100) | median real fix is <35 lines |
| New non-test files | 2 | more is a design, not a fix |
| Distinct Go packages / Python modules | 2 | catches small diff / wide surface |
| Test files / lines | unlimited | never cap the behaviour we want |

Over budget → `BLAST_RADIUS.VERDICT: WARN:OVER_BUDGET` with the metric(s),
and a one-line **why it can't be smaller** in Risks.

## Hard stops

| Rule | Checked by | Halt |
|---|---|---|
| Out-of-scope paths (list below) | planner + fix agent | `OUT_OF_SCOPE` |
| Hand edits to generated files (`*.pb.go`, `zz_generated.*`, `*_bpfel.go`) — regenerate instead | planner + fix agent | `OUT_OF_SCOPE` |
| `BUILD.bazel` other than gazelle output (`bazel run //:gazelle -- fix`) | fix agent | `OUT_OF_SCOPE` |
| Plan file not traceable to an approved `SUSPECT_SITES` entry (or its package's tests/BUILD) | planner | `UNRELATED_FILES` |
| **Plan drift**: a changed non-test file not in the approved plan's *Files to Change* | fix agent + orchestrator | `PLAN_DRIFT` |
| **Plan drift**: net non-test lines > max(2 × plan estimate, estimate + 20) | fix agent + orchestrator | `PLAN_DRIFT` |
| **Plan drift**: a `BUILD.bazel` hunk adding a dependency not listed in *Files to Change* (gazelle reorders are fine) | fix agent + orchestrator | `PLAN_DRIFT` |
| Ticket / PR references in added code comments (`agents/conventions/git.md` §Comments inside diffs; grep in SKILL.md) | RCA re-prompt · fix agent fixes before commit · orchestrator | `STYLE` |

Out of scope (never touch): `test-scripts/**`, `**/*.tf`, `.github/**`,
`.claude/**`, `CODEOWNERS`, `**/CLAUDE.md`, `**/AGENTS.md`, `agents/**`,
`.mcp.json`, `**/migrations/**`, `charts/**`, `Conf/**`.

On `PLAN_DRIFT` the fix agent stops before committing and says what grew and
why; the human can revise the plan (re-run Gate 2) or take over the branch.

## Tests

- Unit tests only (Go `go_test` or Python `pytest_test`). No e2e in this version.
- Repro must be red before the fix and green after.
- Every targeted `bazel test` must pass before commit.

## Iterations

- Gate revisions: ≤2 per gate (then the orchestrator only offers approve / reject).
- Fix agent red→green attempts: ≤2 inside one fix run.
