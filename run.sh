#!/usr/bin/env bash
# One-command entry point: sets up everything on first run, then hunts jobs.
# Not a developer? Double-click START-HERE-Mac.command (or START-HERE-Windows.bat) instead.
#   ./run.sh menu         simple numbered menu for everything below
#   ./run.sh              login + search + report + ask before applying
#   ./run.sh side         same, using the side-gig profile (config.side.yaml)
#   ./run.sh --apply      same, apply without asking (for cron/scheduled runs)
#   ./run.sh --no-apply   search and report only
#   ./run.sh prefs        change resume, skills, roles, location, work mode, job type
#   ./run.sh resume PDF   switch resume (skills are re-detected from it)
#   ./run.sh pick         numbered list of matches; choose which to apply to
#   ./run.sh contacts     emails / phones / links to send your resume manually
#   ./run.sh side pick    any command for the side-gig profile
#   ./run.sh setup        re-run the setup wizard
#   ./run.sh search|open|apply|login ...   run a single step (see: ./run.sh --help)
set -euo pipefail
cd "$(dirname "$0")"

find_python() {
  local c
  for c in "${PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3.10 python3 \
           /usr/local/bin/python3 /opt/homebrew/bin/python3; do
    if [ -n "$c" ] && command -v "$c" >/dev/null 2>&1 &&
       "$c" -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>/dev/null; then
      echo "$c"
      return 0
    fi
  done
  return 1
}

if [ ! -x .venv/bin/python ]; then
  if ! PYTHON="$(find_python)"; then
    cat >&2 <<'EOF'

Python 3.10 or newer is needed (it's free).
  1. Download it from https://www.python.org/downloads/ (the big yellow button)
  2. Open the downloaded file and install it
  3. Start Naukri Job Hunter again
EOF
    [ "$(uname)" = "Darwin" ] && open "https://www.python.org/downloads/"
    exit 1
  fi
  echo "Setting things up for the first time. This takes a few minutes..."
  "$PYTHON" -m venv .venv
fi
if ! cmp -s requirements.txt .venv/.installed-requirements; then
  echo "Installing dependencies..."
  .venv/bin/pip install -q --upgrade pip
  .venv/bin/pip install -q -r requirements.txt
  cp requirements.txt .venv/.installed-requirements
fi

has_chrome() {
  [ -d "/Applications/Google Chrome.app" ] || [ -d "$HOME/Applications/Google Chrome.app" ] ||
    command -v google-chrome >/dev/null 2>&1 || command -v google-chrome-stable >/dev/null 2>&1
}
if ! has_chrome && [ ! -f .venv/.chromium-installed ]; then
  echo "Google Chrome not found (recommended: https://www.google.com/chrome/)."
  echo "Installing Playwright's bundled Chromium as a fallback..."
  .venv/bin/python -m playwright install chromium
  touch .venv/.chromium-installed
fi

if [ ! -f config.yaml ] && [ "${1:-}" != "setup" ]; then
  .venv/bin/python hunt.py setup
fi

COMMANDS="run login search open apply pick contacts prefs resume menu"
is_command() { [[ " $COMMANDS " == *" ${1:-} "* ]]; }

case "${1:-}" in
  setup|-h|--help) exec .venv/bin/python hunt.py "$@" ;;
esac
if is_command "${1:-}"; then
  exec .venv/bin/python hunt.py "$@"
fi
if [ -n "${1:-}" ] && [ -f "config.$1.yaml" ]; then
  profile="$1"
  shift
  if is_command "${1:-}"; then
    cmd="$1"
    shift
    exec .venv/bin/python hunt.py "$cmd" --profile "$profile" "$@"
  fi
  exec .venv/bin/python hunt.py run --profile "$profile" "$@"
fi
exec .venv/bin/python hunt.py run "$@"
