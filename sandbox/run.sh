#!/usr/bin/env bash
# One step: (re-)run the idempotent sandbox setup, then start the mock-only
# seller on http://localhost:8000. Ctrl+C stops it.
#
#   ./sandbox/run.sh                 setup + start
#   ./sandbox/run.sh --skip-setup    start only
#   ./sandbox/run.sh --reload        setup + start with auto-reload on code edits
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

SKIP_SETUP=0
SERVER_ARGS=()
for arg in "$@"; do
  case "$arg" in
    --skip-setup) SKIP_SETUP=1 ;;
    --reload) SERVER_ARGS+=(--reload) ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [ "$SKIP_SETUP" -eq 0 ]; then
  ./sandbox/setup.sh
fi
command -v uv >/dev/null 2>&1 || export PATH="$HOME/.local/bin:$PATH"

printf '\n==> Starting the sandbox seller (Ctrl+C to stop)\n'
exec uv run --locked --python 3.12 python sandbox/run_server.py "${SERVER_ARGS[@]+"${SERVER_ARGS[@]}"}"
