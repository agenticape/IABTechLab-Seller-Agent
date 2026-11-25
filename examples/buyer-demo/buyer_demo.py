#!/usr/bin/env python3
"""Standalone buyer-agent demo against a live AAMP seller (REST).

Sends the standard seller-agent / AdCP REST requests (discovery, pricing,
quotes, negotiation, booking) and writes a trimmed request/response log.
Mock buyer identity only. Never logs API keys. Never approves or rejects
seller approvals.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from zoneinfo import ZoneInfo

import httpx

# ---------------------------------------------------------------------------
# Defaults and catalog hints (fictional aws_workshop mock catalog)
# ---------------------------------------------------------------------------

DEFAULT_SELLER_URL = "https://iab-seller.agenticape.ai"
LOCAL_TZ = ZoneInfo("Australia/Sydney")
# CSV catalogs set each product's floor to 85% of list. The seller never
# returns this; the demo derives it locally so a below-floor lowball can be
# labelled in the log.
FLOOR_RATIO = 0.85
APPROVAL_THRESHOLD = 250_000
TIER_DISCOUNT = {"public": 0.0, "seat": 0.05, "agency": 0.10, "advertiser": 0.15}

PRODUCT_CTV_NBA = "inv-ctv-apex-sports-nba"
PRODUCT_CTV_SERIES = "inv-ctv-apex-series"
PRODUCT_LINEAR_SPORTS = "inv-lin-sportspulse-live"
PRODUCT_DISPLAY = "inv-dsp-gnn-richmedia"

FLIGHT_START = "2027-04-01"
FLIGHT_END = "2027-04-30"
# Small PD booking: well under the $250k approval gate.
SMALL_IMPS = 200_000
# High-value PD booking: at the advertiser price for Apex NBA (~$44.20 CPM)
# this is about $353,600 gross and should return 202 pending_approval.
LARGE_IMPS = 8_000_000
NEG_IMPS = 500_000

DEFAULT_IDENTITY = {
    "advertiser": {
        "advertiser_id": "adv-sandbox-001",
        "agency_id": "agency-sandbox-001",
        "advertiser_name": "Sandbox Outdoor Co (fictional)",
        "agency_name": "Sandbox Media Agency (fictional)",
    },
    "agency": {
        "agency_id": "agency-sandbox-001",
        "agency_name": "Sandbox Media Agency (fictional)",
    },
    "seat": {
        "seat_id": "seat-sandbox-dsp-001",
        "seat_name": "Sandbox DSP Seat (fictional)",
    },
}

ROOT = Path(__file__).resolve().parent
RUNS_DIR = ROOT / "runs"
SENSITIVE_HEADERS = {"x-api-key", "authorization"}
BODY_TRIM = 4000
# Keys that are useful to keep at the front of a trimmed body.
HIGHLIGHT_KEYS = (
    "status",
    "quote_id",
    "deal_id",
    "proposal_id",
    "approval_id",
    "negotiation_id",
    "product_id",
    "access_tier",
    "buyer_tier",
    "final_price",
    "final_cpm",
    "base_price",
    "base_cpm",
    "tier_discount",
    "volume_discount",
    "rationale",
    "recommendation",
    "price",
    "price_range",
    "pricing",
    "openrtb_params",
    "name",
    "url",
    "skills",
    "capabilities",
    "protocols",
    "availableImpressions",
    "estimatedCpm",
    "round",
    "rounds_remaining",
    "action",
    "seller_price",
    "buyer_price",
    "detail",
    "error",
    "message",
)

SCENARIO_ORDER = (
    "discovery",
    "pricing",
    "negotiation",
    "booking",
    "anon-write",
    "extras",
)

SCENARIO_HELP = {
    "discovery": "Anonymous vs keyed catalog and discovery (tier / price disclosure)",
    "pricing": "POST /pricing by tier, then a keyed quote",
    "negotiation": "Reasonable counter, then a below-floor lowball",
    "booking": "Small deal under $250k, then one over (leave 202 pending)",
    "anon-write": "Anonymous write attempts (expect 401)",
    "extras": "Agent card, MCP endpoint, media kit, a small proposal",
}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def now_local() -> datetime:
    return datetime.now(LOCAL_TZ)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def money(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, dict) and "amount_micros" in value:
        return value["amount_micros"] / 1_000_000
    if isinstance(value, (int, float)):
        return float(value)
    return None


def micros(value: float, currency: str = "USD") -> dict:
    return {"amount_micros": int(round(value * 1_000_000)), "currency": currency}


def fmt_money(value: Any) -> str:
    amount = money(value)
    if amount is None:
        return "n/a"
    return f"${amount:,.2f}"


def derived_floor(list_cpm: float) -> float:
    return round(list_cpm * FLOOR_RATIO, 2)


def walk_collect(obj: Any, pred) -> list[Any]:
    hits: list[Any] = []
    if pred(obj):
        hits.append(obj)
    if isinstance(obj, dict):
        for v in obj.values():
            hits.extend(walk_collect(v, pred))
    elif isinstance(obj, list):
        for v in obj:
            hits.extend(walk_collect(v, pred))
    return hits


def redact(obj: Any, secrets: list[str]) -> Any:
    """Return a copy with API keys stripped from headers and string values."""
    secrets = [s for s in secrets if s]
    if obj is None or not secrets:
        # Still redact sensitive header names even if we have no secret values.
        if isinstance(obj, dict):
            return {
                k: (
                    "***REDACTED***"
                    if str(k).lower() in SENSITIVE_HEADERS
                    else redact(v, secrets)
                )
                for k, v in obj.items()
            }
        if isinstance(obj, list):
            return [redact(v, secrets) for v in obj]
        return obj

    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if str(k).lower() in SENSITIVE_HEADERS:
                out[k] = "***REDACTED***"
            else:
                out[k] = redact(v, secrets)
        return out
    if isinstance(obj, list):
        return [redact(v, secrets) for v in obj]
    if isinstance(obj, str):
        text = obj
        for secret in secrets:
            if secret and secret in text:
                text = text.replace(secret, "***REDACTED***")
        return text
    return obj


def trim_body(obj: Any, limit: int = BODY_TRIM) -> Any:
    """Keep highlight fields and cap overall JSON size."""
    if obj is None:
        return None
    if isinstance(obj, dict):
        # Prefer highlight keys, then the rest until the size cap.
        ordered: dict[str, Any] = {}
        for k in HIGHLIGHT_KEYS:
            if k in obj:
                ordered[k] = obj[k]
        for k, v in obj.items():
            if k not in ordered:
                ordered[k] = v
        # Special-case large catalogs
        if "products" in ordered and isinstance(ordered["products"], list):
            products = ordered["products"]
            ordered["products_count"] = len(products)
            ordered["products"] = products[:5]
            if len(products) > 5:
                ordered["products_omitted"] = len(products) - 5
        raw = json.dumps(ordered, default=str)
        if len(raw) <= limit:
            return ordered
        return _truncate_json(ordered, limit)
    if isinstance(obj, list):
        return {"list_count": len(obj), "items": obj[:5]}
    if isinstance(obj, str):
        return obj if len(obj) <= limit else obj[:limit] + "…[truncated]"
    return obj


def _truncate_json(obj: Any, limit: int) -> Any:
    raw = json.dumps(obj, default=str)
    if len(raw) <= limit:
        return obj
    return {"_truncated": True, "_preview": raw[:limit] + "…"}


def pick(obj: Any, *keys: str) -> dict:
    if not isinstance(obj, dict):
        return {}
    return {k: obj[k] for k in keys if k in obj}


# ---------------------------------------------------------------------------
# Key loading (values never printed)
# ---------------------------------------------------------------------------


class Keys:
    def __init__(self) -> None:
        self.by_tier: dict[str, str] = {}
        self.ids: dict[str, dict] = {k: dict(v) for k, v in DEFAULT_IDENTITY.items()}
        self.key_ids: dict[str, str] = {}
        self.source = "none"

    def secrets(self) -> list[str]:
        return list(self.by_tier.values())

    def get(self, who: str) -> Optional[str]:
        if who in ("public", "anonymous", None):
            return None
        return self.by_tier.get(who)

    def require(self, who: str) -> str:
        key = self.get(who)
        if not key:
            needed = {
                "advertiser": "SELLER_API_KEY (or KEYS_FILE)",
                "agency": "SELLER_API_KEY_AGENCY (or KEYS_FILE)",
                "seat": "SELLER_API_KEY_SEAT (or KEYS_FILE)",
            }.get(who, who)
            raise SystemExit(
                f"Missing {who} API key. Set {needed}. See RUN.md. "
                "No key value is printed here."
            )
        return key

    def has(self, who: str) -> bool:
        return bool(self.get(who))


def load_keys() -> Keys:
    keys = Keys()
    keys_file = os.environ.get("KEYS_FILE", "").strip()
    if keys_file:
        path = Path(keys_file).expanduser()
        if not path.is_file():
            raise SystemExit(f"KEYS_FILE does not exist: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        buyers = data.get("buyers") or {}
        for tier, rec in buyers.items():
            if isinstance(rec, dict) and rec.get("api_key"):
                keys.by_tier[tier] = rec["api_key"]
                if rec.get("key_id"):
                    keys.key_ids[tier] = rec["key_id"]
                ident = rec.get("identity") or {}
                if ident:
                    keys.ids.setdefault(tier, {}).update(ident)
        keys.source = f"KEYS_FILE ({path})"

    # Env vars override the file so a user can supply a single key.
    env_map = {
        "advertiser": ("SELLER_API_KEY", "SELLER_API_KEY_ADVERTISER"),
        "agency": ("SELLER_API_KEY_AGENCY",),
        "seat": ("SELLER_API_KEY_SEAT",),
    }
    for tier, names in env_map.items():
        for name in names:
            value = os.environ.get(name, "").strip()
            if value:
                keys.by_tier[tier] = value
                extra = " + env" if keys.source.startswith("KEYS_FILE") else "env"
                keys.source = extra if keys.source in ("none", "env") else keys.source
                break

    return keys


# ---------------------------------------------------------------------------
# HTTP client that records every call
# ---------------------------------------------------------------------------


class Recorder:
    def __init__(self, secrets: list[str]) -> None:
        self.secrets = secrets
        self.calls: list[dict] = []
        self.notes: list[str] = []
        self.findings: list[str] = []

    def note(self, message: str) -> None:
        self.notes.append(message)
        print(f"  [INFO] {message}", flush=True)

    def finding(self, message: str) -> None:
        self.findings.append(message)
        print(f"  [NOTE] {message}", flush=True)

    def record(self, entry: dict) -> None:
        safe = redact(entry, self.secrets)
        self.calls.append(safe)
        status = safe.get("status_code")
        latency = safe.get("latency_ms")
        title = f"{safe.get('method')} {safe.get('path')} as {safe.get('who')}"
        print(f"  [{status}] {title}  ({latency} ms)", flush=True)
        highlights = safe.get("highlights") or {}
        if highlights:
            summary = ", ".join(f"{k}={v}" for k, v in highlights.items())
            print(f"         {summary}", flush=True)


class SellerClient:
    def __init__(
        self,
        base_url: str,
        keys: Keys,
        recorder: Recorder,
        timeout: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.keys = keys
        self.recorder = recorder
        self.client = httpx.Client(base_url=self.base_url, timeout=timeout, follow_redirects=True)

    def close(self) -> None:
        self.client.close()

    def call(
        self,
        method: str,
        path: str,
        who: str = "public",
        body: Any = None,
        params: Optional[dict] = None,
        extract: Optional[list[str]] = None,
    ) -> httpx.Response:
        headers: dict[str, str] = {}
        if who not in ("public", "anonymous"):
            headers["X-Api-Key"] = self.keys.require(who)
        started = datetime.now(timezone.utc)
        try:
            resp = self.client.request(method, path, json=body, headers=headers, params=params)
        except httpx.RequestError as exc:
            elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
            self.recorder.record(
                {
                    "method": method,
                    "path": path,
                    "who": who,
                    "request_body": trim_body(body),
                    "status_code": "ERROR",
                    "latency_ms": elapsed,
                    "error": type(exc).__name__,
                    "error_message": str(exc),
                    "highlights": {"error": type(exc).__name__},
                }
            )
            raise

        elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        parsed: Any
        try:
            parsed = resp.json()
        except ValueError:
            parsed = resp.text

        highlights = _highlights(parsed, extract, resp.status_code)
        self.recorder.record(
            {
                "method": method,
                "path": path,
                "who": who,
                "request_body": trim_body(body),
                "status_code": resp.status_code,
                "latency_ms": elapsed,
                "response": trim_body(parsed),
                "highlights": highlights,
            }
        )
        return resp


def _highlights(parsed: Any, extract: Optional[list[str]], status: int) -> dict:
    out: dict[str, Any] = {"http": status}
    if not isinstance(parsed, dict):
        if isinstance(parsed, str):
            out["body"] = parsed[:180]
        return out
    # Unwrap common envelopes
    inner = parsed.get("quote") or parsed.get("deal") or parsed
    if extract:
        for key in extract:
            src = inner if isinstance(inner, dict) and key in inner else parsed
            if isinstance(src, dict) and key in src:
                value = src[key]
                if key.endswith("_cpm") or key in ("final_price", "base_price", "bidfloor"):
                    out[key] = fmt_money(value)
                else:
                    out[key] = value
    else:
        for key in (
            "access_tier",
            "quote_id",
            "deal_id",
            "proposal_id",
            "approval_id",
            "negotiation_id",
            "status",
            "recommendation",
            "final_price",
            "buyer_tier",
        ):
            src = inner if isinstance(inner, dict) and key in inner else parsed
            if isinstance(src, dict) and key in src:
                value = src[key]
                out[key] = fmt_money(value) if key == "final_price" else value
        if isinstance(inner, dict):
            pricing = inner.get("pricing") if isinstance(inner.get("pricing"), dict) else None
            if pricing and "final_cpm" in pricing:
                out["final_cpm"] = fmt_money(pricing["final_cpm"])
            if "final_price" in inner:
                out["final_price"] = fmt_money(inner["final_price"])
    return out


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------


def scenario_discovery(s: SellerClient, rec: Recorder) -> None:
    rec.note("Anonymous catalog and discovery vs the same calls with an advertiser key.")
    r = s.call("GET", "/products", params={"limit": 500}, extract=["products"])
    anon_products = (r.json() or {}).get("products") if r.status_code == 200 else []
    rec.note(f"Anonymous GET /products returned {len(anon_products)} products.")
    if s.keys.has("advertiser"):
        r2 = s.call("GET", "/products", who="advertiser", params={"limit": 500})
        keyed = (r2.json() or {}).get("products") if r2.status_code == 200 else []
        rec.note(f"Keyed GET /products returned {len(keyed)} products (compare disclosure).")

    s.call("GET", f"/products/{PRODUCT_CTV_NBA}", extract=["product_id", "name"])
    s.call(
        "POST",
        "/products/avails",
        body={
            "productid": PRODUCT_CTV_NBA,
            "startdate": "2027-04-01T00:00:00Z",
            "enddate": "2027-04-30T00:00:00Z",
            "requestedImpressions": 2_000_000,
        },
        extract=["availableImpressions", "estimatedCpm", "totalCost"],
    )

    claim = {
        "query": "ctv sports pricing",
        "buyer_tier": "agency",
        "agency_id": "agency-made-up",
    }
    r = s.call("POST", "/discovery", body=claim, extract=["access_tier"])
    if r.status_code == 200:
        tier = r.json().get("access_tier")
        rec.finding(
            f"Anonymous discovery that claimed agency still got access_tier={tier} "
            "(should be public; price ranges, not exact prices)."
        )
        _note_price_disclosure(rec, r.json(), "anonymous")

    if s.keys.has("advertiser"):
        r = s.call(
            "POST",
            "/discovery",
            who="advertiser",
            body={"query": "ctv sports pricing"},
            extract=["access_tier"],
        )
        if r.status_code == 200:
            rec.finding(f"Keyed discovery access_tier={r.json().get('access_tier')}.")
            _note_price_disclosure(rec, r.json(), "advertiser")
    if s.keys.has("agency"):
        s.call(
            "POST",
            "/discovery",
            who="agency",
            body={"query": "ctv sports pricing"},
            extract=["access_tier"],
        )


def _note_price_disclosure(rec: Recorder, payload: dict, label: str) -> None:
    ranges = walk_collect(payload, lambda o: isinstance(o, dict) and "price_range" in o)
    exact = walk_collect(payload, lambda o: isinstance(o, dict) and "price" in o and "price_range" not in o)
    rec.note(
        f"{label} discovery: {len(ranges)} price_range field(s), "
        f"{len(exact)} exact price field(s)."
    )
    if ranges:
        sample = ranges[0].get("price_range")
        rec.finding(f"{label} sample price_range={sample}")
    if exact:
        sample = {k: exact[0].get(k) for k in ("product_id", "name", "price") if k in exact[0]}
        rec.finding(f"{label} sample exact price={sample}")


def scenario_pricing(s: SellerClient, rec: Recorder) -> None:
    pid = PRODUCT_CTV_SERIES
    rec.note(f"POST /pricing on {pid}, then a keyed PD quote.")
    r = s.call("POST", "/pricing", body={"product_id": pid}, extract=["final_price", "tier_discount"])
    if r.status_code == 200:
        rec.finding(
            f"Anonymous /pricing: final={fmt_money(r.json().get('final_price'))}, "
            f"tier_discount={r.json().get('tier_discount')}"
        )
    # Self-asserted advertiser tier without a key should still be public.
    r = s.call(
        "POST",
        "/pricing",
        body={"product_id": pid, "buyer_tier": "advertiser"},
        extract=["final_price", "tier_discount"],
    )
    if r.status_code == 200:
        rec.finding(
            "Anonymous /pricing that claimed advertiser in the body: "
            f"final={fmt_money(r.json().get('final_price'))}, "
            f"tier_discount={r.json().get('tier_discount')} (expect public / 0%)"
        )

    for tier in ("seat", "agency", "advertiser"):
        if not s.keys.has(tier):
            rec.note(f"Skipping keyed /pricing as {tier}: no key supplied.")
            continue
        r = s.call(
            "POST",
            "/pricing",
            who=tier,
            body={"product_id": pid},
            extract=["final_price", "tier_discount", "volume_discount", "base_price"],
        )
        if r.status_code == 200:
            rec.finding(
                f"{tier} /pricing: list={fmt_money(r.json().get('base_price'))}, "
                f"final={fmt_money(r.json().get('final_price'))}, "
                f"tier_discount={r.json().get('tier_discount')}"
            )

    if not s.keys.has("advertiser"):
        rec.note("Skipping quote: no advertiser key.")
        return
    r = s.call(
        "POST",
        "/api/v1/quotes",
        who="advertiser",
        body={
            "idempotency_key": _idem("quote-pd-series"),
            "product_id": pid,
            "deal_type": "PD",
            "impressions": 1_000_000,
            "flight_start": FLIGHT_START,
            "flight_end": FLIGHT_END,
        },
        extract=["quote_id", "buyer_tier", "final_cpm", "status"],
    )
    if r.status_code == 200:
        quote = r.json().get("quote") or {}
        pricing = quote.get("pricing") or {}
        rec.finding(
            f"Quote {quote.get('quote_id')}: {fmt_money(pricing.get('final_cpm'))} CPM "
            f"(list {fmt_money(pricing.get('base_cpm'))}), tier={quote.get('buyer_tier')}, "
            f"expires {quote.get('expires_at')}"
        )


def scenario_negotiation(s: SellerClient, rec: Recorder) -> None:
    pid = PRODUCT_LINEAR_SPORTS
    rec.note(
        f"Two short PD negotiations on {pid} ({NEG_IMPS:,} imps, "
        f"{FLIGHT_START}..{FLIGHT_END}). No booking in this scenario."
    )
    # Agency tier is 10% off list ($32 -> $28.80) with an internal floor of
    # $27.20, so there is a band for a reasonable counter. Advertiser tier
    # already sits on the floor, so a below-quote offer is also a lowball.
    who = "agency" if s.keys.has("agency") else "advertiser" if s.keys.has("advertiser") else None
    if who is None:
        raise SystemExit("Negotiation needs SELLER_API_KEY or SELLER_API_KEY_AGENCY (or KEYS_FILE).")
    if who == "advertiser":
        rec.finding(
            "Only an advertiser key was provided. Advertiser pricing on this "
            "catalog already sits on the 85% floor, so a 'reasonable' offer "
            "below the quote is also a below-floor lowball. Set "
            "SELLER_API_KEY_AGENCY (or KEYS_FILE) to see an in-band counter."
        )

    quoted = _quote(s, rec, who, pid, NEG_IMPS, "neg-reasonable")
    if quoted is None:
        return
    qid, cpm, list_cpm = quoted
    floor = derived_floor(list_cpm) if list_cpm else None
    rec.note(
        f"Derived internal floor (list x {FLOOR_RATIO}) = "
        f"{fmt_money(floor) if floor else 'n/a'} — not returned by the seller."
    )

    reasonable = round(cpm * 0.955, 2)
    rec.note(f"Reasonable counter: buyer offers {fmt_money(reasonable)} (95.5% of quote).")
    _counter(s, rec, who, qid, reasonable, "neg-reasonable-offer")

    quoted2 = _quote(s, rec, who, pid, NEG_IMPS, "neg-lowball")
    if quoted2 is None:
        return
    qid2, cpm2, list2 = quoted2
    lowball = round((list2 or cpm2) * 0.50, 2)
    rec.note(
        f"Below-floor lowball on a fresh quote: buyer offers {fmt_money(lowball)} "
        f"(50% of list {fmt_money(list2)}). Expect a counter, not an accept, "
        "and no mention of the word 'floor'."
    )
    _counter(s, rec, who, qid2, lowball, "neg-lowball-offer")


def _quote(
    s: SellerClient, rec: Recorder, who: str, pid: str, imps: int, label: str
) -> Optional[tuple[str, float, Optional[float]]]:
    r = s.call(
        "POST",
        "/api/v1/quotes",
        who=who,
        body={
            "idempotency_key": _idem(label),
            "product_id": pid,
            "deal_type": "PD",
            "impressions": imps,
            "flight_start": FLIGHT_START,
            "flight_end": FLIGHT_END,
        },
        extract=["quote_id", "buyer_tier", "final_cpm"],
    )
    if r.status_code != 200:
        rec.finding(f"Quote {label} failed with HTTP {r.status_code}.")
        return None
    quote = r.json().get("quote") or {}
    cpm = money((quote.get("pricing") or {}).get("final_cpm"))
    list_cpm = money((quote.get("pricing") or {}).get("base_cpm"))
    if cpm is None:
        rec.finding("Quote had no final_cpm; stopping this branch.")
        return None
    rec.finding(
        f"Quote {quote.get('quote_id')}: {fmt_money(cpm)} CPM "
        f"(list {fmt_money(list_cpm)}), tier={quote.get('buyer_tier')}"
    )
    return quote["quote_id"], cpm, list_cpm


def _counter(
    s: SellerClient, rec: Recorder, who: str, quote_id: str, offer: float, label: str
) -> None:
    r = s.call(
        "POST",
        "/api/v1/negotiations/messages",
        who=who,
        body={
            "idempotency_key": _idem(label),
            "action": "counter",
            "quote_id": quote_id,
            "buyer_price": micros(offer),
        },
        extract=["negotiation_id", "status"],
    )
    if r.status_code != 200:
        rec.finding(f"Counter {label} failed with HTTP {r.status_code}.")
        return
    body = r.json()
    rnd = body.get("round") or {}
    seller = money(rnd.get("seller_price"))
    rec.finding(
        f"Seller {rnd.get('action')} at {fmt_money(seller)} "
        f"(round {rnd.get('round_number')}, "
        f"rounds_remaining={body.get('rounds_remaining')}): "
        f"{rnd.get('rationale')}"
    )
    text = (rnd.get("rationale") or "").lower()
    if "floor" in text or "minimum" in text or "reserve" in text:
        rec.finding("Surprising: seller rationale appears to name the floor/reserve.")


def scenario_booking(s: SellerClient, rec: Recorder) -> None:
    if not s.keys.has("advertiser"):
        raise SystemExit("Booking needs SELLER_API_KEY (advertiser) or KEYS_FILE.")
    rec.note(
        "One small PD booking under the $250,000 approval gate, then one over. "
        "The over-threshold booking should return 202 pending_approval and is "
        "left pending — this demo never approves or rejects."
    )

    # Small booking
    r = s.call(
        "POST",
        "/api/v1/quotes",
        who="advertiser",
        body={
            "idempotency_key": _idem("quote-small"),
            "product_id": PRODUCT_DISPLAY,
            "deal_type": "PD",
            "impressions": SMALL_IMPS,
            "flight_start": FLIGHT_START,
            "flight_end": FLIGHT_END,
        },
        extract=["quote_id", "final_cpm"],
    )
    if r.status_code == 200:
        quote = r.json().get("quote") or {}
        qid = quote.get("quote_id")
        cpm = money((quote.get("pricing") or {}).get("final_cpm")) or 0
        gross = cpm * SMALL_IMPS / 1000
        rec.finding(
            f"Small quote {qid}: {fmt_money(cpm)} CPM x {SMALL_IMPS:,} = ${gross:,.0f} gross "
            f"(under ${APPROVAL_THRESHOLD:,})."
        )
        r = s.call(
            "POST",
            "/api/v1/deals",
            who="advertiser",
            body={"idempotency_key": _idem("book-small"), "quote_id": qid},
            extract=["deal_id", "status"],
        )
        if r.status_code == 200:
            deal = r.json().get("deal") or {}
            ortb = deal.get("openrtb_params") or {}
            rec.finding(
                f"Small deal {deal.get('deal_id')} status={deal.get('status')} "
                f"final_cpm={fmt_money((deal.get('pricing') or {}).get('final_cpm'))} "
                f"OpenRTB id={ortb.get('id')} bidfloor={fmt_money(ortb.get('bidfloor'))} "
                f"at={ortb.get('at')} wseat={ortb.get('wseat')} wadomain={ortb.get('wadomain')}"
            )
        elif r.status_code == 202:
            rec.finding(
                "Unexpected 202 on the small booking. approval_id="
                f"{(r.json() or {}).get('approval_id')} — left pending."
            )

    # High-value booking
    r = s.call(
        "POST",
        "/api/v1/quotes",
        who="advertiser",
        body={
            "idempotency_key": _idem("quote-large"),
            "product_id": PRODUCT_CTV_NBA,
            "deal_type": "PD",
            "impressions": LARGE_IMPS,
            "flight_start": FLIGHT_START,
            "flight_end": FLIGHT_END,
        },
        extract=["quote_id", "final_cpm"],
    )
    if r.status_code != 200:
        rec.finding(f"Large quote failed with HTTP {r.status_code}.")
        return
    quote = r.json().get("quote") or {}
    qid = quote.get("quote_id")
    cpm = money((quote.get("pricing") or {}).get("final_cpm")) or 0
    gross = cpm * LARGE_IMPS / 1000
    rec.finding(
        f"Large quote {qid}: {fmt_money(cpm)} CPM x {LARGE_IMPS:,} = ${gross:,.0f} gross "
        f"(threshold is ${APPROVAL_THRESHOLD:,})."
    )
    if gross <= APPROVAL_THRESHOLD:
        rec.finding(
            "Gross is not above the approval threshold; the booking may go "
            "straight through. Left as-is if that happens."
        )
    r = s.call(
        "POST",
        "/api/v1/deals",
        who="advertiser",
        body={"idempotency_key": _idem("book-large"), "quote_id": qid},
        extract=["deal_id", "status", "approval_id"],
    )
    if r.status_code == 202:
        rec.finding(
            f"Large booking held: HTTP 202 status={r.json().get('status')} "
            f"approval_id={r.json().get('approval_id')} — left pending, not decided."
        )
    elif r.status_code == 200:
        deal = (r.json() or {}).get("deal") or {}
        rec.finding(
            f"Large booking booked immediately: deal {deal.get('deal_id')} "
            f"status={deal.get('status')} (unexpected if gross was over the gate)."
        )


def scenario_anon_write(s: SellerClient, rec: Recorder) -> None:
    rec.note("Anonymous writes should be rejected (ALLOW_ANONYMOUS_BUYER_WRITES=false).")
    s.call(
        "POST",
        "/api/v1/quotes",
        body={
            "idempotency_key": _idem("anon-quote"),
            "product_id": PRODUCT_DISPLAY,
            "deal_type": "PD",
            "impressions": 100_000,
            "flight_start": FLIGHT_START,
            "flight_end": FLIGHT_END,
        },
    )
    s.call(
        "POST",
        "/api/v1/deals",
        body={"idempotency_key": _idem("anon-book"), "quote_id": "qt-does-not-exist"},
    )
    s.call(
        "POST",
        "/proposals",
        body={
            "product_id": PRODUCT_DISPLAY,
            "deal_type": "PD",
            "price": 14.0,
            "impressions": 100_000,
            "start_date": FLIGHT_START,
            "end_date": FLIGHT_END,
        },
    )
    s.call("GET", "/approvals")
    rec.finding("Expect 401 on anonymous quotes, bookings, proposals and GET /approvals.")


def scenario_extras(s: SellerClient, rec: Recorder) -> None:
    rec.note("Agent card, MCP endpoint reachability, media kit, and a small proposal.")
    r = s.call("GET", "/.well-known/agent.json", extract=["name", "url"])
    if r.status_code == 200:
        card = r.json()
        skills = [sk.get("id") for sk in card.get("skills") or [] if isinstance(sk, dict)]
        caps = card.get("capabilities") or {}
        rec.finding(
            f"Agent card: name={card.get('name')!r} url={card.get('url')} "
            f"protocols={caps.get('protocols')} skills={skills}"
        )

    # Reachability only — do not start a full MCP session. Earlier IAB
    # buyer-agent MCP/A2A probes against this host failed.
    r = s.call("GET", "/mcp")
    rec.finding(
        f"GET /mcp returned HTTP {r.status_code} "
        f"(a 4xx with MCP content-type complaints is still 'the endpoint is there')."
    )
    s.call("GET", "/media-kit")

    if s.keys.has("advertiser"):
        ident = s.keys.ids.get("advertiser") or {}
        r = s.call(
            "POST",
            "/proposals",
            who="advertiser",
            body={
                "product_id": PRODUCT_CTV_SERIES,
                "deal_type": "PD",
                "price": 45.0,
                "impressions": 100_000,
                "start_date": FLIGHT_START,
                "end_date": FLIGHT_END,
                "agency_id": ident.get("agency_id"),
                "advertiser_id": ident.get("advertiser_id"),
            },
            extract=["proposal_id", "status", "recommendation", "approval_id"],
        )
        if r.status_code == 200:
            rec.finding(
                f"Small proposal {r.json().get('proposal_id')} "
                f"status={r.json().get('status')} "
                f"recommendation={r.json().get('recommendation')} "
                "(not converted into a deal)."
            )
    else:
        rec.note("Skipping POST /proposals: no advertiser key.")


SCENARIOS = {
    "discovery": scenario_discovery,
    "pricing": scenario_pricing,
    "negotiation": scenario_negotiation,
    "booking": scenario_booking,
    "anon-write": scenario_anon_write,
    "extras": scenario_extras,
}

_RUN_ID = ""


def _idem(label: str) -> str:
    return f"buyer-demo-{_RUN_ID}-{label}"


# ---------------------------------------------------------------------------
# Connectivity check (read-only)
# ---------------------------------------------------------------------------


def connectivity_check(base_url: str, timeout: float) -> int:
    url = base_url.rstrip("/") + "/health"
    print(f"Connectivity check: GET {url}", flush=True)
    started = datetime.now(timezone.utc)
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.get(url)
        elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
    except httpx.RequestError as exc:
        elapsed = int((datetime.now(timezone.utc) - started).total_seconds() * 1000)
        print(f"FAILED  {type(exc).__name__}: {exc}  ({elapsed} ms)", flush=True)
        return 1
    body = resp.text[:200]
    print(f"HTTP {resp.status_code} in {elapsed} ms", flush=True)
    print(f"body: {body}", flush=True)
    return 0 if resp.status_code == 200 else 1


# ---------------------------------------------------------------------------
# Report writers
# ---------------------------------------------------------------------------


def write_reports(meta: dict, rec: Recorder) -> tuple[Path, Path]:
    RUNS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = meta["started_at"].strftime("%Y%m%d-%H%M%S")
    json_path = RUNS_DIR / f"{stamp}.json"
    md_path = RUNS_DIR / f"{stamp}.md"

    payload = {
        "started_at": iso(meta["started_at"]),
        "finished_at": iso(meta["finished_at"]),
        "timezone": "Australia/Sydney (AEDT/AEST)",
        "seller_url": meta["seller_url"],
        "scenarios": meta["scenarios"],
        "key_source": meta["key_source"],
        "run_id": meta["run_id"],
        "notes": rec.notes,
        "findings": rec.findings,
        "calls": rec.calls,
    }
    json_path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    md_path.write_text(_render_md(payload), encoding="utf-8")
    return json_path, md_path


def _render_md(payload: dict) -> str:
    lines = [
        f"# Buyer demo run {payload['started_at']}",
        "",
        f"- Seller: {payload['seller_url']}",
        f"- Scenarios: {', '.join(payload['scenarios'])}",
        f"- Run id: {payload['run_id']}",
        f"- Key source: {payload['key_source']} (values never written here)",
        f"- Started: {payload['started_at']} (Australia/Sydney)",
        f"- Finished: {payload['finished_at']} (Australia/Sydney)",
        "",
        "## Findings",
        "",
    ]
    if payload["findings"]:
        for item in payload["findings"]:
            lines.append(f"- {item}")
    else:
        lines.append("- (none)")
    lines += ["", "## Calls", ""]
    for i, call in enumerate(payload["calls"], start=1):
        lines.append(
            f"### {i}. {call.get('method')} `{call.get('path')}` as {call.get('who')}"
        )
        lines.append("")
        lines.append(
            f"- Status: {call.get('status_code')} in {call.get('latency_ms')} ms"
        )
        if call.get("highlights"):
            hi = ", ".join(f"{k}={v}" for k, v in call["highlights"].items())
            lines.append(f"- Highlights: {hi}")
        if call.get("request_body") is not None:
            lines.append("- Request body:")
            lines.append("```json")
            lines.append(json.dumps(call["request_body"], indent=2, default=str))
            lines.append("```")
        if call.get("error"):
            lines.append(f"- Error: {call.get('error')}: {call.get('error_message')}")
        if call.get("response") is not None:
            lines.append("- Response (trimmed):")
            lines.append("```json")
            lines.append(json.dumps(call["response"], indent=2, default=str))
            lines.append("```")
        lines.append("")
    lines += ["## Notes", ""]
    for item in payload["notes"]:
        lines.append(f"- {item}")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def parse_args(argv: list[str]) -> argparse.Namespace:
    epilog = "Scenarios:\n" + "\n".join(
        f"  {name:12} {desc}" for name, desc in SCENARIO_HELP.items()
    )
    p = argparse.ArgumentParser(
        description="Standalone buyer demo for a live AAMP seller (REST only).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=epilog,
    )
    p.add_argument(
        "--seller-url",
        default=os.environ.get("SELLER_URL", DEFAULT_SELLER_URL),
        help="Seller base URL (or set SELLER_URL). Default: %(default)s",
    )
    p.add_argument(
        "--check",
        action="store_true",
        help="Read-only connectivity check (GET /health) and exit. No writes.",
    )
    p.add_argument(
        "--scenario",
        action="append",
        dest="scenarios",
        metavar="NAME",
        help="Run one named scenario (repeatable). Names: " + ", ".join(SCENARIO_ORDER),
    )
    p.add_argument(
        "--all",
        action="store_true",
        help="Run the full scenario suite (creates quotes, a small deal, and a pending approval).",
    )
    p.add_argument(
        "--timeout",
        type=float,
        default=60.0,
        help="HTTP timeout in seconds (Fly may cold-start). Default: %(default)s",
    )
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    global _RUN_ID
    args = parse_args(argv if argv is not None else sys.argv[1:])
    base_url = args.seller_url.rstrip("/")

    if args.check:
        return connectivity_check(base_url, args.timeout)

    wanted: list[str] = []
    if args.all:
        wanted = list(SCENARIO_ORDER)
    if args.scenarios:
        for name in args.scenarios:
            if name not in SCENARIOS:
                raise SystemExit(
                    f"Unknown scenario {name!r}. Choose from: {', '.join(SCENARIO_ORDER)}"
                )
            if name not in wanted:
                wanted.append(name)
    if not wanted:
        print(
            "No scenario selected. Use --check, --scenario NAME, or --all.\n"
            "This tool does not run the suite by default (booking creates a deal "
            "and a pending approval).\n",
            file=sys.stderr,
        )
        parse_args(["--help"])
        return 2

    keys = load_keys()
    rec = Recorder(secrets=keys.secrets())
    started = now_local()
    _RUN_ID = started.strftime("%Y%m%d%H%M%S") + uuid.uuid4().hex[:6]

    print(f"Buyer demo against {base_url}", flush=True)
    print(f"Scenarios: {', '.join(wanted)}", flush=True)
    print(f"Key source: {keys.source}", flush=True)
    print(f"Run id: {_RUN_ID}", flush=True)

    client = SellerClient(base_url, keys, rec, timeout=args.timeout)
    try:
        for name in wanted:
            print(f"\n=== {name}: {SCENARIO_HELP[name]} ===", flush=True)
            try:
                SCENARIOS[name](client, rec)
            except httpx.RequestError as exc:
                rec.finding(f"Scenario {name} stopped: {type(exc).__name__}: {exc}")
    finally:
        client.close()

    finished = now_local()
    json_path, md_path = write_reports(
        {
            "started_at": started,
            "finished_at": finished,
            "seller_url": base_url,
            "scenarios": wanted,
            "key_source": keys.source,
            "run_id": _RUN_ID,
        },
        rec,
    )
    print(f"\nWrote {md_path}", flush=True)
    print(f"Wrote {json_path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
