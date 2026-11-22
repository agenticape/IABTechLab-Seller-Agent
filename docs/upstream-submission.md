# Upstream submission kit

Text to paste when filing issues and opening PRs against
[IABTechLab/seller-agent](https://github.com/IABTechLab/seller-agent) from
the agenticape fork. Nothing here has been posted. Background and longer
write-ups are in [upstream-issues.md](upstream-issues.md).

Every branch below is based on upstream `main` at `eb40328` ("fix(api): keep
anonymous package reads in the public view (#116)"). It holds only `src/`,
`tests/`, `docs/` and `.env.example` changes, with nothing from `sandbox/`,
`infra/fly`, `fly.toml` or other fork-only files. Each branch passes
`pytest tests/unit tests/integration`, `ruff check src/` and
`ruff format --check src/ tests/` on its own, with the CI env vars
(`ANTHROPIC_API_KEY=test-key-for-ci`, `CREWAI_TELEMETRY_OPT_OUT=true`,
`OTEL_SDK_DISABLED=true`). Commit messages follow the repo's hygiene policy:
no tracker IDs and no session trailers.

## Suggested order

1. File the new issues in "New issues" and add the comments in "Comments on
   existing issues".
2. Open the split PRs in the order of the PR table below, putting each new
   issue number into its PR's `Fixes #` line. The split PRs touch some of the
   same files (`config/settings.py`, `.env.example`,
   `docs/guides/configuration.md`, `services/deal_service.py`,
   `interfaces/api/deps.py`), so after each merge the next branch may need a
   rebase. The conflicts are adjacent additions.
3. `upstream/security-pricing-fixes` holds all twelve fix commits together.
   Use it instead of the split PRs if the maintainers prefer one review.

Two findings are security-relevant: the SSRF in issue E and the
unauthenticated write routes (#86). If the repository has GitHub private
vulnerability reporting enabled (Security tab, "Report a vulnerability"),
file issue E there instead of as a public issue.

---

## New issues

### A. Discovery serves tier pricing to an unverified tier claim

Title:

```text
POST /discovery and POST /sessions price at a buyer tier claimed in the request body
```

Body:

````markdown
## Summary

`POST /discovery` builds its buyer context from the request body. A caller with no API key that sends `buyer_tier: "agency"` and any `agency_id` is answered at the agency tier instead of public price ranges. `POST /sessions` builds its context the same way. This is the same class of issue as #114 (closed) on `/packages`.

## Steps to reproduce

```bash
curl -s -X POST $SELLER/discovery -H 'Content-Type: application/json' \
  -d '{"query":"CTV inventory","buyer_tier":"agency","agency_id":"anything"}'
```

## Expected

A public-tier answer, because nothing verifies the claim.

## Actual

The answer is priced at the agency tier.

## Proposed fix

Resolve the buyer through `deps._verified_buyer_context` on `/discovery` and `/sessions`, as quotes and deal booking already do. That helper floors unverifiable claims to PUBLIC and keeps the registry ceiling and the blocked-agent 403. Move keyed package reads and media-kit search to the same helper for consistency.
````

### B. Pricing waterfall goes below the product floor

Title:

```text
Stacked tier and volume discounts price below the product's floor
```

Body:

````markdown
## Summary

`PricingRulesEngine.calculate_price` clamps only to the global `DEFAULT_PRICE_FLOOR_CPM`, never to the product's own floor, so stacked tier and volume discounts can undercut it.

## Steps to reproduce

With the `aws_workshop` CSV catalog, an advertiser-tier key (15% tier discount) prices 50M impressions (20% volume discount) on `inv-ctv-apex-series` (list $45.00, floor $38.25):

```bash
curl -s -X POST $SELLER/pricing -H "X-Api-Key: $ADVERTISER_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"inv-ctv-apex-series","volume":50000000}'
```

## Expected

A price at or above $38.25.

## Actual

$30.60.

## Proposed fix

Pass the product floor to `calculate_price` and clamp the final price to `max(product floor, global floor)`. Record the clamp in `applied_rules`. The buyer-facing rationale says discounts were capped by seller pricing policy without naming the floor. Quotes, `/pricing` and template bookings pass the product floor.
````

### C. Negotiation discloses the floor after one lowball offer

Title:

```text
Negotiation counters every below-floor offer at exactly the product floor
```

Body:

````markdown
## Summary

`NegotiationEngine` answers any below-floor offer with a counter at exactly the product floor. The wording never says "floor", but one lowball offer reveals the seller's reserve price.

## Steps to reproduce

Quote PD as an agency-tier buyer, then send a counter far below the floor:

```bash
curl -s -X POST $SELLER/api/v1/negotiations/messages -H "X-Api-Key: $AGENCY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"quote_id":"<quote_id>","action":"counter","buyer_price":{"amount_micros":1000000,"currency":"USD"},"idempotency_key":"neg-1"}'
```

## Expected

A counter that doesn't reveal the reserve price.

## Actual

`seller_price` equals the product floor.

## Proposed fix

Counter at `floor + (seller's last position - floor) x share`, never below the floor. By default `share` is the gap share the tier strategy already keeps (`1 - gap_split_buyer_share`). A new optional setting, `NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE`, overrides it, and `0` restores the current behaviour.
````

### D. `target_cpm` lets any caller quote at the floor

Title:

```text
POST /api/v1/quotes honours any target_cpm at or above the floor, for every tier
```

Body:

````markdown
## Summary

`create_quote` accepts any `target_cpm` at or above the product floor for every tier, including PUBLIC. Any buyer can quote, and then book, at the floor, while the quote's rationale still describes the tier price.

## Steps to reproduce

A seat-tier key on `inv-dsp-gnn-richmedia` (list $14.00, seat price $13.30, floor $11.90):

```bash
curl -s -X POST $SELLER/api/v1/quotes -H "X-Api-Key: $SEAT_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"inv-dsp-gnn-richmedia","deal_type":"PD","impressions":1000000,"target_cpm":11.90,"idempotency_key":"q-1"}'
```

## Expected

Quoted at the seat price ($13.30). A target is honoured only within the buyer's negotiation policy.

## Actual

Quoted at $11.90, with a rationale that says "Final price: $13.30".

## Proposed fix

Honour a target at or above the verified buyer's waterfall price. For negotiation-enabled tiers, also honour a target within the negotiation band (`max_negotiation_discount` from matching rules, default 10%). Never go below `max(product floor, global floor)`. Rebuild the rationale around the quoted price.
````

### E. Buyer-supplied `agent_url` is fetched without SSRF checks

Title:

```text
agent_url is fetched server-side with no scheme, address or redirect checks
```

Body:

````markdown
## Summary

Any request carrying an `agent_url` (`/discovery`, `/proposals`, `/api/v1/quotes`, `/sessions` and others) makes the seller `GET {agent_url}/.well-known/agent.json` with no validation, redirects followed, and a 15 s timeout. Any caller can make the seller send requests to loopback, private-network or cloud-metadata addresses.

## Steps to reproduce

```bash
curl -s -X POST $SELLER/discovery -H 'Content-Type: application/json' \
  -d '{"query":"x","agent_url":"http://169.254.169.254/latest/meta-data"}'
```

## Expected

The URL is refused without any outbound request.

## Actual

The seller sends the GET.

## Proposed fix

Before fetching, require `https`, reject credentials in the URL, and require the host to resolve only to public addresses (refuse loopback, private, link-local, reserved, multicast and unspecified). Don't follow redirects, and lower the default timeout to 5 s (`AGENT_CARD_FETCH_TIMEOUT_SECONDS`). Treat a refused URL like an unreachable card (PUBLIC tier). `AGENT_CARD_FETCH_ALLOW_PRIVATE_NETWORKS=true` relaxes the check for local development. DNS rebinding between the check and the fetch remains; pinning the resolved address in the transport would close it.
````

### F. PG quotes don't reserve capacity

Title:

```text
Programmatic Guaranteed quotes do not reserve capacity, so the same impressions can be sold twice
```

Body:

````markdown
## Summary

Each PG quote is checked against the product's `maximum_impressions` on its own. Two buyers can each quote, and book, the full guaranteed capacity of the same product.

## Steps to reproduce

On a product with `maximum_impressions=1000000`, two buyers each create a PG quote for 800,000 impressions over the same flight, then book.

## Expected

The second quote is refused or limited to the remaining 200,000.

## Actual

Both are quoted and booked.

## Proposed fix

A PG quote soft-holds its impressions in a per-product ledger until the quote expires, and booking turns the hold into a commitment until the flight ends. A PG quote over the remaining capacity gets 409 `insufficient_avails` with `available_impressions`. Uncapped products are not tracked. `PG_QUOTE_INVENTORY_HOLD_ENABLED` (default `true`) turns it off. Known limits: storage has no compare-and-set, so a tight race is still possible, and deal cancellation does not release the commitment.
````

### G. Proposals are accepted at the floor

Title:

```text
POST /proposals accepts any price at or above the floor instead of the buyer's tier price
```

Body:

````markdown
## Summary

`ProposalHandlingFlow.evaluate_pricing` treats any price at or above the product floor as acceptable, so any buyer, PUBLIC included, can close a proposal at the floor. The approval threshold also values a proposal at the requested price, so a low offer later negotiated toward list stays under the threshold.

## Steps to reproduce

An agency-tier key proposes $45.50 on `inv-ctv-apex-sports-nba` (list $52.00, agency price $46.80, floor $44.20):

```bash
curl -s -X POST $SELLER/proposals -H "X-Api-Key: $AGENCY_KEY" \
  -H 'Content-Type: application/json' \
  -d '{"product_id":"inv-ctv-apex-sports-nba","deal_type":"PD","price":45.50,"impressions":100000,"start_date":"2027-01-01","end_date":"2027-01-31"}'
```

## Expected

A counter toward the agency tier price.

## Actual

`accepted`.

## Proposed fix

Accept only at or above the verified buyer's waterfall price (the same `PricingRulesEngine` pricing as quotes), with the product floor as a hard minimum. Counter lower offers through `NegotiationEngine`, and downgrade an LLM crew `accept` below that price to a counter (related: #126). Value a proposal at `max(requested, list)` for the approval threshold.
````

### H. Booked deals carry an empty `wseat`

Title:

```text
Booked deals always send wseat: [] (and no wadomain), so any DSP seat can bid on a private deal
```

Body:

````markdown
## Summary

Deals booked through `POST /api/v1/deals` and `POST /deals` always have `openrtb_params.wseat: []`, and the quote booking path omits `wadomain`. Nothing ties a buyer's private deal to the buyer's seats or advertiser domains.

## Steps to reproduce

Book any quote and read `openrtb_params.wseat` / `wadomain`.

## Expected

The buyer's verified seat IDs and advertiser domains.

## Actual

`[]`, or the key is missing.

## Proposed fix

Add optional `seat_ids` and `advertiser_domains` to `BuyerIdentity` and buyer API key creation. When booking, put the caller's verified seat IDs (`seat_id` plus `seat_ids`) in `wseat` and its advertiser domains in `wadomain`. Only seller-issued API key identities count; request-body fields never fill the lists. Keys without these fields keep today's open deal.
````

---

## Comments on existing issues

### #86 (six mutating routes accept unauthenticated callers)

````markdown
A fix is on branch `upstream/write-route-auth` of agenticape/IABTechLab-Seller-Agent (PR to follow). It also covers the buyer write surface:

- Operator key required: `POST /api/v1/deals/curated`, `/deprecate`, `/migrate`, `/bulk`, plus `GET /api/v1/deals/{id}/buyer-status` and `/ssp-troubleshoot` (they call a buyer URL or an SSP, like push and distribute).
- API key required: `POST /api/v1/quotes`, `/proposals`, `/proposals/{id}/counter`, `/api/v1/negotiations/messages`, `/sessions`, `/api/v1/orders`, `/api/v1/change-requests`. `ALLOW_ANONYMOUS_BUYER_WRITES=true` (default `false`) restores anonymous PUBLIC-tier access.
- Sessions are bound to the key that opened them: another key gets 404, including on close (which currently returns 200 for unknown ids). `GET /sessions` lists only the caller's sessions unless the caller is an operator.
- `POST /agentic-audience/match` stays public (stateless scoring).
- Legacy `POST /deals` is handled separately under #128.
````

### #128 (legacy `POST /deals` returns fixed demo terms)

````markdown
A fix is on branch `upstream/legacy-deals-endpoint` of agenticape/IABTechLab-Seller-Agent (PR to follow):

- An API key is required (401 otherwise).
- The stored proposal is loaded: 404 `proposal_not_found` if missing; 409 `proposal_not_bookable` unless the same key submitted it (`submit_proposal` now records the key-derived pricing key); 409 `proposal_not_accepted` unless the proposal decision, an approval or an accepted negotiation accepted it.
- The deal uses the proposal's product, deal type, price (or the negotiated price), impressions and flight. A repeat call returns the same deal.
- Approval decisions now update the stored proposal's status.

The OpenDirect registration attempt and the `deal.registered` event are unchanged; that half of this issue is still open.
````

### #129 (accepting a proposal-led negotiation and booking its quote skips the approval decision)

````markdown
A fix for the booking side is on branch `upstream/quote-booking-approval-gate` of agenticape/IABTechLab-Seller-Agent (PR to follow). `deal_service.book_deal` applies the same rules as `POST /proposals` at the price actually being booked: the `APPROVAL_REQUIRED_ABOVE_VALUE` threshold on gross value, and the opt-in gate for `deal_booking` (or `proposal_decision` when the booking uses a proposal-led negotiated price). A held booking returns HTTP 202 `{"status": "pending_approval", "approval_id": ...}` and mints nothing. The buyer re-submits after the decision: approved terms book, a rejection is 409 `booking_rejected`, and an approval for different terms raises a new request.
````

### #147 (quote-backed PG deal returns at=1)

````markdown
A fix is on branch `upstream/openrtb-auction-type` of agenticape/IABTechLab-Seller-Agent (PR to follow). The inversion is in `deal_service.book_deal` (PG/PD got `at=1`, PA got `at=3`). The branch adds one `build_openrtb_deal_params` in `services/deals_api.py`, used by quote booking, `DealGenerationFlow` and the Deals API sync path. PG/PD get `at=3` with `bidfloor` as the deal price, PA gets `at=1`, `wseat`/`wadomain` are always present, and PG carries guaranteed impressions in `ext`. The booking currency follows the quote's currency.
````

### #131 and #146

Not fixed by these branches; no comment needed. #146's CSV currency
labelling is unchanged, although `bidfloorcur` on booked deals now follows
the quote's currency (`upstream/openrtb-auction-type`).

---

## Pull requests

Compare URLs open GitHub's "Open a pull request" page from the fork branch to
upstream `main`.

| # | Branch | Fixes | Depends on |
|---|---|---|---|
| 1 | `upstream/write-route-auth` | #86 | - |
| 2 | `upstream/agent-url-ssrf-guard` | issue E | - |
| 3 | `upstream/discovery-tier-verification` | issue A | - |
| 4 | `upstream/pricing-floor-enforcement` | issues B, D, G | - |
| 5 | `upstream/negotiation-floor-counter` | issue C | - |
| 6 | `upstream/quote-booking-approval-gate` | #129 | - |
| 7 | `upstream/legacy-deals-endpoint` | #128 | - |
| 8 | `upstream/openrtb-auction-type` | #147 | - |
| 9 | `upstream/deal-allow-lists` | issue H | 7 and 8 |
| 10 | `upstream/pg-quote-holds` | issue F | - |
| - | `upstream/security-pricing-fixes` | all of the above | alternative to 1-10 |

### 1. `upstream/write-route-auth`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/write-route-auth>

Title:

```text
fix(auth): require credentials on state-changing routes
```

Description:

````markdown
Fixes #86.

Several routes that create or change state accepted anonymous callers.

**Operator key required**
- `POST /api/v1/deals/curated`, `/api/v1/deals/{id}/deprecate`, `/api/v1/deals/{id}/migrate`
- `POST /api/v1/deals/bulk` (acts on any quote or deal by id, skipping `POST /api/v1/deals`'s ownership, tier and approval checks)
- `GET /api/v1/deals/{id}/buyer-status` and `/ssp-troubleshoot` (call a caller-supplied buyer URL or an SSP, like push/distribute)

**API key required** (`deps._buyer_write_api_key_record`): `POST /api/v1/quotes`, `/proposals`, `/proposals/{id}/counter`, `/api/v1/negotiations/messages`, `/sessions`, `/api/v1/orders`, `/api/v1/change-requests`. `ALLOW_ANONYMOUS_BUYER_WRITES=true` (default `false`) restores anonymous PUBLIC-tier access.

**Sessions** belong to the key that opened them. Reading, messaging or closing one takes that key or an operator key (404 otherwise, including unknown ids, which used to close with 200). `GET /sessions` lists only the caller's sessions unless the caller is an operator.

`POST /agentic-audience/match` stays public (stateless score), as do `/health`, the agent card and read-only discovery.

**Breaking for buyer agents:** keyless writes get 401; bulk/migrate are operator-only; foreign or unknown session ids are 404.

Tests: `tests/unit/test_mutating_route_auth.py`. Tests of the anonymous PUBLIC-tier path opt in through a new `anonymous_buyer_writes` fixture in `tests/conftest.py`. Docs: `docs/api/authentication.md`, `docs/guides/configuration.md`, `.env.example`.
````

### 2. `upstream/agent-url-ssrf-guard`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/agent-url-ssrf-guard>

Title:

```text
fix(registry): guard buyer-supplied agent_url fetches against SSRF
```

Description:

````markdown
Fixes #<issue E>.

`fetch_agent_card` now runs `agent_url_fetch_violation` before any request:

- `https` only, no credentials in the URL;
- the host must resolve only to public addresses (loopback, private, link-local, reserved, multicast and unspecified are refused);
- redirects are not followed;
- timeout from `AGENT_CARD_FETCH_TIMEOUT_SECONDS` (default 5 s, max 30).

A refused URL is logged and treated like an unreachable card (PUBLIC tier). `AGENT_CARD_FETCH_ALLOW_PRIVATE_NETWORKS` (default `false`) relaxes the check for local development.

Not covered: DNS rebinding between the check and the fetch. Pinning the resolved address in the transport would close it.

Tests: `tests/unit/test_agent_url_ssrf_guard.py`; `test_agent_registry.py`'s success case stubs DNS resolution. Docs: `docs/guides/configuration.md`, `.env.example`.
````

### 3. `upstream/discovery-tier-verification`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/discovery-tier-verification>

Title:

```text
fix(api): verify buyer tier on discovery, packages, media-kit search and sessions
```

Description:

````markdown
Fixes #<issue A>.

`POST /discovery` and `POST /sessions` built their buyer context from request-body claims, so a keyless request claiming `buyer_tier=agency` was priced at the agency tier. Both now resolve the buyer through `deps._verified_buyer_context`, which floors unverifiable claims to PUBLIC and keeps the registry ceiling and the blocked-agent 403. Keyed package reads and media-kit search use the same helper.

Tests: `tests/unit/test_discovery_tier_verification.py`.
````

### 4. `upstream/pricing-floor-enforcement`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/pricing-floor-enforcement>

Title:

```text
fix(pricing): enforce the product floor and tier price on pricing, quotes and proposals
```

Description:

````markdown
Fixes #<issue B>, fixes #<issue D>, fixes #<issue G>.

Three commits, each with its own test:

1. **Clamp the waterfall at the product floor.** `calculate_price` takes the product floor and clamps to `max(product floor, global floor)`. The clamp is recorded in `applied_rules`; the buyer-facing rationale doesn't name the floor. Test: `test_pricing_floor_clamp.py`.
2. **`target_cpm` only within the buyer's tier policy.** `PricingRulesEngine.apply_target_price` honours a target at or above the verified buyer's waterfall price, or within the negotiation band (default 10%) for negotiation-enabled tiers. It never goes below the floor, and the rationale and quote history use the quoted price. Test: `test_quote_target_cpm.py`.
3. **Accept proposals at the tier price.** `ProposalHandlingFlow` accepts only at or above the buyer's waterfall price (floor as hard minimum) and counters lower offers; a crew `accept` below that price becomes a counter. The approval threshold values a proposal at `max(requested, list)`. Test: `test_proposal_tier_acceptance.py`. Existing flow tests that proposed between floor and public price now propose at the tier price.

**Behaviour change:** buyers can no longer close at the floor through `target_cpm` or `POST /proposals`.
````

### 5. `upstream/negotiation-floor-counter`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/negotiation-floor-counter>

Title:

```text
fix(negotiation): counter below-floor offers above the floor
```

Description:

````markdown
Fixes #<issue C>.

A below-floor offer is now countered at `floor + (seller's last position - floor) x share`, never below the floor. `share` defaults to the gap share the tier strategy keeps (`1 - gap_split_buyer_share`). `NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE` overrides it, and `0` restores counter-at-floor. The proposal flow builds its engine through `negotiation_service.build_negotiation_engine` so both surfaces share the setting. The rationale still names neither the floor nor the price.

Tests: `tests/unit/test_negotiation_below_floor_margin.py`; existing tests that asserted a counter at the floor now assert a counter at or above it. Docs: `docs/guides/configuration.md`, `.env.example`.
````

### 6. `upstream/quote-booking-approval-gate`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/quote-booking-approval-gate>

Title:

```text
fix(deals): apply the approval gate to quote booking
```

Description:

````markdown
Fixes #129 (booking side).

`deal_service.book_deal` applies the approval rules at the price being booked: the `APPROVAL_REQUIRED_ABOVE_VALUE` threshold on gross value, and the opt-in gate for `deal_booking`, or `proposal_decision` when the booking uses a proposal-led negotiated price.

A held booking returns **HTTP 202** with `status: "pending_approval"` and an `approval_id`, and mints nothing. The buyer re-submits after the operator decides: approved terms book, a rejection is 409 `booking_rejected`, and an approval for different terms raises a new request. `resume` handles `deal_booking` approvals.

**Behaviour change for buyer agents:** `POST /api/v1/deals` can return 202.

Tests: `tests/unit/test_quote_booking_approval_gate.py`. Docs: `docs/guides/approval-rules.md`.
````

### 7. `upstream/legacy-deals-endpoint`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/legacy-deals-endpoint>

Title:

```text
fix(deals): build legacy POST /deals deals from the caller's accepted proposal
```

Description:

````markdown
Fixes the terms half of #128.

`POST /deals` now requires an API key (`deps._require_api_key_record`) and loads the stored proposal:

- 404 `proposal_not_found` if missing;
- 409 `proposal_not_bookable` unless the same key submitted it (`submit_proposal` records the key-derived pricing key);
- 409 `proposal_not_accepted` unless the proposal decision, an approval or an accepted negotiation accepted it.

The deal uses the proposal's product, deal type, price (or negotiated price), impressions and flight, and a repeat call returns the same deal. Approval decisions now update the stored proposal's status. The OpenDirect registration attempt is unchanged.

**Breaking:** keyless callers get 401, and proposals submitted without a key can't use this route.

Tests: `tests/unit/test_legacy_deal_generation.py`.
````

### 8. `upstream/openrtb-auction-type`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/openrtb-auction-type>

Title:

```text
fix(deals): emit OpenRTB at=3 for PG/PD and at=1 for PA from one builder
```

Description:

````markdown
Fixes #147.

Quote booking emitted `at=1` for PG/PD and `at=3` for PA, the reverse of OpenRTB. The proposal deal path and the Deals API sync path each built their own dict.

`build_openrtb_deal_params` in `services/deals_api.py` is now the single builder for all three paths: PG/PD `at=3` (`bidfloor` is the deal price), PA `at=1` (`bidfloor` is the floor), `wseat` and `wadomain` always present (empty means unrestricted), and PG carries guaranteed impressions in `ext`. The booking currency follows the quote's currency.

**Breaking for consumers of `openrtb_params.at`.**

Tests: `tests/unit/test_openrtb_deal_params.py`; `test_deal_booking_endpoints.py` now expects PA `at=1` and adds a PG/PD `at=3` case.
````

### 9. `upstream/deal-allow-lists`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/deal-allow-lists>

This branch is stacked on `upstream/legacy-deals-endpoint` and includes the
`upstream/openrtb-auction-type` commit, so until PRs 7 and 8 merge, its
compare view also shows their commits. Open it after they merge, or open it
as a draft that says so.

Title:

```text
fix(deals): populate wseat and wadomain from the verified buyer
```

Description:

````markdown
Fixes #<issue H>. Depends on the legacy `POST /deals` fix (#128) and the OpenRTB builder (#147); review the last commit only.

`BuyerIdentity` and buyer API key creation gain optional `seat_ids` and `advertiser_domains`. Quote booking (`POST /api/v1/deals`) and legacy deal generation (`POST /deals`) put the caller's verified seat IDs (`seat_id` plus `seat_ids`) in `wseat` and its advertiser domains in `wadomain`, via `verified_deal_allow_lists`. Only seller-issued API key identities count. Keys without these fields keep the current open deal.

Tests: `tests/unit/test_deal_allow_lists.py`, plus a legacy-path case in `test_legacy_deal_generation.py`. Docs: `docs/api/authentication.md`, `docs/guides/agent-management.md`.
````

### 10. `upstream/pg-quote-holds`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/pg-quote-holds>

Title:

```text
fix(quotes): soft-hold PG quote impressions against product capacity
```

Description:

````markdown
Fixes #<issue F>.

A PG quote holds its impressions in a per-product ledger until the quote expires. Booking turns the hold into a commitment until the flight ends (+1 day). A PG quote over the remaining capacity is refused with 409 `insufficient_avails` and `available_impressions`. Products without `maximum_impressions` are not tracked. `PG_QUOTE_INVENTORY_HOLD_ENABLED` (default `true`) turns it off.

Known limits: the storage backend has no compare-and-set, so two concurrent quotes can still race. Cancelling a deal does not release its commitment.

Tests: `tests/unit/test_pg_quote_inventory_hold.py`. Docs: `docs/guides/configuration.md`, `.env.example`.
````

### Combined: `upstream/security-pricing-fixes`

Compare: <https://github.com/IABTechLab/seller-agent/compare/main...agenticape:IABTechLab-Seller-Agent:upstream/security-pricing-fixes>

Title:

```text
Pricing, booking and auth fixes (floor enforcement, approval gate, OpenRTB at, write-route auth, SSRF)
```

Description:

````markdown
All twelve fixes from the split PRs, as one branch with one commit per fix (each with its own regression test), for maintainers who prefer a single review.

| Commit | Fixes |
|---|---|
| fix(api): verify buyer tier on discovery, packages, media-kit search and sessions | #<issue A> |
| fix(pricing): clamp the pricing waterfall at the product floor | #<issue B> |
| fix(negotiation): counter below-floor offers above the floor | #<issue C> |
| fix(deals): apply the approval gate to quote booking | #129 |
| fix(quotes): honour target_cpm only within the buyer's tier policy | #<issue D> |
| fix(quotes): soft-hold PG quote impressions against product capacity | #<issue F> |
| fix(deals): build legacy POST /deals deals from the caller's accepted proposal | #128 |
| fix(proposals): accept proposals at the buyer's tier price, not the floor | #<issue G> |
| fix(deals): emit OpenRTB at=3 for PG/PD and at=1 for PA from one builder | #147 |
| fix(deals): populate wseat and wadomain from the verified buyer | #<issue H> |
| fix(auth): require credentials on state-changing routes | #86 |
| fix(registry): guard buyer-supplied agent_url fetches against SSRF | #<issue E> |

**Breaking changes for buyer agents**
- Buyer write routes need an API key unless `ALLOW_ANONYMOUS_BUYER_WRITES=true`.
- `bulk`, `migrate`, `deprecate`, `curated`, `buyer-status` and `ssp-troubleshoot` are operator-only.
- Unknown or foreign session ids are 404 (including close).
- `POST /api/v1/deals` can return 202 `pending_approval`.
- OpenRTB `at`: PG/PD now 3, PA now 1; `wseat`/`wadomain` always present.
- Legacy `POST /deals` needs a key and the caller's accepted proposal, and returns its terms.
- `target_cpm` below the tier price is honoured only inside the negotiation band; PG quotes can return 409 `insufficient_avails`.

New settings: `NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE`, `PG_QUOTE_INVENTORY_HOLD_ENABLED`, `ALLOW_ANONYMOUS_BUYER_WRITES`, `AGENT_CARD_FETCH_TIMEOUT_SECONDS`, `AGENT_CARD_FETCH_ALLOW_PRIVATE_NETWORKS` (documented in `docs/guides/configuration.md` and `.env.example`).

Verified: `pytest tests/unit` 2038 passed; `pytest tests/integration` 39 passed, 28 skipped; `ruff check src/` and `ruff format --check src/ tests/` clean.

Not covered: `GET /api/v1/deals/export`, single-deal and performance reads, and the orders/change-request lists are still readable without a key; the MCP and AgentCore interfaces were not audited; #131 and #146 are unchanged.
````
