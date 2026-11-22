"""Hosted (Fly.io) entry point for the mock-only seller sandbox.

Runs inside the container built from infra/fly/Dockerfile (via
infra/fly/entrypoint.sh, which drops root). Usage:

    python sandbox/fly_server.py            # serve (default)
    python sandbox/fly_server.py rekey      # revoke + re-mint the sandbox keys
    python sandbox/fly_server.py check      # print the effective mock-only config

On every boot it loads sandbox/.env.fly with the same pinning rules as the
local sandbox, refuses to start unless the config is mock-only, and mints the
operator + fictional buyer keys into $DATA_DIR/keys.json if they are missing
or no longer valid. Only key ids are ever logged, never the secrets.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

import sandbox_env

FLY_ENV = sandbox_env.SANDBOX_DIR / ".env.fly"
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
KEYS_FILE = DATA_DIR / "keys.json"

# SELLER_AGENT_URL is the one pinned key the deployment may set itself
# (fly.toml [env]); mock_only_violations(public_seller_url=True) still
# requires it to be https.
PINNED_KEYS = sandbox_env.PINNED_KEYS - {"SELLER_AGENT_URL"}


def log(msg: str) -> None:
    print(f"[fly-sandbox] {msg}", flush=True)


def load_fly_env() -> None:
    """Pinned keys always come from .env.fly; other keys there are defaults."""
    os.chdir(sandbox_env.REPO_ROOT)
    values = sandbox_env._parse_env_file(FLY_ENV)
    for key, value in values.items():
        if key in PINNED_KEYS or not os.environ.get(key):
            os.environ[key] = value
    for key in PINNED_KEYS - values.keys():
        os.environ[key] = ""


def assert_mock_only() -> None:
    problems = sandbox_env.mock_only_violations(public_seller_url=True)
    if problems:
        log("REFUSING TO START: the hosted sandbox config is not mock-only:")
        for p in problems:
            log(f"  - {p}")
        log(f"Fix {FLY_ENV.relative_to(sandbox_env.REPO_ROOT)} or fly.toml [env].")
        raise SystemExit(2)


def ensure_keys(force: bool) -> None:
    import mint_keys

    public_url = os.environ["SELLER_AGENT_URL"].rstrip("/")
    data, minted = asyncio.run(mint_keys._mint(force, keys_file=KEYS_FILE, base_url=public_url))
    if minted:
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        tmp = KEYS_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
        os.chmod(tmp, 0o600)
        tmp.replace(KEYS_FILE)
    log(f"{'Minted new' if minted else 'Reusing existing'} sandbox API keys in {KEYS_FILE}")
    log(f"  operator          key_id={data['operator']['key_id']}")
    for tier, b in data["buyers"].items():
        log(f"  buyer ({tier:<10}) key_id={b['key_id']}")
    log(f"  secrets are only in that file. Operator: fly ssh console -C 'cat {KEYS_FILE}'")


def serve() -> None:
    import uvicorn

    port = int(os.environ.get("PORT", "8080"))
    sys.stdin = open(os.devnull, encoding="utf-8")  # noqa: SIM115
    log(
        f"Serving mock-only seller on 0.0.0.0:{port}; agent card advertises "
        f"{os.environ['SELLER_AGENT_URL']}; catalog {os.environ['CSV_DATA_DIR']}"
    )
    uvicorn.run(
        "ad_seller.interfaces.api.main:app",
        host="0.0.0.0",
        port=port,
        workers=1,
        proxy_headers=True,
        forwarded_allow_ips="*",
        log_level="info",
    )
    # Same reason as sandbox/run_server.py: straggling CrewAI/chromadb atexit
    # handlers otherwise outlive Fly's kill_timeout on every stop/deploy.
    log("Server stopped.")
    sys.stderr.flush()
    os._exit(0)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="serve", choices=["serve", "rekey", "check"])
    args = parser.parse_args()

    load_fly_env()
    assert_mock_only()

    if args.command == "check":
        from ad_seller.config.settings import Settings

        s = Settings()
        log("mock-only guard passed")
        for field in (
            "seller_agent_url",
            "ad_server_type",
            "csv_data_dir",
            "storage_type",
            "database_url",
            "ssp_connectors",
            "deal_sync_connectors",
            "opendirect_base_url",
            "agent_registry_enabled",
        ):
            log(f"  {field.upper()}={getattr(s, field)!r}")
        log(f"  ANTHROPIC_API_KEY {'set' if s.anthropic_api_key else 'not set'}")
        return

    ensure_keys(force=args.command == "rekey")
    if args.command == "serve":
        serve()


if __name__ == "__main__":
    main()
