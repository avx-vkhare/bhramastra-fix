#!/usr/bin/env python3
"""Append-only run ledger for the /fix pipeline.

Ledger file: $BHRAMASTRA_LEDGER or ~/.bhramastra/ledger.jsonl.
One JSON object per line: {ts, run_id, ticket, event, stage, actor, data}.

Subcommands:
  start   --ticket T [--hint H]           -> appends run_started, prints run_id
  append  --run-id R --event E [--stage S] [--actor agent|human]
          [--data JSON | --data-file PATH]
  harvest --run-id R [--transcript PATH]  -> tokens / model / effort / tool errors /
                                             skill calls from Claude Code transcripts
  rate    --ticket T --rating good|ok|poor [--note N]   -> human verdict on the run
  report  [--ticket T] [--csv]            -> one row per run
  analytics [--json]                      -> aggregates across all runs
  show    <run_id|ticket>                 -> raw events
  refresh-prs [--ticket T]                -> poll gh, append pr_state on change

See ../references/ledger.md for the event vocabulary.
"""

import argparse
import csv
import json
import os
import re
import statistics
import subprocess
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

EVENTS = {
    "run_started", "eligibility", "override", "logs_downloaded",
    "stage_started", "stage_finished", "gate_opened", "gate_decision",
    "repro_test", "tests_run", "halted", "pr_opened", "pr_state",
    "manual_edit", "run_finished", "usage", "skill_used", "ai_rating",
    "lessons_injected", "lessons_distilled", "lesson_review", "human_feedback",
    "review_round", "review_reply",
}
INTERVENTION_DECISIONS = {"revise", "reject"}
TERMINAL_PR_STATES = {"MERGED", "CLOSED"}
RATINGS = ("good", "ok", "poor")
SKILL_FAILED = {"error", "failed", "partial"}
PR_URL_RE = re.compile(r"github\.com/([^/]+/[^/]+)/pull/(\d+)")

PROJECTS = Path.home() / ".claude" / "projects"
STAGE_BY_AGENT = {"fix-intake": "intake", "fix-rca": "rca", "fix-planner": "plan", "fix-coder": "fix",
                  "fix-responder": "review", "fix-lessons": "learn"}
TOKEN_FIELDS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
AGENT_TOOLS = {"Agent", "Task"}


def ledger_path() -> Path:
    return Path(os.environ.get("BHRAMASTRA_LEDGER", Path.home() / ".bhramastra" / "ledger.jsonl"))


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_ts(ts: str) -> datetime:
    ts = re.sub(r"\.\d+", "", ts)
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def write_event(run_id: str, ticket: str, event: str, stage: str | None, actor: str, data: dict) -> dict:
    rec = {"ts": now(), "run_id": run_id, "ticket": ticket, "event": event,
           "stage": stage, "actor": actor, "data": data}
    path = ledger_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(rec, sort_keys=True) + "\n")
    return rec


def read_events() -> list[dict]:
    path = ledger_path()
    if not path.exists():
        return []
    events = []
    for n, line in enumerate(path.read_text().splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            print(f"ledger: skipping malformed line {n}", file=sys.stderr)
    return events


def group_runs(events: list[dict]) -> dict[str, list[dict]]:
    runs: dict[str, list[dict]] = {}
    for e in events:
        runs.setdefault(e["run_id"], []).append(e)
    return runs


def latest_run(runs: dict[str, list[dict]], ticket: str) -> str | None:
    mine = [(evs[0]["ts"], rid) for rid, evs in runs.items() if evs[0]["ticket"] == ticket]
    return max(mine)[1] if mine else None


def fmt_duration(seconds: float | None) -> str:
    if seconds is None or seconds < 0:
        return ""
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, _ = divmod(rem, 60)
    return f"{h}h{m:02d}m" if h else f"{m}m"


def tokens_total(u: dict) -> int:
    return sum(int(u.get(k) or 0) for k in TOKEN_FIELDS)


def find_rework(ticket: str) -> str:
    for base in (Path.cwd(), Path.home() / "audit-ai"):
        f = base / f"metrics-{ticket}.json"
        if not f.exists():
            continue
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            return ""
        tr = d.get("total_rework") or d.get("data", {}).get("metrics", {}).get("total_rework") or {}
        ratio = tr.get("ratio")
        return f"{ratio:.2f}" if isinstance(ratio, (int, float)) else ""
    return ""


# ---------------------------------------------------------------- transcripts

def iter_jsonl(path: Path):
    with path.open() as f:
        for line in f:
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def find_transcripts(run_id: str, limit: int = 200) -> list[Path]:
    """Session transcripts mentioning run_id (several if the run was resumed in a new session)."""
    cands = sorted(PROJECTS.glob("*/*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)[:limit]
    hits = []
    for p in cands:
        with p.open() as f:
            if any(run_id in line for line in f):
                hits.append(p)
    return hits


def scan_transcript(path: Path, since: str | None = None, until: str | None = None) -> dict:
    """Token usage, models, effort, tool calls/errors, skill and agent calls in one transcript."""
    usage: dict[str, dict] = {}
    models, efforts = Counter(), Counter()
    tools, tool_errors = Counter(), Counter()
    uses: dict[str, tuple[str, dict]] = {}
    skills, agents = [], []
    first = last = None
    for d in iter_jsonl(path):
        ts = d.get("timestamp")
        if ts and ((since and parse_ts(ts) < parse_ts(since)) or (until and parse_ts(ts) > parse_ts(until))):
            continue
        m = d.get("message")
        if not isinstance(m, dict):
            continue
        if ts:
            first, last = first or ts, ts
        content = m.get("content") if isinstance(m.get("content"), list) else []
        if d.get("type") == "assistant":
            rid = d.get("requestId") or d.get("uuid")
            if rid not in usage:
                models[m.get("model") or "?"] += 1
                efforts[str(d.get("effort") or "?")] += 1
            cur = usage.setdefault(rid, {})
            for k in TOKEN_FIELDS:
                cur[k] = max(cur.get(k, 0), int((m.get("usage") or {}).get(k) or 0))
            for b in content:
                if b.get("type") != "tool_use":
                    continue
                name, inp = b.get("name", "?"), b.get("input") or {}
                tools[name] += 1
                uses[b.get("id")] = (name, inp)
                if name in AGENT_TOOLS:
                    agents.append((b.get("id"), inp.get("subagent_type") or "general-purpose"))
        else:
            for b in content:
                if b.get("type") != "tool_result" or b.get("tool_use_id") not in uses:
                    continue
                name, inp = uses[b["tool_use_id"]]
                tur = d.get("toolUseResult") if isinstance(d.get("toolUseResult"), dict) else {}
                err = bool(b.get("is_error")) or tur.get("success") is False
                if err:
                    tool_errors[name] += 1
                if name == "Skill":
                    text = b.get("content") if isinstance(b.get("content"), str) else ""
                    skills.append({"tool_use_id": b["tool_use_id"], "skill": inp.get("skill", "?"),
                                   "outcome": "error" if err else "ok",
                                   "error": text[:300] if err else ""})
    totals = {k: sum(u[k] for u in usage.values()) for k in TOKEN_FIELDS}
    return {**totals, "requests": len(usage), "models": dict(models), "efforts": dict(efforts),
            "tool_calls": dict(tools), "tool_errors": dict(tool_errors), "skills": skills,
            "agents": agents, "first_ts": first, "last_ts": last}


def subagent_files(transcript: Path) -> dict[str, Path]:
    """toolUseId of the spawning Agent call -> subagent transcript."""
    out = {}
    for meta in (transcript.with_suffix("") / "subagents").glob("*.meta.json"):
        try:
            tuid = json.loads(meta.read_text()).get("toolUseId")
        except json.JSONDecodeError:
            continue
        f = meta.with_name(meta.name.replace(".meta.json", ".jsonl"))
        if tuid and f.exists():
            out[tuid] = f
    return out


def usage_data(s: dict) -> dict:
    keep = TOKEN_FIELDS + ("requests", "models", "efforts", "tool_calls", "tool_errors")
    d = {k: s[k] for k in keep}
    d["total_tokens"] = tokens_total(s)
    if s["first_ts"] and s["last_ts"]:
        d["duration_s"] = int((parse_ts(s["last_ts"]) - parse_ts(s["first_ts"])).total_seconds())
    return d


def cmd_harvest(args) -> int:
    runs = group_runs(read_events())
    if args.run_id not in runs:
        print(f"ledger: unknown run_id {args.run_id!r}", file=sys.stderr)
        return 2
    evs = runs[args.run_id]
    ticket, since = evs[0]["ticket"], evs[0]["ts"]
    # After run_finished only Learn runs (fix-lessons, then propose/review-set write
    # their events), so the window ends at the run's last event other than harvest's own.
    finished = [e["ts"] for e in evs if e["event"] == "run_finished"]
    until = max(e["ts"] for e in evs if e["event"] not in ("usage", "skill_used")) if finished else None
    have_agents = {e["data"].get("agent_id") for e in evs if e["event"] == "usage"}
    have_skills = {e["data"].get("tool_use_id") for e in evs if e["event"] == "skill_used"}
    last_orch = {e["data"].get("session"): e["data"] for e in evs
                 if e["event"] == "usage" and e["data"].get("scope") == "orchestrator"}

    transcripts = [Path(args.transcript)] if args.transcript else find_transcripts(args.run_id)
    if not transcripts:
        print(f"ledger: no transcript under {PROJECTS} mentions {args.run_id}", file=sys.stderr)
        return 1

    def record_skills(skills, invoked_by, stage):
        for sk in skills:
            if sk["tool_use_id"] in have_skills:
                continue
            have_skills.add(sk["tool_use_id"])
            write_event(args.run_id, ticket, "skill_used", stage, "agent",
                        {**sk, "source": "transcript", "invoked_by": invoked_by})

    added = 0
    for t in transcripts:
        main = scan_transcript(t, since, until)
        subs = subagent_files(t)
        for tuid, atype in main["agents"]:
            f = subs.get(tuid)
            aid = f.stem.removeprefix("agent-") if f else None
            if not f or aid in have_agents:
                continue
            s = scan_transcript(f)
            stage = STAGE_BY_AGENT.get(atype, "other")
            write_event(args.run_id, ticket, "usage", stage, "agent",
                        {"scope": "agent", "agent_id": aid, "agent_type": atype,
                         "transcript": str(f), **usage_data(s)})
            record_skills(s["skills"], atype, stage)
            have_agents.add(aid)
            added += 1
        orch = {"scope": "orchestrator", "session": t.stem, "transcript": str(t), **usage_data(main)}
        if last_orch.get(t.stem) != orch:
            write_event(args.run_id, ticket, "usage", "orchestrator", "agent", orch)
        record_skills(main["skills"], "orchestrator", None)
    print(f"ledger: harvested {len(transcripts)} transcript(s), {added} new agent(s)")
    return 0


# ---------------------------------------------------------------- summaries

def summarize(run_id: str, evs: list[dict]) -> dict:
    evs = sorted(evs, key=lambda e: e["ts"])
    first, last = evs[0], evs[-1]
    by = lambda name: [e for e in evs if e["event"] == name]

    elig = by("eligibility")
    verdict = elig[-1]["data"].get("verdict", "") if elig else ""
    failed = []
    if elig:
        for name, r in (elig[-1]["data"].get("criteria") or {}).items():
            if r.get("status") in ("fail", "unknown", "needs_override"):
                failed.append(f"{name}:{r.get('status')}")

    gate_wait = 0.0
    opened: dict[str, datetime] = {}
    gates: dict[str, list[str]] = {}
    ratings: dict[str, str] = {}
    reasons = []
    for e in evs:
        gate = (e.get("data") or {}).get("gate")
        if e["event"] == "gate_opened" and gate:
            opened[gate] = parse_ts(e["ts"])
        elif e["event"] == "gate_decision" and gate:
            gates.setdefault(gate, []).append(e["data"].get("decision", "?"))
            if e["data"].get("rating"):
                ratings[gate] = e["data"]["rating"]
            if e["data"].get("decision") in INTERVENTION_DECISIONS and e["data"].get("reason"):
                reasons.append(f"{gate}:{e['data']['reason']}")
            if gate in opened:
                gate_wait += (parse_ts(e["ts"]) - opened.pop(gate)).total_seconds()

    decisions = [e["data"].get("decision") for e in by("gate_decision")]
    interventions = (sum(d in INTERVENTION_DECISIONS for d in decisions)
                     + len(by("override")) + len(by("manual_edit")))
    touchpoints = sum(1 for e in evs if e.get("actor") == "human")

    halted = by("halted")
    finished = by("run_finished")
    if finished:
        status = finished[-1]["data"].get("outcome", "finished")
        if status == "halted" and halted:
            status = f"halted:{halted[-1]['data'].get('reason', '?')}"
    elif halted:
        status = f"halted:{halted[-1]['data'].get('reason', '?')}"
    else:
        status = "in-progress"

    pr = by("pr_opened")
    pr_url = pr[-1]["data"].get("url", "") if pr else ""
    pr_states = by("pr_state")
    pr_state, time_to_merge, st = "", None, {}
    if pr_states:
        st = pr_states[-1]["data"]
        pr_state = st.get("state", "")
        if st.get("isDraft") and pr_state == "OPEN":
            pr_state = "DRAFT"
        if st.get("mergedAt") and pr:
            time_to_merge = (parse_ts(st["mergedAt"]) - parse_ts(pr[-1]["ts"])).total_seconds()
    elif pr:
        pr_state = "DRAFT"
    # commits /fix review-handle pushed are AI follow-ups, not human rework
    rounds = by("review_round")
    ai_review_commits = sum(1 for e in rounds if e["data"].get("commit"))
    post_pr_commits = ""
    if pr and st.get("commits") is not None and pr[-1]["data"].get("commits") is not None:
        post_pr_commits = max(st["commits"] - pr[-1]["data"]["commits"] - ai_review_commits, 0)

    # tokens / model / effort (harvested); orchestrator keeps the last snapshot per session
    agent_usage = [e for e in by("usage") if e["data"].get("scope") == "agent"]
    orch = {e["data"].get("session"): e["data"] for e in by("usage") if e["data"].get("scope") == "orchestrator"}
    tok_stage = Counter()
    for e in agent_usage:
        tok_stage[e["data"].get("stage") or e["stage"] or "other"] += tokens_total(e["data"])
    for u in orch.values():
        tok_stage["orchestrator"] += tokens_total(u)
    all_usage = [e["data"] for e in agent_usage] + list(orch.values())
    models = sorted({m for u in all_usage for m in (u.get("models") or {}) if m not in ("?", "<synthetic>")})
    efforts = sorted({x for u in all_usage for x in (u.get("efforts") or {}) if x != "?"})
    tool_errors = sum(sum((u.get("tool_errors") or {}).values()) for u in all_usage)

    skills: dict[str, dict] = {}
    for e in by("skill_used"):
        d = e["data"]
        s = skills.setdefault(d.get("skill", "?"), {"uses": 0, "failed": 0})
        if d.get("source") == "transcript":
            s["uses"] += 1
        if d.get("outcome") in SKILL_FAILED:
            s["failed"] += 1
    for s in skills.values():
        s["uses"] = max(s["uses"], s["failed"], 1)

    stage_fin = {}
    for e in by("stage_finished"):
        stage_fin[e["stage"]] = e["data"]
    reprompts = sum(1 for e in by("stage_finished") if (e["data"].get("context") or {}).get("reprompted"))
    tests = by("tests_run")
    rating = by("ai_rating")
    injected = {s: set() for s in ("intake", "rca", "plan", "fix")}
    for e in by("lessons_injected"):
        injected.setdefault(e["data"].get("stage") or e["stage"] or "other", set()).update(e["data"].get("ids") or [])

    wall = (parse_ts(last["ts"]) - parse_ts(first["ts"])).total_seconds()
    end_of_run = finished[-1]["ts"] if finished else last["ts"]
    run_wall = (parse_ts(end_of_run) - parse_ts(first["ts"])).total_seconds()

    first_pass = lambda g: (gates.get(g) or [""])[0] == "approve" if g in gates else ""
    return {
        "run_id": run_id,
        "ticket": first["ticket"],
        "started": first["ts"],
        "status": status,
        "eligibility": verdict,
        "failed_criteria": ";".join(failed),
        "gates": " ".join(f"{g}:{'/'.join(d)}" for g, d in sorted(gates.items())),
        "interventions": interventions,
        "human_touchpoints": touchpoints,
        "run_time": fmt_duration(run_wall),
        "gate_wait": fmt_duration(gate_wait),
        "agent_time": fmt_duration(max(run_wall - gate_wait, 0)),
        "pr_raised": "yes" if pr else "no",
        "pr_url": pr_url,
        "pr_state": pr_state,
        "time_to_merge": fmt_duration(time_to_merge),
        "rework": find_rework(first["ticket"]),
        # AI performance
        "rca_confidence": (stage_fin.get("rca") or {}).get("confidence", ""),
        "hint": (first["data"] or {}).get("hint", ""),
        "hint_check": (stage_fin.get("rca") or {}).get("hint_check", ""),
        "gate1_first_pass": first_pass("gate1"),
        "gate2_first_pass": first_pass("gate2"),
        "gate1_rating": ratings.get("gate1", ""),
        "gate2_rating": ratings.get("gate2", ""),
        "revise_reasons": ";".join(reasons),
        "rca_iterations": sum(1 for e in by("stage_started") if e["stage"] == "rca"),
        "plan_iterations": sum(1 for e in by("stage_started") if e["stage"] == "plan"),
        "fix_attempts": tests[-1]["data"].get("attempts", "") if tests else "",
        "context_reprompts": reprompts,
        "post_pr_commits": post_pr_commits,
        "review_rounds": sum(1 for e in rounds if e["data"].get("shipped")),
        "review_replies": len(by("review_reply")),
        "review_comments": st.get("comments", ""),
        "review_decision": st.get("reviewDecision") or "",
        "ai_rating": rating[-1]["data"].get("rating", "") if rating else "",
        "ai_rating_note": rating[-1]["data"].get("note", "") if rating else "",
        "autonomous_merge": (pr_state == "MERGED" and interventions == 0 and post_pr_commits == 0),
        # cost / compliance
        "tokens_total": sum(tok_stage.values()),
        "tokens_output": sum(int(u.get("output_tokens") or 0) for u in all_usage),
        "tokens_cache_read": sum(int(u.get("cache_read_input_tokens") or 0) for u in all_usage),
        "tokens_by_stage": " ".join(f"{k}={v}" for k, v in sorted(tok_stage.items())),
        "models": ",".join(models),
        "efforts": ",".join(efforts),
        "model_ok": (bool(models) and all("opus" in m for m in models) and efforts == ["high"]) if all_usage else "",
        "tool_errors": tool_errors,
        "skills": " ".join(f"{k}={v['uses']}" for k, v in sorted(skills.items())),
        "skills_failed": " ".join(f"{k}={v['failed']}" for k, v in sorted(skills.items()) if v["failed"]),
        "lessons_injected": " ".join(f"{k}={len(v)}" for k, v in sorted(injected.items()) if v),
        "lessons_proposed": sum(len(e["data"].get("proposed") or []) for e in by("lessons_distilled")),
        "lessons_approved": sum(1 for e in by("lesson_review") if e["data"].get("decision") == "approve"),
        "_injected": {k: bool(v) for k, v in injected.items()},
        "_skills": skills,
        "_gates": gates,
        "_overridden": bool(by("override")),
        "_tok_stage": dict(tok_stage),
        "_run_wall": run_wall,
        "_gate_wait": gate_wait,
        "_time_to_merge": time_to_merge,
        "_last_event_age": wall,
    }


def cmd_start(args) -> int:
    run_id = f"{args.ticket}-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    data = {"cwd": str(Path.cwd())}
    if args.hint:
        data["hint"] = args.hint
    write_event(run_id, args.ticket, "run_started", None, "human", data)
    print(run_id)
    return 0


def cmd_append(args) -> int:
    if args.event not in EVENTS:
        print(f"ledger: unknown event {args.event!r}; expected one of {sorted(EVENTS)}", file=sys.stderr)
        return 2
    data = {}
    if args.data_file:
        data = json.loads(Path(args.data_file).read_text())
    elif args.data:
        data = json.loads(args.data)
    runs = group_runs(read_events())
    if args.run_id not in runs:
        print(f"ledger: unknown run_id {args.run_id!r} (run 'start' first)", file=sys.stderr)
        return 2
    ticket = runs[args.run_id][0]["ticket"]
    write_event(args.run_id, ticket, args.event, args.stage, args.actor, data)
    return 0


def cmd_rate(args) -> int:
    runs = group_runs(read_events())
    rid = args.run_id or latest_run(runs, args.ticket)
    if not rid or rid not in runs:
        print(f"ledger: no run for {args.run_id or args.ticket}", file=sys.stderr)
        return 2
    write_event(rid, runs[rid][0]["ticket"], "ai_rating", None, "human",
                {"rating": args.rating, "note": args.note or ""})
    print(f"{rid}: rated {args.rating}")
    return 0


def cmd_report(args) -> int:
    rows = [summarize(rid, evs) for rid, evs in group_runs(read_events()).items()]
    if args.ticket:
        rows = [r for r in rows if r["ticket"] == args.ticket]
    rows.sort(key=lambda r: r["started"])
    cols = [c for c in (rows[0].keys() if rows else []) if not c.startswith("_")]
    if not rows:
        print("ledger: no runs recorded", file=sys.stderr)
        return 0
    if args.csv:
        w = csv.DictWriter(sys.stdout, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
        return 0
    shown = ["ticket", "started", "status", "gates", "interventions", "run_time",
             "tokens_total", "skills_failed", "pr_state", "ai_rating", "pr_url"]
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in shown}
    print("  ".join(c.ljust(widths[c]) for c in shown))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in shown))
    return 0


def pct(n: int, d: int) -> str:
    return f"{n}/{d} ({100 * n // d}%)" if d else "0/0"


def med(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def cmd_analytics(args) -> int:
    rows = [summarize(rid, evs) for rid, evs in group_runs(read_events()).items()]
    if not rows:
        print("ledger: no runs recorded", file=sys.stderr)
        return 0
    n = len(rows)
    eligible = [r for r in rows if r["eligibility"] == "ELIGIBLE" or r["_overridden"]]
    g1 = [r for r in rows if "gate1" in r["_gates"]]
    g1_ok = [r for r in g1 if "approve" in r["_gates"]["gate1"]]
    g2 = [r for r in rows if "gate2" in r["_gates"]]
    g2_ok = [r for r in g2 if "approve" in r["_gates"]["gate2"]]
    prs = [r for r in rows if r["pr_raised"] == "yes"]
    merged = [r for r in prs if r["pr_state"] == "MERGED"]
    closed = [r for r in prs if r["pr_state"] == "CLOSED"]

    skills: dict[str, list[int]] = {}
    for r in rows:
        for k, v in r["_skills"].items():
            s = skills.setdefault(k, [0, 0])
            s[0] += v["uses"]
            s[1] += v["failed"]
    stage_tok: dict[str, list[int]] = {}
    for r in rows:
        for k, v in r["_tok_stage"].items():
            stage_tok.setdefault(k, []).append(v)
    conf: dict[str, list[int]] = {}
    for r in g1:
        c = conf.setdefault(r["rca_confidence"] or "?", [0, 0])
        c[0] += 1
        c[1] += bool(r["gate1_first_pass"])

    out = {
        "runs": n,
        "outcomes": dict(Counter(r["status"] for r in rows)),
        "funnel": {
            "started": n,
            "eligible": len(eligible),
            "gate1_approved": len(g1_ok),
            "gate2_approved": len(g2_ok),
            "pr_opened": len(prs),
            "merged": len(merged),
            "closed_unmerged": len(closed),
            "autonomous_merge": sum(1 for r in rows if r["autonomous_merge"] is True),
        },
        "ineligible_reasons": dict(Counter(c for r in rows for c in r["failed_criteria"].split(";") if c)),
        "halt_reasons": dict(Counter(r["status"].split(":", 1)[1] for r in rows if r["status"].startswith("halted:"))),
        "gate1_first_pass": pct(sum(1 for r in g1 if r["gate1_first_pass"]), len(g1)),
        "gate2_first_pass": pct(sum(1 for r in g2 if r["gate2_first_pass"]), len(g2)),
        "revise_reject_reasons": dict(Counter(x for r in rows for x in r["revise_reasons"].split(";") if x)),
        "gate_ratings": dict(Counter(f"{g}:{r[f'{g}_rating']}" for r in rows for g in ("gate1", "gate2")
                                     if r[f"{g}_rating"])),
        "rca_confidence_vs_gate1_first_pass": {k: pct(v[1], v[0]) for k, v in conf.items()},
        "ai_rating": dict(Counter(r["ai_rating"] for r in rows if r["ai_rating"])),
        "median_interventions": med([r["interventions"] for r in rows if r["gates"]]),
        "median_post_pr_commits": med([r["post_pr_commits"] for r in prs if r["post_pr_commits"] != ""]),
        "median_review_comments": med([r["review_comments"] for r in prs if r["review_comments"] != ""]),
        "median_run_time": fmt_duration(med([r["_run_wall"] for r in rows])),
        "median_gate_wait": fmt_duration(med([r["_gate_wait"] for r in rows if r["gates"]])),
        "median_time_to_merge": fmt_duration(med([r["_time_to_merge"] for r in merged])),
        "tokens_median_per_run": med([r["tokens_total"] for r in rows if r["tokens_total"]]),
        "tokens_median_by_stage": {k: med(v) for k, v in sorted(stage_tok.items())},
        "tokens_per_merged_pr": (sum(r["tokens_total"] for r in rows) // len(merged)) if merged else None,
        "model_effort_compliance": pct(sum(1 for r in rows if r["model_ok"] is True),
                                       sum(1 for r in rows if r["model_ok"] != "")),
        "skill_failures": {k: pct(f, u) for k, (u, f) in sorted(skills.items())},
        "lessons_proposed": sum(r["lessons_proposed"] for r in rows),
        "lessons_approved": sum(r["lessons_approved"] for r in rows),
        "first_pass_with_vs_without_lessons": {
            f"{g}_{label}": pct(sum(1 for r in grp if r[f"{g}_first_pass"]), len(grp))
            for g, stage in (("gate1", "rca"), ("gate2", "plan"))
            for label, grp in (("with", [r for r in rows if g in r["_gates"] and r["_injected"].get(stage)]),
                               ("without", [r for r in rows if g in r["_gates"] and not r["_injected"].get(stage)]))
        },
        "hint_check": dict(Counter(r["hint_check"] for r in rows if r["hint"] and r["hint_check"])),
        "gate1_first_pass_with_vs_without_hint": {
            "with": pct(sum(1 for r in g1 if r["hint"] and r["gate1_first_pass"]), sum(1 for r in g1 if r["hint"])),
            "without": pct(sum(1 for r in g1 if not r["hint"] and r["gate1_first_pass"]),
                           sum(1 for r in g1 if not r["hint"])),
        },
        "gate1_first_pass_by_month": {
            m: pct(sum(1 for r in g1 if r["started"][:7] == m and r["gate1_first_pass"]),
                   sum(1 for r in g1 if r["started"][:7] == m))
            for m in sorted({r["started"][:7] for r in g1})
        },
    }
    if args.json:
        print(json.dumps(out, indent=2, default=str))
        return 0
    for k, v in out.items():
        if isinstance(v, dict):
            print(f"{k}:")
            for kk, vv in v.items():
                print(f"  {kk:<34} {vv}")
        else:
            print(f"{k:<36} {v}")
    return 0


def cmd_show(args) -> int:
    for e in read_events():
        if args.key in (e["run_id"], e["ticket"]):
            print(json.dumps(e, sort_keys=True))
    return 0


def cmd_refresh(args) -> int:
    runs = group_runs(read_events())
    for rid, evs in runs.items():
        if args.ticket and evs[0]["ticket"] != args.ticket:
            continue
        opened = [e for e in evs if e["event"] == "pr_opened"]
        if not opened:
            continue
        states = [e for e in evs if e["event"] == "pr_state"]
        rated = any(e["event"] == "ai_rating" for e in evs)
        if states and states[-1]["data"].get("state") in TERMINAL_PR_STATES:
            if not rated:
                print(f"{evs[0]['ticket']}: {states[-1]['data']['state']} but unrated — "
                      f"/fix rate {evs[0]['ticket']} <good|ok|poor> [note]")
            continue
        m = PR_URL_RE.search(opened[-1]["data"].get("url", ""))
        if not m:
            continue
        repo, number = m.groups()
        proc = subprocess.run(
            ["gh", "pr", "view", number, "--repo", repo, "--json",
             "state,isDraft,mergedAt,closedAt,reviewDecision,comments,reviews,commits,additions,deletions"],
            capture_output=True, text=True)
        if proc.returncode != 0:
            print(f"ledger: gh pr view {repo}#{number} failed: {proc.stderr.strip()}", file=sys.stderr)
            continue
        raw = json.loads(proc.stdout)
        snap = {
            "number": int(number),
            "state": raw.get("state"),
            "isDraft": raw.get("isDraft"),
            "mergedAt": raw.get("mergedAt"),
            "closedAt": raw.get("closedAt"),
            "reviewDecision": raw.get("reviewDecision"),
            "comments": len(raw.get("comments") or []),
            "reviews": len(raw.get("reviews") or []),
            "commits": len(raw.get("commits") or []),
            "additions": raw.get("additions"),
            "deletions": raw.get("deletions"),
        }
        if states and states[-1]["data"] == snap:
            continue
        prev = states[-1]["data"] if states else {"comments": 0, "reviews": 0}
        write_event(rid, evs[0]["ticket"], "pr_state", None, "agent", snap)
        print(f"{evs[0]['ticket']}: {repo}#{number} -> {snap['state']}{' (draft)' if snap['isDraft'] else ''}")
        if snap["comments"] > prev.get("comments", 0) or snap["reviews"] > prev.get("reviews", 0):
            print(f"  new review activity — learn from it: /fix learn {evs[0]['ticket']}")
        if snap["state"] in TERMINAL_PR_STATES and not rated:
            print(f"  rate it: /fix rate {evs[0]['ticket']} <good|ok|poor> [note]")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start")
    p.add_argument("--ticket", required=True)
    p.add_argument("--hint", help="user's free-text hint on where to look (recorded to measure hint value)")
    p.set_defaults(fn=cmd_start)

    p = sub.add_parser("append")
    p.add_argument("--run-id", required=True)
    p.add_argument("--event", required=True)
    p.add_argument("--stage")
    p.add_argument("--actor", choices=["agent", "human"], default="agent")
    g = p.add_mutually_exclusive_group()
    g.add_argument("--data")
    g.add_argument("--data-file")
    p.set_defaults(fn=cmd_append)

    p = sub.add_parser("harvest")
    p.add_argument("--run-id", required=True)
    p.add_argument("--transcript", help="session .jsonl (default: every transcript mentioning the run_id)")
    p.set_defaults(fn=cmd_harvest)

    p = sub.add_parser("rate")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--ticket")
    g.add_argument("--run-id")
    p.add_argument("--rating", required=True, choices=RATINGS)
    p.add_argument("--note")
    p.set_defaults(fn=cmd_rate)

    p = sub.add_parser("report")
    p.add_argument("--ticket")
    p.add_argument("--csv", action="store_true")
    p.set_defaults(fn=cmd_report)

    p = sub.add_parser("analytics")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=cmd_analytics)

    p = sub.add_parser("show")
    p.add_argument("key")
    p.set_defaults(fn=cmd_show)

    p = sub.add_parser("refresh-prs")
    p.add_argument("--ticket")
    p.set_defaults(fn=cmd_refresh)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
