# BhramASTRA `/fix`: Jira bug → failing test → draft PR, with human gates

`/fix AVX-N` is a Claude Code skill for the **cloudn** repo. It takes a Jira
ticket and goes through these steps:

1. Checks whether the ticket is a good candidate.
2. Finds the root cause.
3. Proves the root cause with a unit test that fails on master.
4. Plans the smallest fix.
5. Writes the fix, runs the affected tests, and opens a **draft** PR.

Three gates need your decision: the root cause plus the failing test, the
plan, and the final diff. Nothing is committed or pushed before the third. Every run is recorded in a local ledger. The skill also
learns from your gate feedback, your own notes, and PR review comments, but
only from lessons you approve.

- How it works and how to use it: [`.claude/skills/fix/WORKFLOW.md`](.claude/skills/fix/WORKFLOW.md)
- Example run: [`.claude/skills/fix/README.md`](.claude/skills/fix/README.md)
- Team deck source: [`.claude/skills/fix/docs/presentation.md`](.claude/skills/fix/docs/presentation.md)

## Install on a new machine

`/fix` is installed per user. Nothing goes into the cloudn repo. The install
**links** your clone into Claude Code, so a later `git pull` updates `/fix`
in place.

**1. Check the requirements**

- Claude Code (`claude` on your PATH), a cloudn checkout, `git`, `jq`, `bazel` (version 8, as master uses), and Python **3.10 or newer**.
- The GitHub CLI, logged in: `gh auth login`, then `gh auth status`. It must be able to see this private repo and `AviatrixDev/cloudn`.
- Optional, for tracelog bundles: AWS SSO and the avx-tool-shed skills `net-download-tracelog` and `net-topology`.

**2. Clone and run the installer**

```bash
gh repo clone avx-vkhare/bhramastra-fix ~/bhramastra-fix   # or: git clone git@github.com:avx-vkhare/bhramastra-fix.git ~/bhramastra-fix
cd ~/bhramastra-fix
./install.sh
```

The installer does the following, and is safe to re-run:

| Creates | Pointing to / containing |
|---|---|
| `~/.claude/skills/fix` (symlink) | `~/bhramastra-fix/.claude/skills/fix` — the orchestrator, scripts and references |
| `~/.claude/agents/fix-*.md` (symlinks) | `~/bhramastra-fix/.claude/agents/fix-*.md` — the stage agents |
| `~/.bhramastra/` | everything `/fix` keeps: the ledger, the lessons (`lessons.jsonl`, seeded from `lessons/seed-lessons.jsonl` only if you don't have one yet), and per-ticket run folders in `runs/` |
| `~/.claude/settings.json` entry | `~/.bhramastra` added to `permissions.additionalDirectories`, so agents can read and write run folders without permission prompts (the file is backed up first) |

If something is already at one of those paths (for example an older copied
install), it is moved to `~/.claude/fix-backup-<timestamp>/`, never deleted.
At the end the installer checks the tools above and prints ✓ or ✗ for each.

**3. Check it works, from cloudn**

```bash
cd <your cloudn checkout>
claude
```

Then, inside Claude Code:

```
/mcp                  # jira should be listed as connected (cloudn's .mcp.json provides it)
/fix help             # prints the subcommands and the flow
/fix check AVX-N      # eligibility only, a safe first run
```

**Later**

| To | Run |
|---|---|
| Update | `git -C ~/bhramastra-fix pull`. Re-run `./install.sh` only if a new agent file was added |
| Check the links and tools | `~/bhramastra-fix/install.sh --check` |
| Move the clone | Move it, then run `./install.sh` from the new place; it re-points the links |
| Uninstall | `~/bhramastra-fix/install.sh --uninstall`. This removes only the links; `~/.bhramastra/` is kept |

## Use

Every `/fix` subcommand (same list as `/fix help`):

| Command | What it does |
|---|---|
| **Run** | |
| `/fix check AVX-N [hint…]` | Is it a candidate? Intake + eligibility only; no branch or code changes (writes `~/.bhramastra/runs/AVX-N/` + a ledger entry) |
| `/fix AVX-N [hint…]` | Full run: RCA + red test → Gate 1 → plan → Gate 2 → fix → Gate 3 → draft PR |
| `/fix AVX-N --dry-run [hint…]` | Same, but stops before Gate 3 — nothing committed or pushed |
| `/fix resume AVX-N [hint…]` | Continue an interrupted or halted run (a new hint replaces the old one) |
| **Review** | |
| `/fix review-handle AVX-N [--dry-run]` | Answer the PR's review comments: triage → Gate R1 → fix → Gate R2 → push + in-thread replies; then learns from them |
| **Teach it** | |
| `/fix learn AVX-N` | Lessons from new PR review comments and follow-up commits (repeatable; each comment once) |
| `/fix teach AVX-N "<feedback>"` | Lessons from your own feedback on that run |
| `/fix teach "<rule>"` | Write a lesson directly, no ticket (approved on entry after one confirmation) |
| `/fix rate AVX-N good\|ok\|poor ["note"]` | Your verdict after merge/close, plus post-merge lessons |
| `/fix lessons [review]` | Review proposed lessons (approve / reword / reject) |
| `/fix lessons list \| gaps \| stats` | Approved lessons · doc-gap lessons · per-lesson effectiveness |
| **Look** | |
| `/fix status [AVX-N] [--refresh]` | Ledger report per run; `--refresh` pulls PR state from GitHub first |
| `/fix analytics` | Funnel, first-pass rates, reasons, tokens, skill failures, lessons impact |
| `/fix help` | This list and the flow diagram |

## Layout

| Path | What |
|---|---|
| `.claude/skills/fix/SKILL.md` | the orchestrator: stages, gates, ledger, learning, resume |
| `.claude/agents/fix-{intake,rca,planner,coder,responder,lessons}.md` | the stage subagents, run one at a time on Opus |
| `.claude/skills/fix/references/` | eligibility rules, guardrails, handoff formats, testing, ledger, lessons |
| `install.sh` | links the skill and agents into `~/.claude`, seeds `~/.bhramastra/`, checks tools |
| `.claude/skills/fix/scripts/` | the deterministic parts: `eligibility.py`, `context_docs.py`, `ledger.py`, `lessons.py`, `fix_probe.py`, `fix_guard.py`, `fix_review.py` (Python stdlib only) |
| `lessons/seed-lessons.jsonl` | approved lessons from the pilot runs |
| `docs/design-plan.md` | the original design plan (historical) |

## Local state (per user, not in this repo)

| What | Where |
|---|---|
| Run ledger | `~/.bhramastra/ledger.jsonl` (`BHRAMASTRA_LEDGER`) |
| Lessons | `~/.bhramastra/lessons.jsonl` (`BHRAMASTRA_LESSONS`) |
| Per-ticket artifacts (RCA, plan, test output, logs, review rounds) | `~/.bhramastra/runs/AVX-N/` (`BHRAMASTRA_RUNS`) |

**Sharing lessons:** to share one of your approved lessons, add its line to
`lessons/seed-lessons.jsonl` in a PR to this repo. Lesson ids are local, so
renumber it if the id clashes.

## Status

This is a pilot. The first real run was AVX-81794, which produced draft PR
#59914. Both gates passed first time. Its review feedback became the seed
lessons and new guardrails. Out of scope for now: e2e repro, automatic
`make test-branch`, and automatic PR-state polling.
