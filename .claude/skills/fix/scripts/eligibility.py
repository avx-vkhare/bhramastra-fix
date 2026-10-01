#!/usr/bin/env python3
"""Evaluate /fix eligibility rules against ticket facts gathered by fix-intake.

Usage: eligibility.py <facts.json> [--rules <eligibility.json>]

facts.json (written by the fix-intake agent):
  {
    "ticket": "AVX-12345",
    "issuetype": "Bug",
    "status": "Open",
    "components": ["BGP"],
    "severity": "S3",                      # customfield_10033, may be null
    "desired_versions": ["10.2.0"],        # customfield_10238, may be []
    "fix_versions": ["10.2.0"],            # fixVersions, may be []
    "has_repro": {"value": true, "evidence": "Steps to reproduce: ..."},
    "has_logs":  {"value": false, "evidence": "no bundle prefix, no .tgz"}
  }

Prints a JSON verdict to stdout and exits 0 (ELIGIBLE), 1 (INELIGIBLE),
or 2 (NEEDS_OVERRIDE). Exit 3 means bad input.
"""

import argparse
import json
import re
import sys
from pathlib import Path

DEFAULT_RULES = Path(__file__).resolve().parent.parent / "references" / "eligibility.json"
VERSION_RE = re.compile(r"(\d+(?:\.\d+)+|\d+)")

PASS, FAIL, UNKNOWN, WARN, OVERRIDE = "pass", "fail", "unknown", "warn", "needs_override"


def parse_version(text: str) -> tuple[int, ...] | None:
    m = VERSION_RE.search(text or "")
    if not m:
        return None
    return tuple(int(p) for p in m.group(1).split("."))


def version_at_least(version: tuple[int, ...], minimum: tuple[int, ...]) -> bool:
    width = max(len(version), len(minimum))
    pad = lambda v: v + (0,) * (width - len(v))
    return pad(version) >= pad(minimum)


def check_issuetype(facts: dict, rules: dict) -> dict:
    value = facts.get("issuetype")
    if not value:
        return {"status": UNKNOWN, "detail": "issuetype missing"}
    ok = value.lower() in {t.lower() for t in rules["allowed_issuetypes"]}
    return {"status": PASS if ok else FAIL, "detail": value}


def check_status(facts: dict, rules: dict) -> dict:
    value = facts.get("status")
    if not value:
        return {"status": UNKNOWN, "detail": "status missing"}
    bad = value.lower() in {s.lower() for s in rules["excluded_statuses"]}
    return {"status": FAIL if bad else PASS, "detail": value}


def check_components(facts: dict, rules: dict) -> dict:
    comps = facts.get("components") or []
    if not comps:
        return {"status": UNKNOWN, "detail": "no components set"}
    scope = {c.lower() for c in rules["components_in_scope"]}
    hits = [c for c in comps if c.lower() in scope]
    if hits:
        return {"status": PASS, "detail": f"in scope: {', '.join(hits)}"}
    return {"status": FAIL, "detail": f"none in scope: {', '.join(comps)}"}


def check_version(facts: dict, rules: dict) -> dict:
    minimum = parse_version(rules["min_desired_version"])
    source, versions = "desired_versions", facts.get("desired_versions") or []
    if not versions:
        source, versions = "fix_versions", facts.get("fix_versions") or []
    if not versions:
        return {"status": UNKNOWN, "detail": "no Desired Version and no fixVersions"}
    parsed = [(v, parse_version(v)) for v in versions]
    ok = [v for v, p in parsed if p is not None and version_at_least(p, minimum)]
    if ok:
        return {"status": PASS, "detail": f"{source}: {', '.join(ok)} >= {rules['min_desired_version']}"}
    if all(p is None for _, p in parsed):
        return {"status": UNKNOWN, "detail": f"{source} unparseable: {', '.join(versions)}"}
    return {"status": FAIL, "detail": f"{source}: {', '.join(versions)} < {rules['min_desired_version']}"}


def check_repro_or_logs(facts: dict, rules: dict) -> dict:
    repro = bool((facts.get("has_repro") or {}).get("value"))
    logs = bool((facts.get("has_logs") or {}).get("value"))
    detail = f"repro={'yes' if repro else 'no'}, logs={'yes' if logs else 'no'}"
    mode = rules.get("repro_or_logs", "hard")
    if repro or logs or mode == "off":
        return {"status": PASS, "detail": detail}
    # override: a human may know how to reproduce even though the ticket doesn't say.
    return {"status": OVERRIDE if mode == "override" else FAIL, "detail": detail}


def check_severity(facts: dict, rules: dict) -> dict:
    value = facts.get("severity")
    sev_rules = rules["severity"]
    if not value:
        return {"status": WARN, "detail": "severity not set", "hard": sev_rules["hard_stop"]}
    ok = value.upper() in {s.upper() for s in sev_rules["allowed"]}
    status = PASS if ok else (FAIL if sev_rules["hard_stop"] else WARN)
    return {"status": status, "detail": value, "hard": sev_rules["hard_stop"]}


CHECKS = {
    "issuetype": check_issuetype,
    "status": check_status,
    "component": check_components,
    "desired_version": check_version,
    "repro_or_logs": check_repro_or_logs,
    "severity": check_severity,
}


def evaluate(facts: dict, rules: dict) -> dict:
    results = {name: fn(facts, rules) for name, fn in CHECKS.items()}
    statuses = [r["status"] for r in results.values()]
    if FAIL in statuses:
        verdict = "INELIGIBLE"
    elif UNKNOWN in statuses or OVERRIDE in statuses:
        verdict = "NEEDS_OVERRIDE"
    else:
        verdict = "ELIGIBLE"
    return {"ticket": facts.get("ticket"), "verdict": verdict, "criteria": results}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("facts")
    ap.add_argument("--rules", default=str(DEFAULT_RULES))
    args = ap.parse_args()
    try:
        facts = json.loads(Path(args.facts).read_text())
        rules = json.loads(Path(args.rules).read_text())
    except (OSError, json.JSONDecodeError) as e:
        print(f"eligibility: cannot read input: {e}", file=sys.stderr)
        return 3
    out = evaluate(facts, rules)
    print(json.dumps(out, indent=2))
    return {"ELIGIBLE": 0, "INELIGIBLE": 1, "NEEDS_OVERRIDE": 2}[out["verdict"]]


if __name__ == "__main__":
    sys.exit(main())
