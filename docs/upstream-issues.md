# Upstream issue drafts

Write-ups of the pricing, booking and auth defects fixed on this fork, ready
to file against [IABTechLab/seller-agent](https://github.com/IABTechLab/seller-agent).
Each entry maps to one commit on the fork, so each fix can be cherry-picked
into its own upstream PR together with its regression test. Paste-ready
issue and PR text, with the upstream branches, is in
[upstream-submission.md](upstream-submission.md).

All repros use the fictional `aws_workshop` CSV catalog
(`AD_SERVER_TYPE=csv`, `CSV_DATA_DIR=data/csv/samples/aws_workshop`). In that
catalog a product's list price is the CSV `floor_price_cpm`, and its internal
floor is 85% of list. `$SELLER` is the base URL of a local seller, and
`$SEAT_KEY`, `$AGENCY_KEY` and `$ADV_KEY` are buyer API keys at the seat,
agency and advertiser tiers, minted locally.

Behaviour that is a business choice keeps the upstream default and gains a
setting. Security fixes are on by default and have an opt-out only where
existing clients may rely on the old behaviour.

Related open issues: [#86](https://github.com/IABTechLab/seller-agent/issues/86),
[#128](https://github.com/IABTechLab/seller-agent/issues/128),
[#129](https://github.com/IABTechLab/seller-agent/issues/129),
[#131](https://github.com/IABTechLab/seller-agent/issues/131),
[#146](https://github.com/IABTechLab/seller-agent/issues/146),
[#147](https://github.com/IABTechLab/seller-agent/issues/147).

---

## 1. `POST /discovery` serves exact tier prices to an unverified tier claim

**Summary.** `POST /discovery` built its buyer context from the request body.
A keyless caller that sends `buyer_tier: "agency"` and any `agency_id` gets
exact agency-tier CPMs instead of public price ranges. `POST /sessions` had
the same gap. This is the same class of leak as the closed
[#114](https://github.com/IABTechLab/seller-agent/issues/114) on `/packages`.

**Repro.**

```bash
curl -s -X POST $SELLER/discovery -H 'Content-Type: application/json' \
  -d '{"query":"CTV inventory","buyer_tier":"agency","agency_id":"anything"}'
```

**Expected.** Public-tier answer (price ranges, no exact tier pricing),
because nothing verifies the claim.

**Actual.** The answer is priced at the agency tier.

**Proposed fix.** Resolve the buyer through `deps._verified_buyer_context`
on `/discovery` and `/sessions`. That helper floors unverifiable claims to
PUBLIC and keeps the registry ceiling and the blocked-agent 403. The keyed
package reads and media-kit search move to the same helper so every
discovery surface verifies the tier the same way. An audit of `/packages`,
`/media-kit/search` and `/sessions` found no other path that trusts body
claims.

**Fork commit.** `fix(api): verify buyer tier on discovery, packages, media-kit search and sessions`
(test: `tests/unit/test_discovery_tier_verification.py`).

---

## 2. Stacked tier and volume discounts price below the product floor

**Summary.** `PricingRulesEngine.calculate_price` clamps only to the global
`DEFAULT_PRICE_FLOOR_CPM`, never to the product's own floor. Stacked
discounts can undercut it.

**Repro.** Advertiser key (15% tier discount) prices 50M impressions
(20% volume discount) on `inv-ctv-apex-series` (list $45.00, floor $38.25):

```bash
curl -s -X POST $SELLER/pricing -H "X-Api-Key: $ADV_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"inv-ctv-apex-series","volume":50000000}'
```

**Expected.** Price at or above $38.25.

**Actual.** $30.60.

**Proposed fix.** `calculate_price` takes the product floor and clamps the
final price to `max(product floor, global floor)`. The clamp is recorded in
`applied_rules`; the buyer-facing rationale says discounts were capped by
seller pricing policy without naming the floor. Quotes, `/pricing` and
template bookings pass the product floor.

**Fork commit.** `fix(pricing): clamp the pricing waterfall at the product floor`
(test: `tests/unit/test_pricing_floor_clamp.py`).

---

## 3. A below-floor negotiation offer is countered exactly at the floor

**Summary.** The negotiation engine answers every below-floor offer with a
counter at the product floor (step 3.5 in `engines/negotiation_engine.py`).
The wording never says "floor", but one lowball offer reveals the seller's
reserve price.

**Repro.** Agency key quotes PD on a product, then offers far below the floor:

```bash
curl -s -X POST $SELLER/api/v1/negotiations/messages -H "X-Api-Key: $AGENCY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"quote_id":"<quote_id>","action":"counter","buyer_price":{"amount_micros":1000000,"currency":"USD"},"idempotency_key":"neg-1"}'
```

**Expected.** A counter that does not disclose the reserve.

**Actual.** The seller counters at exactly the product floor.

**Proposed fix.** Counter at
`floor + (seller's last position - floor) x share`, never below the floor.
`share` defaults to the gap share the tier strategy already keeps
(`1 - gap_split_buyer_share`). The new setting
`NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE` overrides it, and `0` restores the
current counter-at-floor behaviour. The proposal flow builds its engine
through `negotiation_service.build_negotiation_engine` so both surfaces use
the setting.

**Fork commit.** `fix(negotiation): counter below-floor offers above the floor`
(test: `tests/unit/test_negotiation_below_floor_margin.py`).

---

## 4. Quote booking bypasses the approval gate

**Summary.** `POST /proposals` holds a high-value decision for a human, but
`POST /api/v1/deals` books a quote of the same value directly. That includes
a proposal-led negotiated price whose proposal approval is pending or
rejected. This is the booking half of
[#129](https://github.com/IABTechLab/seller-agent/issues/129).

**Repro.** With `APPROVAL_REQUIRED_ABOVE_VALUE=250000`, the advertiser quotes
PD for 8M impressions on `inv-ctv-apex-sports-nba` (a $52 product) (about $354k gross) and books it:

```bash
curl -s -X POST $SELLER/api/v1/deals -H "X-Api-Key: $ADV_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"quote_id":"<quote_id>","idempotency_key":"book-1"}'
```

**Expected.** The booking is held for an operator decision, as an
equivalent proposal would be.

**Actual.** A deal is minted immediately.

**Proposed fix.** `deal_service.book_deal` applies the approval rules at the
price actually being booked: the `APPROVAL_REQUIRED_ABOVE_VALUE` threshold on
gross value, plus the opt-in gate for `deal_booking` (or `proposal_decision`
when the booking uses a proposal-led negotiated price). A held booking
returns HTTP 202 with `status: "pending_approval"` and an `approval_id`, and
mints nothing. The buyer re-submits once an operator decides: approved terms
book, a rejection is 409 `booking_rejected`, and an approval for different
terms raises a new request. The re-submit can use a new `idempotency_key`;
proposals still have no idempotency key
([#131](https://github.com/IABTechLab/seller-agent/issues/131), not fixed
here).

**Fork commit.** `fix(deals): apply the approval gate to quote booking`
(test: `tests/unit/test_quote_booking_approval_gate.py`).

---

## 5. `target_cpm` lets any caller quote at the floor

**Summary.** `create_quote` accepts any `target_cpm` at or above the product
floor for every tier, keyless PUBLIC callers included. Anyone can quote, and
then book, at the floor, while the rationale still describes the tier
waterfall price.

**Repro.**

```bash
curl -s -X POST $SELLER/api/v1/quotes -H "X-Api-Key: $SEAT_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"inv-dsp-gnn-richmedia","deal_type":"PD","impressions":1000000,"target_cpm":11.90,"idempotency_key":"q-1"}'
```

**Expected.** A seat-tier buyer is quoted at its tier price ($13.30 on a
$14.00 product). A target is honoured only inside the buyer's negotiation
policy.

**Actual.** Quoted at $11.90 (the floor), with a rationale that says
"Final price: $13.30".

**Proposed fix.** `PricingRulesEngine.apply_target_price` honours a target
at or above the verified buyer's waterfall price. For negotiation-enabled
tiers it also honours a target within the negotiation band
(`max_negotiation_discount` from matching rules, default 10%). It never goes
below `max(product floor, global floor)`. The rationale is rebuilt around the
price actually quoted, and quote history records that price.

**Fork commit.** `fix(quotes): honour target_cpm only within the buyer's tier policy`
(test: `tests/unit/test_quote_target_cpm.py`).

---

## 6. PG quotes don't reserve capacity, so guaranteed impressions can be sold twice

**Summary.** Each PG quote is checked against the product's
`maximum_impressions` on its own. Two buyers can each quote, and book, the
full guaranteed capacity.

**Repro.** On a product with `maximum_impressions=1000000`, two buyers each
request a PG quote for 800,000 impressions over the same flight. Both
succeed, and both book.

**Expected.** The second quote is refused, or offered only the remaining
200,000.

**Actual.** Both are quoted and booked.

**Proposed fix.** A PG quote soft-holds its impressions in a per-product
ledger until the quote expires. Booking turns the hold into a commitment
that lasts until the flight ends. A PG quote larger than the remaining
capacity is refused with 409 `insufficient_avails` and the
`available_impressions`. Products with no declared cap are not tracked.
`PG_QUOTE_INVENTORY_HOLD_ENABLED` (default `true`) turns the hold off. Known
limits: the storage backend has no compare-and-set, so a tight race between
two quotes is still possible, and cancelling a deal does not release its
commitment.

**Fork commit.** `fix(quotes): soft-hold PG quote impressions against product capacity`
(test: `tests/unit/test_pg_quote_inventory_hold.py`).

---

## 7. Legacy `POST /deals` mints demo terms for any `proposal_id`

**Summary.** `POST /deals` takes any `proposal_id` without authentication
and returns a deal with hardcoded demo terms ($15 PD on "display"), whether
the proposal exists, was accepted, or belongs to the caller. See
[#128](https://github.com/IABTechLab/seller-agent/issues/128).

**Repro.**

```bash
curl -s -X POST $SELLER/deals -H 'Content-Type: application/json' \
  -d '{"proposal_id":"does-not-exist"}'
```

**Expected.** 401 without a key; 404 for an unknown proposal; 409 for a
proposal that isn't the caller's or isn't accepted; otherwise a deal on the
proposal's own terms.

**Actual.** 200 with a $15 PD display deal.

**Proposed fix.** Require an API key (new `deps._require_api_key_record`).
Load the stored proposal and return 404 `proposal_not_found` if it's
missing. Return 409 `proposal_not_bookable` unless the same key submitted it
(`submit_proposal` now records the key-derived pricing key). Return 409
`proposal_not_accepted` unless the proposal decision, an approval or an
accepted negotiation accepted it. The deal carries the proposal's product,
deal type, price (or the negotiated price), impressions and flight, and a
repeat call returns the same deal. Approval decisions now update the stored
proposal's status. Proposals submitted without a key can't use this route.

**Fork commit.** `fix(deals): build legacy POST /deals deals from the caller's accepted proposal`
(test: `tests/unit/test_legacy_deal_generation.py`).

---

## 8. Proposals are accepted at the floor instead of the buyer's tier price

**Summary.** `ProposalHandlingFlow.evaluate_pricing` treats any price at or
above the product floor as acceptable. Every buyer, keyless PUBLIC callers
included, can close a proposal at the floor. Separately, the approval
threshold values a proposal at the requested price, so a low offer that the
negotiation moves toward list can stay under the threshold.

**Repro.** The agency key proposes $45.50 on `inv-ctv-apex-sports-nba`, a $52.00 product (floor $44.20,
agency tier price $46.80):

```bash
curl -s -X POST $SELLER/proposals -H "X-Api-Key: $AGENCY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"inv-ctv-apex-sports-nba","deal_type":"PD","price":45.50,"impressions":100000,"start_date":"2027-01-01","end_date":"2027-01-31"}'
```

**Expected.** A counter toward the agency tier price.

**Actual.** `accepted`.

**Proposed fix.** A proposal is acceptable only at or above the verified
buyer's waterfall price (computed by `PricingRulesEngine`, as on the quote
path), with the product floor as a hard minimum. A lower offer is countered
through the `NegotiationEngine`, and an LLM crew `accept` below that price is
downgraded to a counter (related:
[#126](https://github.com/IABTechLab/seller-agent/issues/126)). The approval
threshold values a proposal at `max(requested, list)`.

**Fork commit.** `fix(proposals): accept proposals at the buyer's tier price, not the floor`
(test: `tests/unit/test_proposal_tier_acceptance.py`).

---

## 9. Quote-booked deals emit an inverted OpenRTB auction type

**Summary.** `deal_service.book_deal` emits `at=1` for PG/PD and `at=3` for
PA, the reverse of OpenRTB 2.x, so DSPs are told fixed-price deals are
first-price auctions and vice versa. The proposal and Deals API sync paths
each build their own OpenRTB dict. See
[#147](https://github.com/IABTechLab/seller-agent/issues/147).

**Repro.** Quote and book a PG deal through `POST /api/v1/quotes` and
`POST /api/v1/deals`, then read `openrtb_params.at`.

**Expected.** PG/PD `at=3` (fixed price, `bidfloor` is the deal price); PA
`at=1`.

**Actual.** PG/PD `at=1`; PA `at=3`.

**Proposed fix.** A single `build_openrtb_deal_params` in
`services/deals_api.py` serves all three paths. PG/PD get `at=3`, PA gets
`at=1`. `wseat` and `wadomain` are always present (empty means
unrestricted), and PG carries guaranteed impressions in `ext`. The booking
currency follows the quote's currency. That keeps `bidfloorcur` consistent
with the quote, but doesn't fix the CSV currency labelling in
[#146](https://github.com/IABTechLab/seller-agent/issues/146).

**Fork commit.** `fix(deals): emit OpenRTB at=3 for PG/PD and at=1 for PA from one builder`
(test: `tests/unit/test_openrtb_deal_params.py`).

---

## 10. Booked deals have empty `wseat`, so any DSP seat can bid on a private deal

**Summary.** Booked deals always go out with `wseat: []`, and with no
`wadomain` key on the quote booking path. Nothing ties a buyer's private
deal to the buyer's seats or advertiser domains.

**Repro.** Book any quote and read `openrtb_params.wseat`.

**Expected.** The buyer's verified seat IDs and advertiser domains.

**Actual.** `[]` / missing.

**Proposed fix.** `BuyerIdentity` and buyer API key creation gain optional
`seat_ids` and `advertiser_domains`. Quote booking and legacy deal
generation stamp the caller's verified seat IDs (`seat_id` plus `seat_ids`)
as `wseat` and its advertiser domains as `wadomain`, through
`verified_deal_allow_lists`. Only seller-issued API key identities count;
self-asserted request fields never populate the lists. Keys without these
fields keep today's open deal.

**Fork commit.** `fix(deals): populate wseat and wadomain from the verified buyer`
(test: `tests/unit/test_deal_allow_lists.py`).

---

## 11a. State-changing routes accept anonymous callers

**Summary.** Several routes that create or change state have no auth guard.
This extends [#86](https://github.com/IABTechLab/seller-agent/issues/86) to
the buyer write surface and the session routes.

**Repro.**

```bash
curl -s -X POST $SELLER/api/v1/deals/curated -H 'Content-Type: application/json' -d '{...}'
curl -s -X POST $SELLER/sessions/<any-id>/close
curl -s -X POST $SELLER/api/v1/deals/bulk -H 'Content-Type: application/json' -d '{...}'
```

**Expected.** 401/403 without the right credentials.

**Actual.** Each request is processed.

**Proposed fix.**
- Operator key required: `POST /api/v1/deals/curated`, `/deprecate`,
  `/migrate` and `/bulk` (bulk acts on any quote or deal by id and skips
  `POST /api/v1/deals`'s ownership, tier and approval checks), and
  `GET .../buyer-status` and `.../ssp-troubleshoot`, which call a buyer URL
  or an SSP, like push and distribute.
- API key required on the buyer write routes: `POST /api/v1/quotes`,
  `/proposals`, `/proposals/{id}/counter`, `/api/v1/negotiations/messages`,
  `/sessions`, `/api/v1/orders` and `/api/v1/change-requests`.
  `ALLOW_ANONYMOUS_BUYER_WRITES=true` (default `false`) restores anonymous
  PUBLIC-tier access for deployments that rely on it.
- Sessions belong to the key that opened them. Reading, messaging or closing
  one takes that key or an operator key (404 otherwise, including unknown ids,
  which used to close with 200). `GET /sessions` lists only the caller's
  sessions unless the caller is an operator.
- `POST /agentic-audience/match` stays public (stateless score), as do
  `/health`, the agent card and read-only discovery.

**Fork commit.** `fix(auth): require credentials on state-changing routes`
(test: `tests/unit/test_mutating_route_auth.py`).

## 11b. Buyer-supplied `agent_url` is fetched with no SSRF guard

**Summary.** Any request carrying an `agent_url` (`/discovery`,
`/proposals`, quotes, sessions and others) makes the seller
`GET {agent_url}/.well-known/agent.json` with no checks and a 15 s timeout.
A caller can point it at loopback, the private network or a cloud metadata
endpoint.

**Repro.**

```bash
curl -s -X POST $SELLER/discovery -H 'Content-Type: application/json' \
  -d '{"query":"x","agent_url":"http://169.254.169.254/latest/meta-data"}'
```

**Expected.** The URL is refused without any outbound request.

**Actual.** The seller sends the GET.

**Proposed fix.** `fetch_agent_card` first runs `agent_url_fetch_violation`:
https only, no credentials in the URL, and the host must resolve only to
public addresses (loopback, private, link-local, reserved, multicast and
unspecified are refused). Redirects are not followed. The timeout comes from
`AGENT_CARD_FETCH_TIMEOUT_SECONDS` (default 5 s). A refused URL is logged
and treated like an unreachable card (PUBLIC tier).
`AGENT_CARD_FETCH_ALLOW_PRIVATE_NETWORKS` (default `false`) relaxes the check
for local development. Residual risk: DNS rebinding between the check and the
fetch is not closed. Pinning the resolved address in the transport would
close it.

**Fork commit.** `fix(registry): guard buyer-supplied agent_url fetches against SSRF`
(test: `tests/unit/test_agent_url_ssrf_guard.py`).

---

## Breaking changes for buyer agents

- Buyer write routes need an API key unless the seller sets
  `ALLOW_ANONYMOUS_BUYER_WRITES=true` (11a).
- `POST /api/v1/deals/bulk` and `/migrate` are operator-only (11a).
- Unknown or foreign session ids return 404, including on close (11a).
- `POST /api/v1/deals` can return 202 `pending_approval`; re-submit after the
  operator decides (4).
- OpenRTB `at` flips: PG/PD are now 3, PA is now 1. `wseat`/`wadomain` are
  always present (9, 10).
- Legacy `POST /deals` needs a key and the caller's accepted proposal, and
  returns the proposal's terms (7).
- `target_cpm` below the tier price is no longer honoured outside the
  negotiation band (5). PG quotes can return 409 `insufficient_avails` (6).

## Not covered here

- `GET /api/v1/deals/export`, `GET /api/v1/deals/{id}` and its performance
  read, and the `GET` lists for orders and change requests are still
  readable without a key.
- The MCP (`/mcp`) and AgentCore interfaces were not audited for the same
  issues.
- [#131](https://github.com/IABTechLab/seller-agent/issues/131) (proposal
  idempotency) and [#146](https://github.com/IABTechLab/seller-agent/issues/146)
  (CSV currency) are not fixed.
