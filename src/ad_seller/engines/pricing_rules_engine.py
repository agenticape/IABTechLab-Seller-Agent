# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Pricing Rules Engine - Tiered pricing by buyer identity.

Evaluates pricing based on:
- Buyer authentication level (public, seat, agency, advertiser)
- Historical relationship (total spend, deal count, recency)
- Advertiser-level incentives (cross-agency consistency)
- Volume commitments and loyalty tiers
"""

from typing import Any, Optional

from ..models.buyer_identity import AccessTier, BuyerContext
from ..models.core import DealType, PricingModel
from ..models.flow_state import PricingDecision
from ..models.pricing_tiers import (
    DiscountType,
    PricingRule,
    TieredPricingConfig,
)


class PricingRulesEngine:
    """Engine for computing tiered pricing based on buyer identity.

    Key behaviors:
    - Same advertiser through different agencies gets consistent pricing
    - Agencies without advertiser reveal get agency-level pricing only
    - Public gets ranges, not fixed prices
    - Revealing more identity unlocks better deals

    Example:
        engine = PricingRulesEngine(config)
        decision = engine.calculate_price(
            product_id="ctv-premium",
            base_price=35.0,
            buyer_context=buyer_context,
            volume=5000000,
        )
    """

    def __init__(self, config: TieredPricingConfig) -> None:
        """Initialize the pricing rules engine.

        Args:
            config: Tiered pricing configuration
        """
        self._config = config

    @property
    def config(self) -> TieredPricingConfig:
        """Get the pricing configuration."""
        return self._config

    def calculate_price(
        self,
        product_id: str,
        base_price: float,
        buyer_context: Optional[BuyerContext] = None,
        deal_type: DealType = DealType.PREFERRED_DEAL,
        volume: int = 0,
        inventory_type: Optional[str] = None,
        floor_price: Optional[float] = None,
    ) -> PricingDecision:
        """Calculate the final price for a buyer.

        Args:
            product_id: Product being priced
            base_price: Base/MSRP price (CPM)
            buyer_context: Buyer identity context
            deal_type: Type of deal being created
            volume: Number of impressions
            inventory_type: Type of inventory
            floor_price: The product's own floor CPM. Stacked tier, rule and
                volume discounts never take the final price below
                ``max(floor_price, global_floor_cpm)``.

        Returns:
            PricingDecision with final price and rationale
        """
        # Determine effective tier
        if buyer_context:
            tier = buyer_context.effective_tier
        else:
            tier = AccessTier.PUBLIC

        tier_config = self._config.get_tier_config(tier)

        # Start with base price
        price = base_price
        applied_rules: list[str] = []

        # Apply tier discount
        tier_discount = tier_config.tier_discount
        if tier_discount > 0:
            price = price * (1 - tier_discount)
            applied_rules.append(f"Tier discount: -{tier_discount * 100:.0f}%")

        # Find and apply matching pricing rules
        matching_rules = self._config.find_matching_rules(
            tier=tier,
            agency_id=buyer_context.identity.agency_id if buyer_context else None,
            advertiser_id=buyer_context.identity.advertiser_id if buyer_context else None,
            holding_company=buyer_context.identity.agency_holding_company
            if buyer_context
            else None,
            product_id=product_id,
            inventory_type=inventory_type,
        )

        rule_discount = 0.0
        override_applied = False
        for rule in matching_rules:
            if rule.base_price_override is not None:
                price = rule.base_price_override
                applied_rules.append(
                    f"Rule '{rule.rule_name}': Price override ${rule.base_price_override}"
                )
                override_applied = True
                break  # Price override takes precedence, stop evaluating rules

            if rule.discount_percentage > 0:
                rule_discount = max(rule_discount, rule.discount_percentage)

        # A higher-priority rule's discount can already be sitting in
        # rule_discount by the time we hit the override below it in the
        # list — don't let it sneak back in and knock more off an override
        # price that's supposed to be the final word. Reset it outright
        # rather than just skipping the price multiply: rule_discount also
        # feeds the rationale string below, and a leftover value there
        # would have the rationale claim a discount that was never
        # actually applied to the override price.
        if override_applied:
            rule_discount = 0.0
        elif rule_discount > 0:
            price = price * (1 - rule_discount)
            applied_rules.append(f"Rule discount: -{rule_discount * 100:.0f}%")

        # Apply volume discounts if enabled and volume specified
        volume_discount = 0.0
        if tier_config.volume_discounts_enabled and volume > 0:
            volume_discount = self._calculate_volume_discount(volume, matching_rules)
            if volume_discount > 0:
                price = price * (1 - volume_discount)
                applied_rules.append(f"Volume discount: -{volume_discount * 100:.1f}%")

        # Enforce the floor: the product's own floor when it has one, never
        # below the global floor. Discounts stack multiplicatively, so a high
        # tier plus a large volume commitment can otherwise undercut it.
        floor_capped = False
        if floor_price is not None and floor_price > self._config.global_floor_cpm:
            if price < floor_price:
                price = floor_price
                floor_capped = True
                applied_rules.append(f"Product floor enforced: ${floor_price}")
        elif price < self._config.global_floor_cpm:
            price = self._config.global_floor_cpm
            floor_capped = True
            applied_rules.append(f"Floor enforced: ${self._config.global_floor_cpm}")

        # Enforce ceiling if set
        if self._config.global_ceiling_cpm and price > self._config.global_ceiling_cpm:
            price = self._config.global_ceiling_cpm
            applied_rules.append(f"Ceiling enforced: ${self._config.global_ceiling_cpm}")

        # Build rationale
        rationale = self._build_rationale(
            base_price=base_price,
            final_price=price,
            tier=tier,
            tier_discount=tier_discount,
            rule_discount=rule_discount,
            volume_discount=volume_discount,
            buyer_context=buyer_context,
            floor_capped=floor_capped,
        )

        return PricingDecision(
            product_id=product_id,
            deal_type=deal_type,
            buyer_tier=tier.value,
            buyer_identity=buyer_context.identity.model_dump() if buyer_context else None,
            base_price=base_price,
            tier_discount=tier_discount,
            volume_discount=volume_discount,
            final_price=round(price, 2),
            currency=self._config.default_currency,
            pricing_model=PricingModel.CPM,
            rationale=rationale,
            applied_rules=applied_rules,
        )

    def apply_target_price(
        self,
        decision: PricingDecision,
        target_price: float,
        buyer_context: Optional[BuyerContext] = None,
        floor_price: Optional[float] = None,
    ) -> PricingDecision:
        """Honour a buyer's target price only where the buyer's tier allows it.

        - At or above the waterfall price (``decision.final_price``) the
          target is the buyer's own price and is honoured for any tier.
        - Below it, only negotiation-enabled tiers may land the target, and
          only down to ``waterfall x (1 - max negotiation discount)`` (the
          matching rules' ``max_negotiation_discount``, else 10%, the same
          default ``is_price_acceptable`` uses).
        - Never below ``max(floor_price, global_floor_cpm)``.

        The returned decision's rationale states the price actually quoted.
        """
        tier = buyer_context.effective_tier if buyer_context else AccessTier.PUBLIC
        tier_config = self._config.get_tier_config(tier)
        hard_floor = max(floor_price or 0.0, self._config.global_floor_cpm)
        waterfall = decision.final_price

        if target_price >= waterfall:
            accepted = True
        elif tier_config.negotiation_enabled:
            rules = self._config.find_matching_rules(
                tier=tier,
                agency_id=buyer_context.identity.agency_id if buyer_context else None,
                advertiser_id=buyer_context.identity.advertiser_id if buyer_context else None,
            )
            max_negotiation = max(
                (r.max_negotiation_discount for r in rules if r.negotiation_enabled),
                default=0.10,
            )
            accepted = target_price >= max(hard_floor, waterfall * (1 - max_negotiation))
        else:
            accepted = False

        final = round(target_price, 2) if accepted else waterfall
        if self._config.global_ceiling_cpm and final > self._config.global_ceiling_cpm:
            final = self._config.global_ceiling_cpm
        final = max(final, hard_floor)

        parts = [
            p
            for p in decision.rationale.split(" | ")
            if not p.startswith("Final price:") and not p.startswith("(Total savings")
        ]
        if accepted:
            parts.append(f"Buyer target ${target_price:.2f} CPM accepted")
        else:
            parts.append(f"Buyer target ${target_price:.2f} CPM not available at this tier")
        parts.append(f"Final price: ${final:.2f} CPM")
        applied = [
            *decision.applied_rules,
            f"Target price {'accepted' if accepted else 'declined'}: ${target_price:.2f}",
        ]
        return decision.model_copy(
            update={"final_price": final, "rationale": " | ".join(parts), "applied_rules": applied}
        )

    def _calculate_volume_discount(
        self,
        volume: int,
        rules: list[PricingRule],
    ) -> float:
        """Calculate volume discount based on impression count.

        Args:
            volume: Number of impressions
            rules: Applicable pricing rules with volume discounts

        Returns:
            Discount percentage (0-1)
        """
        max_discount = 0.0

        # Check volume discounts from rules
        for rule in rules:
            for vd in rule.volume_discounts:
                if volume >= vd.min_impressions:
                    if vd.max_impressions is None or volume <= vd.max_impressions:
                        if vd.discount_type == DiscountType.PERCENTAGE:
                            max_discount = max(max_discount, vd.discount_value)

        # Default volume tiers if no rules
        if max_discount == 0.0:
            if volume >= 50_000_000:
                max_discount = 0.20
            elif volume >= 20_000_000:
                max_discount = 0.15
            elif volume >= 10_000_000:
                max_discount = 0.10
            elif volume >= 5_000_000:
                max_discount = 0.05

        return max_discount

    def _build_rationale(
        self,
        base_price: float,
        final_price: float,
        tier: AccessTier,
        tier_discount: float,
        rule_discount: float,
        volume_discount: float,
        buyer_context: Optional[BuyerContext],
        floor_capped: bool = False,
    ) -> str:
        """Build human-readable pricing rationale.

        Args:
            base_price: Starting price
            final_price: Calculated final price
            tier: Buyer's access tier
            tier_discount: Applied tier discount
            rule_discount: Applied rule discount
            volume_discount: Applied volume discount
            buyer_context: Buyer identity context
            floor_capped: Whether the floor clamp raised the price. The
                rationale is buyer-facing, so it says the discounts were
                capped without naming the floor or its value.

        Returns:
            Human-readable rationale string
        """
        parts = [f"Base price: ${base_price:.2f} CPM"]

        if tier_discount > 0:
            parts.append(f"{tier.value.title()} tier: -{tier_discount * 100:.0f}%")

        if rule_discount > 0:
            parts.append(f"Custom rule: -{rule_discount * 100:.0f}%")

        if volume_discount > 0:
            parts.append(f"Volume discount: -{volume_discount * 100:.1f}%")

        if floor_capped:
            parts.append("Discounts capped by seller pricing policy")

        total_discount = 1 - (final_price / base_price) if base_price > 0 else 0
        parts.append(f"Final price: ${final_price:.2f} CPM")

        if total_discount > 0:
            parts.append(f"(Total savings: {total_discount * 100:.1f}%)")

        return " | ".join(parts)

    def get_price_display(
        self,
        base_price: float,
        buyer_context: Optional[BuyerContext] = None,
    ) -> dict[str, Any]:
        """Get price display appropriate for buyer's tier.

        Public tier sees ranges; authenticated tiers see exact prices.

        Args:
            base_price: Base price to display
            buyer_context: Buyer identity context

        Returns:
            Price display dict with appropriate format
        """
        if buyer_context:
            tier = buyer_context.effective_tier
        else:
            tier = AccessTier.PUBLIC

        tier_config = self._config.get_tier_config(tier)

        if tier_config.show_exact_price:
            # Show exact price with tier discount
            discounted_price = base_price * (1 - tier_config.tier_discount)
            return {
                "type": "exact",
                "price": round(discounted_price, 2),
                "currency": self._config.default_currency,
                "negotiation_enabled": tier_config.negotiation_enabled,
            }
        else:
            # Show range for public tier
            variance = tier_config.price_range_variance
            low = base_price * (1 - variance)
            high = base_price * (1 + variance)
            return {
                "type": "range",
                "low": round(low, 0),
                "high": round(high, 0),
                "currency": self._config.default_currency,
                "display": f"${low:.0f}-${high:.0f} CPM",
            }

    def is_price_acceptable(
        self,
        offered_price: float,
        product_floor: float,
        buyer_context: Optional[BuyerContext] = None,
    ) -> tuple[bool, str]:
        """Check if an offered price is acceptable.

        Args:
            offered_price: Price offered by buyer
            product_floor: Product's floor price
            buyer_context: Buyer identity context

        Returns:
            Tuple of (acceptable, reason)
        """
        # Check against global floor
        if offered_price < self._config.global_floor_cpm:
            return False, f"Below global floor (${self._config.global_floor_cpm} CPM)"

        # Check against product floor
        if offered_price < product_floor:
            return False, f"Below product floor (${product_floor} CPM)"

        # Additional checks based on tier
        if buyer_context:
            tier = buyer_context.effective_tier
            tier_config = self._config.get_tier_config(tier)

            # Check negotiation limits
            if tier_config.negotiation_enabled:
                # Allow some negotiation below tier price
                rules = self._config.find_matching_rules(
                    tier=tier,
                    agency_id=buyer_context.identity.agency_id,
                    advertiser_id=buyer_context.identity.advertiser_id,
                )
                max_negotiation = max(
                    (r.max_negotiation_discount for r in rules if r.negotiation_enabled),
                    default=0.10,
                )
                min_acceptable = product_floor * (1 - max_negotiation)
                if offered_price < min_acceptable:
                    return False, f"Below negotiation floor (${min_acceptable:.2f} CPM)"

        return True, "Price acceptable"
