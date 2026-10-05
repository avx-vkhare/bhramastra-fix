#!/usr/bin/env python3
"""Curated lessons for /fix: human feedback -> reviewed lessons -> injected into future runs.

Store: $BHRAMASTRA_LESSONS or ~/.bhramastra/lessons.jsonl — one lesson per line,
rewritten on update. Only `approved` lessons are ever injected; a human approves each.

Subcommands:
  feedback   --run-id R [--phase run_end|review|post_merge|manual]
                                                        -> JSON bundle of NEW human signals (for fix-lessons);
                                                           PR review comments already distilled are skipped
  teach      --ticket T --text "..." [--stage S]        -> record your own feedback on T's latest run
  add        --stage S --trigger .. --lesson .. [--component C].. [--path P].. [--kind K] [--reason R]
                                                        -> write a lesson yourself (approved; no run needed)
  propose    --run-id R --phase P --file CANDIDATES.json [--feedback BUNDLE.json]
                                                        -> add proposed lessons / reinforce existing
  review-set --id L --decision approve|reject|retire [--edit JSON] [--run-id R]
  list       [--status S] [--kind lesson|doc_gap] [--json]
  select     --stage S [--component C]... [--path P]... [--limit N] [--json]
  verify     --artifact A --ids L-0001,L-0002           -> LESSONS_APPLIED covers every injected id
  stats      [--json]                                   -> per lesson: injected / applied / recurred

See ../references/lessons.md.
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import context_docs  # noqa: E402
import ledger  # noqa: E402

STAGES = ("intake", "rca", "plan", "fix", "any")
KINDS = ("lesson", "doc_gap")
STATUSES = ("proposed", "approved", "rejected", "retired")
DECISION_STATUS = {"approve": "approved", "reject": "rejected", "retire": "retired"}
GATE_OF_STAGE = {"rca": "gate1", "plan": "gate2"}
MIN_TEXT = 20
PHASES = ("run_end", "review", "post_merge", "manual")
PR_PHASES = {"review", "post_merge"}
RUN_END_ID_PREFIXES = ("gd-", "ov-", "sf-")
BOT_PREFIXES = ("app/", "github-actions", "copilot", "codecov", "dependabot", "renovate")
REPLY_MARKER = "<!-- bhramastra-reply -->"   # /fix review-handle replies; not reviewer feedback
APPLIED_RE = re.compile(r"LESSONS_APPLIED:\s*\n((?:[ \t]*[-*].*\n?)+)")


def store_path() -> Path:
    return Path(os.environ.get("BHRAMASTRA_LESSONS", Path.home() / ".bhramastra" / "lessons.jsonl"))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load() -> list[dict]:
    p = store_path()
    if not p.exists():
        return []
    lessons = []
    for n, line in enumerate(p.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            lessons.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"lessons: skipping malformed line {n} of {p}", file=sys.stderr)
    return lessons


def save(lessons: list[dict]) -> None:
    p = store_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text("".join(json.dumps(item, sort_keys=True) + "\n" for item in lessons))
    tmp.replace(p)


def next_id(lessons: list[dict]) -> str:
    n = max((int(item["id"].split("-")[1]) for item in lessons), default=0)
    return f"L-{n + 1:04d}"


def run_events(run_id: str) -> list[dict]:
    evs = ledger.group_runs(ledger.read_events()).get(run_id)
    if not evs:
        sys.exit(f"lessons: unknown run_id {run_id!r}")
    return sorted(evs, key=lambda e: e["ts"])


def gh_json(args: list[str], many: bool = False):
    """`many`: the caller wants a list (a `--jq '.[] | {...}'` stream may hold one object)."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    out = proc.stdout.strip()
    if not out:
        return []
    try:
        val = json.loads(out)
    except json.JSONDecodeError:   # --jq emits one JSON value per line
        return [json.loads(line) for line in out.splitlines() if line.strip()]
    return [val] if many and not isinstance(val, list) else val


def is_bot(login: str | None) -> bool:
    login = (login or "").lower()
    # gh shows app accounts without "[bot]" (e.g. codecov-for-aviatrix), hence prefixes
    return not login or login.endswith("[bot]") or login.startswith(BOT_PREFIXES)


def pr_feedback(pr: dict, consumed: set[str]) -> dict:
    """NEW review comments, reviews, PR comments and human follow-up commits for the run's PR.

    Reviewer comments are the most valuable signal; each carries a stable `id`
    so a later `learn` pass only sees comments that were not distilled yet.
    """
    m = ledger.PR_URL_RE.search(pr.get("url", ""))
    if not m:
        return {}
    repo, number = m.groups()
    out = {"url": pr["url"]}
    inline = gh_json(["api", f"repos/{repo}/pulls/{number}/comments", "--paginate", "--jq",
                      ".[] | {id: (\"rc-\" + (.id|tostring)), user: .user.login, path, line: (.line // .original_line), "
                      "in_reply_to: .in_reply_to_id, body, url: .html_url, "
                      "diff_hunk: (.diff_hunk | split(\"\\n\") | .[-6:] | join(\"\\n\"))}"], many=True) or []
    out["inline_comments"] = [c for c in inline if not is_bot(c.get("user")) and c["id"] not in consumed
                              and REPLY_MARKER not in (c.get("body") or "")]
    view = gh_json(["pr", "view", number, "--repo", repo, "--json", "reviews,comments,commits"]) or {}
    out["reviews"] = [{"id": f"rv-{r.get('id')}", "user": (r.get("author") or {}).get("login"),
                       "state": r.get("state"), "body": r.get("body")}
                      for r in view.get("reviews") or []
                      if r.get("body") and not is_bot((r.get("author") or {}).get("login"))
                      and REPLY_MARKER not in r["body"]
                      and f"rv-{r.get('id')}" not in consumed]
    out["comments"] = [{"id": f"ic-{c.get('id')}", "user": (c.get("author") or {}).get("login"), "body": c.get("body")}
                       for c in view.get("comments") or []
                       if not is_bot((c.get("author") or {}).get("login")) and f"ic-{c.get('id')}" not in consumed
                       and REPLY_MARKER not in (c.get("body") or "")]
    commits = view.get("commits") or []
    base = pr.get("commits")
    follow = commits[base:] if isinstance(base, int) else []
    follow = [c for c in follow if f"cm-{c.get('oid')}" not in consumed]
    out["follow_up_commits"] = [{"id": f"cm-{c.get('oid')}", "oid": c.get("oid"),
                                 "message": c.get("messageHeadline")} for c in follow]
    if follow and pr.get("head_sha"):
        cmp = gh_json(["api", f"repos/{repo}/compare/{pr['head_sha']}...{follow[-1]['oid']}",
                       "--jq", ".files[] | {filename, additions, deletions, patch}"], many=True) or []
        for f in cmp:
            f["patch"] = (f.get("patch") or "")[:3000]
        out["follow_up_diff"] = cmp
    return out


def cmd_feedback(args) -> int:
    evs = run_events(args.run_id)
    ticket = evs[0]["ticket"]
    cwd = (evs[0]["data"] or {}).get("cwd") or str(Path.cwd())
    distilled_evs = [e for e in evs if e["event"] == "lessons_distilled"]
    distilled = [e["data"] for e in distilled_evs]
    consumed = {i for d in distilled for i in d.get("consumed") or []}
    # commits pushed by /fix review-handle are the AI's own, not human follow-ups
    consumed |= {f"cm-{e['data']['commit']}" for e in evs if e["event"] == "review_round" and e["data"].get("commit")}
    # Distillations written before run_end signals had ids consumed them by time only.
    legacy_cut = max((e["ts"] for e in distilled_evs if (e["data"] or {}).get("phase") == "run_end"
                      and not any(i.startswith(RUN_END_ID_PREFIXES) for i in e["data"].get("consumed") or [])),
                     default="")
    signals = []
    # Every signal has an id and is distilled once. Run-end signals (gates, overrides,
    # skill failures) are offered at run_end — again on a later run_end if the run
    # resumed and produced new ones. Your own feedback and hand edits (hf-/me-) are
    # picked up by any pass; the /fix rate verdict (ar-) only at post_merge.
    for n, e in enumerate(evs):
        d = e.get("data") or {}
        sig = None
        if e["event"] in ("human_feedback", "manual_edit"):
            sig = {"id": f"{'hf' if e['event'] == 'human_feedback' else 'me'}-{e['ts']}", "source": e["event"],
                   "stage": d.get("stage") or e.get("stage")}
            sig.update({"quote": d.get("text")} if e["event"] == "human_feedback" else {"detail": d})
        elif e["event"] == "ai_rating":
            if args.phase == "post_merge":
                sig = {"id": f"ar-{e['ts']}", "source": "ai_rating", "rating": d.get("rating"), "quote": d.get("note")}
        elif args.phase != "run_end" or (legacy_cut and e["ts"] <= legacy_cut):
            continue
        elif e["event"] == "gate_decision":
            if d.get("decision") != "approve" or d.get("feedback") or d.get("rating") in ("ok", "poor"):
                sig = {"id": f"gd-{d.get('gate')}-{e['ts']}-{n}", "source": f"{d.get('gate')}_{d.get('decision')}",
                       "reason": d.get("reason"), "rating": d.get("rating"), "quote": d.get("feedback")}
        elif e["event"] == "override":
            sig = {"id": f"ov-{e['ts']}-{n}", "source": e["event"], "stage": e.get("stage"), "detail": d}
        elif e["event"] == "skill_used" and d.get("outcome") in ledger.SKILL_FAILED and d.get("source") == "reported":
            sig = {"id": f"sf-{e['ts']}-{n}", "source": "skill_failed", "skill": d.get("skill"), "quote": d.get("detail")}
        if sig and sig["id"] not in consumed:
            signals.append({**sig, "ts": e["ts"]})
    halted = [e["data"] for e in evs if e["event"] == "halted"]
    pr = [e["data"] for e in evs if e["event"] == "pr_opened"]
    bundle = {
        "run_id": args.run_id,
        "ticket": ticket,
        "phase": args.phase,
        "art": str(Path(cwd) / ".bhramastra" / ticket),
        "eligibility": next((e["data"] for e in reversed(evs) if e["event"] == "eligibility"), {}),
        "hint": (evs[0]["data"] or {}).get("hint", ""),
        "hint_check": next((e["data"].get("hint_check") for e in reversed(evs)
                            if e["event"] == "stage_finished" and e.get("stage") == "rca"), ""),
        "signals": signals,
        "halted": halted,
        "lessons_injected": [e["data"] for e in evs if e["event"] == "lessons_injected"],
        "already_distilled": [d.get("phase") for d in distilled],
    }
    if args.phase in PR_PHASES and pr:
        bundle["pr"] = pr_feedback(pr[-1], consumed)
    prb = bundle.get("pr", {})
    pr_signal = bool(prb.get("inline_comments") or prb.get("reviews") or prb.get("comments")
                     or prb.get("follow_up_commits"))
    bundle["signal_ids"] = sorted(
        [s["id"] for s in signals]
        + [x["id"] for k in ("inline_comments", "reviews", "comments", "follow_up_commits") for x in prb.get(k) or []])
    bundle["has_signal"] = bool(signals or pr_signal)
    print(json.dumps(bundle, indent=2))
    return 0


def validate(c: dict) -> list[str]:
    errs = []
    if c.get("stage") not in STAGES:
        errs.append(f"stage must be one of {STAGES}")
    if c.get("kind", "lesson") not in KINDS:
        errs.append(f"kind must be one of {KINDS}")
    for f in ("trigger", "lesson"):
        if len((c.get(f) or "").strip()) < MIN_TEXT:
            errs.append(f"{f} must be ≥{MIN_TEXT} chars")
    if not c.get("evidence"):
        errs.append("evidence[] required (quote + source)")
    return errs


def cmd_propose(args) -> int:
    evs = run_events(args.run_id)
    ticket = evs[0]["ticket"]
    cands = json.loads(Path(args.file).read_text())
    lessons = load()
    by_id = {item["id"]: item for item in lessons}
    created, reinforced, errors = [], [], []
    # validate the whole batch first: a partial write would be re-created by the retry
    for i, c in enumerate(cands):
        if c.get("action") == "reinforce":
            if c.get("id") not in by_id:
                errors.append(f"candidate {i}: reinforce of unknown id {c.get('id')!r}")
        elif errs := validate(c):
            errors.append(f"candidate {i}: " + "; ".join(errs))
    if errors:
        print(json.dumps({"proposed": [], "reinforced": [], "errors": errors,
                          "note": "nothing written; fix the candidates and re-run"}, indent=2))
        return 1
    for c in cands:
        evidence = [{**ev, "run_id": args.run_id, "ticket": ticket} for ev in c.get("evidence") or []]
        if c.get("action") == "reinforce":
            target = by_id[c["id"]]
            target.setdefault("evidence", []).extend(evidence)
            target["updated"] = now()
            reinforced.append(target["id"])
            continue
        lid = next_id(lessons)
        item = {
            "id": lid, "status": "proposed", "kind": c.get("kind", "lesson"), "stage": c["stage"],
            "components": c.get("components") or [], "paths": c.get("paths") or [],
            "reason": c.get("reason"), "trigger": c["trigger"].strip(), "lesson": c["lesson"].strip(),
            "evidence": evidence, "created": now(), "updated": now(),
        }
        lessons.append(item)
        by_id[lid] = item
        created.append(lid)
    save(lessons)
    consumed = json.loads(Path(args.feedback).read_text()).get("signal_ids", []) if args.feedback else []
    ledger.write_event(args.run_id, ticket, "lessons_distilled", None, "agent",
                       {"phase": args.phase, "proposed": created, "reinforced": reinforced, "errors": errors,
                        "consumed": consumed})
    print(json.dumps({"proposed": created, "reinforced": reinforced, "errors": []}, indent=2))
    return 0


def cmd_review_set(args) -> int:
    lessons = load()
    item = next((x for x in lessons if x["id"] == args.id), None)
    if not item:
        print(f"lessons: unknown id {args.id}", file=sys.stderr)
        return 2
    if args.edit:
        edit = json.loads(args.edit)
        for k in ("stage", "components", "paths", "reason", "trigger", "lesson", "kind"):
            if k in edit:
                item[k] = edit[k]
        errs = validate(item)
        if errs:
            print("lessons: edit invalid: " + "; ".join(errs), file=sys.stderr)
            return 2
    item["status"] = DECISION_STATUS[args.decision]
    item["updated"] = now()
    item.setdefault("history", []).append({"ts": now(), "decision": args.decision, "edited": bool(args.edit)})
    save(lessons)
    if args.run_id:
        evs = run_events(args.run_id)
        ledger.write_event(args.run_id, evs[0]["ticket"], "lesson_review", None, "human",
                           {"id": args.id, "decision": args.decision, "edited": bool(args.edit)})
    print(f"{args.id}: {item['status']}")
    return 0


def latest_run(ticket: str) -> str:
    runs = [(evs[0]["ts"], rid) for rid, evs in ledger.group_runs(ledger.read_events()).items()
            if evs[0]["ticket"] == ticket and any(e["event"] == "run_finished" and
                                                   (e["data"] or {}).get("outcome") not in ("ineligible", "checked")
                                                   for e in evs)]
    if not runs:
        sys.exit(f"lessons: no finished run for {ticket} (ineligible/check runs don't count)")
    return max(runs)[1]


def cmd_teach(args) -> int:
    """Your own feedback on a finished run; distilled by fix-lessons like a gate comment."""
    if len(args.text.strip()) < MIN_TEXT:
        sys.exit(f"lessons: feedback must be ≥{MIN_TEXT} chars")
    rid = args.run_id or latest_run(args.ticket)
    ledger.write_event(rid, args.ticket, "human_feedback", None, "human",
                       {"text": args.text.strip(), "stage": args.stage})
    print(rid)
    return 0


def cmd_add(args) -> int:
    """A lesson you write yourself — no distillation, approved on entry."""
    item = {"stage": args.stage, "kind": args.kind, "trigger": args.trigger, "lesson": args.lesson,
            "components": args.component, "paths": args.path, "reason": args.reason,
            "evidence": [{"source": "manual", "quote": args.note or "written by a human"}]}
    errs = validate(item)
    if errs:
        print("lessons: " + "; ".join(errs), file=sys.stderr)
        return 2
    lessons = load()
    lid = next_id(lessons)
    lessons.append({"id": lid, "status": "approved", **item, "trigger": args.trigger.strip(),
                    "lesson": args.lesson.strip(), "created": now(), "updated": now(),
                    "history": [{"ts": now(), "decision": "approve", "edited": False, "manual": True}]})
    save(lessons)
    print(f"{lid}: approved")
    return 0


def fmt(item: dict) -> str:
    scope = ",".join(item.get("components") or []) or "-"
    paths = ",".join(item.get("paths") or []) or "-"
    tag = " [doc gap]" if item.get("kind") == "doc_gap" else ""
    return (f"{item['id']} ({item['status']}, {item['stage']}, comp={scope}, paths={paths}, "
            f"evidence={len(item.get('evidence') or [])}){tag}\n"
            f"    WHEN  {item['trigger']}\n    DO    {item['lesson']}")


def cmd_list(args) -> int:
    rows = [x for x in load() if (not args.status or x["status"] == args.status)
            and (not args.kind or x.get("kind") == args.kind)]
    if args.json:
        print(json.dumps(rows, indent=2))
    else:
        print("\n".join(fmt(x) for x in rows) or "lessons: none")
    return 0


def path_hit(lesson_paths: list[str], seeds: list[str]) -> bool:
    for lp in lesson_paths:
        lp = lp.rstrip("/")
        for s in seeds:
            s = s.split(":", 1)[0].rstrip("/")
            if s.startswith(lp) or lp.startswith(s):
                return True
    return False


def select(root: Path, stage: str, components: list[str], paths: list[str], limit: int) -> list[dict]:
    # A lesson naming components needs a component match (paths only rank it higher):
    # directory prefixes like controller-conduit/ are shared by every feature. A lesson
    # naming only paths matches real suspect paths, or failing that the component seeds.
    comp_seeds = [s for c in components for s in context_docs.component_seeds(root, c)[0]]
    comps = {c.lower() for c in components}
    scored = []
    for item in load():
        if item["status"] != "approved" or item["stage"] not in (stage, "any"):
            continue
        lcomps = {c.lower() for c in item.get("components") or []}
        lpaths = item.get("paths") or []
        if lcomps:
            if not lcomps & comps:
                continue
            score = 3 if lpaths and path_hit(lpaths, paths) else 2
        elif lpaths:
            if path_hit(lpaths, paths):
                score = 3
            elif path_hit(lpaths, comp_seeds):
                score = 2
            else:
                continue
        else:
            score = 1
        scored.append((score, len(item.get("evidence") or []), item))
    scored.sort(key=lambda t: (-t[0], -t[1], t[2]["id"]))
    return [t[2] for t in scored[:limit]]


def cmd_select(args) -> int:
    root = Path(args.repo_root).resolve()
    picked = select(root, args.stage, args.component, args.path, args.limit)
    if args.json:
        print(json.dumps(picked, indent=2))
    else:
        print("\n".join(fmt(x) for x in picked) or "LESSONS: none")
    return 0


def parse_applied(text: str) -> dict[str, tuple[str, str]]:
    m = APPLIED_RE.search(text)
    out = {}
    for line in (m.group(1).splitlines() if m else []):
        item = re.match(r"\s*[-*]\s*`?(L-\d+)`?\s*[—–:-]+\s*`?(applied|not_applicable)`?\s*[—–:(-]*\s*(.*?)\)?\s*$", line)
        if item:
            out[item.group(1)] = (item.group(2), item.group(3).strip())
    return out


def cmd_verify(args) -> int:
    ids = [i for i in (args.ids or "").split(",") if i]
    p = Path(args.artifact)
    got = parse_applied(p.read_text() if p.is_file() else "")
    missing = [i for i in ids if i not in got or len(got[i][1]) < 10]
    res = {"missing": missing,
           "applied": [i for i in ids if got.get(i, ("",))[0] == "applied"],
           "not_applicable": [i for i in ids if got.get(i, ("",))[0] == "not_applicable"]}
    print(json.dumps(res))
    return 1 if missing else 0


def lesson_stats() -> dict[str, dict]:
    runs = ledger.group_runs(ledger.read_events())
    stats = {x["id"]: {"injected": 0, "applied": 0, "recurred": 0, "first_pass": 0, "item": x} for x in load()}
    for evs in runs.values():
        decisions: dict[str, list[dict]] = {}
        for e in sorted(evs, key=lambda e: e["ts"]):
            if e["event"] == "gate_decision":
                decisions.setdefault(e["data"].get("gate"), []).append(e["data"])
        seen: set[str] = set()
        for e in evs:
            if e["event"] != "lessons_injected":
                continue
            d = e["data"]
            gate = GATE_OF_STAGE.get(d.get("stage"))
            for lid in d.get("ids") or []:
                if lid not in stats or lid in seen:
                    continue
                seen.add(lid)
                s = stats[lid]
                s["injected"] += 1
                if lid not in (d.get("applied") or []):
                    continue
                s["applied"] += 1
                gd = decisions.get(gate) or []
                reason = s["item"].get("reason")
                if any(x.get("decision") in ("revise", "reject") and reason and x.get("reason") == reason for x in gd):
                    s["recurred"] += 1
                if gd and gd[0].get("decision") == "approve":
                    s["first_pass"] += 1
    return stats


def cmd_stats(args) -> int:
    stats = lesson_stats()
    rows = []
    for lid, s in stats.items():
        item = s.pop("item")
        s.update({"id": lid, "status": item["status"], "stage": item["stage"], "reason": item.get("reason") or ""})
        s["suggest_retire"] = s["applied"] >= 2 and s["recurred"] * 2 >= s["applied"]
        rows.append(s)
    if args.json:
        print(json.dumps(rows, indent=2))
        return 0
    if not rows:
        print("lessons: none")
        return 0
    cols = ["id", "status", "stage", "reason", "injected", "applied", "first_pass", "recurred", "suggest_retire"]
    w = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(w[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w[c]) for c in cols))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--repo-root", default=".")
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("feedback")
    p.add_argument("--run-id", required=True)
    p.add_argument("--phase", choices=PHASES, default="run_end")
    p.set_defaults(fn=cmd_feedback)

    p = sub.add_parser("teach")
    p.add_argument("--ticket", required=True)
    p.add_argument("--run-id", help="default: the ticket's latest finished run")
    p.add_argument("--text", required=True)
    p.add_argument("--stage", choices=STAGES, help="where you think it should have been caught (optional)")
    p.set_defaults(fn=cmd_teach)

    p = sub.add_parser("add")
    p.add_argument("--stage", required=True, choices=STAGES)
    p.add_argument("--trigger", required=True)
    p.add_argument("--lesson", required=True)
    p.add_argument("--component", action="append", default=[])
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--kind", choices=KINDS, default="lesson")
    p.add_argument("--reason")
    p.add_argument("--note", help="why / where this came from (stored as evidence)")
    p.set_defaults(fn=cmd_add)

    p = sub.add_parser("propose")
    p.add_argument("--run-id", required=True)
    p.add_argument("--phase", choices=PHASES, required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--feedback", help="the bundle distilled; its signal_ids are marked consumed")
    p.set_defaults(fn=cmd_propose)

    p = sub.add_parser("review-set")
    p.add_argument("--id", required=True)
    p.add_argument("--decision", required=True, choices=sorted(DECISION_STATUS))
    p.add_argument("--edit", help="JSON with fields to overwrite (trigger, lesson, stage, components, paths, reason, kind)")
    p.add_argument("--run-id")
    p.set_defaults(fn=cmd_review_set)

    p = sub.add_parser("list")
    p.add_argument("--status", choices=STATUSES)
    p.add_argument("--kind", choices=KINDS)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_list)

    p = sub.add_parser("select")
    p.add_argument("--stage", required=True, choices=[s for s in STAGES if s != "any"])
    p.add_argument("--component", action="append", default=[])
    p.add_argument("--path", action="append", default=[])
    p.add_argument("--limit", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_select)

    p = sub.add_parser("verify")
    p.add_argument("--artifact", required=True)
    p.add_argument("--ids", default="")
    p.set_defaults(fn=cmd_verify)

    p = sub.add_parser("stats")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_stats)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
