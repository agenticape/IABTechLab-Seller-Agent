"""``target_cpm`` on a quote is honoured only where the buyer's tier allows.

``create_quote`` accepted any ``target_cpm`` at or above the product floor
for every tier, including keyless PUBLIC callers, so anyone could quote
(and book) at the floor; the rationale still described the waterfall price.
A target is now honoured at or above the verified buyer's waterfall price,
or within the negotiation band for negotiation-enabled tiers, never below
the floor, and the rationale names the price actually quoted.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from httpx import ASGITransport

from ad_seller.interfaces.api.main import _get_optional_api_key_record, app
from ad_seller.models.buyer_identity import BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition

# 35.00 list, 28.00 floor. Tier prices: public 35.00, seat 33.25,
# agency 31.50, advertiser 29.75. Negotiation band (10%): agency down to
# 28.35, advertiser down to the 28.00 floor.
BASE, FLOOR = 35.0, 28.0


def _catalog():
    product = ProductDefinition(
        product_id="ctv-1",
        name="CTV",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=BASE,
        floor_cpm=FLOOR,
        minimum_impressions=100_000,
    )
    return {"products": {"ctv-1": product}, "inventory_types": ["ctv"]}


IDENTITIES = {
    "public": None,
    "seat": BuyerIdentity(seat_id="seat-1"),
    "agency": BuyerIdentity(agency_id="agency-1"),
    "advertiser": BuyerIdentity(agency_id="agency-1", advertiser_id="adv-1"),
}


@pytest.fixture
def storage():
    store = {}
    s = AsyncMock()
    s.get = AsyncMock(side_effect=lambda k: store.get(k))
    s.set = AsyncMock(side_effect=lambda k, v, ttl=None: store.__setitem__(k, v))
    s.get_quote = AsyncMock(side_effect=lambda q: store.get(f"quote:{q}"))
    s.set_quote = AsyncMock(side_effect=lambda q, d, ttl=86400: store.__setitem__(f"quote:{q}", d))
    s._store = store
    return s


async def _quote(storage, tier, target):
    identity = IDENTITIES[tier]
    record = MagicMock(identity=identity) if identity else None
    app.dependency_overrides[_get_optional_api_key_record] = lambda: record
    try:
        with (
            patch(
                "ad_seller.interfaces.api.main._get_static_product_catalog",
                new=AsyncMock(return_value=_catalog()),
            ),
            patch("ad_seller.storage.factory.get_storage", return_value=storage),
            patch(
                "ad_seller.services.rate_card_service.get_rate_card_entries",
                new=AsyncMock(return_value=None),
            ),
        ):
            async with httpx.AsyncClient(
                transport=ASGITransport(app=app), base_url="http://test"
            ) as c:
                resp = await c.post(
                    "/api/v1/quotes",
                    json={
                        "idempotency_key": f"idem-{tier}-{target}",
                        "product_id": "ctv-1",
                        "deal_type": "PD",
                        "impressions": 1_000_000,
                        "target_cpm": {
                            "amount_micros": int(round(target * 1_000_000)),
                            "currency": "USD",
                        },
                    },
                )
    finally:
        app.dependency_overrides.clear()
    assert resp.status_code == 200, resp.text
    pricing = resp.json()["quote"]["pricing"]
    return pricing["final_cpm"]["amount_micros"] / 1_000_000, pricing["rationale"]


@pytest.mark.parametrize(
    "tier, target, expected",
    [
        ("public", FLOOR, 35.0),  # keyless caller can no longer quote at the floor
        ("public", 36.0, 36.0),  # at/above its own price: honoured
        ("seat", 30.0, 33.25),  # seat tier does not negotiate
        ("agency", 29.0, 29.0),  # inside the agency band
        ("agency", 28.10, 31.5),  # below the agency band
        ("advertiser", FLOOR, FLOOR),  # band reaches the floor
        ("advertiser", 27.0, 29.75),  # never below the floor
    ],
)
async def test_target_cpm_policy(storage, tier, target, expected):
    final, rationale = await _quote(storage, tier, target)

    assert final == pytest.approx(expected)
    assert f"Final price: ${expected:.2f} CPM" in rationale
    assert rationale.count("Final price:") == 1


async def test_declined_target_is_explained_and_recorded(storage):
    final, rationale = await _quote(storage, "public", FLOOR)

    assert "not available at this tier" in rationale
    history = next(v for k, v in storage._store.items() if k.startswith("quote_history:"))
    assert history["quoted_cpm"] == final == 35.0
