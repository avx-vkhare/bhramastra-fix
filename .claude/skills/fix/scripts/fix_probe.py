#!/usr/bin/env python3
"""Where did an interrupted rca/fix stage leave the branch? Deterministic, read-only.

  fix_probe.py --ticket T --branch B [--base origin/master] [--since ISO_TS] [--stage rca|fix]

Prints JSON and a `case` the orchestrator acts on (SKILL.md "Interrupted agents and resume"):

  pr_open       PR exists, its head == HEAD, tree clean        -> orchestrator checks only (no agent)
  pushed_no_pr  branch pushed, remote == HEAD, no PR, clean    -> fix agent: open the PR only
  committed     commits ahead of base, not (fully) pushed      -> fix agent: verify, test, push, PR
  dirty         uncommitted edits (maybe also commits / a PR)  -> agent: review hunks vs plan, finish
  clean         nothing done yet                               -> run the stage normally
  wrong_branch  HEAD is not B                                  -> ask the user; never switch a dirty tree

`--since` (the stage's stage_started ts) keeps untracked files older than the
stage out of `untracked` — the repo root often has unrelated untracked files.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

TEST_MARKERS = ("_test.go", "/test_", "_test.py", "/tests/", "conftest.py")


def git(*args: str) -> str:
    p = subprocess.run(["git", *args], capture_output=True, text=True)
    # rstrip only: porcelain lines start with a meaningful space (" M path")
    return p.stdout.rstrip("\n") if p.returncode == 0 else ""


def is_test(path: str) -> bool:
    return any(m in "/" + path for m in TEST_MARKERS) or path.endswith("BUILD.bazel")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ticket", required=True)
    ap.add_argument("--branch", required=True)
    ap.add_argument("--base", default="origin/master")
    ap.add_argument("--since", help="ISO timestamp; untracked files modified before it are ignored")
    ap.add_argument("--stage", choices=["rca", "fix"], default="fix")
    args = ap.parse_args()

    root = Path(git("rev-parse", "--show-toplevel") or ".")
    head_branch = git("branch", "--show-current")
    head = git("rev-parse", "HEAD")
    out: dict = {"ticket": args.ticket, "branch": args.branch, "current_branch": head_branch, "head": head}

    modified = [line[3:].split(" -> ")[-1]
                for line in git("status", "--porcelain", "--untracked-files=no").splitlines() if line]
    since = datetime.fromisoformat(args.since.replace("Z", "+00:00")).timestamp() if args.since else None
    untracked = []
    for f in git("ls-files", "--others", "--exclude-standard").splitlines():
        if f.startswith(".bhramastra/"):
            continue
        try:
            if since is None or (root / f).stat().st_mtime >= since:
                untracked.append(f)
        except OSError:
            pass
    out["modified"] = modified
    out["untracked"] = untracked
    out["nontest_dirty"] = [f for f in modified + untracked if not is_test(f)]

    commits = [dict(zip(("sha", "subject"), line.split(" ", 1)))
               for line in git("log", "--format=%H %s", f"{args.base}..HEAD").splitlines() if line]
    out["commits"] = commits
    out["commits_prefixed"] = all(c["subject"].startswith(f"{args.ticket}:") for c in commits)
    remote = git("ls-remote", "--heads", "origin", args.branch).split("\t")[0] or None
    out["remote_sha"] = remote
    out["pushed"] = bool(remote) and remote == head

    pr = None
    p = subprocess.run(["gh", "pr", "view", args.branch, "--json", "number,url,isDraft,state,headRefOid"],
                       capture_output=True, text=True)
    if p.returncode == 0:
        pr = json.loads(p.stdout)
        if pr.get("state") != "OPEN":
            pr = None if pr.get("state") == "CLOSED" else pr
    out["pr"] = pr

    art = root / ".bhramastra" / args.ticket
    out["artifact"] = {"rca.md": (art / "rca.md").exists(), "fix.md": (art / "fix.md").exists()}

    dirty = bool(modified or untracked)
    if head_branch != args.branch:
        case = "wrong_branch"
    elif dirty:
        case = "dirty"
    elif args.stage == "rca":
        case = "clean"
    elif pr and pr.get("headRefOid") == head:
        case = "pr_open"
    elif commits and out["pushed"]:
        case = "pushed_no_pr"
    elif commits:
        case = "committed"
    else:
        case = "clean"
    out["case"] = case
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
