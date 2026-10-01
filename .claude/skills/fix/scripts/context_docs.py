#!/usr/bin/env python3
"""Resolve the repo docs a /fix stage agent MUST read before working.

Claude Code auto-loads only the root CLAUDE.md; nested CLAUDE.md files in
cloudn are pointers to AGENTS.md, and agents/*.md is never auto-loaded. This
script makes the required reading explicit so the orchestrator can inject it
into the agent prompt and verify the agent's CONTEXT_LOADED block against it.

Usage:
  context_docs.py --stage intake|rca|plan|fix
                  [--component "BGP" ...] [--path go/aviatrix.com/... ...]
                  [--repo-root .] [--json] [--verify ARTIFACT]
  context_docs.py --seeds --component "BGP"    # code seeds from feature-map.md
  context_docs.py --check-map                  # component-map.md vs feature-map.md drift

Paths may be files or dirs, with or without :line suffixes.
"""

import argparse
import json
import re
import sys
from pathlib import Path

ALWAYS = ["CLAUDE.md", "AGENTS.md", "agents/architecture.md", "agents/feature-map.md"]
BY_STAGE = {
    "intake": [],
    "rca": ["agents/operations.md", "agents/languages.md", "agents/conventions/git.md"],   # git.md: comment rules for the repro test
    "plan": ["agents/conventions/code-quality.md", "agents/conventions/git.md", "agents/build.md"],
    "fix": ["agents/conventions/code-quality.md", "agents/conventions/git.md", "agents/build.md"],
}
BY_LANG = {
    ".go": ["agents/conventions/go.md"],
    ".py": ["agents/conventions/python.md", "agents/conventions/python-imports.md"],
}
SECURITY_PREFIXES = ("go/aviatrix.com/avxapi", "go/aviatrix.com/appserver", "cloudx-local/api",
                     "cloudx-local/daemon/v2_5")
SECURITY_DOCS = ["agents/security/review.md", "agents/security/api.md"]

# Component seeds come from the repo's own map: references/component-map.md names
# feature-map rows per Jira component; the paths are read from agents/feature-map.md.
COMPONENT_MAP = Path(__file__).resolve().parent.parent / "references" / "component-map.md"
FEATURE_MAP = "agents/feature-map.md"
FEATURE_MAP_BASE = "go/aviatrix.com/conduit/v2"   # feature-map: "Paths are under ... unless noted"
REPO_ROOT_PREFIXES = ("go/", "cloudx-", "python/", "protos/", "charts/", "api/")
BACKTICK = re.compile(r"`([^`]+)`")


def table_rows(text: str) -> list[list[str]]:
    rows = []
    for line in text.splitlines():
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        rows.append([c.strip() for c in line.strip().strip("|").split("|")])
    return rows


def load_component_map() -> dict[str, dict]:
    """{component_lower: {"features": [...], "extra": [...]}} from component-map.md."""
    out = {}
    for cells in table_rows(COMPONENT_MAP.read_text()):
        if len(cells) < 3 or cells[0] == "Component":
            continue
        feats = [f.strip() for f in cells[1].split(";") if f.strip() not in ("", "—", "-")]
        out[cells[0].lower()] = {"features": feats, "extra": BACKTICK.findall(cells[2])}
    return out


def load_feature_paths(root: Path) -> dict[str, list[str]]:
    """{feature row name: [existing repo-relative paths]} parsed from agents/feature-map.md."""
    out = {}
    for cells in table_rows((root / FEATURE_MAP).read_text()):
        if len(cells) < 2:
            continue
        paths, anchor = [], None
        for tok in BACKTICK.findall(" | ".join(cells[1:])):
            tok = tok.strip().rstrip("*")
            if "/" not in tok and "." not in tok:
                continue
            cands = [tok] if tok.startswith(REPO_ROOT_PREFIXES) else [f"{FEATURE_MAP_BASE}/{tok}"]
            if anchor:   # "`cloudx-local/` (`microseg/`, ...)" -> relative to the preceding dir
                cands.append(f"{anchor}/{tok}")
            hit = next((c.rstrip("/") for c in cands if (root / c.rstrip("/")).exists()), None)
            if hit:
                paths.append(hit)
                if tok.endswith("/") and tok.startswith(REPO_ROOT_PREFIXES):
                    anchor = hit
        out[cells[0]] = paths
    return out


def component_seeds(root: Path, component: str) -> tuple[list[str], list[str]]:
    """(seed paths, problems) for a Jira component."""
    entry = load_component_map().get(component.lower())
    if entry is None:
        return [], [f"component {component!r} not in {COMPONENT_MAP.name}"]
    features = load_feature_paths(root)
    seeds, problems = [], []
    for f in entry["features"]:
        if f not in features:
            problems.append(f"{component}: feature-map has no row {f!r}")
        seeds += features.get(f, [])
    for d in entry["extra"]:
        if (root / d).exists():
            seeds.append(d)
        else:
            problems.append(f"{component}: extra dir {d} does not exist")
    return list(dict.fromkeys(seeds)), problems


def nearest_agents_md(root: Path, rel: str) -> list[str]:
    """All AGENTS.md from the path up to (not including) the repo root, nearest first."""
    rel = rel.split(":", 1)[0].strip("/")
    p = root / rel
    cur = p if p.is_dir() else p.parent
    found = []
    while cur != root and root in cur.parents:
        cand = cur / "AGENTS.md"
        if cand.is_file():
            found.append(str(cand.relative_to(root)))
        cur = cur.parent
    return found


def resolve(root: Path, stage: str, components: list[str], paths: list[str]) -> list[dict]:
    docs: dict[str, str] = {}

    def add(doc: str, why: str) -> None:
        if doc not in docs and (root / doc).is_file():
            docs[doc] = why

    for d in ALWAYS:
        add(d, "always")
    for d in BY_STAGE[stage]:
        add(d, f"stage:{stage}")

    seeds = list(paths)
    for c in components:
        seeds += component_seeds(root, c)[0]
        if c.lower() in ("api",):
            for d in SECURITY_DOCS:
                add(d, f"component:{c}")

    for s in seeds:
        for d in nearest_agents_md(root, s):
            add(d, f"nearest to {s.split(':', 1)[0]}")
        clean = s.split(":", 1)[0]
        suffix = Path(clean).suffix
        if suffix in BY_LANG:
            for d in BY_LANG[suffix]:
                add(d, f"lang{suffix}")
        elif clean.startswith("go/"):
            for d in BY_LANG[".go"]:
                add(d, "lang.go")
        elif clean.startswith(("cloudx-", "python/")):
            for d in BY_LANG[".py"]:
                add(d, "lang.py")
        if clean.startswith(SECURITY_PREFIXES):
            for d in SECURITY_DOCS:
                add(d, f"security:{clean}")

    return [{"doc": d, "why": w} for d, w in docs.items()]


GENERIC_TAKEAWAYS = re.compile(r"^(read|reviewed|loaded|ok|done|n/?a|general guidance|noted)\.?$", re.I)
MIN_TAKEAWAY_CHARS = 20


def verify(artifact: Path, required: list[str]) -> list[str]:
    """Return required docs missing a specific takeaway in the artifact's CONTEXT_LOADED block."""
    text = artifact.read_text() if artifact.is_file() else ""
    m = re.search(r"CONTEXT_LOADED:\s*\n((?:[ \t]+-.*\n?)+)", text)
    loaded: dict[str, str] = {}
    for line in (m.group(1).splitlines() if m else []):
        item = re.match(r"\s*-\s*`?([^`\s]+)`?\s*[—:-]+\s*(.*)$", line)
        if item:
            loaded[item.group(1)] = item.group(2).strip()
    missing = []
    for doc in required:
        takeaway = loaded.get(doc, "")
        if len(takeaway) < MIN_TAKEAWAY_CHARS or GENERIC_TAKEAWAYS.match(takeaway):
            missing.append(doc)
    return missing


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--stage", choices=sorted(BY_STAGE))
    ap.add_argument("--component", action="append", default=[])
    ap.add_argument("--path", action="append", default=[])
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--verify", metavar="ARTIFACT",
                    help="check ARTIFACT's CONTEXT_LOADED covers the required docs; exit 1 and list gaps if not")
    ap.add_argument("--seeds", action="store_true",
                    help="print the code seed paths for each --component (from feature-map.md) and exit")
    ap.add_argument("--check-map", action="store_true",
                    help="validate component-map.md rows against feature-map.md; exit 1 on drift")
    args = ap.parse_args()
    root = Path(args.repo_root).resolve()
    if not (root / "CLAUDE.md").is_file():
        print(f"context_docs: {root} has no CLAUDE.md; run from the cloudn root", file=sys.stderr)
        return 2
    if args.check_map or args.seeds:
        comps = args.component or [c for c in load_component_map()]
        result, bad = {}, []
        for c in comps:
            seeds, problems = component_seeds(root, c)
            result[c] = seeds
            bad += problems + ([f"{c}: no seeds"] if not seeds else [])
        print(json.dumps(result if args.seeds else {"problems": bad}, indent=2))
        return 1 if bad else 0
    if not args.stage:
        ap.error("--stage is required unless --seeds/--check-map")
    docs = resolve(root, args.stage, args.component, args.path)
    if args.verify:
        required = [d["doc"] for d in docs]
        missing = verify(Path(args.verify), required)
        print(json.dumps({"required": required, "missing": missing}))
        return 1 if missing else 0
    if args.json:
        print(json.dumps(docs, indent=2))
    else:
        for d in docs:
            print(f"{d['doc']}\t({d['why']})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
