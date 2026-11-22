#!/usr/bin/env bash
# Mock-only seller sandbox setup for macOS / Linux. Safe to re-run.
#
#   ./sandbox/setup.sh            install uv (if missing), Python 3.12, deps,
#                                 sandbox config and API keys
#   ./sandbox/setup.sh --rekey    same, but revoke and re-mint the API keys
#   ./sandbox/setup.sh --reset    same, but delete the sandbox DB first
#                                 (all quotes/deals/keys) and re-mint keys
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

PYTHON_VERSION="3.12"
LOCAL_DIR="sandbox/.local"
MINT_ARGS=()

for arg in "$@"; do
  case "$arg" in
    --rekey) MINT_ARGS+=(--force) ;;
    --reset)
      rm -f "$LOCAL_DIR"/ad_seller_sandbox.db* "$LOCAL_DIR/keys.json"
      echo "Sandbox database and keys deleted."
      ;;
    -h|--help) sed -n '2,9p' "$0"; exit 0 ;;
    *) echo "Unknown option: $arg" >&2; exit 2 ;;
  esac
done

step() { printf '\n==> %s\n' "$*"; }

step "Checking for uv"
if ! command -v uv >/dev/null 2>&1; then
  if [ -x "$HOME/.local/bin/uv" ]; then
    export PATH="$HOME/.local/bin:$PATH"
  else
    echo "uv not found; installing it from https://astral.sh/uv (user-local, no sudo)."
    if command -v curl >/dev/null 2>&1; then
      curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
      wget -qO- https://astral.sh/uv/install.sh | sh
    else
      echo "Neither curl nor wget is available. Install uv manually, then re-run:" >&2
      echo "  macOS:  brew install uv" >&2
      echo "  other:  https://docs.astral.sh/uv/getting-started/installation/" >&2
      exit 1
    fi
    export PATH="$HOME/.local/bin:$PATH"
  fi
fi
uv --version

step "Installing Python $PYTHON_VERSION (managed by uv; the system Python is not used)"
uv python install "$PYTHON_VERSION"

step "Syncing locked dependencies into .venv"
uv sync --locked --python "$PYTHON_VERSION" --extra dev

step "Sandbox config"
mkdir -p "$LOCAL_DIR"
if [ -f "$LOCAL_DIR/sandbox.env" ]; then
  echo "Keeping existing $LOCAL_DIR/sandbox.env"
else
  cp sandbox/.env.sandbox "$LOCAL_DIR/sandbox.env"
  echo "Created $LOCAL_DIR/sandbox.env from sandbox/.env.sandbox"
fi

step "API keys"
uv run --locked --python "$PYTHON_VERSION" python sandbox/mint_keys.py "${MINT_ARGS[@]+"${MINT_ARGS[@]}"}"

cat <<EOF

Sandbox ready (mock-only: CSV catalog, local SQLite, no ad server/SSP/registry).
  Start the seller:   ./sandbox/run.sh
  Run the scenarios:  ./sandbox/scenarios.sh        (in a second terminal)
  Your config:        $LOCAL_DIR/sandbox.env  (git-ignored; add ANTHROPIC_API_KEY here)
  Your API keys:      $LOCAL_DIR/keys.json    (git-ignored)
EOF
