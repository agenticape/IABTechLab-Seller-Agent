"""Shared sandbox plumbing: env loading and the mock-only safety guard.

Every sandbox entry point (run_server.py, mint_keys.py, scenarios.py) calls
``load_sandbox_env()`` before importing anything from ``ad_seller`` so the
settings singleton is built from the sandbox config, then
``assert_mock_only()`` to refuse to run against anything real.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from urllib.parse import urlparse

REPO_ROOT = Path(__file__).resolve().parent.parent
SANDBOX_DIR = REPO_ROOT / "sandbox"
LOCAL_DIR = SANDBOX_DIR / ".local"
TEMPLATE_ENV = SANDBOX_DIR / ".env.sandbox"
LOCAL_ENV = LOCAL_DIR / "sandbox.env"
KEYS_FILE = LOCAL_DIR / "keys.json"

HOST = "127.0.0.1"
PORT = 8000
# 127.0.0.1 rather than "localhost": on Windows, "localhost" tries ::1 first
# and a refused IPv6 connect costs ~2s per request before IPv4 fallback.
BASE_URL = f"http://{HOST}:{PORT}"

# Keys whose value always comes from the sandbox env files, even when blank,
# so a value exported in the user's shell can never re-enable a real
# integration. Everything else (e.g. ANTHROPIC_API_KEY) keeps a non-blank
# shell value when the file leaves it blank.
PINNED_KEYS = {
    "AD_SERVER_TYPE",
    "CSV_DATA_DIR",
    "GAM_ENABLED",
    "FREEWHEEL_ENABLED",
    "FREEWHEEL_SH_MCP_URL",
    "FREEWHEEL_BC_MCP_URL",
    "FREEWHEEL_API_URL",
    "S3_DATA_BUCKET",
    "INVENTORY_SYNC_ENABLED",
    "SSP_CONNECTORS",
    "SSP_ROUTING_RULES",
    "PUBMATIC_MCP_URL",
    "MAGNITE_API_URL",
    "INDEX_EXCHANGE_API_URL",
    "DEAL_SYNC_CONNECTORS",
    "DEALS_API_MCP_URL",
    "OPENDIRECT_BASE_URL",
    "AGENT_REGISTRY_ENABLED",
    "AGENT_REGISTRY_URL",
    "AGENT_REGISTRY_EXTRA_URLS",
    "AAMP_REGISTRY_URL",
    "STORAGE_TYPE",
    "DATABASE_URL",
    "REDIS_URL",
    "AUDIT_FALLBACK_PATH",
    "SELLER_AGENT_URL",
}

_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _parse_env_file(path: Path) -> dict[str, str]:
    from dotenv import dotenv_values

    return {k: (v or "") for k, v in dotenv_values(path, encoding="utf-8").items()}


def load_sandbox_env() -> None:
    """Load .env.sandbox, then sandbox/.local/sandbox.env, into os.environ.

    Must run before any ``ad_seller`` import. Also chdirs to the repo root so
    the relative paths in the env files resolve the same on every OS.
    """
    os.chdir(REPO_ROOT)
    values = _parse_env_file(TEMPLATE_ENV)
    if LOCAL_ENV.exists():
        values.update(_parse_env_file(LOCAL_ENV))
    for key, value in values.items():
        if value == "" and key not in PINNED_KEYS:
            continue
        os.environ[key] = value
    # A dangerous key the env files don't mention must not leak in from the
    # shell either (e.g. a REDIS_URL exported for another project).
    for key in PINNED_KEYS - values.keys():
        os.environ[key] = ""


def _is_local_url(url: str) -> bool:
    return (urlparse(url).hostname or "") in _LOCAL_HOSTS


def mock_only_violations() -> list[str]:
    """Return every reason the effective settings are NOT mock-only."""
    from ad_seller.config.settings import Settings

    s = Settings()
    problems: list[str] = []

    if s.ad_server_type != "csv":
        problems.append(f"AD_SERVER_TYPE={s.ad_server_type!r} (sandbox requires 'csv')")
    samples = (REPO_ROOT / "data" / "csv" / "samples").resolve()
    csv_dir = (REPO_ROOT / s.csv_data_dir).resolve()
    if samples not in csv_dir.parents:
        problems.append(f"CSV_DATA_DIR={s.csv_data_dir!r} is outside data/csv/samples/")
    for flag in ("gam_enabled", "freewheel_enabled", "inventory_sync_enabled"):
        if getattr(s, flag):
            problems.append(f"{flag.upper()}=true")
    for field in (
        "freewheel_sh_mcp_url",
        "freewheel_bc_mcp_url",
        "freewheel_api_url",
        "s3_data_bucket",
        "ssp_connectors",
        "ssp_routing_rules",
        "pubmatic_mcp_url",
        "magnite_api_url",
        "index_exchange_api_url",
        "deal_sync_connectors",
        "deals_api_mcp_url",
        "aamp_registry_url",
        "agent_registry_extra_urls",
        "redis_url",
    ):
        if getattr(s, field):
            problems.append(f"{field.upper()} is set ({getattr(s, field)!r}); must be blank")
    for field in ("opendirect_base_url", "agent_registry_url", "seller_agent_url"):
        if not _is_local_url(getattr(s, field)):
            problems.append(f"{field.upper()}={getattr(s, field)!r} is not localhost")
    if s.storage_type != "sqlite" or not s.database_url.startswith("sqlite:///"):
        problems.append(
            f"STORAGE_TYPE={s.storage_type!r} DATABASE_URL={s.database_url!r} "
            "(sandbox requires a local SQLite file)"
        )
    return problems


def assert_mock_only() -> None:
    problems = mock_only_violations()
    if problems:
        print("REFUSING TO RUN: the sandbox config is not mock-only:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        print(
            f"Fix {LOCAL_ENV.relative_to(REPO_ROOT)} (or delete it and re-run setup).",
            file=sys.stderr,
        )
        raise SystemExit(2)
