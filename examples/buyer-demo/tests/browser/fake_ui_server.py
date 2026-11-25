"""Serve the demo UI against the in-process fake seller, for browser checks.

No network and no real keys: the seller is the same httpx.MockTransport the
unit tests use, and run logs go to a temporary directory.

    uv run python tests/browser/fake_ui_server.py --port 8799
"""

from __future__ import annotations

import argparse
import sys
import tempfile
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parents[1]))

import buyer_demo  # noqa: E402
import demo_ui  # noqa: E402
from test_buyer_demo_ui import FakeKeys, fake_seller  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8799)
    args = parser.parse_args()

    buyer_demo.RUNS_DIR = Path(tempfile.mkdtemp(prefix="buyer-demo-browser-")) / "runs"
    settings = demo_ui.Settings(host="127.0.0.1", port=args.port, open_browser=False)
    app = demo_ui.DemoApp(
        settings,
        "http://seller.test",
        FakeKeys(("advertiser", "agency", "seat")),
        5.0,
        buyer_demo.run_scenarios,
        buyer_demo.SCENARIO_HELP,
        buyer_demo.SCENARIO_ORDER,
        transport=httpx.MockTransport(fake_seller),
    )
    server = demo_ui.build_server(app)
    print(f"fake UI server on http://127.0.0.1:{args.port}/", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
