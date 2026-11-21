"""Start the seller agent on http://localhost:8000 with the mock-only config.

Usage (from the repo root):  uv run python sandbox/run_server.py [--reload]
"""

from __future__ import annotations

import argparse
import os
import socket
import sys

import sandbox_env


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((host, port)) == 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reload", action="store_true", help="auto-reload on code changes")
    args = parser.parse_args()

    sandbox_env.load_sandbox_env()
    sandbox_env.assert_mock_only()

    if _port_in_use(sandbox_env.HOST, sandbox_env.PORT):
        print(
            f"Port {sandbox_env.PORT} is already in use. Is the sandbox server already "
            f"running? Check {sandbox_env.BASE_URL}/health or stop the other process.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    import uvicorn

    # CrewAI shows a "view your execution traces? [y/N] (20s timeout)" prompt
    # after each flow whenever stdin is a TTY, which stalls POST /proposals
    # for 20s in a foreground server. Ctrl+C still works (it is a signal).
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115

    print(f"Sandbox seller agent (mock-only) -> {sandbox_env.BASE_URL}  (docs: /docs)")
    print("Press Ctrl+C to stop.")
    uvicorn.run(
        "ad_seller.interfaces.api.main:app",
        host=sandbox_env.HOST,
        port=sandbox_env.PORT,
        reload=args.reload,
        reload_dirs=[str(sandbox_env.REPO_ROOT / "src")] if args.reload else None,
        log_level="info",
    )
    # Same reason as ad_seller._telemetry_shim.force_exit_after: straggling
    # CrewAI/chromadb atexit handlers otherwise hang Ctrl+C for minutes.
    print("Sandbox server stopped.", flush=True)
    sys.stderr.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
