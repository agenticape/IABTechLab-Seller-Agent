"""Scripted mock-buyer scenarios against the local sandbox seller.

Start the server first (sandbox/run.ps1 or sandbox/run.sh), then from the
repo root:

    uv run python sandbox/scenarios.py                # scenarios 1-5
    uv run python sandbox/scenarios.py --only 3,4     # a subset
    uv run python sandbox/scenarios.py --buyer-agent ../buyer-agent   # + scenario 6
    uv run python sandbox/scenarios.py --verbose      # dump request/response bodies

Result tags:
    PASS  the seller behaved as expected
    FAIL  the seller (or the sandbox) is broken; exit code is 1
    WARN  current upstream behaviour a sell-side tester should look at
          (usually a known upstream issue); does not fail the run unless --strict
    INFO  context worth reading (ids, prices, activation params)
    SKIP  scenario not run (e.g. scenario 6 without --buyer-agent)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

import httpx
import sandbox_env

# CSV catalogs set each product's floor to 85% of the CSV price
# (catalog_service.product_from_inventory_item). Buyer-facing surfaces never
# expose it, so the runner re-derives it to check prices against it.
FLOOR_RATIO = 0.85

# PricingRulesEngine default tier discounts (the aws_workshop rate_card.json
# is not read by the pricing path; see SANDBOX.md).
EXPECTED_TIER_DISCOUNT = {"public": 0.0, "seat": 0.05, "agency": 0.10, "advertiser": 0.15}

# Must match APPROVAL_REQUIRED_ABOVE_VALUE in sandbox/.env.sandbox.
APPROVAL_THRESHOLD = 250_000

RUN_ID = uuid.uuid4().hex[:8]


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------

_COLORS = {"PASS": "32", "FAIL": "31", "WARN": "33", "INFO": "36", "SKIP": "90"}
_use_color = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
if _use_color and os.name == "nt":
    os.system("")  # enables ANSI escape processing in Windows consoles


def _tag(status: str) -> str:
    text = f"[{status}]"
    return f"\033[{_COLORS[status]}m{text}\033[0m" if _use_color else text


@dataclass
class Result:
    status: str
    message: str


@dataclass
class Scenario:
    number: int
    title: str
    results: list[Result] = field(default_factory=list)

    def record(self, status: str, message: str) -> None:
        self.results.append(Result(status, message))
        print(f"  {_tag(status)} {message}")

    def check(self, ok: bool, message: str, detail: str = "") -> bool:
        self.record(
            "PASS" if ok else "FAIL", message if ok or not detail else f"{message} -- {detail}"
        )
        return ok

    def warn(self, message: str) -> None:
        self.record("WARN", message)

    def info(self, message: str) -> None:
        self.record("INFO", message)

    def skip(self, message: str) -> None:
        self.record("SKIP", message)


class ScenarioAbort(Exception):
    """Stop the current scenario after a FAIL that later steps depend on."""


# ----------------------------------------------------------------------------
# HTTP helpers
# ----------------------------------------------------------------------------


@dataclass
class Ctx:
    client: httpx.Client
    keys: dict[str, Any]
    verbose: bool
    products: dict[str, dict] = field(default_factory=dict)

    def key(self, who: str) -> Optional[str]:
        if who == "public":
            return None
        if who == "operator":
            return self.keys["operator"]["api_key"]
        return self.keys["buyers"][who]["api_key"]

    def call(
        self,
        method: str,
        path: str,
        who: str = "public",
        body: Any = None,
        raw_key: Optional[str] = None,
    ) -> httpx.Response:
        headers = {}
        api_key = raw_key if raw_key is not None else self.key(who)
        if api_key:
            headers["X-Api-Key"] = api_key
        resp = self.client.request(method, path, json=body, headers=headers)
        if self.verbose:
            print(f"      > {method} {path} as {who}" + (f" {json.dumps(body)}" if body else ""))
            print(f"      < {resp.status_code} {resp.text[:800]}")
        return resp

    def floor(self, product_id: str) -> float:
        return round(self.products[product_id]["base_cpm"] * FLOOR_RATIO, 2)


def _money(m: Any) -> Optional[float]:
    if isinstance(m, dict) and "amount_micros" in m:
        return m["amount_micros"] / 1_000_000
    return m


def _micros(value: float) -> dict:
    return {"amount_micros": int(round(value * 1_000_000)), "currency": "USD"}


def _find_keys(obj: Any, pred: Callable[[str], bool], path: str = "") -> list[str]:
    hits: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            p = f"{path}.{k}" if path else k
            if pred(k):
                hits.append(p)
            hits += _find_keys(v, pred, p)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            hits += _find_keys(v, pred, f"{path}[{i}]")
    return hits


def _floor_field_leaks(payload: Any) -> list[str]:
    # openrtb_params.bidfloor is the deal's agreed price (the OpenRTB field
    # name), not the seller's reserve, so it is excluded.
    return [
        p
        for p in _find_keys(payload, lambda k: "floor" in k.lower() or "reserve" in k.lower())
        if not p.endswith("bidfloor")
    ]


def _index_products(ctx: Ctx, products: list[dict]) -> None:
    ctx.products = {
        p["product_id"]: {
            "name": p["name"],
            "base_cpm": _money(p["base_price"]),
            "currency": p["base_price"]["currency"],
            "inventory_type": p["ext"].get("inventory_type"),
            "deal_types": p["ext"].get("deal_types", []),
        }
        for p in products
    }


def _idem(label: str) -> str:
    return f"sandbox-{RUN_ID}-{label}"


# ----------------------------------------------------------------------------
# Scenarios
# ----------------------------------------------------------------------------


def scenario_1_discovery(ctx: Ctx, sc: Scenario) -> None:
    r = ctx.call("GET", "/health")
    sc.check(r.status_code == 200, "GET /health is healthy", r.text)

    r = ctx.call("GET", "/.well-known/agent.json")
    if sc.check(r.status_code == 200, "GET /.well-known/agent.json returns the agent card", r.text):
        card = r.json()
        skills = [s.get("id") for s in card.get("skills", [])]
        sc.info(f"agent '{card.get('name')}' at {card.get('url')} skills={skills}")
        sc.check(
            "localhost" in (card.get("url") or "") or "127.0.0.1" in (card.get("url") or ""),
            "agent card advertises a localhost URL",
            card.get("url", ""),
        )

    r = ctx.call("GET", "/products?limit=500")
    if not sc.check(r.status_code == 200, "GET /products lists the catalog", r.text):
        raise ScenarioAbort
    products = r.json()["products"]
    _index_products(ctx, products)
    sc.check(
        len(products) == 15 and all(pid.startswith("inv-") for pid in ctx.products),
        f"catalog is the fictional aws_workshop CSV ({len(products)} products, ids inv-*)",
        f"got ids {list(ctx.products)[:5]}...",
    )
    for pid, p in list(ctx.products.items())[:15]:
        sc.info(
            f"{pid:<34} {p['inventory_type']:<13} ${p['base_cpm']:>6.2f} {p['currency']} "
            f"deal_types={p['deal_types']}"
        )
    leaks = _floor_field_leaks(r.json())
    sc.check(not leaks, "product list exposes no floor/reserve fields", f"found {leaks}")

    r = ctx.call("GET", "/products/inv-ctv-apex-sports-nba")
    sc.check(
        r.status_code == 200 and r.json().get("product_id") == "inv-ctv-apex-sports-nba",
        "GET /products/{id} returns one product",
        r.text,
    )
    r = ctx.call("GET", "/products/does-not-exist")
    sc.check(r.status_code == 404, "unknown product id is a 404", f"HTTP {r.status_code}")

    body = {
        "productid": "inv-ctv-apex-sports-nba",
        "startdate": "2027-01-01T00:00:00Z",
        "enddate": "2027-01-31T00:00:00Z",
        "requestedImpressions": 2_000_000,
    }
    r = ctx.call("POST", "/products/avails", body=body)
    if sc.check(r.status_code == 200, "POST /products/avails answers for 2M imps", r.text):
        a = r.json()
        sc.info(
            f"avails: available={a.get('availableImpressions')} "
            f"guaranteed={a.get('guaranteedImpressions')} estimatedCpm={a.get('estimatedCpm')} "
            f"totalCost={a.get('totalCost')}"
        )
        sc.check(
            "deliveryConfidence" not in a,
            "avails omit deliveryConfidence (no forecast source, never fabricated)",
        )


def scenario_2_tiered_pricing(ctx: Ctx, sc: Scenario) -> None:
    pid = "inv-ctv-apex-series"
    base = ctx.products[pid]["base_cpm"]
    floor = ctx.floor(pid)
    sc.info(f"product {pid}: list ${base:.2f}, internal floor ${floor:.2f} (never sent to buyers)")

    prices: dict[str, float] = {}
    for tier in ("public", "seat", "agency", "advertiser"):
        r = ctx.call("POST", "/pricing", who=tier, body={"product_id": pid})
        if not sc.check(r.status_code == 200, f"{tier:<10} POST /pricing", r.text):
            continue
        p = r.json()
        prices[tier] = p["final_price"]
        sc.check(
            abs(p["tier_discount"] - EXPECTED_TIER_DISCOUNT[tier]) < 1e-9,
            f"{tier:<10} ${p['final_price']:>6.2f} (tier discount {p['tier_discount']:.0%})",
            f"expected {EXPECTED_TIER_DISCOUNT[tier]:.0%}",
        )
        leaks = _floor_field_leaks(p)
        mentions = "floor" in p.get("rationale", "").lower()
        sc.check(
            not leaks and not mentions,
            f"{tier:<10} response has no floor field and the rationale does not mention it",
            f"fields={leaks} rationale={p.get('rationale')!r}",
        )
        sc.check(
            p["final_price"] >= floor,
            f"{tier:<10} price ${p['final_price']:.2f} >= floor ${floor:.2f}",
        )

    order = [prices.get(t) for t in ("public", "seat", "agency", "advertiser")]
    if None not in order:
        sc.check(
            all(a > b for a, b in zip(order, order[1:])),
            "prices strictly improve public > seat > agency > advertiser",
            str(order),
        )

    # Self-asserted identity without a seller-issued key must floor to public.
    r = ctx.call(
        "POST",
        "/pricing",
        body={
            "product_id": pid,
            "buyer_tier": "advertiser",
            "agency_id": "agency-made-up",
            "advertiser_id": "adv-made-up",
        },
    )
    if r.status_code == 200:
        sc.check(
            r.json()["final_price"] == prices.get("public"),
            "anonymous buyer claiming advertiser tier in the body still gets public price",
            f"got ${r.json()['final_price']}",
        )

    r = ctx.call("POST", "/pricing", body={"product_id": pid}, raw_key="not-a-real-key")
    sc.check(r.status_code == 401, "an invalid API key is rejected (401)", f"HTTP {r.status_code}")

    # Volume discounts stack on top of the tier discount.
    volume = 50_000_000
    r = ctx.call("POST", "/pricing", who="advertiser", body={"product_id": pid, "volume": volume})
    if r.status_code == 200:
        p = r.json()
        msg = (
            f"advertiser + {volume:,} imps volume discount -> ${p['final_price']:.2f} "
            f"(tier {p['tier_discount']:.0%} + volume {p['volume_discount']:.0%})"
        )
        if p["final_price"] < floor:
            sc.warn(
                f"{msg} is BELOW the product floor ${floor:.2f}: the pricing engine only "
                "enforces the global floor (DEFAULT_PRICE_FLOOR_CPM), not the product floor"
            )
        else:
            sc.check(True, msg)


def scenario_3_quote_then_book(ctx: Ctx, sc: Scenario) -> None:
    pid = "inv-dsp-gnn-richmedia"
    quote_body = {
        "idempotency_key": _idem("quote-pd"),
        "product_id": pid,
        "deal_type": "PD",
        "impressions": 1_000_000,
        "flight_start": "2027-01-01",
        "flight_end": "2027-01-31",
    }
    r = ctx.call("POST", "/api/v1/quotes", who="advertiser", body=quote_body)
    if not sc.check(r.status_code == 200, "advertiser POST /api/v1/quotes (PD, 1M imps)", r.text):
        raise ScenarioAbort
    quote = r.json()["quote"]
    qid = quote["quote_id"]
    sc.info(
        f"quote {qid}: ${_money(quote['pricing']['final_cpm']):.2f} CPM "
        f"(list ${_money(quote['pricing']['base_cpm']):.2f}), tier={quote['buyer_tier']}, "
        f"expires {quote['expires_at']}"
    )
    sc.check(not _floor_field_leaks(quote), "quote exposes no floor/reserve fields")

    r = ctx.call("POST", "/api/v1/quotes", who="advertiser", body=quote_body)
    sc.check(
        r.status_code == 200 and r.json()["quote"]["quote_id"] == qid,
        "replaying the same idempotency_key returns the same quote",
        r.text[:200],
    )
    r = ctx.call(
        "POST", "/api/v1/quotes", who="advertiser", body={**quote_body, "impressions": 2_000_000}
    )
    sc.check(
        r.status_code == 409,
        "same idempotency_key with a different body is a 409",
        f"HTTP {r.status_code}",
    )

    book_body = {"idempotency_key": _idem("book-pd"), "quote_id": qid}
    r = ctx.call("POST", "/api/v1/deals", body=book_body)
    sc.check(r.status_code == 401, "anonymous booking is rejected (401)", f"HTTP {r.status_code}")
    r = ctx.call("POST", "/api/v1/deals", who="seat", body=book_body)
    sc.check(
        r.status_code == 403,
        "a different buyer cannot book this advertiser's quote (403)",
        f"HTTP {r.status_code} {r.text[:200]}",
    )

    r = ctx.call("POST", "/api/v1/deals", who="advertiser", body=book_body)
    if not sc.check(r.status_code == 200, "advertiser POST /api/v1/deals books the quote", r.text):
        raise ScenarioAbort
    deal = r.json()["deal"]
    deal_id = deal["deal_id"]
    sc.info(f"Deal ID: {deal_id}  status={deal['status']}  type={deal['deal_type']}")
    sc.check(
        _money(deal["pricing"]["final_cpm"]) == _money(quote["pricing"]["final_cpm"]),
        f"booked CPM ${_money(deal['pricing']['final_cpm']):.2f} equals the quoted CPM",
    )
    ortb = deal.get("openrtb_params") or {}
    sc.info(
        f"OpenRTB activation: pmp.deals[].id={ortb.get('id')} "
        f"bidfloor=${_money(ortb.get('bidfloor')):.2f} at={ortb.get('at')} "
        f"wseat={ortb.get('wseat')} wadomain={ortb.get('wadomain')}"
    )
    for dsp, text in (deal.get("activation_instructions") or {}).items():
        sc.info(f"activation ({dsp}): {text}")
    sc.check(
        deal_id.startswith("DEMO-"),
        "Deal ID is a local DEMO- id (nothing was pushed to a real ad server/SSP)",
        deal_id,
    )

    r = ctx.call("POST", "/api/v1/deals", who="advertiser", body=book_body)
    sc.check(
        r.status_code == 200 and r.json()["deal"]["deal_id"] == deal_id,
        "replaying the booking idempotency_key returns the same Deal ID (no double booking)",
        r.text[:200],
    )
    r = ctx.call(
        "POST",
        "/api/v1/deals",
        who="advertiser",
        body={"idempotency_key": _idem("book-pd-again"), "quote_id": qid},
    )
    sc.check(
        r.status_code in (409, 410, 400),
        "booking the same quote with a NEW key is refused (quote already booked)",
        f"HTTP {r.status_code} {r.text[:200]}",
    )

    r = ctx.call("GET", f"/api/v1/deals/{deal_id}", who="advertiser")
    sc.check(r.status_code == 200, f"GET /api/v1/deals/{deal_id} reads the deal back", r.text[:200])

    # PG: fixed price + guaranteed terms should not run as a first-price auction.
    pg_pid = "inv-ctv-apex-series"
    r = ctx.call(
        "POST",
        "/api/v1/quotes",
        who="advertiser",
        body={
            "idempotency_key": _idem("quote-pg"),
            "product_id": pg_pid,
            "deal_type": "PG",
            "impressions": 500_000,
            "flight_start": "2027-01-01",
            "flight_end": "2027-01-31",
        },
    )
    if sc.check(r.status_code == 200, "advertiser quotes a PG deal on CTV", r.text[:200]):
        pg_qid = r.json()["quote"]["quote_id"]
        r = ctx.call(
            "POST",
            "/api/v1/deals",
            who="advertiser",
            body={"idempotency_key": _idem("book-pg"), "quote_id": pg_qid},
        )
        if sc.check(r.status_code == 200, "PG quote books", r.text[:200]):
            pg = r.json()["deal"]
            at = (pg.get("openrtb_params") or {}).get("at")
            sc.info(
                f"PG Deal ID {pg['deal_id']} guaranteed={pg['terms'].get('guaranteed')} "
                f"openrtb at={at}"
            )
            if at == 1:
                sc.warn("PG deal carries at=1 (first-price auction) - upstream issue #147")
            else:
                sc.check(True, f"PG deal at={at}")


def scenario_4_negotiation(ctx: Ctx, sc: Scenario) -> None:
    pid = "inv-lin-sportspulse-live"
    floor = ctx.floor(pid)
    r = ctx.call(
        "POST",
        "/api/v1/quotes",
        who="agency",
        body={
            "idempotency_key": _idem("quote-neg"),
            "product_id": pid,
            "deal_type": "PD",
            "impressions": 3_000_000,
            "flight_start": "2027-02-01",
            "flight_end": "2027-02-28",
        },
    )
    if not sc.check(
        r.status_code == 200, "agency quotes PD on SportsPulse linear (3M imps)", r.text
    ):
        raise ScenarioAbort
    quote = r.json()["quote"]
    qid = quote["quote_id"]
    quoted = _money(quote["pricing"]["final_cpm"])
    sc.info(f"quote {qid}: ${quoted:.2f} CPM (list ${ctx.products[pid]['base_cpm']:.2f})")

    # Buyer ladder: open below the floor, then step up into the band between
    # the floor and the quote, where the seller's concession logic applies.
    ladder = [round(quoted * f, 2) for f in (0.80, 0.90, 0.955, 0.98)]
    neg_id = None
    agreed: Optional[float] = None
    last_seller: Optional[float] = None
    first_msg: Optional[dict] = None
    for i, offer in enumerate(ladder, start=1):
        msg = {
            "idempotency_key": _idem(f"neg-round-{i}"),
            "action": "counter",
            "quote_id": qid,
            "buyer_price": _micros(offer),
        }
        if neg_id:
            msg["negotiation_id"] = neg_id
        r = ctx.call("POST", "/api/v1/negotiations/messages", who="agency", body=msg)
        if not sc.check(r.status_code == 200, f"round {i}: buyer offers ${offer:.2f}", r.text):
            raise ScenarioAbort
        first_msg = first_msg or msg
        rnd = r.json()
        neg_id = rnd["negotiation_id"]
        seller = _money(rnd["round"]["seller_price"])
        action = rnd["round"]["action"]
        sc.info(
            f"round {rnd['round']['round_number']}: seller {action} at ${seller:.2f} "
            f'(rounds_remaining={rnd.get("rounds_remaining")}) - "{rnd["round"]["rationale"]}"'
        )
        text = rnd["round"]["rationale"].lower()
        sc.check(
            seller is None or seller >= floor,
            f"round {i}: seller price ${seller:.2f} is not below the floor ${floor:.2f}",
        )
        sc.check(
            "floor" not in text and "minimum" not in text and not _floor_field_leaks(rnd),
            f"round {i}: response never names the floor",
            text,
        )
        last_seller = seller
        if action in ("accept", "final_offer") or (seller is not None and seller <= offer):
            agreed = offer if action == "accept" else seller
            break

    if first_msg:
        r = ctx.call("POST", "/api/v1/negotiations/messages", who="agency", body=first_msg)
        sc.check(
            r.status_code == 200 and r.json()["round"]["round_number"] == 1,
            "replaying round 1's idempotency_key returns round 1 (no extra round consumed)",
            r.text[:200],
        )

    if agreed is None:
        agreed = last_seller
    if agreed is None:
        sc.check(False, "negotiation produced an agreed price")
        raise ScenarioAbort
    sc.info(
        "note: a below-floor offer is countered AT the floor by design "
        "(negotiation_engine step 3.5), so a lowballing buyer learns the floor price"
    )

    r = ctx.call(
        "POST",
        "/api/v1/negotiations/messages",
        who="agency",
        body={
            "idempotency_key": _idem("neg-accept"),
            "action": "accept",
            "quote_id": qid,
            "negotiation_id": neg_id,
            "buyer_price": _micros(agreed),
        },
    )
    if not sc.check(
        r.status_code == 200 and r.json().get("status") == "accepted",
        f"buyer accepts at ${agreed:.2f}",
        r.text,
    ):
        raise ScenarioAbort

    r = ctx.call(
        "POST",
        "/api/v1/deals",
        who="agency",
        body={"idempotency_key": _idem("book-neg"), "quote_id": qid},
    )
    if not sc.check(r.status_code == 200, "agency books the negotiated quote", r.text):
        raise ScenarioAbort
    deal = r.json()["deal"]
    booked = _money(deal["pricing"]["final_cpm"])
    sc.info(f"Deal ID {deal['deal_id']}: {deal['pricing']['rationale']}")
    sc.check(
        abs(booked - agreed) < 0.005,
        f"deal booked at the agreed ${agreed:.2f} CPM (quote was ${quoted:.2f})",
        f"booked ${booked:.2f}",
    )
    ortb = deal.get("openrtb_params") or {}
    sc.check(
        abs((_money(ortb.get("bidfloor")) or 0) - agreed) < 0.005,
        f"OpenRTB bidfloor matches the agreed price (${_money(ortb.get('bidfloor')):.2f})",
    )


def _submit_proposal(ctx: Ctx, sc: Scenario, label: str, body: dict) -> Optional[dict]:
    r = ctx.call("POST", "/proposals", who="advertiser", body=body)
    gross = body["price"] * body["impressions"] / 1000
    if not sc.check(
        r.status_code == 200,
        f"{label}: POST /proposals ${body['price']:.2f} x {body['impressions']:,} imps "
        f"= ${gross:,.0f} gross",
        r.text,
    ):
        return None
    return r.json()


def scenario_5_approval_gate(ctx: Ctx, sc: Scenario) -> None:
    sc.info(f"APPROVAL_REQUIRED_ABOVE_VALUE={APPROVAL_THRESHOLD:,} (sandbox/.env.sandbox)")
    ident = ctx.keys["buyers"]["advertiser"]["identity"]
    base = {
        "product_id": "inv-ctv-apex-sports-nba",
        "deal_type": "PG",
        "start_date": "2027-03-01",
        "end_date": "2027-05-31",
        "agency_id": ident["agency_id"],
        "advertiser_id": ident["advertiser_id"],
    }

    small = _submit_proposal(ctx, sc, "control", {**base, "price": 50.0, "impressions": 100_000})
    if small:
        sc.check(
            small["status"] != "pending_approval",
            f"below-threshold proposal is decided without a human (status={small['status']})",
        )

    big = _submit_proposal(
        ctx, sc, "high-value", {**base, "price": 50.0, "impressions": 10_000_000}
    )
    if not big:
        raise ScenarioAbort
    if not sc.check(
        big["status"] == "pending_approval" and big.get("approval_id"),
        f"high-value proposal is held for approval (status={big['status']})",
        json.dumps(big),
    ):
        raise ScenarioAbort
    approval_id = big["approval_id"]
    sc.info(f"proposal {big['proposal_id']} -> approval {approval_id}")
    if not big.get("pricing_verified"):
        sc.info(f"pricing_verified=false: {big.get('pricing_verification_reason')}")

    r = ctx.call("GET", "/approvals")
    sc.check(
        r.status_code == 401, "anonymous GET /approvals is rejected (401)", f"HTTP {r.status_code}"
    )
    r = ctx.call("GET", "/approvals", who="advertiser")
    sc.check(
        r.status_code == 403, "buyer key GET /approvals is forbidden (403)", f"HTTP {r.status_code}"
    )
    r = ctx.call("GET", "/approvals", who="operator")
    pending = (
        [a["approval_id"] for a in r.json().get("approvals", [])] if r.status_code == 200 else []
    )
    sc.check(approval_id in pending, "operator sees the approval in GET /approvals", r.text[:200])

    r = ctx.call(
        "POST",
        f"/approvals/{approval_id}/decide",
        who="advertiser",
        body={"decision": "approve", "decided_by": "buyer-trying-to-self-approve"},
    )
    sc.check(
        r.status_code == 403,
        "a buyer cannot approve its own proposal (403)",
        f"HTTP {r.status_code}",
    )

    r = ctx.call(
        "POST",
        f"/approvals/{approval_id}/decide",
        who="operator",
        body={"decision": "approve", "decided_by": "sandbox-operator", "reason": "scenario 5"},
    )
    sc.check(r.status_code == 200, "operator approves", r.text[:200])
    r = ctx.call("POST", f"/approvals/{approval_id}/resume", who="operator")
    ok = r.status_code == 200 and r.json().get("status") == "accepted"
    sc.check(ok, "resumed flow finalises the proposal as accepted", r.text[:300])

    big2 = _submit_proposal(
        ctx, sc, "high-value #2", {**base, "price": 45.0, "impressions": 8_000_000}
    )
    if big2 and big2.get("approval_id"):
        aid2 = big2["approval_id"]
        r = ctx.call(
            "POST",
            f"/approvals/{aid2}/decide",
            who="operator",
            body={"decision": "reject", "decided_by": "sandbox-operator", "reason": "too cheap"},
        )
        sc.check(r.status_code == 200, "operator rejects the second one", r.text[:200])
        r = ctx.call("POST", f"/approvals/{aid2}/resume", who="operator")
        sc.check(
            r.status_code == 200 and r.json().get("status") == "rejected",
            "resumed flow finalises it as rejected",
            r.text[:300],
        )
    sc.info(
        "known gaps here: #129 (booking a negotiated quote ignores the proposal's approval), "
        "#131 (POST /proposals has no idempotency key), #118 (silent deterministic fallback)"
    )


def scenario_6_buyer_agent(ctx: Ctx, sc: Scenario, buyer_agent: Optional[str]) -> None:
    if not buyer_agent:
        sc.skip(
            "pass --buyer-agent PATH (a local clone of github.com/IABTechLab/buyer-agent) "
            "to drive this seller with the IAB buyer's own DealsClient"
        )
        return
    root = Path(buyer_agent).expanduser().resolve()
    if not (root / "src" / "ad_buyer").is_dir():
        sc.check(False, f"{root} does not look like a buyer-agent checkout (no src/ad_buyer)")
        return

    probe = sandbox_env.SANDBOX_DIR / "buyer_agent_probe.py"
    env = {
        **os.environ,
        "SANDBOX_SELLER_URL": sandbox_env.BASE_URL,
        "SANDBOX_BUYER_API_KEY": ctx.key("agency"),
        "SANDBOX_RUN_ID": RUN_ID,
        # Keep the buyer pinned to this seller and off every hosted default.
        "SELLER_ENDPOINTS": sandbox_env.BASE_URL,
        "IAB_SERVER_URL": sandbox_env.BASE_URL,
        "OPENDIRECT_BASE_URL": "http://localhost:3000",
        "ANTHROPIC_API_KEY": os.environ.get("ANTHROPIC_API_KEY", ""),
        "VIRTUAL_ENV": "",
    }
    cmd = [
        "uv",
        "run",
        "--locked",
        "--python",
        "3.12",
        "--project",
        str(root),
        "python",
        str(probe),
    ]
    sc.info(f"running the IAB buyer-agent DealsClient from {root}")
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=900)
    except FileNotFoundError:
        sc.check(False, "uv not found on PATH")
        return
    except subprocess.TimeoutExpired:
        sc.check(False, "buyer-agent probe timed out (first run syncs its deps; retry)")
        return
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("PROBE ")]
    for ln in lines:
        status, _, message = ln[len("PROBE ") :].partition(" ")
        sc.record(status if status in _COLORS else "INFO", message)
    if proc.returncode != 0 or not lines:
        tail = (proc.stderr or proc.stdout).strip().splitlines()[-15:]
        sc.check(False, f"buyer-agent probe exited {proc.returncode}", "\n      ".join(tail))


SCENARIOS: dict[int, tuple[str, Callable[..., None]]] = {
    1: ("Discovery: agent card, products, avails", scenario_1_discovery),
    2: (
        "Tiered pricing public/seat/agency/advertiser + floor protection",
        scenario_2_tiered_pricing,
    ),
    3: ("Quote then book with idempotency (Deal ID + OpenRTB params)", scenario_3_quote_then_book),
    4: ("Multi-round negotiation, then book at the agreed CPM", scenario_4_negotiation),
    5: ("High-value proposal held by the approval gate", scenario_5_approval_gate),
    6: ("IAB buyer-agent (local clone) against this seller", scenario_6_buyer_agent),
}


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--only", help="comma-separated scenario numbers, e.g. 2,3")
    parser.add_argument("--buyer-agent", help="path to a local buyer-agent clone (scenario 6)")
    parser.add_argument("--strict", action="store_true", help="treat WARN as failure")
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="print request/response bodies"
    )
    args = parser.parse_args()

    if not sandbox_env.KEYS_FILE.exists():
        print(
            f"No keys at {sandbox_env.KEYS_FILE}. Run sandbox/setup.ps1 or sandbox/setup.sh first."
        )
        return 2
    keys = json.loads(sandbox_env.KEYS_FILE.read_text(encoding="utf-8"))

    wanted = sorted(SCENARIOS)
    if args.only:
        wanted = sorted({int(x) for x in args.only.split(",") if x.strip()})
    if 6 not in wanted and args.buyer_agent:
        wanted.append(6)

    client = httpx.Client(base_url=sandbox_env.BASE_URL, timeout=120.0)
    try:
        client.get("/health")
    except httpx.HTTPError:
        print(
            f"Seller is not reachable at {sandbox_env.BASE_URL}. Start it in another terminal:\n"
            "  Windows: .\\sandbox\\run.ps1      macOS/Linux: ./sandbox/run.sh"
        )
        return 2

    ctx = Ctx(client=client, keys=keys, verbose=args.verbose)
    print(f"Sandbox scenarios against {sandbox_env.BASE_URL} (run id {RUN_ID})")

    ran: list[Scenario] = []
    for n in wanted:
        title, fn = SCENARIOS[n]
        sc = Scenario(n, title)
        ran.append(sc)
        print(f"\n=== Scenario {n}: {title} ===")
        try:
            if n > 1 and not ctx.products:
                r = client.get("/products", params={"limit": 500})
                r.raise_for_status()
                _index_products(ctx, r.json()["products"])
            if n == 6:
                fn(ctx, sc, args.buyer_agent)
            else:
                fn(ctx, sc)
        except ScenarioAbort:
            sc.record("FAIL", "scenario stopped: a step later steps depend on failed")
        except (httpx.HTTPError, KeyError, TypeError, ValueError) as exc:
            sc.record("FAIL", f"unexpected {type(exc).__name__}: {exc}")

    print("\n=== Summary ===")
    failed = False
    for sc in ran:
        counts = {
            s: sum(r.status == s for r in sc.results) for s in ("PASS", "FAIL", "WARN", "SKIP")
        }
        bad = counts["FAIL"] or (args.strict and counts["WARN"])
        overall = "SKIP" if counts["SKIP"] and not counts["PASS"] else ("FAIL" if bad else "PASS")
        failed = failed or overall == "FAIL"
        extra = f", {counts['WARN']} warn" if counts["WARN"] else ""
        print(
            f"  {_tag(overall)} {sc.number}. {sc.title}  "
            f"({counts['PASS']} pass, {counts['FAIL']} fail{extra})"
        )
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
