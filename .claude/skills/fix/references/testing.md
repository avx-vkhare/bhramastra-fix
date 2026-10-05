# Targeted unit tests

Pick the language from the **root-cause site**, not where the symptom shows:
`.go` suspect → Go test; `.py` suspect → pytest. Cross-language bug → test the
side where behaviour first goes wrong. No e2e (`test-scripts/end-to-end/`) —
if only an e2e can reproduce it, `HALT: NEEDS_E2E`.

## Go

- Test lives in the suspect package's `*_test.go` (same package name as the
  surrounding tests — match internal vs `_test` package).
- Style (`agents/conventions/go.md`): testify `require`/`assert`, table-driven
  if the file already is, `ctx := logging.InitTestCtx(t)`, log assertions via
  `go/aviatrix.com/util/testutils.NewLogObserver`.
- Helpers: `go/aviatrix.com/util/testutils/` (skips, log observer),
  `avxetcd/avxetcdtest` (in-memory etcd), `persistence/inmemory/`,
  `conduit/v2/controller-conduit/testutils/`. Mongo-backed tests
  (`persistence/mongotest`) need a real mongod — avoid for repros.
- Target: the package's `go_test` in its `BUILD.bazel` (usually `<pkg>_test`).
  Find it: `grep -n 'go_test' <pkg>/BUILD.bazel`. New test file in an existing
  package → run `bazel run //:gazelle -- fix <pkg>` if the target lists `srcs`
  explicitly.

```bash
# single test (repro)
bazel test //go/aviatrix.com/<pkg>:<pkg>_test --test_filter='^TestName$' --test_output=errors
# whole package (post-fix)
bazel test //go/aviatrix.com/<pkg>:<pkg>_test --test_output=errors
```

`.bazelrc` already enables `-race` and verbose test output. For a suspected
race, add `--config=flake-debug` (30 runs).

The orchestrator re-runs the repro command with `--nocache_test_results
--test_output=all --test_arg=-test.v` and looks for `--- FAIL: <NAME>` /
`--- PASS: <NAME>`. A `--test_filter` that matches nothing still exits 0
(`testing: warning: no tests to run`) — that counts as **no tests**, not green.

## Python

- Test beside existing tests for the module (`test_*.py` / `*_test.py`),
  pytest style, reuse fixtures from the nearest `conftest.py`
  (`cloudx-local/conftest.py`: `fake_controller`, `mock_accountdb_default`,
  mongomock via `bson_uuid_codec`). No lazy imports (`agents/conventions/python.md`).
- `cloudx-local` target: `//cloudx-local:cloudxd_tests` (8 shards, 20s default
  timeout, `mongo_test` marker skipped unless `RUN_MONGO_TESTS`).
- Other trees: find the nearest `pytest_test` in a `BUILD.bazel`
  (`cloudx-gateway`, `cloudx-common`, `cloudx-local/api`, …).

```bash
# single test (repro)
bazel test //cloudx-local:cloudxd_tests --test_arg=-k --test_arg='test_name' --test_output=errors
# equivalent
bazel test //cloudx-local:cloudxd_tests --test_filter='test_name' --test_output=errors
```

Post-fix, run the repro plus `-k` over the touched module's test file name
(running all of `cloudxd_tests` is allowed but slow).

## Not run automatically

`make test-branch` (blastradius over the whole branch) is offered to the user
at handoff, never run by the pipeline.

## Infra noise on this box

Remote-cache "Missing digest", `microseg_test_mongo` timeouts and `kind_test`
failures are environmental. Report them as such in `TEST_REPORT`; they do not
count as the fix failing, but a targeted test you wrote or touched must pass.
