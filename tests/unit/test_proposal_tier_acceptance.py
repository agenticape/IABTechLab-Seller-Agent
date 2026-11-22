"""Proposal acceptance is tier-aware.

``ProposalHandlingFlow.evaluate_pricing`` accepted any price at or above
the product floor, so every buyer, including keyless PUBLIC callers, could
close a proposal at the floor instead of its tier price. A proposal is now
acceptable only at or above the verified buyer's waterfall price (the same
pricing the quote path uses), with the product floor as a hard minimum; a
lower offer is countered, even when the review crew recommends accepting.
The approval gate values a proposal at max(requested, list) price.
"""

import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

_broken_flows = ["ad_seller.flows.execution_activation_flow"]
for _mod_name in _broken_flows:
    if _mod_name not in sys.modules:
        _stub = ModuleType(_mod_name)
        _cls_name = _mod_name.rsplit(".", 1)[-1].replace("_", " ").title().replace(" ", "")
        setattr(_stub, _cls_name, type(_cls_name, (), {}))
        sys.modules[_mod_name] = _stub

from ad_seller.flows.proposal_handling_flow import ProposalHandlingFlow  # noqa: E402
from ad_seller.models.buyer_identity import BuyerContext, BuyerIdentity  # noqa: E402
from ad_seller.models.core import DealType, PricingModel  # noqa: E402
from ad_seller.models.flow_state import (  # noqa: E402
    ProductDefinition,
    ProposalDecision,
    ProposalEvaluation,
    ProposalReviewOutput,
)

# 35.00 list. Tier prices: public 35.00, agency 31.50, advertiser 29.75.
BASE = 35.0


def _product(floor_cpm=20.0):
    return ProductDefinition(
        product_id="ctv-1",
        name="CTV",
        inventory_type="ctv",
        supported_deal_types=[DealType.PREFERRED_DEAL],
        supported_pricing_models=[PricingModel.CPM],
        base_cpm=BASE,
        floor_cpm=floor_cpm,
        minimum_impressions=100_000,
    )


def _context(tier):
    identity = {
        "public": BuyerIdentity(),
        "agency": BuyerIdentity(agency_id="agency-1"),
        "advertiser": BuyerIdentity(agency_id="agency-1", advertiser_id="adv-1"),
    }[tier]
    return BuyerContext(
        identity=identity,
        is_authenticated=tier != "public",
        authentication_method="api_key" if tier != "public" else None,
    )


def _settings(budget):
    return SimpleNamespace(
        proposal_flow_time_budget_seconds=budget,
        proposal_crew_min_budget_seconds=120.0,
        approval_gate_enabled=False,
        approval_required_flows="",
        approval_required_above_value=0.0,
    )


def _crew(decision):
    result = MagicMock()
    result.pydantic = ProposalReviewOutput(
        decision=ProposalDecision(decision),
        rationale="crew decision",
        audience_summary="none",
    )
    crew = MagicMock()
    crew.kickoff_async = AsyncMock(return_value=result)
    return crew


async def _run(tier, price, floor_cpm=20.0, crew_decision=None):
    # No crew decision: budget below the crew minimum, deterministic path.
    factory = MagicMock(return_value=_crew(crew_decision)) if crew_decision else MagicMock()
    with (
        patch("ad_seller.flows.proposal_handling_flow.create_proposal_review_crew", factory),
        patch(
            "ad_seller.flows.proposal_handling_flow.get_settings",
            return_value=_settings(300.0 if crew_decision else 20.0),
        ),
        patch("ad_seller.flows.proposal_handling_flow.emit_event", new_callable=AsyncMock),
    ):
        return await ProposalHandlingFlow().handle_proposal_async(
            proposal_id="prop-tier",
            proposal_data={
                "product_id": "ctv-1",
                "deal_type": "preferred_deal",
                "price": price,
                "impressions": 1_000_000,
                "start_date": "2027-01-01",
                "end_date": "2027-01-31",
                "buyer_id": "buyer-1",
            },
            buyer_context=_context(tier),
            products={"ctv-1": _product(floor_cpm)},
        )


@pytest.mark.parametrize(
    "tier, price, expected",
    [
        ("public", 25.0, "counter"),  # above floor, below the public price
        ("public", 35.0, "accept"),
        ("agency", 30.0, "counter"),
        ("agency", 31.5, "accept"),
        ("advertiser", 29.75, "accept"),
    ],
)
async def test_acceptance_uses_the_tier_price(tier, price, expected):
    result = await _run(tier, price)
    assert result["recommendation"] == expected
    if expected == "counter":
        assert result["counter_terms"]["proposed_price"] > price


async def test_floor_is_a_hard_minimum_over_the_waterfall():
    # Advertiser waterfall 29.75 sits under a 33.00 floor.
    assert (await _run("advertiser", 32.0, floor_cpm=33.0))["recommendation"] == "counter"
    assert (await _run("advertiser", 33.0, floor_cpm=33.0))["recommendation"] == "accept"


async def test_crew_accept_below_tier_price_is_countered():
    result = await _run("public", 25.0, crew_decision="accept")
    assert result["recommendation"] == "counter"


async def test_crew_accept_at_tier_price_stands():
    result = await _run("public", 35.0, crew_decision="accept")
    assert result["recommendation"] == "accept"


def test_approval_value_uses_the_higher_of_requested_and_list():
    flow = ProposalHandlingFlow()
    flow.state.evaluation = ProposalEvaluation(
        proposal_id="p",
        proposal_line_id="",
        product_id="ctv-1",
        requested_price=10.0,
        minimum_acceptable_price=8.0,
        recommended_price=BASE,
        requested_impressions=2_000_000,
        available_impressions=2_000_000,
        recommendation="counter",
    )
    assert flow._deal_value() == pytest.approx(70_000.0)
    flow.state.evaluation.requested_price = 40.0
    assert flow._deal_value() == pytest.approx(80_000.0)
