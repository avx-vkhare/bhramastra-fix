#!/usr/bin/env bash
# Link this clone into Claude Code (user level) so `git pull` updates /fix in place.
#
#   ./install.sh              link skill + agents, create ~/.bhramastra, seed lessons, check tools
#   ./install.sh --check      only report what is linked and which tools are missing
#   ./install.sh --uninstall  remove the links (your ledger and lessons are kept)
#
# Safe to re-run. Anything already at a target path that isn't our link is moved
# to ~/.claude/fix-backup-<timestamp>/, never deleted.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CLAUDE="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
STATE="$HOME/.bhramastra"
BACKUP="$CLAUDE/fix-backup-$(date +%Y%m%d-%H%M%S)"
MODE="${1:-install}"

ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
bad()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

# source -> target pairs: the skill directory and every fix-*.md agent
links=("$REPO/.claude/skills/fix|$CLAUDE/skills/fix")
for a in "$REPO"/.claude/agents/fix-*.md; do
  links+=("$a|$CLAUDE/agents/$(basename "$a")")
done

backup() {   # move a real file/dir (or a foreign link) out of the way
  mkdir -p "$BACKUP"
  mv "$1" "$BACKUP/"
  warn "moved existing $1 → $BACKUP/"
}

link_all() {
  mkdir -p "$CLAUDE/skills" "$CLAUDE/agents"
  for pair in "${links[@]}"; do
    src="${pair%%|*}"; dst="${pair##*|}"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
      ok "already linked: $dst"
      continue
    fi
    if [ -e "$dst" ] || [ -L "$dst" ]; then backup "$dst"; fi
    ln -s "$src" "$dst"
    ok "linked $dst → $src"
  done
}

unlink_all() {
  for pair in "${links[@]}"; do
    src="${pair%%|*}"; dst="${pair##*|}"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then
      unlink "$dst"; ok "removed link $dst"
    fi
  done
  echo "Kept $STATE (ledger + lessons). Delete it yourself if you want a clean slate."
}

seed_state() {
  mkdir -p "$STATE"
  if [ -f "$STATE/lessons.jsonl" ]; then
    ok "kept your lessons: $STATE/lessons.jsonl (seed not copied over it)"
  else
    cp "$REPO/lessons/seed-lessons.jsonl" "$STATE/lessons.jsonl"
    ok "seeded $(wc -l < "$STATE/lessons.jsonl" | tr -d ' ') shared lessons → $STATE/lessons.jsonl"
  fi
}

check_links() {
  local missing=0
  for pair in "${links[@]}"; do
    src="${pair%%|*}"; dst="${pair##*|}"
    if [ -L "$dst" ] && [ "$(readlink "$dst")" = "$src" ]; then ok "$dst"; else bad "$dst not linked"; missing=1; fi
  done
  return $missing
}

check_tools() {
  if command -v python3 >/dev/null && python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
    ok "python3 $(python3 -c 'import platform; print(platform.python_version())')"
  else
    bad "python3 ≥ 3.10 is required by the scripts"
  fi
  for t in git jq gh bazel claude; do
    if command -v "$t" >/dev/null; then ok "$t"; else bad "$t not on PATH"; fi
  done
  if command -v gh >/dev/null; then
    if gh auth status >/dev/null 2>&1; then ok "gh is logged in"; else bad "gh is not logged in — run: gh auth login"; fi
  fi
  if python3 "$CLAUDE/skills/fix/scripts/fix_guard.py" --help >/dev/null 2>&1; then
    ok "scripts run (fix_guard.py --help)"
  else
    bad "scripts don't run from $CLAUDE/skills/fix/scripts"
  fi
  echo "  Not checked here (README → Install, step 1): Jira MCP in cloudn (/mcp),"
  echo "  AWS SSO for tracelogs, avx-tool-shed skills net-download-tracelog and net-topology."
}

case "$MODE" in
  install|"")
    echo "Linking /fix from $REPO into $CLAUDE"
    link_all
    echo "State in $STATE"
    seed_state
    echo "Checks"
    check_tools
    echo
    echo "Done. Start Claude Code from your cloudn checkout and run: /fix help"
    echo "Update later with: git -C $REPO pull   (re-run ./install.sh if a new agent was added)"
    ;;
  --check)
    echo "Links"; check_links || true
    echo "Tools"; check_tools
    ;;
  --uninstall)
    unlink_all
    ;;
  *)
    echo "usage: $0 [--check | --uninstall]" >&2; exit 2
    ;;
esac
