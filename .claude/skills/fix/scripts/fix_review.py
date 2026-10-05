#!/usr/bin/env python3
"""Deterministic parts of `/fix review-handle`: find the PR, sync its branch, fetch the
open review items, build the round's plan, post the replies.

  fix_review.py locate  --ticket T                       PR + branch + run_id from the ledger
  fix_review.py sync    --branch B                       clean tree? switch + fast-forward to origin
  fix_review.py items   --ticket T --out F               open threads / review bodies / PR comments
  fix_review.py plan    --approved F --out review-plan.md
  fix_review.py reply   --ticket T --replies F --sha S --posted F

Our replies carry REPLY_MARKER so later passes (and Learn) can tell them from a
reviewer's words. Items already answered in an earlier round are skipped unless
the reviewer wrote again after our reply.

Exit codes: 0 ok · 1 refused (JSON says why) · 2 usage / environment error.
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger  # noqa: E402

REPLY_MARKER = "<!-- bhramastra-reply -->"
NOISE = {"MODULE.bazel.lock"}   # rewritten by bazel on this box; the user's rule is to revert it
# gh shows app accounts without "[bot]" (e.g. codecov-for-aviatrix), hence prefixes
BOT_PREFIXES = ("app/", "github-actions", "copilot", "codecov", "dependabot", "renovate")
THREADS_QUERY = """
query($owner: String!, $name: String!, $number: Int!, $cursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $number) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id isResolved isOutdated path line originalLine
          comments(first: 50) {
            nodes { databaseId body url createdAt diffHunk author { login __typename } }
          }
        }
      }
    }
  }
}"""


def run(*cmd: str) -> subprocess.CompletedProcess:
    return subprocess.run(list(cmd), capture_output=True, text=True)


def git(*args: str) -> str:
    p = run("git", *args)
    return p.stdout.rstrip("\n") if p.returncode == 0 else ""


def emit(obj: dict, code: int = 0) -> int:
    print(json.dumps(obj, indent=2))
    return code


def is_bot(login: str | None, typename: str | None = None) -> bool:
    login = (login or "").lower()
    return not login or typename == "Bot" or login.endswith("[bot]") or login.startswith(BOT_PREFIXES)


def ours(body: str | None) -> bool:
    return REPLY_MARKER in (body or "")


def pr_of(ticket: str) -> dict:
    """The last PR /fix opened for the ticket, with its run_id and repo."""
    runs = ledger.group_runs(ledger.read_events())
    best = None
    for rid, evs in runs.items():
        if evs[0]["ticket"] != ticket:
            continue
        for e in evs:
            if e["event"] == "pr_opened" and (best is None or e["ts"] > best[0]):
                best = (e["ts"], rid, e["data"])
    if not best:
        return {}
    m = ledger.PR_URL_RE.search(best[2].get("url", ""))
    return {"run_id": best[1], "url": best[2].get("url"), "number": best[2].get("number"),
            "branch": best[2].get("branch"), "repo": m.group(1) if m else ""}


def handled_ids(ticket: str) -> dict[str, str]:
    """item id -> ts of our last reply to it (from review_reply events)."""
    out: dict[str, str] = {}
    for e in ledger.read_events():
        if e["ticket"] == ticket and e["event"] == "review_reply" and e["data"].get("id"):
            out[e["data"]["id"]] = max(out.get(e["data"]["id"], ""), e["ts"])
    return out


# ---------------------------------------------------------------- locate / sync

def cmd_locate(args) -> int:
    pr = pr_of(args.ticket)
    if not pr:
        return emit({"error": f"no PR opened by /fix for {args.ticket} in the ledger"}, 1)
    view = run("gh", "pr", "view", str(pr["number"]), "--repo", pr["repo"],
               "--json", "state,isDraft,headRefName,headRefOid,url")
    if view.returncode != 0:
        return emit({**pr, "error": f"gh pr view failed: {view.stderr.strip()}"}, 2)
    v = json.loads(view.stdout)
    rounds = sum(1 for e in ledger.group_runs(ledger.read_events()).get(pr["run_id"], [])
                 if e["event"] == "review_round" and e["data"].get("shipped"))
    out = {**pr, "branch": v.get("headRefName") or pr["branch"], "state": v.get("state"),
           "isDraft": v.get("isDraft"), "head_sha": v.get("headRefOid"), "rounds_shipped": rounds}
    if v.get("state") != "OPEN":
        return emit({**out, "error": f"PR is {v.get('state')}, not OPEN"}, 1)
    return emit(out)


def cmd_sync(args) -> int:
    b = args.branch
    dirty = [line[3:] for line in git("status", "--porcelain=v1", "--untracked-files=no").splitlines() if line]
    restored = [f for f in dirty if f in NOISE]
    for f in restored:
        run("git", "restore", "--", f)
    dirty = [f for f in dirty if f not in NOISE]
    if dirty:
        return emit({"error": "tracked files have uncommitted edits; commit or stash them first",
                     "files": dirty}, 1)
    if run("git", "fetch", "origin", b).returncode != 0:
        return emit({"error": f"git fetch origin {b} failed"}, 2)
    before = git("branch", "--show-current")
    if before != b:
        exists = run("git", "rev-parse", "--verify", "--quiet", f"refs/heads/{b}").returncode == 0
        p = run("git", "switch", b) if exists else run("git", "switch", "-c", b, "--track", f"origin/{b}")
        if p.returncode != 0:
            return emit({"error": f"git switch {b} failed", "stderr": p.stderr.strip()}, 1)
    p = run("git", "merge", "--ff-only", f"origin/{b}")
    if p.returncode != 0:
        # local commits that were never pushed: refuse rather than reset them away
        return emit({"error": f"{b} has diverged from origin/{b}; cannot fast-forward",
                     "ahead": git("rev-list", "--count", f"origin/{b}..{b}"),
                     "behind": git("rev-list", "--count", f"{b}..origin/{b}")}, 1)
    return emit({"branch": b, "switched_from": before if before != b else None,
                 "head": git("rev-parse", "HEAD"), "restored_noise": restored})


# ---------------------------------------------------------------- items

def fetch_threads(repo: str, number: int) -> list[dict]:
    owner, name = repo.split("/", 1)
    nodes, cursor = [], None
    while True:
        cmd = ["gh", "api", "graphql", "-f", f"query={THREADS_QUERY}", "-F", f"owner={owner}",
               "-F", f"name={name}", "-F", f"number={number}"]
        if cursor:
            cmd += ["-F", f"cursor={cursor}"]
        p = run(*cmd)
        if p.returncode != 0:
            sys.exit(f"fix_review: GraphQL reviewThreads failed: {p.stderr.strip()}")
        page = json.loads(p.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]
        nodes += page["nodes"]
        if not page["pageInfo"]["hasNextPage"]:
            return nodes
        cursor = page["pageInfo"]["endCursor"]


def cmd_items(args) -> int:
    pr = pr_of(args.ticket)
    if not pr:
        return emit({"error": f"no PR opened by /fix for {args.ticket}"}, 1)
    done = handled_ids(args.ticket)
    items = []
    for t in fetch_threads(pr["repo"], int(pr["number"])):
        cs = [c for c in t["comments"]["nodes"] if not is_bot((c.get("author") or {}).get("login"),
                                                                (c.get("author") or {}).get("__typename"))]
        if t["isResolved"] or t["isOutdated"] or not cs:
            continue
        tid = f"th-{t['id']}"
        last = cs[-1]
        if ours(last.get("body")):
            continue                       # we answered last; waiting for the reviewer
        if tid in done and last.get("createdAt", "") <= done[tid]:
            continue
        items.append({"id": tid, "kind": "thread", "path": t.get("path"), "line": t.get("line") or t.get("originalLine"),
                      # the replies endpoint only accepts the thread's first comment, even a bot's
                      "reply_to": t["comments"]["nodes"][0]["databaseId"], "diff_hunk": "\n".join((cs[0].get("diffHunk") or "").splitlines()[-8:]),
                      "comments": [{"author": (c.get("author") or {}).get("login"), "body": c.get("body"),
                                    "url": c.get("url"), "ours": ours(c.get("body"))} for c in cs]})
    view = run("gh", "pr", "view", str(pr["number"]), "--repo", pr["repo"], "--json", "reviews,comments")
    v = json.loads(view.stdout) if view.returncode == 0 else {}
    for kind, prefix, rows in (("review", "rv", v.get("reviews") or []), ("comment", "ic", v.get("comments") or [])):
        for r in rows:
            login = (r.get("author") or {}).get("login")
            iid = f"{prefix}-{r.get('id')}"
            if not r.get("body") or is_bot(login) or ours(r.get("body")) or iid in done:
                continue
            items.append({"id": iid, "kind": kind, "state": r.get("state"), "url": r.get("url"),
                          "comments": [{"author": login, "body": r.get("body"), "url": r.get("url"), "ours": False}]})
    out = {"ticket": args.ticket, "pr": pr, "items": items}
    Path(args.out).write_text(json.dumps(out, indent=2) + "\n")
    return emit({"out": args.out, "items": len(items),
                 "by_kind": {k: sum(1 for i in items if i["kind"] == k) for k in ("thread", "review", "comment")}})


# ---------------------------------------------------------------- plan / reply

def cmd_plan(args) -> int:
    approved = json.loads(Path(args.approved).read_text())
    changes = [a for a in approved if a.get("decision") == "approve" and a.get("kind") == "change"]
    files = sorted({f for a in changes for f in a.get("files") or []})
    lines = sum(int(a.get("lines") or 0) for a in changes)
    body = ["# Review round plan (generated from approved triage)", "", "## Files to Change"]
    body += [f"- `{f}` — review request" for f in files] or ["- (none — replies only)"]
    body += ["", "## Items"] + [f"- {a['id']}: {a.get('ask', '')}" for a in changes]
    body += ["", "BLAST_RADIUS:", "  GATE: plan", f"  LINES_NONTEST: ~{lines}/150", ""]
    Path(args.out).write_text("\n".join(body))
    return emit({"out": args.out, "files": files, "lines_estimate": lines, "change_items": len(changes)})


def cmd_reply(args) -> int:
    pr = pr_of(args.ticket)
    items = {i["id"]: i for i in json.loads(Path(args.items).read_text())["items"]}
    replies = json.loads(Path(args.replies).read_text())
    posted_path = Path(args.posted)
    posted = json.loads(posted_path.read_text()) if posted_path.exists() else []
    done = {p["id"] for p in posted}
    errors = []
    for r in replies:
        if r["id"] in done:
            continue
        item = items.get(r["id"])
        if not item:
            errors.append(f"{r['id']}: not in items.json")
            continue
        text = r["body"].replace("{sha}", (args.sha or "")[:10]).strip()
        if "{sha}" in r["body"] and not args.sha:
            errors.append(f"{r['id']}: reply needs a commit sha")
            continue
        body = f"{text}\n\n{REPLY_MARKER}"
        if item["kind"] == "thread":
            p = run("gh", "api", f"repos/{pr['repo']}/pulls/{pr['number']}/comments/{item['reply_to']}/replies",
                    "-f", f"body={body}", "--jq", ".html_url")
        else:
            first = (item["comments"][0]["body"] or "").strip().splitlines()[0][:120]
            p = run("gh", "pr", "comment", str(pr["number"]), "--repo", pr["repo"],
                    "--body", f"> {first}\n\n{body}")
        if p.returncode != 0:
            errors.append(f"{r['id']}: {p.stderr.strip()[:300]}")
            continue
        posted.append({"id": r["id"], "kind": item["kind"], "url": p.stdout.strip()})
        posted_path.write_text(json.dumps(posted, indent=2) + "\n")   # after each post, so a retry skips it
    return emit({"posted": posted, "errors": errors}, 1 if errors else 0)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("locate")
    p.add_argument("--ticket", required=True)
    p.set_defaults(fn=cmd_locate)
    p = sub.add_parser("sync")
    p.add_argument("--branch", required=True)
    p.set_defaults(fn=cmd_sync)
    p = sub.add_parser("items")
    p.add_argument("--ticket", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_items)
    p = sub.add_parser("plan")
    p.add_argument("--approved", required=True)
    p.add_argument("--out", required=True)
    p.set_defaults(fn=cmd_plan)
    p = sub.add_parser("reply")
    p.add_argument("--ticket", required=True)
    p.add_argument("--items", required=True)
    p.add_argument("--replies", required=True)
    p.add_argument("--sha")
    p.add_argument("--posted", required=True)
    p.set_defaults(fn=cmd_reply)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
