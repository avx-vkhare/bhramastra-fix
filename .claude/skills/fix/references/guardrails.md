# Guardrails

Two kinds of rule:

- **Size budget → warning at Gate 2.** The planner estimates; the human
  sees ⚠ on anything over budget and decides. Never a halt on its own.
- **Hard stops → `HALT`, never "proceed anyway".** Denied paths, generated
  files, and code that drifts from the plan the human approved. They are
  checked by `scripts/fix_guard.py` **before anything is committed**; the
  human then sees the real diff at Gate 3 before the commit, push and draft PR.

## Size budget (planner estimate → ⚠ at Gate 2)

| Metric | Budget | Note |
|---|---|---|
| Non-test files changed | 5 (warn from 4) | median real fix is 1–2 files |
| Non-test lines (added + deleted) | 150 (warn from 100) | median real fix is <35 lines |
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
| **Plan drift**: a changed non-test file (new untracked files included) not in the approved plan's *Files to Change* | `fix_guard.py check --stage fix` (fix agent + orchestrator) | `PLAN_DRIFT` |
| **Plan drift**: non-test lines (added + deleted) > max(2 × plan estimate, estimate + 20) | same | `PLAN_DRIFT` |
| **Plan drift**: a `BUILD.bazel` hunk adding a dependency not listed in *Files to Change* (gazelle reorders are fine) | same | `PLAN_DRIFT` |
| RCA stage changed a production file | `fix_guard.py check --stage rca` | `UNRELATED_FILES` |
| A read-only agent (intake, planner) changed the tree | `fix_guard.py check --stage readonly` | `UNRELATED_FILES` |
| Ticket / PR references in added code comments, new files included (`agents/conventions/git.md` §Comments inside diffs) | `fix_guard.py check` · one re-prompt of the agent, then halt | `STYLE` |

All changes are measured against `$ART/baseline.json`, taken right after the
branch is created: files that were already untracked (`.serena/`, notes) never
count, and upstream master commits never count. `MODULE.bazel.lock` rewritten
by bazel is reported as noise and never committed.

Out of scope (never touch): `test-scripts/**`, `**/*.tf`, `.github/**`,
`.claude/**`, `CODEOWNERS`, `**/CLAUDE.md`, `**/AGENTS.md`, `agents/**`,
`.mcp.json`, `**/migrations/**`, `charts/**`, `Conf/**`.

On `PLAN_DRIFT` nothing has been committed: the human can revise the plan
(re-run Gate 2; the edits stay on the branch) or take over the branch.

## Tests

- Unit tests only (Go `go_test` or Python `pytest_test`). No e2e in this version.
- Repro must be red before the fix and green after — **re-run by the
  orchestrator** (`fix_guard.py repro`), not taken from the agent's report.
  Green needs proof the test ran (a filter matching nothing is not green).
- Every targeted `bazel test` must pass before commit.

## Iterations

- Gate revisions: ≤2 per gate (then the orchestrator only offers approve / reject).
- Fix agent red→green attempts: ≤2 inside one fix run.
