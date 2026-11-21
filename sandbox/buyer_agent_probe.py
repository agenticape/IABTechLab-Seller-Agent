"""Scenario 6 helper: drive the sandbox seller with the IAB buyer-agent's code.

Runs INSIDE a local buyer-agent clone's environment (scenarios.py launches it
with ``uv run --project <buyer-agent>``), so it imports ``ad_buyer`` and uses
the buyer's own wire client (``ad_buyer.clients.deals_client.DealsClient``)
for discovery, quote and booking. It talks only to SANDBOX_SELLER_URL.

Each result line is printed as ``PROBE <STATUS> <message>`` for scenarios.py.
"""

from __future__ import annotations

import asyncio
import os
import sys

import httpx


def emit(status: str, message: str) -> None:
    print(f"PROBE {status} {message}", flush=True)


async def main() -> int:
    seller = os.environ["SANDBOX_SELLER_URL"]
    api_key = os.environ["SANDBOX_BUYER_API_KEY"]
    run_id = os.environ.get("SANDBOX_RUN_ID", "probe")

    try:
        from ad_buyer.clients.deals_client import DealsClient
        from ad_buyer.models.deals import DealBookingRequest, QuoteRequest
    except ImportError as exc:
        emit("FAIL", f"cannot import ad_buyer from this environment: {exc}")
        return 1
    import ad_buyer

    emit("INFO", f"ad_buyer {getattr(ad_buyer, '__version__', '?')} from {ad_buyer.__file__}")

    async with httpx.AsyncClient(base_url=seller, timeout=30) as http:
        card = (await http.get("/.well-known/agent.json")).json()
        emit("PASS", f"buyer fetched the seller agent card: {card.get('name')}")
        products = (await http.get("/products", params={"limit": 100})).json()["products"]
        video = [p for p in products if "PD" in p.get("ext", {}).get("deal_types", [])]
        if not video:
            emit("FAIL", "no PD-capable product in the seller catalog")
            return 1
        target = video[0]
        emit("PASS", f"buyer discovered {len(products)} products; picked {target['product_id']}")

    async with DealsClient(seller, api_key=api_key) as client:
        quote = await client.request_quote(
            QuoteRequest(
                product_id=target["product_id"],
                deal_type="PD",
                impressions=750_000,
                flight_start="2027-04-01",
                flight_end="2027-04-30",
            )
        )
        emit(
            "PASS",
            f"DealsClient.request_quote -> {quote.quote_id} at "
            f"${quote.pricing.final_cpm:.2f} CPM (tier={quote.buyer_tier})",
        )
        deal = await client.book_deal(
            DealBookingRequest(quote_id=quote.quote_id, notes=f"sandbox run {run_id}")
        )
        emit("PASS", f"DealsClient.book_deal -> Deal ID {deal.deal_id} status={deal.status}")
        ortb = deal.openrtb_params
        if ortb is not None:
            emit("INFO", f"buyer parsed OpenRTB params: {ortb.model_dump(exclude_none=True)}")
        if abs((deal.pricing.final_cpm or 0) - (quote.pricing.final_cpm or 0)) < 0.005:
            emit("PASS", "buyer-side deal price matches its quote")
        else:
            emit("FAIL", f"deal ${deal.pricing.final_cpm} != quote ${quote.pricing.final_cpm}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
