"""Mint (or reuse) the sandbox operator key and fictional buyer keys.

Writes them to sandbox/.local/keys.json (git-ignored) and prints only the
file path and key ids, never the secrets. Idempotent: if every key in the
file still validates against the sandbox SQLite DB, nothing is minted.

Usage (from the repo root):  uv run python sandbox/mint_keys.py [--force]

This writes straight to storage, the same bootstrap path as
``ad-seller create-operator-key``, so the server does not need to be running.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import sandbox_env

LABEL_PREFIX = "sandbox-"

# Fictional buyer identities, one per pricing tier. The tier is derived from
# which ids are present (see BuyerIdentity.access_tier): seat_id -> seat,
# agency_id -> agency, agency_id + advertiser_id -> advertiser.
BUYERS: dict[str, dict[str, Any]] = {
    "seat": {
        "seat_id": "seat-sandbox-dsp-001",
        "seat_name": "Sandbox DSP Seat (fictional)",
        "dsp_platform": "sandbox-dsp",
    },
    "agency": {
        "agency_id": "agency-sandbox-001",
        "agency_name": "Sandbox Media Agency (fictional)",
    },
    "advertiser": {
        "agency_id": "agency-sandbox-001",
        "agency_name": "Sandbox Media Agency (fictional)",
        "advertiser_id": "adv-sandbox-001",
        "advertiser_name": "Sandbox Outdoor Co (fictional)",
        # OpenRTB allow-lists stamped on this buyer's deals (wseat / wadomain)
        "seat_ids": ["seat-sandbox-dsp-001"],
        "advertiser_domains": ["sandbox-outdoor.example"],
    },
}


async def _existing_keys_valid(service, data: dict) -> bool:
    keys = [data.get("operator", {}).get("api_key")]
    keys += [b.get("api_key") for b in data.get("buyers", {}).values()]
    if not all(keys) or set(data.get("buyers", {})) != set(BUYERS):
        return False
    if any(data["buyers"][tier].get("identity") != ident for tier, ident in BUYERS.items()):
        return False
    for key in keys:
        try:
            if await service.validate_key(key) is None:
                return False
        except ValueError:
            return False
    return True


async def _mint(
    force: bool,
    keys_file: Path = sandbox_env.KEYS_FILE,
    base_url: str = sandbox_env.BASE_URL,
) -> tuple[dict, bool]:
    from ad_seller.auth.api_key_service import ApiKeyService
    from ad_seller.models.api_key import ApiKeyCreateRequest, OperatorApiKeyCreateRequest
    from ad_seller.storage.factory import close_storage, get_storage

    try:
        storage = await get_storage()
        service = ApiKeyService(storage)

        if not force and keys_file.exists():
            data = json.loads(keys_file.read_text(encoding="utf-8"))
            if await _existing_keys_valid(service, data):
                return data, False

        # Retire any earlier sandbox keys so labels stay unique and stale
        # copies of keys.json stop working.
        for info in await service.list_keys():
            if info.is_active and (info.label or "").startswith(LABEL_PREFIX):
                await service.revoke_key(info.key_id)

        op = await service.create_operator_key(
            OperatorApiKeyCreateRequest(label=f"{LABEL_PREFIX}operator")
        )
        data = {
            "base_url": base_url,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "operator": {"key_id": op.key_id, "api_key": op.api_key},
            "buyers": {},
        }
        for tier, ident in BUYERS.items():
            resp = await service.create_key(
                ApiKeyCreateRequest(label=f"{LABEL_PREFIX}buyer-{tier}", **ident)
            )
            data["buyers"][tier] = {
                "key_id": resp.key_id,
                "api_key": resp.api_key,
                "tier": tier,
                "identity": ident,
            }
        return data, True
    finally:
        await close_storage()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--force", action="store_true", help="revoke and re-mint all keys")
    args = parser.parse_args()

    sandbox_env.load_sandbox_env()
    sandbox_env.assert_mock_only()

    data, minted = asyncio.run(_mint(args.force))
    if minted:
        sandbox_env.LOCAL_DIR.mkdir(parents=True, exist_ok=True)
        sandbox_env.KEYS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        if os.name == "posix":
            os.chmod(sandbox_env.KEYS_FILE, 0o600)

    rel = sandbox_env.KEYS_FILE.relative_to(sandbox_env.REPO_ROOT)
    print(f"{'Minted new' if minted else 'Reusing existing'} sandbox API keys -> {rel}")
    print(f"  operator          key_id={data['operator']['key_id']}")
    for tier, b in data["buyers"].items():
        print(f"  buyer ({tier:<10}) key_id={b['key_id']}")
    print("  (secrets are only in that file; it is git-ignored)")


if __name__ == "__main__":
    main()
