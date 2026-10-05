#!/usr/bin/env python3
"""Deterministic orchestrator checks for /fix — the parts that must not rest on an agent's word.

  fix_guard.py snapshot --out F                          HEAD + dirty/untracked files right now
  fix_guard.py changes  --baseline F                     files changed since that snapshot
  fix_guard.py check    --baseline F --stage readonly|rca|fix [--plan plan.md]
  fix_guard.py repro    --rca rca.md --expect red|green --out F
  fix_guard.py existing --ticket T [--branch B]          branches / open PRs that already exist for T
  fix_guard.py ship     --baseline F --ticket T --branch B --plan plan.md
                        --fingerprint X --message F --body F

`changes` diffs the worktree against the snapshot's HEAD (commits + uncommitted
edits + new untracked files), ignoring files that were already dirty or
untracked when the snapshot was taken and are unchanged since. Take the
baseline right after the run's branch is created; take a fresh snapshot just
before a read-only agent and `check --stage readonly` after it.

`ship` re-runs `check --stage fix`, refuses unless the fingerprint equals the
one the human approved at Gate 3, then commits, pushes (never forced) and opens
the draft PR, or reuses the open PR for the branch.

Exit codes: 0 ok · 1 check failed (JSON `verdict` / `result` says why) ·
2 usage or environment error · 3 git/gh step failed during ship.
"""

import argparse
import hashlib
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from fnmatch import fnmatch
from pathlib import Path

TEST_MARKERS = ("_test.go", "/test_", "_test.py", "/tests/", "/testdata/", "conftest.py")
NOISE = {"MODULE.bazel.lock"}            # rewritten by bazel itself; reported, never committed
OUT_OF_SCOPE = ("test-scripts/*", "*.tf", ".github/*", ".claude/*", "CODEOWNERS", "*/CODEOWNERS",
                "CLAUDE.md", "*/CLAUDE.md", "AGENTS.md", "*/AGENTS.md", "agents/*", ".mcp.json",
                "migrations/*", "*/migrations/*", "charts/*", "Conf/*")
GENERATED = ("*.pb.go", "*zz_generated.*", "*_bpfel.go")
COMMENT_RE = re.compile(r"^\s*(//|#).*\b(AVX-[0-9]+|PR ?#?[0-9]{4,})")
DEP_RE = re.compile(r'^"(//|@|:)')
SIG_HASH_LIMIT = 5 * 1024 * 1024
ART_PREFIX = ".bhramastra/"


def run(*cmd: str, check: bool = False, **kw) -> subprocess.CompletedProcess:
    p = subprocess.run(list(cmd), capture_output=True, text=True, **kw)
    if check and p.returncode != 0:
        sys.exit(f"fix_guard: {' '.join(cmd)} failed: {p.stderr.strip()}")
    return p


def git(*args: str) -> str:
    p = run("git", *args)
    return p.stdout.rstrip("\n") if p.returncode == 0 else ""


def emit(obj: dict, code: int) -> int:
    print(json.dumps(obj, indent=2))
    return code


def file_sig(path: str, full: bool = False) -> str:
    p = Path(path)
    if not p.is_file():
        return "absent"
    st = p.stat()
    if st.st_size > SIG_HASH_LIMIT and not full:
        return f"size:{st.st_size}:{st.st_mtime_ns}"
    return hashlib.sha256(p.read_bytes()).hexdigest()


def tracked_dirty() -> list[str]:
    out = run("git", "status", "--porcelain=v1", "-z", "--untracked-files=no").stdout
    entries, paths = out.split("\0"), []
    i = 0
    while i < len(entries):
        e = entries[i]
        i += 1
        if len(e) < 4:
            continue
        paths.append(e[3:])
        if e[0] in "RC":          # rename/copy: the next entry is the source path
            i += 1
    return paths


def untracked() -> list[str]:
    out = run("git", "ls-files", "-z", "--others", "--exclude-standard").stdout
    return [p for p in out.split("\0") if p and not p.startswith(ART_PREFIX)]


def kind(path: str) -> str:
    name = path.rsplit("/", 1)[-1]
    if path in NOISE:
        return "noise"
    if name in ("BUILD.bazel", "BUILD"):
        return "build"
    if any(m in "/" + path for m in TEST_MARKERS):
        return "test"
    return "prod"


def count_lines(path: str) -> int:
    try:
        return len(Path(path).read_text(errors="replace").splitlines())
    except OSError:
        return 0


# ---------------------------------------------------------------- snapshot / changes

def cmd_snapshot(args) -> int:
    head = git("rev-parse", "HEAD")
    if not head:
        return emit({"error": "not a git repository"}, 2)
    snap = {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "head": head,
        "branch": git("branch", "--show-current"),
        "dirty": {p: file_sig(p) for p in tracked_dirty()},
        "untracked": {p: file_sig(p) for p in untracked()},
    }
    Path(args.out).write_text(json.dumps(snap, indent=2) + "\n")
    return emit({"out": args.out, "head": head, "branch": snap["branch"],
                 "dirty": len(snap["dirty"]), "untracked": len(snap["untracked"])}, 0)


def added_lines(base: str, changes: list[dict]) -> dict[str, list[tuple[int, str]]]:
    """path -> [(line_no, text)] for every added line (tracked diff vs base, or a whole new untracked file)."""
    out: dict[str, list[tuple[int, str]]] = {}
    tracked = [c["path"] for c in changes if c["status"] != "untracked" and c["status"] != "deleted"]
    if tracked:
        diff = run("git", "diff", "-U0", "--no-renames", "--no-color", base, "--", *tracked).stdout
        cur, ln = None, 0
        for line in diff.splitlines():
            if line.startswith("+++ "):
                cur = line[6:] if line.startswith("+++ b/") else None
            elif line.startswith("@@"):
                m = re.search(r"\+(\d+)", line)
                ln = int(m.group(1)) if m else 0
            elif cur and line.startswith("+"):
                out.setdefault(cur, []).append((ln, line[1:]))
                ln += 1
    for c in changes:
        if c["status"] == "untracked":
            try:
                text = Path(c["path"]).read_text(errors="replace")
            except OSError:
                continue
            out[c["path"]] = list(enumerate(text.splitlines(), 1))
    return out


def removed_lines(base: str, path: str) -> list[str]:
    diff = run("git", "diff", "-U0", "--no-renames", "--no-color", base, "--", path).stdout
    return [ln[1:] for ln in diff.splitlines() if ln.startswith("-") and not ln.startswith("--- ")]


def compute_changes(bl: dict) -> list[dict]:
    base = bl["head"]
    changes: dict[str, dict] = {}
    status = run("git", "diff", "--name-status", "-z", "--no-renames", base).stdout.split("\0")
    for st, path in zip(status[0::2], status[1::2]):
        if path and not path.startswith(ART_PREFIX):
            changes[path] = {"path": path, "status": {"A": "added", "D": "deleted"}.get(st[:1], "modified"),
                             "added": 0, "deleted": 0}
    nums = run("git", "diff", "--numstat", "-z", "--no-renames", base).stdout.split("\0")
    for rec in nums:
        parts = rec.split("\t")
        if len(parts) == 3 and parts[2] in changes:
            a, d = parts[0], parts[1]
            changes[parts[2]]["added"] = int(a) if a.isdigit() else 0
            changes[parts[2]]["deleted"] = int(d) if d.isdigit() else 0
    # a file already dirty at snapshot time and unchanged since is not this run's work
    for path, sig in (bl.get("dirty") or {}).items():
        if path in changes and file_sig(path) == sig:
            del changes[path]
    for path in untracked():
        if (bl.get("untracked") or {}).get(path) == file_sig(path):
            continue
        changes[path] = {"path": path, "status": "untracked", "added": count_lines(path), "deleted": 0}
    for c in changes.values():
        c["kind"] = kind(c["path"])
    return sorted(changes.values(), key=lambda c: c["path"])


def fingerprint(changes: list[dict]) -> str:
    h = hashlib.sha256()
    for c in changes:
        if c["kind"] != "noise":
            h.update(f"{c['path']}\0{file_sig(c['path'], full=True)}\n".encode())
    return h.hexdigest()[:16]


def load_baseline(path: str) -> dict:
    try:
        return json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as e:
        sys.exit(f"fix_guard: cannot read baseline {path}: {e}")


def cmd_changes(args) -> int:
    bl = load_baseline(args.baseline)
    ch = compute_changes(bl)
    return emit({"base": bl["head"], "changes": ch, "fingerprint": fingerprint(ch)}, 0)


# ---------------------------------------------------------------- check

def plan_files(plan: str) -> tuple[list[str], int | None]:
    text = Path(plan).read_text() if plan and Path(plan).is_file() else ""
    files: list[str] = []
    m = re.search(r"^##\s*Files to Change\s*\n(.*?)(?=^##\s|\Z)", text, re.M | re.S)
    for line in (m.group(1).splitlines() if m else []):
        f = re.match(r"\s*[-*]\s*`([^`]+)`", line)
        if f:
            files.append(f.group(1).split(":", 1)[0].removeprefix("./"))
    est = re.search(r"LINES_NONTEST:\s*~?\s*(\d+)", text)
    return files, int(est.group(1)) if est else None


def evaluate(bl: dict, stage: str, plan: str | None) -> dict:
    base = bl["head"]
    ch = compute_changes(bl)
    res: dict = {"stage": stage, "base": base, "fingerprint": fingerprint(ch), "changes": ch,
                 "noise": [c["path"] for c in ch if c["kind"] == "noise"]}
    real = [c for c in ch if c["kind"] != "noise"]
    res["diffstat"] = [f"{c['path']}  +{c['added']} -{c['deleted']}  ({c['kind']}, {c['status']})" for c in real]
    if stage == "readonly":
        res["verdict"] = "HALT:UNRELATED_FILES" if real else "PASS"
        return res

    adds = added_lines(base, real)
    res["comment_hits"] = [{"path": p, "line": n, "text": t.strip()[:160]}
                           for p, lines in adds.items() for n, t in lines if COMMENT_RE.search(t)]
    prod = [c for c in real if c["kind"] == "prod"]
    res["out_of_scope"] = [c["path"] for c in real if any(fnmatch(c["path"], g) for g in OUT_OF_SCOPE)]
    res["generated"] = [c["path"] for c in real if any(fnmatch(c["path"], g) for g in GENERATED)]
    new_deps = {}
    for c in real:
        if c["kind"] == "build":
            removed = {x.strip() for x in removed_lines(base, c["path"])}
            deps = [t.strip() for _, t in adds.get(c["path"], []) if DEP_RE.match(t.strip()) and t.strip() not in removed]
            if deps:
                new_deps[c["path"]] = deps
    res["build_new_deps"] = new_deps

    if stage == "rca":
        res["prod_files"] = [c["path"] for c in prod]
        if res["prod_files"]:
            res["verdict"] = "HALT:UNRELATED_FILES"
        elif res["comment_hits"]:
            res["verdict"] = "STYLE"
        else:
            res["verdict"] = "PASS"
        return res

    planned, est = plan_files(plan or "")
    lines = sum(c["added"] + c["deleted"] for c in prod)
    limit = max(2 * est, est + 20) if est is not None else None
    res["planned_files"] = planned
    res["unplanned"] = [c["path"] for c in prod if c["path"] not in planned]
    res["unplanned_deps"] = {p: d for p, d in new_deps.items() if p not in planned}
    res["nontest_lines"] = {"changed": lines, "estimate": est, "limit": limit}
    if not real:
        res["verdict"] = "HALT:FIX_INCOMPLETE"
    elif res["out_of_scope"]:
        res["verdict"] = "HALT:OUT_OF_SCOPE"
    elif res["unplanned"] or res["unplanned_deps"] or (limit is not None and lines > limit):
        res["verdict"] = "HALT:PLAN_DRIFT"
    elif est is None and prod:
        res["verdict"] = "HALT:PLAN_DRIFT"
        res["detail"] = "plan.md has no LINES_NONTEST estimate to hold the diff to"
    elif res["comment_hits"]:
        res["verdict"] = "STYLE"
    else:
        res["verdict"] = "PASS"
    return res


def cmd_check(args) -> int:
    if args.stage == "fix" and not args.plan:
        return emit({"error": "--plan is required for --stage fix"}, 2)
    res = evaluate(load_baseline(args.baseline), args.stage, args.plan)
    return emit(res, 0 if res["verdict"] == "PASS" else 1)


# ---------------------------------------------------------------- repro

def parse_repro(rca: str) -> dict:
    text = Path(rca).read_text()
    m = re.search(r"^\s*REPRO_TEST:\s*\n((?:[ \t]+\S.*\n?)+)", text, re.M)
    fields = {}
    for line in (m.group(1).splitlines() if m else []):
        kv = re.match(r"\s*([A-Z_]+):\s*(.*)$", line)
        if kv:
            fields[kv.group(1)] = kv.group(2).strip()
    failure = fields.get("FAILURE", "")
    if len(failure) >= 2 and failure[0] == failure[-1] and failure[0] in "\"'`":
        failure = failure[1:-1]
    fields["FAILURE"] = failure
    return fields


def norm(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


def failure_found(failure: str, output: str) -> bool:
    out = norm(output)
    parts = [norm(p) for p in re.split(r"\.\.\.|…", failure) if len(norm(p)) >= 8]
    return bool(parts) and all(p in out for p in parts)


def cmd_repro(args) -> int:
    rt = parse_repro(args.rca)
    cmd, name, lang = rt.get("COMMAND", ""), rt.get("NAME", ""), rt.get("LANG", "").lower()
    base = {"expect": args.expect, "name": name, "command": cmd, "lang": lang}
    try:
        argv = shlex.split(cmd)
    except ValueError as e:
        return emit({**base, "result": "bad_command", "detail": str(e)}, 2)
    # the command is agent-written: only a plain `bazel test` invocation is run, never a shell line
    if len(argv) < 3 or argv[0] != "bazel" or argv[1] != "test" or any(t in cmd for t in (";", "&", "|", "`", "$(", ">", "<")):
        return emit({**base, "result": "bad_command", "detail": "COMMAND must be a single `bazel test ...` with no shell syntax"}, 2)
    if not name:
        return emit({**base, "result": "bad_command", "detail": "REPRO_TEST has no NAME"}, 2)
    argv += ["--nocache_test_results", "--test_output=all"]
    if lang == "go":
        argv.append("--test_arg=-test.v")
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=args.timeout,
                           cwd=git("rev-parse", "--show-toplevel") or None)
        rc, output = p.returncode, p.stdout + p.stderr
    except subprocess.TimeoutExpired as e:
        rc, output = -1, (e.stdout or b"").decode(errors="replace") + (e.stderr or b"").decode(errors="replace")
    if lang == "go":
        ran_pass = bool(re.search(rf"--- PASS: {re.escape(name)}(\s|$)", output))
        ran_fail = bool(re.search(rf"--- FAIL: {re.escape(name)}(\s|$)", output))
    else:
        ran_pass = bool(re.search(r"\b[1-9]\d* passed\b", output))
        ran_fail = bool(re.search(r"\b[1-9]\d* failed\b", output))
    found = failure_found(rt["FAILURE"], output)
    # bazel: 0 = tests passed, 3 = built but tests failed, 4 = no tests ran, else build/infra error
    if rc == 0:
        result = "green" if ran_pass else "no_tests"
    elif rc == 3:
        result = "red" if found else "red_unmatched"
    elif rc == 4:
        result = "no_tests"
    elif rc == -1:
        result = "timeout"
    else:
        result = "build_error"
    tail = "\n".join(output.splitlines()[-200:])
    Path(args.out).write_text(f"$ {shlex.join(argv)}\n# exit={rc} result={result}\n\n{tail}\n")
    return emit({**base, "result": result, "ok": result == args.expect, "exit": rc, "ran_pass": ran_pass,
                 "ran_fail": ran_fail, "failure_found": found, "out": args.out, "argv": argv},
                0 if result == args.expect else 1)


# ---------------------------------------------------------------- existing branches / PRs

def origin_repo() -> str:
    m = re.search(r"github\.com[:/]([^/]+/[^/.]+)", git("remote", "get-url", "origin"))
    return m.group(1) if m else ""


def cmd_existing(args) -> int:
    t = args.ticket
    pat = re.compile(rf"(^|/){re.escape(t)}(-|$)")
    local = [b for b in git("for-each-ref", "--format=%(refname:short)", "refs/heads/").splitlines() if pat.search(b)]
    remote = []
    for line in git("ls-remote", "--heads", "origin", f"refs/heads/*{t}*").splitlines():
        ref = line.split("\t")[-1].removeprefix("refs/heads/")
        if pat.search(ref):
            remote.append(ref)
    prs = []
    p = run("gh", "pr", "list", "--repo", origin_repo(), "--state", "open", "--search", f"{t} in:title",
            "--json", "number,url,headRefName,title,isDraft")
    if p.returncode == 0 and p.stdout.strip():
        prs = [x for x in json.loads(p.stdout)
               if x.get("title", "").startswith(f"{t}:") or pat.search(x.get("headRefName", ""))]
    out = {"ticket": t, "local": local, "remote": remote, "prs": prs, "gh_ok": p.returncode == 0,
           "any": bool(local or remote or prs)}
    if args.branch:
        taken = set(local) | set(remote)
        n = 2
        while f"{args.branch}-{n}" in taken:
            n += 1
        out["branch_taken"] = args.branch in taken
        out["suggested"] = f"{args.branch}-{n}" if args.branch in taken else args.branch
    return emit(out, 1 if out["any"] else 0)


# ---------------------------------------------------------------- ship

def cmd_ship(args) -> int:
    t = args.ticket
    if git("branch", "--show-current") != args.branch:
        return emit({"error": f"HEAD is not {args.branch}"}, 2)
    bl = load_baseline(args.baseline)
    res = evaluate(bl, "fix", args.plan)
    if res["verdict"] != "PASS":
        return emit({"error": "check failed; nothing committed", "verdict": res["verdict"], "check": res}, 1)
    if res["fingerprint"] != args.fingerprint:
        return emit({"error": "the diff changed since it was approved; re-run the checks and Gate 3",
                     "approved": args.fingerprint, "now": res["fingerprint"]}, 1)
    msg = Path(args.message).read_text().strip()
    title = msg.splitlines()[0] if msg else ""
    if not title.startswith(f"{t}: ") or len(title) > 72:
        return emit({"error": f"commit title must start with '{t}: ' and be ≤72 chars", "title": title}, 2)

    files = [c["path"] for c in res["changes"] if c["kind"] != "noise"]
    for f in files:
        p = run("git", "add", "--", f)
        if p.returncode != 0:
            return emit({"error": f"git add {f} failed", "stderr": p.stderr.strip()}, 3)
    committed = None
    if run("git", "diff", "--cached", "--quiet").returncode != 0:
        p = run("git", "commit", "-F", args.message)
        if p.returncode != 0:      # usually a pre-commit hook; the edits stay staged for the coder to fix
            return emit({"error": "git commit failed", "output": (p.stdout + p.stderr)[-4000:]}, 3)
        committed = git("rev-parse", "HEAD")
    subjects = git("log", "--format=%s", f"{bl['head']}..HEAD").splitlines()
    if not subjects or not all(s.startswith(f"{t}:") for s in subjects):
        return emit({"error": f"every commit since the branch point must start with '{t}:'", "subjects": subjects}, 1)

    p = run("git", "push", "-u", "origin", args.branch)
    if p.returncode != 0:
        return emit({"error": "git push failed (never forced)", "output": (p.stdout + p.stderr)[-4000:],
                     "committed": committed}, 3)

    fields = "url,number,state,isDraft,additions,deletions,changedFiles,commits,headRefOid"
    view = run("gh", "pr", "view", args.branch, "--json", fields)
    pr = json.loads(view.stdout) if view.returncode == 0 else None
    created = False
    if not pr or pr.get("state") != "OPEN":
        run("gh", "label", "create", "bhramastra", "--color", "6f42c1", "--description", "Raised by BhramASTRA")
        p = run("gh", "pr", "create", "--draft", "--label", "bhramastra", "--base", "master",
                "--head", args.branch, "--title", title, "--body-file", args.body)
        if p.returncode != 0:
            return emit({"error": "gh pr create failed", "output": (p.stdout + p.stderr)[-4000:],
                         "committed": committed, "pushed": True}, 3)
        created = True
        view = run("gh", "pr", "view", args.branch, "--json", fields)
        pr = json.loads(view.stdout) if view.returncode == 0 else {}
    return emit({"committed": committed, "pushed": True, "created": created,
                 "pr": {"url": pr.get("url"), "number": pr.get("number"), "branch": args.branch,
                        "isDraft": pr.get("isDraft"), "files": pr.get("changedFiles"),
                        "added": pr.get("additions"), "deleted": pr.get("deletions"),
                        "commits": len(pr.get("commits") or []), "head_sha": pr.get("headRefOid")},
                 "noise_left_uncommitted": res["noise"]}, 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("snapshot")
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_snapshot)

    p = sub.add_parser("changes")
    p.add_argument("--baseline", required=True)
    p.set_defaults(fn=cmd_changes)

    p = sub.add_parser("check")
    p.add_argument("--baseline", required=True)
    p.add_argument("--stage", required=True, choices=["readonly", "rca", "fix"])
    p.add_argument("--plan")
    p.set_defaults(fn=cmd_check)

    p = sub.add_parser("repro")
    p.add_argument("--rca", required=True)
    p.add_argument("--expect", required=True, choices=["red", "green"])
    p.add_argument("--out", required=True)
    p.add_argument("--timeout", type=int, default=1500)
    p.set_defaults(fn=cmd_repro)

    p = sub.add_parser("existing")
    p.add_argument("--ticket", required=True)
    p.add_argument("--branch")
    p.set_defaults(fn=cmd_existing)

    p = sub.add_parser("ship")
    for a in ("--baseline", "--ticket", "--branch", "--plan", "--fingerprint", "--message", "--body"):
        p.add_argument(a, required=True)
    p.set_defaults(fn=cmd_ship)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
