# Handoff formats

Every stage agent writes its artifact to `.bhramastra/<T>/<stage>.md` and ends
its reply with the block(s) below. The orchestrator reads the file, not the
reply. Field names are fixed; values are free text unless an enum is given.

## CONTEXT_LOADED (every stage, first block in the artifact)

```
CONTEXT_LOADED:
  - CLAUDE.md — <one concrete takeaway used in this stage>
  - agents/feature-map.md — <e.g. "BGP: bgp_translator.go ↔ bgp_service.go">
  - go/aviatrix.com/conduit/AGENTS.md — <takeaway>
```

One line per doc from the required list the orchestrator gave you, same path
spelling. A takeaway must be specific to this ticket or the doc's content —
"read it" / "general guidance" counts as not loaded. Docs you read beyond the
list may be appended.

## TICKET_FACTS + facts.json (intake)

`intake.md`:

```
TICKET_FACTS:
  KEY:         AVX-12345
  SUMMARY:     <one line>
  TYPE:        Bug
  STATUS:      <status>
  SEVERITY:    S3 | none
  COMPONENTS:  BGP, Routing
  DESIRED:     10.2.0 | none            (customfield_10238)
  FIX_VERSIONS: 10.2.0, 10.1.1 | none
  AFFECTS:     10.1.0 | none
  REPRO:       yes — <quoted steps or pointer> | no
  LOGS:        prefix=<bundle prefix> | attachments=<n .tgz> | none
  CANDIDATE_DIRS:
    - go/aviatrix.com/conduit/v2/gateway-conduit — <why>
  LINKED:      AVX-111, AVX-222 | none
  HINT:        "<user hint verbatim>" → <resolved dirs/files; unresolved parts> | none
```

`facts.json` — input to `scripts/eligibility.py` (schema in that file's docstring).

## ROOT_CAUSE_ANALYSIS (rca)

```
ROOT_CAUSE_ANALYSIS:
  WHAT:      <one sentence — what failed>
  WHY:       <one sentence — underlying cause>
  COMPONENT: <Go package or Python module>
  CONFIDENCE: high | med | low
  EVIDENCE:
    - "<≤200 chars quoted>" (<log path:line | file:line | PR# | AVX-key>)
  RULED_OUT:
    - <alternative hypothesis> — <evidence against>
  FIX:       <specific actionable fix direction — file:line if known>
  SUSPECT_SITES:
    - path/to/file.go:123 — <why>
```

## HINT_CHECK (rca, only when a hint was given)

```
HINT_CHECK: confirmed | partly | refuted | not_used — <evidence, file:line>
```
`confirmed` = root cause is where the hint pointed; `partly` = right area,
different site; `refuted` = evidence points elsewhere; `not_used` = hint was
not actionable (say why).

## REPRO_TEST (rca)

```
REPRO_TEST:
  LANG:      go | python
  FILE:      path/to/foo_test.go
  NAME:      TestFoo_RejectsX
  COMMAND:   bazel test //go/aviatrix.com/x:x_test --test_filter='^TestFoo_RejectsX$' --test_output=errors
  RESULT:    fail
  FAILURE:   "<the assertion message / error line, verbatim>"
  MATCHES_RCA: yes — <why this failure is the WHY above, not a setup error>
```

A compile error, import error, missing fixture, or timeout is **not** a repro —
fix the test until it fails on the assertion that encodes the bug.

The orchestrator re-runs `COMMAND` (`fix_guard.py repro`, adding
`--nocache_test_results --test_output=all`, and `-test.v` for Go): red =
bazel exit 3 + `FAILURE` found in the output; green = exit 0 + `--- PASS: <NAME>`
(Go) or `N passed` (pytest). `COMMAND` must be one plain `bazel test …` with
no shell syntax; `FAILURE` may elide with `...` between verbatim fragments.

## PLAN (planner)

```
## Summary
<2-3 sentences>

## Files to Change
- `path/to/file.go` — <reason>

## Approach
1. <step>

## Risks
- <risk or "None">

## Acceptance Criteria
- [ ] REPRO_TEST <NAME> passes
- [ ] <criterion>

BLAST_RADIUS:
  GATE: plan
  FILES_NONTEST: 2/5
  LINES_NONTEST: ~30/150      (added + deleted non-test lines)
  NEW_FILES: 0/2
  PACKAGES: 1/2
  DENYLIST_HITS: none
  VERDICT: PASS | WARN:OVER_BUDGET (<metrics>) | HALT:OUT_OF_SCOPE | HALT:UNRELATED_FILES
```

## TEST_REPORT (fix)

```
TEST_REPORT:
  REPRO_TEST: pass | fail
  ATTEMPTS: <red→green rounds needed, 1 = first try>
  NEW_TESTS: <n beyond the repro>
  TEST_COMMANDS:
    bazel test //go/aviatrix.com/x:x_test --test_filter='^TestFoo_RejectsX$' --test_output=errors
    bazel test //go/aviatrix.com/x:x_test --test_output=errors
  RESULTS:
    - //go/aviatrix.com/x:x_test — PASSED
  COVERS:
    - <acceptance criterion> → <test name>
  UNCOVERED:
    - <criterion> — <why> | none
```

Followed by a `BLAST_RADIUS` block with `GATE: post-code` computed from
`fix_guard.py check --stage fix` (worktree vs the branch point, new files included).

The fix agent also writes `$ART/commit-msg.txt` (`<TICKET>: <summary ≤64>`,
blank line, 2–4 lines of why) and `$ART/pr-body.md`; the orchestrator's
`fix_guard.py ship` uses both after Gate 3.

## LESSONS_APPLIED (every stage that received LESSONS; right after CONTEXT_LOADED)

One line per injected lesson id — every id, no exceptions.

```
LESSONS_APPLIED:
  - L-0003 — applied — checked bgp_translator desired state first; it was correct, moved on to gateway side
  - L-0011 — not_applicable — lesson is about Python migrations; this fix is Go-only
```
`applied` means you acted on it in this stage (say how); `not_applicable`
means its trigger doesn't match this ticket (say why). `LESSONS: none` in the
prompt → omit the block.

## SKILLS_USED (every stage, last block in the artifact)

One line per skill invocation — including ones that failed or fell back.
`outcome` is whether the skill **did the job you needed**, not whether it loaded.

```
SKILLS_USED:
  - avx-tool-shed:net-topology — ok — gave spoke→transit attachment for gw-1
  - net-download-tracelog — fallback — plugin name not found; bare name worked
  - avx-tool-shed:net-topology — failed — etcd_data.txt missing from bundle
```
`outcome` ∈ `ok | failed | fallback | partial`. No skills used → `SKILLS_USED: none`.

## HALT (any stage)

```
HALT:
  STAGE:  rca
  REASON: REPRO_FAILED | AMBIGUOUS_RCA | NEEDS_E2E | OUT_OF_SCOPE | UNRELATED_FILES | PLAN_DRIFT | STYLE | TESTS_FAILING | FIX_INCOMPLETE | CONTEXT_MISSING | TOOL_ERROR
  DETAIL: <what was tried, what blocked>
  NEXT:   <what a human would need to provide>
```
