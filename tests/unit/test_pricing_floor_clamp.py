"""The pricing waterfall never ends below the product floor.

Tier, rule and volume discounts stack multiplicatively. With the default
config an advertiser-tier buyer (15%) committing 50M impressions (20%) on a
$45.00 product was priced at $30.60, under the product's $38.25 floor; only
the $1 global floor was enforced. The engine now clamps the final price to
``max(product floor, global floor)`` and records the clamp.
"""

from unittest.mock import AsyncMock, patch

import pytest

from ad_seller.engines.pricing_rules_engine import PricingRulesEngine
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity
from ad_seller.models.core import DealType, PricingModel
from ad_seller.models.flow_state import ProductDefinition
from ad_seller.models.pricing_tiers import TieredPricingConfig

BASE = 45.0
FLOOR = 38.25


def _advertiser():
    return BuyerContext(
        identity=BuyerIdentity(agency_id="agency-1", advertiser_id="adv-1"),
        is_authenticated=True,
        authentication_method="api_key",
    )


def _engine():
    return PricingRulesEngine(TieredPricingConfig(seller_organization_id="default"))


class TestEngineFloorClamp:
    def test_stacked_discounts_clamped_to_product_floor(self):
        decision = _engine().calculate_price(
            product_id="p1",
            base_price=BASE,
            buyer_context=_advertiser(),
            volume=50_000_000,
            floor_price=FLOOR,
        )

        assert decision.final_price == FLOOR
        assert any("Product floor enforced" in r for r in decision.applied_rules)

    def test_clamp_is_recorded_without_naming_the_floor(self):
        decision = _engine().calculate_price(
            product_id="p1",
            base_price=BASE,
            buyer_context=_advertiser(),
            volume=50_000_000,
            floor_price=FLOOR,
        )

        assert "capped by seller pricing policy" in decision.rationale
        assert "floor" not in decision.rationale.lower()
        assert f"{FLOOR:.2f}" in decision.rationale  # the final price itself

    def test_no_clamp_when_waterfall_stays_above_floor(self):
        decision = _engine().calculate_price(
            product_id="p1",
            base_price=BASE,
            buyer_context=_advertiser(),
            volume=0,
            floor_price=FLOOR,
        )

        assert decision.final_price == pytest.approx(BASE * 0.85)
        assert "capped" not in decision.rationale

    def test_global_floor_still_applies_without_product_floor(self):
        engine = PricingRulesEngine(
            TieredPricingConfig(seller_organization_id="default", global_floor_cpm=40.0)
        )
        decision = engine.calculate_price(
            product_id="p1", base_price=BASE, buyer_context=_advertiser(), volume=50_000_000
        )

        assert decision.final_price == 40.0

    def test_global_floor_wins_when_higher_than_product_floor(self):
        engine = PricingRulesEngine(
            TieredPricingConfig(seller_organization_id="default", global_floor_cpm=40.0)
        )
        decision = engine.calculate_price(
            product_id="p1",
            base_price=BASE,
            buyer_context=_advertiser(),
            volume=50_000_000,
            floor_price=FLOOR,
        )

        assert decision.final_price == 40.0


class TestQuotePricingUsesProductFloor:
    async def test_get_pricing_clamps_to_product_floor(self):
        from ad_seller.services import quote_service

        product = ProductDefinition(
            product_id="p1",
            name="P1",
            inventory_type="ctv",
            supported_deal_types=[DealType.PREFERRED_DEAL],
            supported_pricing_models=[PricingModel.CPM],
            base_cpm=BASE,
            floor_cpm=FLOOR,
        )
        with patch(
            "ad_seller.services.rate_card_service.get_rate_card_entries",
            new=AsyncMock(return_value=None),
        ):
            pricing = await quote_service.get_pricing(
                product_id="p1", product=product, buyer_context=_advertiser(), volume=50_000_000
            )

        assert pricing["final_price"] == FLOOR
