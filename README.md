# BhramASTRA `/fix`: Jira bug → failing test → draft PR, with human gates

`/fix AVX-N` is a Claude Code skill for the **cloudn** repo. It takes a Jira
ticket and goes through these steps:

1. Checks whether the ticket is a good candidate.
2. Finds the root cause.
3. Proves the root cause with a unit test that fails on master.
4. Plans the smallest fix.
5. Writes the fix, runs the affected tests, and opens a **draft** PR.

Two gates need your decision: one for the root cause plus the failing test,
and one for the plan. Every run is recorded in a local ledger. The skill also
learns from your gate feedback, your own notes, and PR review comments, but
only from lessons you approve.

- How it works and how to use it: [`.claude/skills/fix/WORKFLOW.md`](.claude/skills/fix/WORKFLOW.md)
- Example run: [`.claude/skills/fix/README.md`](.claude/skills/fix/README.md)
- Team deck source: [`.claude/skills/fix/docs/presentation.md`](.claude/skills/fix/docs/presentation.md)

## Install (user level; nothing goes into cloudn)

```bash
git clone git@github.com:avx-vkhare/bhramastra-fix.git
cd bhramastra-fix
cp -a .claude/. ~/.claude/                     # skill → ~/.claude/skills/fix, agents → ~/.claude/agents/fix-*.md
mkdir -p ~/.bhramastra
cp -n lessons/seed-lessons.jsonl ~/.bhramastra/lessons.jsonl   # optional: shared approved lessons (won't overwrite yours)
```

If you already have an older `~/.claude/skills/fix`, back it up first. The
copy above overwrites files with the same name.

**Requirements:**
- Run Claude Code from the cloudn root.
- `gh auth status` must be OK.
- The Jira MCP server must be connected (`/mcp`).
- `bazel` (version 8, as master uses), `jq` and `python3`.
- AWS SSO, for tracelog bundles.
- The `net-download-tracelog` and `net-topology` skills (avx-tool-shed).

## Use

```
/fix check AVX-N                   # is it a candidate? (no side effects)
/fix AVX-N [hint…]                 # full run; answer Gate 1 and Gate 2
/fix resume AVX-N                  # continue an interrupted run
/fix learn AVX-N                   # review comments came in → proposed lessons
/fix teach AVX-N "<feedback>"      # your own lesson for that run
/fix rate AVX-N good|ok|poor "…"   # after merge
/fix status --refresh · /fix analytics · /fix lessons review
```

## Layout

| Path | What |
|---|---|
| `.claude/skills/fix/SKILL.md` | the orchestrator: stages, gates, ledger, learning, resume |
| `.claude/agents/fix-{intake,rca,planner,coder,lessons}.md` | the stage subagents, run one at a time on Opus |
| `.claude/skills/fix/references/` | eligibility rules, guardrails, handoff formats, testing, ledger, lessons |
| `.claude/skills/fix/scripts/` | the deterministic parts: `eligibility.py`, `context_docs.py`, `ledger.py`, `lessons.py`, `fix_probe.py` (Python stdlib only) |
| `lessons/seed-lessons.jsonl` | approved lessons from the pilot runs |
| `docs/design-plan.md` | the original design plan (historical) |

## Local state (per user, not in this repo)

| What | Where |
|---|---|
| Run ledger | `~/.bhramastra/ledger.jsonl` (`BHRAMASTRA_LEDGER`) |
| Lessons | `~/.bhramastra/lessons.jsonl` (`BHRAMASTRA_LESSONS`) |
| Per-ticket artifacts | `<cloudn>/.bhramastra/AVX-N/` (git-excluded) |

**Sharing lessons:** to share one of your approved lessons, add its line to
`lessons/seed-lessons.jsonl` in a PR to this repo. Lesson ids are local, so
renumber it if the id clashes.

## Status

This is a pilot. The first real run was AVX-81794, which produced draft PR
#59914. Both gates passed first time. Its review feedback became the seed
lessons and new guardrails. Out of scope for now: e2e repro, automatic
`make test-branch`, and automatic PR-state polling.
