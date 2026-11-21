#!/usr/bin/env bash
# Run the mock-buyer scenarios against a running sandbox seller.
# Any arguments are passed through, e.g.:
#   ./sandbox/scenarios.sh --only 3,4 --verbose
#   ./sandbox/scenarios.sh --buyer-agent ../buyer-agent
set -euo pipefail
cd "$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
command -v uv >/dev/null 2>&1 || export PATH="$HOME/.local/bin:$PATH"
exec uv run --locked --python 3.12 python sandbox/scenarios.py "$@"
