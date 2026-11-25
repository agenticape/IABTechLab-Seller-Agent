"""Plain-language captions for buyer-demo calls.

Turns one seller-agent REST call into what the buyer is asking and what the
seller answered, plus a tone (ok / counter / pending / refused) and, for
bookings, a summary card. Used by the live UI; the CLI output does not
depend on it.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Optional

APPROVAL_THRESHOLD = 250_000

# Short labels for the fictional aws_workshop catalog. Unknown ids fall back
# to a name the seller returned, then to the id itself.
PRODUCT_LABELS = {
    "inv-ctv-apex-series": "Apex Series",
    "inv-ctv-apex-sports-nba": "Apex NBA",
    "inv-lin-sportspulse-live": "SportsPulse Live",
    "inv-dsp-gnn-richmedia": "GNN Rich Media",
}

TONE_OK = "ok"
TONE_COUNTER = "counter"
TONE_PENDING = "pending"
TONE_REFUSED = "refused"


class CallContext:
    """What the buyer has learned so far in a run (quotes, product names)."""

    def __init__(self) -> None:
        self.quotes: dict[str, dict[str, Any]] = {}
        self.names: dict[str, str] = {}

    def label(self, product_id: Optional[str]) -> str:
        if not product_id:
            return "that product"
        return PRODUCT_LABELS.get(product_id) or self.names.get(product_id) or product_id


def money(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, dict) and "amount_micros" in value:
        return value["amount_micros"] / 1_000_000
    if isinstance(value, (int, float)):
        return float(value)
    return None


def usd(value: Any) -> str:
    amount = money(value)
    if amount is None:
        return "n/a"
    return f"${amount:,.2f}"


def imps(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return "?"
    if value >= 1_000_000:
        return f"{value / 1_000_000:g}M"
    if value >= 1_000:
        return f"{value / 1_000:g}k"
    return str(int(value))


def flight(start: Any, end: Any) -> str:
    try:
        s = date.fromisoformat(str(start)[:10])
        e = date.fromisoformat(str(end)[:10])
    except ValueError:
        return ""
    if (s.year, s.month) == (e.year, e.month):
        return s.strftime("%b %Y")
    if s.year == e.year:
        return f"{s.strftime('%b')}-{e.strftime('%b %Y')}"
    return f"{s.strftime('%b %Y')}-{e.strftime('%b %Y')}"


def _as(who: str) -> str:
    if who in ("public", "anonymous", None):
        return "with no key"
    article = "an" if who[:1] in "aeiou" else "a"
    return f"as {article} {who}"


def _detail_error(parsed: Any) -> str:
    if not isinstance(parsed, dict):
        return ""
    detail = parsed.get("detail")
    if isinstance(detail, dict):
        return str(detail.get("error") or detail.get("message") or "")
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list) and detail and isinstance(detail[0], dict):
        return str(detail[0].get("msg") or "")
    return ""


def describe_request(
    method: str,
    path: str,
    who: str,
    body: Any,
    params: Optional[dict],
    ctx: CallContext,
) -> str:
    """What the buyer is asking, in plain words."""
    b = body if isinstance(body, dict) else {}
    keyless = who in ("public", "anonymous", None)

    if method == "GET" and path == "/products":
        return "What's in your catalog?" + (" (no key)" if keyless else f" (I'm {_as(who)[3:]})")
    if method == "GET" and path.startswith("/products/"):
        return f"Tell me about {ctx.label(path.rsplit('/', 1)[-1])}."
    if path == "/products/avails":
        when = flight(b.get("startdate"), b.get("enddate"))
        return (
            f"Is {ctx.label(b.get('productid'))} available? "
            f"{imps(b.get('requestedImpressions'))} imps{', ' + when if when else ''}."
        )
    if path == "/discovery":
        text = f'Search your inventory: "{b.get("query", "")}"'
        if keyless and b.get("buyer_tier"):
            return text + f" - I'm {b['buyer_tier']} tier, trust me (no key)."
        return text + f" ({_as(who)})."
    if path == "/pricing":
        label = ctx.label(b.get("product_id"))
        if keyless and b.get("buyer_tier"):
            return f"What does {label} cost? I'm {_as(b['buyer_tier'])[3:]}, trust me (no key)."
        return f"What does {label} cost {_as(who)}?"
    if path == "/api/v1/quotes":
        when = flight(b.get("flight_start"), b.get("flight_end"))
        return (
            f"Can I get {ctx.label(b.get('product_id'))} {_as(who)}? "
            f"{imps(b.get('impressions'))} imps{', ' + when if when else ''}, "
            f"{b.get('deal_type', 'PD')}."
        )
    if path == "/api/v1/negotiations/messages":
        qid = b.get("quote_id")
        quoted = ctx.quotes.get(qid or "", {}).get("cpm")
        offer = usd(b.get("buyer_price"))
        tail = f" You quoted ${quoted:,.2f}." if quoted else ""
        return f"I'd pay {offer} CPM for quote {qid}.{tail}"
    if method == "POST" and path == "/api/v1/deals":
        qid = b.get("quote_id")
        q = ctx.quotes.get(qid or "")
        if keyless:
            return f"Book quote {qid}, please. (No key.)"
        if q and q.get("cpm") is not None and q.get("imps"):
            gross = q["cpm"] * q["imps"] / 1000
            return (
                f"Book quote {qid}: {ctx.label(q.get('product_id'))}, "
                f"{imps(q['imps'])} imps at ${q['cpm']:,.2f} (${gross:,.0f} gross)."
            )
        return f"Book quote {qid}."
    if path == "/proposals":
        return (
            f"Proposal: {ctx.label(b.get('product_id'))} {b.get('deal_type', '')} at "
            f"{usd(b.get('price'))} CPM, {imps(b.get('impressions'))} imps"
            + (" (no key)." if keyless else ".")
        )
    if path == "/approvals":
        return "Show me your pending approvals." + (" (No key.)" if keyless else "")
    if path == "/.well-known/agent.json":
        return "Who are you? Show me your agent card."
    if path == "/mcp":
        return "Do you speak MCP?"
    if path == "/media-kit":
        return "Send me your media kit."
    return f"{method} {path}"


def describe_response(
    method: str,
    path: str,
    who: str,
    request_body: Any,
    status: Any,
    parsed: Any,
    ctx: CallContext,
) -> dict[str, Any]:
    """What the seller answered: ``{"reply", "tone", "card"?}``.

    Also records quotes and product names in ``ctx`` so later captions can
    say what is being booked.
    """
    b = request_body if isinstance(request_body, dict) else {}
    p = parsed if isinstance(parsed, dict) else {}

    if status == "ERROR":
        return {"reply": "No answer (network error).", "tone": TONE_REFUSED}
    if not isinstance(status, int):
        return {"reply": str(status), "tone": TONE_REFUSED}

    if path == "/mcp" and status < 500:
        return {
            "reply": f"{status}: the MCP endpoint is here, but it wants an MCP client.",
            "tone": TONE_OK,
        }
    if status == 401:
        if method == "GET":
            return {"reply": "401: no key, no access.", "tone": TONE_REFUSED}
        return {"reply": "401: no key, no write.", "tone": TONE_REFUSED}
    if status >= 400:
        err = _detail_error(parsed)
        return {
            "reply": f"{status}: refused" + (f" ({err})." if err else "."),
            "tone": TONE_REFUSED,
        }

    if status == 202 and p.get("approval_id"):
        qid = p.get("quote_id") or b.get("quote_id")
        q = ctx.quotes.get(qid or "", {})
        gross = None
        if q.get("cpm") is not None and q.get("imps"):
            gross = q["cpm"] * q["imps"] / 1000
        if gross is not None:
            reply = (
                f"Held for approval: ${gross:,.0f} is over the ${APPROVAL_THRESHOLD // 1000}k gate."
            )
        else:
            reply = "Held for approval by a human at the seller."
        card = {
            "kind": "approval",
            "title": "Booking held for approval",
            "fields": {
                "approval_id": p.get("approval_id"),
                "quote_id": qid,
                "gross": f"${gross:,.0f}" if gross is not None else None,
                "gate": f"${APPROVAL_THRESHOLD:,}",
                "status": p.get("status"),
                "status_url": p.get("status_url"),
            },
        }
        return {"reply": reply, "tone": TONE_PENDING, "card": card}

    if path == "/api/v1/quotes":
        quote = p.get("quote") or {}
        pricing = quote.get("pricing") or {}
        terms = quote.get("terms") or {}
        product = quote.get("product") or {}
        final = money(pricing.get("final_cpm"))
        base = money(pricing.get("base_cpm"))
        pct = pricing.get("tier_discount_pct")
        qid = quote.get("quote_id")
        if qid:
            ctx.quotes[qid] = {
                "cpm": final,
                "imps": terms.get("impressions") or b.get("impressions"),
                "product_id": product.get("product_id") or b.get("product_id"),
            }
        if product.get("product_id") and product.get("name"):
            ctx.names[product["product_id"]] = product["name"]
        return {
            "reply": _price_line(final, base, pct, quote.get("buyer_tier"), qid),
            "tone": TONE_OK,
        }

    if path == "/pricing":
        final = money(p.get("final_price"))
        base = money(p.get("base_price"))
        disc = p.get("tier_discount")
        pct = disc * 100 if isinstance(disc, (int, float)) else None
        tier = who if who not in ("public", "anonymous") else "public"
        reply = _price_line(final, base, pct, tier, None)
        if who in ("public", "anonymous") and b.get("buyer_tier"):
            reply += " Your tier claim was ignored: no key, public price."
        return {"reply": reply, "tone": TONE_OK}

    if path == "/api/v1/negotiations/messages":
        rnd = p.get("round") or {}
        action = rnd.get("action")
        price = usd(rnd.get("seller_price"))
        left = p.get("rounds_remaining")
        left_txt = f" ({left} rounds left)" if left is not None else ""
        if action == "counter":
            return {"reply": f"Counter at {price}{left_txt}.", "tone": TONE_COUNTER}
        if action == "accept":
            return {"reply": f"Accepted at {usd(rnd.get('buyer_price'))}.", "tone": TONE_OK}
        if action in ("reject", "walk_away", "final_offer"):
            return {
                "reply": f"{action.replace('_', ' ').capitalize()} at {price}.",
                "tone": TONE_REFUSED,
            }
        return {"reply": f"Negotiation {p.get('status')}{left_txt}.", "tone": TONE_COUNTER}

    if method == "POST" and path == "/api/v1/deals":
        deal = p.get("deal") or {}
        ortb = deal.get("openrtb_params") or {}
        cpm = usd((deal.get("pricing") or {}).get("final_cpm"))
        state = deal.get("status")
        booked = state in ("booked", "confirmed", "active")
        if booked:
            reply = f"Booked: {deal.get('deal_id')} at {cpm} CPM. Ready to target."
        else:
            reply = f"Deal {deal.get('deal_id')} at {cpm} CPM (status: {state})."
        card = {
            "kind": "deal",
            "title": "Deal booked" if booked else f"Deal created ({state})",
            "fields": {
                "deal_id": deal.get("deal_id"),
                "status": state,
                "final_cpm": cpm,
                "bidfloor": usd(ortb.get("bidfloor")),
                "at": ortb.get("at"),
                "wseat": ortb.get("wseat"),
                "wadomain": ortb.get("wadomain"),
            },
        }
        return {"reply": reply, "tone": TONE_OK, "card": card}

    if method == "GET" and path == "/products":
        products = p.get("products") or []
        for prod in products:
            if isinstance(prod, dict) and prod.get("product_id") and prod.get("name"):
                ctx.names[prod["product_id"]] = prod["name"]
        return {"reply": f"{len(products)} products in the catalog.", "tone": TONE_OK}
    if method == "GET" and path.startswith("/products/"):
        if p.get("product_id") and p.get("name"):
            ctx.names[p["product_id"]] = p["name"]
        return {"reply": f"{p.get('name')}: list {usd(p.get('base_price'))} CPM.", "tone": TONE_OK}
    if path == "/products/avails":
        est = p.get("estimatedCpm")
        return {
            "reply": (
                f"{imps(p.get('availableImpressions'))} imps available"
                + (f", about ${est:,.2f} CPM." if isinstance(est, (int, float)) else ".")
            ),
            "tone": TONE_OK,
        }
    if path == "/discovery":
        tier = p.get("access_tier")
        catalog = p.get("catalog") or []
        exact = any(isinstance(c, dict) and "price" in c for c in catalog)
        how = "exact prices" if exact else "price ranges only"
        return {"reply": f"Access tier: {tier}. {len(catalog)} products, {how}.", "tone": TONE_OK}
    if path == "/proposals":
        state = p.get("status")
        tone = {
            "accepted": TONE_OK,
            "countered": TONE_COUNTER,
            "pending_approval": TONE_PENDING,
            "rejected": TONE_REFUSED,
        }.get(state, TONE_OK)
        return {
            "reply": f"Proposal {p.get('proposal_id')}: {state} (recommendation: {p.get('recommendation')}).",
            "tone": tone,
        }
    if path == "/.well-known/agent.json":
        skills = [s.get("id") for s in p.get("skills") or [] if isinstance(s, dict)]
        return {
            "reply": f"I'm {p.get('name')}. Skills: {', '.join(skills) or 'none listed'}.",
            "tone": TONE_OK,
        }
    if path == "/media-kit":
        return {"reply": f"Media kit: {p.get('total_packages', 0)} packages.", "tone": TONE_OK}
    return {"reply": f"{status} OK.", "tone": TONE_OK}


def _price_line(
    final: Optional[float],
    base: Optional[float],
    pct: Any,
    tier: Optional[str],
    quote_id: Optional[str],
) -> str:
    if final is None:
        return "No price returned."
    line = f"${final:,.2f} CPM"
    if isinstance(pct, (int, float)) and pct > 0 and base is not None:
        line += f", {pct:g}% {tier or ''} discount off ${base:,.2f} list".replace("  ", " ")
    elif base is not None:
        line += " (list price)"
    if quote_id:
        line += f". Quote {quote_id}"
    return line + "."
