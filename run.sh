#!/usr/bin/env bash
# One-command entry point: sets up everything on first run, then hunts jobs.
#   ./run.sh              login + search + report + ask before applying
#   ./run.sh side         same, using the side-gig profile (config.side.yaml)
#   ./run.sh --apply      same, apply without asking (for cron/scheduled runs)
#   ./run.sh --no-apply   search and report only
#   ./run.sh pick         numbered list of matches; choose which to apply to
#   ./run.sh contacts     emails / phones / links to send your resume manually
#   ./run.sh side pick    any command for the side-gig profile
#   ./run.sh setup        re-run the setup wizard
#   ./run.sh search|open|apply|login ...   run a single step (see: ./run.sh --help)
set -euo pipefail
cd "$(dirname "$0")"

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  echo "Python 3.10+ is required: https://www.python.org/downloads/" >&2
  exit 1
fi
if ! "$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Python 3.10+ is required (found $("$PYTHON" --version 2>&1))." >&2
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "Creating Python environment..."
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

COMMANDS="run login search open apply pick contacts"
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
