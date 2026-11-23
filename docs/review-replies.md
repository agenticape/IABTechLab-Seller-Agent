# Review replies: upstream PRs #153–#160

Paste-ready replies to the end-to-end reviews on IABTechLab/seller-agent
#153–#159, a note for #160, the suggested merge order, and texts for the
follow-up issues proposed in the replies. Nothing here has been posted.

All branches are still based on upstream `main@eb40328`. The new commits sit
on top of the reviewed heads (no history rewritten), except #160, which is
stacked and was rebuilt. On every branch the full unit suite passes,
`tests/integration/` passes, and `ruff check src/` and
`ruff format --check src/ tests/` are clean.

| PR | Branch | Reviewed head | New head | Unit tests |
|---|---|---|---|---|
| #153 | `upstream/write-route-auth` | `9ab0f01` | `c0cf4c6` | 1976 passed |
| #154 | `upstream/discovery-tier-verification` | `8c780e2` | `dddc655` | 1909 passed |
| #155 | `upstream/negotiation-floor-counter` | `745f10d` | `ceac528` | 1927 passed |
| #156 | `upstream/quote-booking-approval-gate` | `3f8c5c3` | `f68d461` | 1929 passed |
| #157 | `upstream/legacy-deals-endpoint` | `55f6085` | `4a25208` | 1923 passed |
| #158 | `upstream/openrtb-auction-type` | `a9fbe9d` | `8f48ef5` | 1954 passed |
| #159 | `upstream/pg-quote-holds` | `81c05a5` | `6234cb1` | 1922 passed |
| #160 | `upstream/deal-allow-lists` | `5d6036d` | `4e370e5` (rebuilt) | 1986 passed |

No storage compare-and-set exists on `eb40328`, so every concurrency fix
below uses a per-resource `asyncio.Lock`. That serializes requests within
one worker process only. Multi-worker deployments still race until #132's
`storage.compare_and_set` lands (follow-up issue K).

---

## Suggested merge order (shared by all replies)

1. **#155**: no conflicts with any other open PR from this set.
2. **#158**: conflicts only with #160, which carries its commits.
3. **#153**
4. **#154**: conflicts with #153 in `routers/sessions.py::create_session`.
5. **#157**: conflicts with #153 in `deps.py` (both add
   `_require_api_key_record`), and with #141 in `negotiation_service.py`.
6. **#156**: conflicts with #157 in `negotiation_service.submit_proposal`
   (both record per-proposal state).
7. **#159**: conflicts with #153 in `.env.example`, `settings.py` and
   `docs/guides/configuration.md`, and with #156 in `routers/deals.py` and
   `deal_service.py` (both route bulk create and from-template through new
   checks).
8. **#160** last. It's stacked on #157 and #158. It conflicts with #153 in
   `deps.py`, and with #156 and #159 in the same deal-booking code.

Once each PR lands, I'll rebase the next one onto `main` and resolve its
conflicts. For #156 and #159 the resolution keeps both checks (approval gate
first, then the PG ledger commit) inside the same per-quote booking lock.

---

## #153: Require credentials on state-changing routes (#86)

> Thanks for the thorough run, and for breaking each gate to check the tests.
> All four points are fixed on the branch (now `c0cf4c6`), each with tests
> that fail without the fix:
>
> 1. **Session ownership by key** (`649fd22`): `Session` now stores
>    `owner_key_id`, the `key_id` of the key that opened it. `_owned_session`
>    and `GET /sessions` compare the caller's `key_id` against it. Sessions
>    opened with a key before this change (no `owner_key_id`, but
>    `authentication_method == "api_key"`) fall back to the pricing key, never
>    on the shared `public` one. Your key A/B (same advertiser, different
>    agency) and C/D (no identity) cases now get 404 and are left out of the
>    other key's `GET /sessions`.
> 2. **Sessions with no owning key** (`99b34f9`): when
>    `ALLOW_ANONYMOUS_BUYER_WRITES` is off, they are operator-only.
>    `GET /sessions/{id}` now uses the same dependency as message and close,
>    so anonymous callers get 401 and non-operator keys get 404. With the
>    flag on, keyless sessions stay usable by any caller the route admits.
>    That is documented as a compatibility mode.
> 3. **MCP** (`dbaf45c`): covered here, so #86 can close with this PR.
>    - `bulk_deal_operations`, `create_curated_deal` (overlaps #120),
>      `migrate_deal`, `deprecate_deal` and `troubleshoot_deal` require an
>      operator key over HTTP (`_deny_unless_operator`).
>    - `request_quote` and `create_order` require any valid key unless
>      `ALLOW_ANONYMOUS_BUYER_WRITES` is set (new `_deny_unless_buyer_key`).
>    - Invalid, revoked and expired keys are always refused. stdio stays
>      trusted, as before.
>    - I went through all 48 tools. No other tool creates or changes
>      proposals, negotiations, sessions or change requests.
>    - One gap remains: keyed MCP callers of `request_quote` are still priced
>      with `_public_context()`. It doesn't block #86, so it's follow-up
>      issue A.
> 4. **Smaller items**:
>    - Operator close of an unknown id now returns 404 (`cdda8d7`).
>    - In `docs/api/authentication.md` (`c0cf4c6`), `POST /deals` is gone
>      from the buyer-write list. "Deal booking always needs a key" is
>      replaced by what this branch actually enforces; #157 adds the key on
>      `POST /deals`. The flag is described as a compatibility switch, with
>      no promise about tier, because body claims are floored by #154, not
>      here.
>    - The same commit adds an **Upgrading** section. Buyer agents need a
>      seller-issued key before the upgrade: without one, quotes, proposals,
>      negotiation, sessions, orders and change requests return 401
>      `authentication_required`, and the MCP twins return the same error
>      JSON. Your buyer-agent finding (`booked=[] failed=[]` without a stored
>      key) is the case it covers.
>
> On #145: it ties negotiation ownership to `get_pricing_key()` and has the
> same shared-identity gap as sessions did. This branch only requires *a* key
> on the proposal, counter and negotiation routes. It adds no per-negotiation
> ownership check, so the `key_id` approach used here would carry over to
> #145.
>
> Conflicts: this branch still conflicts with #154 (`create_session`), #157
> (`deps.py`) and #159 (settings and docs). My suggested merge order is #155,
> #158, #153, #154, #157, #156, #159, #160. I'll rebase whichever lands
> second.

## #154: Verify tier for discovery and sessions (#148)

> Thanks, especially for the blocked-agent mutation check. Changes (head now
> `dddc655`):
>
> - **Blocked agents** (item 3, `c4b3c53`): new tests check that a BLOCKED
>   agent in a real SQLite-backed registry gets 403 on `POST /discovery` and
>   `POST /sessions`, with no pricing served and no session created. Both
>   fail with the `is_blocked` check disabled.
> - **Floored sessions** (item 4, `0e9b44a`): an anonymous session whose
>   claims are floored to PUBLIC is now created with an empty identity. The
>   record, `session_index:buyer:public` and the response's
>   `buyer_pricing_key` are all `public`, so a real `adv-x` key never sees it
>   after #153.
> - **Audit rows** (item 2, `dddc655`): `_verified_buyer_context` takes
>   `audit_unknown_agents`. Anonymous `/discovery` passes `False`, so it no
>   longer writes a `verified_trust:*` row or index entry when the agent's
>   status is UNKNOWN (no registry record, an unfetchable card, or a card no
>   registry vouches for). Keyed discovery, the price- and state-moving
>   routes, and registered, approved, preferred or blocked outcomes are still
>   recorded. I kept blocked outcomes on purpose, as security events. The
>   per-URL index append is still a non-atomic read-modify-write on the
>   routes that do record; it should move to compare-and-set once #132 lands
>   (follow-up issue K).
>
> Item 1 (naming a trusted agent's URL gets its tier): agreed, keyless
> callers should stay at PUBLIC until there is proof of possession. It is
> pre-existing and affects quotes on `main` too, so I'd rather not widen this
> PR. Follow-up issue B has the code path, the impact and an interim
> mitigation: apply the registry ceiling only to key-authenticated callers.
>
> Merge order: after #153 (both change `create_session`). I'll rebase
> whichever lands second.

## #155: Don't reveal the floor in below-floor counters (#149)

> Thanks for the inversion. You're right that any fixed function of the
> floor and known values is recoverable. I've switched to your suggestion
> (`ceac528`, head now `ceac528`):
>
> - **The counter.** `NegotiationEngine.evaluate_buyer_offer` no longer has a
>   below-floor branch. A below-floor offer goes through the normal
>   `_generate_counter`, using the buyer's actual offer and stepping down from
>   the seller's last counter under `per_round_concession_cap` and
>   `total_concession_cap`. The result is clamped with `max(counter, floor)`.
>   The buyer-facing text is the same as for any counter. The old "We cannot
>   accept $X" wording marked an offer as below the floor, which allowed a
>   bisection. REST negotiation, chat and the proposal flow all build the
>   engine through `build_negotiation_engine()`.
> - **The setting.** `NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE` is removed, since
>   no share is left. `Settings` ignores extra keys, so a leftover blank line
>   no longer stops startup (a test covers this). The branch no longer
>   touches `.env.example` or `configuration.md`, which also clears the
>   conflict with #153 and #159.
> - **Your table.** Agency Premium Display (quoted 13.50) now counters
>   12.82 → 12.14 → 11.47 for both 10.01 and 1.00. The first one or two
>   lowball counters depend only on the quote and the per-round cap. Tests
>   check that they don't change when the floor moves, and that neither of
>   your formulas recovers it. Round-1 counters to a 1.00 offer for your six
>   products are 12.82, 21.38, 29.93, 47.02, 28.00 and 43.95.
> - **Your two missing tests** are added and mutation-checked:
>   - the proposal flow's counter equals the engine's;
>   - repeated lowballs step down from `last_seller_price`. Computing the gap
>     from `base_price` fails 15 tests.
>
> One residual, stated plainly. When a single capped step already crosses
> the floor, a lowball hits the clamp a round or two before an honest
> near-floor offer. Examples are advertiser CTV Premium (29.75 − 6% =
> 27.965 < 28.00) and agency CTV. The caps still hold. The lowball ends at
> the floor, at most the honest buyer's own margin below the honest outcome
> (28.00 vs 28.01 here), never below the floor. In those cases the clamp
> itself shows the floor to a buyer who knows the caps. Closing that fully
> means not clamping visibly (hold position, randomize the margin, or keep
> caps private). That is a policy trade-off, so I've written it up as
> follow-up issue C rather than pick one here.
>
> Upgrade note: a below-floor offer now gets the generic counter or
> final-offer text, so anything that parsed "We cannot accept $X CPM" needs
> to change.

## #156: Gate quote bookings behind approval (#129, booking side)

> Thanks for the 202 run against buyer-agent and for finding the bypasses.
> Changes (head now `f68d461`):
>
> 1. **Bulk create and from-template** (`998aa9b`):
>    - **Bulk create.** REST and MCP `bulk_deal_operations` price the quote
>      the way `book_deal` does and go through the same per-quote gate. A
>      held item returns `success: false`, `status: "pending_approval"` and
>      `approval_id`. Bulk and `POST /api/v1/deals` share one approval per
>      quote, so neither path orphans the other's approval. As a side
>      effect, bulk now books an accepted negotiation's agreed price instead
>      of the stale quoted one.
>    - **From-template.** REST and the MCP `create_deal_from_template` tool
>      apply the same threshold and opt-in rules. The approval is keyed on
>      the requested terms (buyer, product, type, CPM, impressions, flight).
>      A held request gets 202, re-submitting after approval books once, and
>      a rejection is a 409 `booking_rejected`.
> 2. **Rejected proposal** (`bf1570f`): `submit_proposal` records the
>    proposal's approval id. Booking a quote whose agreed price came from a
>    proposal the operator rejected is now 409 `proposal_rejected` on both
>    quote paths, with no new approval raised. A *pending* proposal approval
>    still leads to a separate booking approval, because the two cover
>    different prices (proposed vs negotiated).
> 3. **Modifications** (`40d68d3`): approving a `deal_booking` approval with
>    `modifications` returns 400 `modifications_not_supported` and leaves
>    the approval pending.
> 4. **Concurrent re-submits** (`f68d461`): a per-quote lock covers the
>    whole path from reading the quote to marking it booked, in `book_deal`
>    and bulk create. One caller mints the deal; the others get 409
>    `quote_already_booked`. Your 8-way re-submit now mints one deal. Without
>    the lock the tests minted 8, and 12 for a mixed REST and bulk race.
>    From-template claims its approval under the same kind of lock. As noted
>    above, the lock covers a single worker process only.
>
> **buyer-agent and the 202.** Agreed: operators shouldn't turn the
> threshold on for buyer-agent traffic until buyer-agent handles it.
> Follow-up issue D (for buyer-agent, with a pending shape for
> iab-agentic-primitives) has the exact bodies. I'd keep the gate off by
> default here, as it is, and land the buyer side before recommending it.
>
> **Quote expiry vs approval.** Quote expiry is hard-coded at 24 h
> (`quote_service.create_quote`, plus the stored TTL).
> `APPROVAL_TIMEOUT_HOURS` exists, but no caller passes it: every
> `request_approval` uses the 24 h default in `events/approval.py`. The
> approval clock starts at the first booking attempt, so the quote always
> expires first, and a late approval ends in 410 followed by a fresh
> approval on re-quote. Follow-up issue L proposes wiring the setting and
> extending (or re-issuing) the quote on approval.
>
> **Still open, pre-existing:** bulk create has no quote-expiry or buyer
> binding check, and proposals held before `bf1570f` carry no approval
> pointer.
>
> Merge order: after #157. This conflicts with #157 in `submit_proposal`,
> with #159 in the bulk and from-template code, and with #160.

## #157: Legacy `POST /deals` books the caller's accepted proposal (#128, terms)

> Thanks, this one had real holes. Changes (head now `4a25208`):
>
> 1. **Approval wins over negotiation** (`77954fc`): when the gate opens an
>    approval, `submit_proposal` stores its id on the proposal.
>    - `POST /deals` checks that approval first. Pending, timed-out or
>      rejected returns 409 `proposal_not_accepted`, whatever any later
>      negotiation says. Your pending/rejected → negotiate → 200 at $11.98
>      sequence is now 409.
>    - An operator *counter* decision is stored as `rejected` but hands the
>      proposal back to negotiation, so it doesn't block. Counter-then-accept
>      still books.
>    - Gated proposals stored before this change (`status ==
>      "pending_approval"`, no approval id) are blocked too.
> 2. **Ownership by key** (`d788a47`): the proposal stores the submitting
>    key's `key_id` (`buyer_key_id`), and `POST /deals` compares it with the
>    caller's. That makes the "was not submitted by this API key" message
>    accurate. Your key A/B and C/D cases are now 409. Records without a
>    `key_id` fall back to the pricing key. There is still no operator
>    override: an operator key books only proposals it submitted itself.
> 3. **Concurrency** (`80ea530`): the read, check, mint and store sequence
>    runs under a per-proposal lock. Six concurrent calls return one deal id
>    (single worker process, as above).
> 4. **Pre-upgrade proposals** (`4a25208`): a record with none of
>    `buyer_key_id`, `buyer_key` or `buyer_auth` now gets 409
>    `proposal_owner_unknown`, telling the buyer to resubmit through
>    `POST /proposals`. That applies to operator keys too, since nobody can
>    be shown to own the resulting deal. The CHANGELOG `[Unreleased]` entry
>    has the breaking and upgrade notes.
>
> **Should a negotiated price after an approval need a new approval?** I
> think it should when it is below the approved terms. Today an approved
> proposal followed by an accepted negotiation books at the negotiated price
> with no new approval, on this route and on `POST /api/v1/deals`. I'd keep
> it out of this PR and solve it once for both routes on top of #156's
> booking gate (follow-up issue E).
>
> **Legacy deals not stored, and `deal.registered` after a failed
> registration:** both confirmed as you describe and left for follow-up
> issue F. With them, the rest of #128 stays open after this PR.
>
> Merge order: after #153 (`deps.py`). This also conflicts with #141 in
> `negotiation_service.py`. #160 is stacked on this branch and has been
> rebuilt on the new head.

## #158: OpenRTB `at` from one builder (#147)

> Thanks for the buyer-agent cross-check and the lowercase push case.
> Changes (head now `8f48ef5`):
>
> 1. **Stored deals** (`1c7835e`): `at` is now derived from `deal_type` on
>    every read path that returns `openrtb_params`. That covers the shared
>    deal mapper (GET by id, the list endpoint and the idempotent booking
>    replay) and `export_deals` (generic format). Storage is not migrated:
>    deals booked before the upgrade keep their stored params but are
>    reported as PG/PD → 3 and PA → 1.
> 2. **Push `deal_type`** (`a028e93`): `build_openrtb_deal_params` and
>    `build_deal_object` both normalize through
>    `deal_service._normalize_deal_type_code` and raise for unknown types.
>    `POST /api/v1/deals/push` returns 422 `invalid_deal_type` before
>    anything is sent. `pd` / `pg` now give `at=3` with `fixed_price_cpm`,
>    `pa` gives `at=1` with `floor_price_cpm`, and `XYZ` gives 422.
> 3. **Untested changes** (`6513413`): booking a stored EUR quote now fails
>    if `book_deal` goes back to `currency="USD"`. The proposal-flow test now
>    asserts `ext.impressions`.
> 4. **PG `ext`** (`4f12eec`): PG always carries `ext.guaranteed: true`, and
>    `impressions` is added only when known. Dropping it wasn't intended.
> 5. **CSV ad server and AgentCore** (`57ec7f7`, `8f48ef5`): both were small
>    enough to fix here.
>    - `CSVAdServerClient.create_deal` / `book_deal` map every accepted
>      spelling through the builder (PG/PD → 3, PA → 1). The CSV `deal_type`
>      column is now `PG` / `PD` / `PA` instead of `PROGRAMMATICGUARANTEED`.
>      Existing rows are unchanged.
>    - The AgentCore direct deal tool now uses `build_openrtb_deal_params`.
>
> **PA `pricing_type`:** agreed that PA should carry a floor type. It comes
> from the `PricingType.FIXED` default on `QuotePricing.pricing_type`, which
> `create_quote` never overrides, and it touches quotes, booked deals,
> curated deals (#123) and buyer-agent. So I've drafted it as follow-up
> issue G rather than widen this PR.
>
> While in the CSV path I also noticed `ExecutionActivationFlow` treats only
> PG as fixed-price, so PD deals reach the ad server floor-priced (follow-up
> issue H).
>
> Release note: existing deals are now *reported* with the derived `at`,
> unknown push types are 422, and every PG carries `ext.guaranteed`.
>
> Merge order: this branch has no conflicts except with #160, which carries
> these commits and has been rebuilt on this head.

## #159: Hold PG capacity at quote time (#150)

> Thanks, this was the most useful review of the set. Changes (head now
> `6234cb1`):
>
> 1. **Who can hold, how much, how long** (`17edb92`):
>    - Only keyed callers place holds. Anonymous REST quotes and MCP
>      `request_quote` are checked (409 when short) but hold nothing, so your
>      12M anonymous quote no longer locks the product.
>    - One buyer (by `key_id`) may hold at most `PG_HOLD_MAX_BUYER_SHARE` of
>      a product's capacity (default 0.5); beyond that it gets 409
>      `pg_hold_limit_exceeded`.
>    - Holds last `PG_HOLD_TTL_SECONDS` (default 3600, at most the 24 h quote
>      TTL).
>    - If the hold has lapsed when the buyer books, booking re-checks
>      capacity before saving anything, so this is "limit holds" *and*
>      "re-check at booking".
> 2. **Bookings outside `POST /api/v1/deals`** (`b0efbcb`):
>    - Bulk create (REST and MCP) calls `commit_pg_hold` before saving the
>      deal. A lapsed and taken hold fails only that operation.
>    - From-template (REST and MCP) reserves and commits through the ledger
>      for PG, with 409 `insufficient_avails`. Your 10M-on-5M sequence is now
>      refused.
> 3. **Lost holds** (`5d50db3`): `commit_pg_hold` adds a missing entry
>    (after a capacity re-check) instead of returning early. Every ledger
>    read-modify-write runs under a per-product lock. Your 10 concurrent 2M
>    quotes now give 2 accepted, with both holds kept, within one worker.
>    Across workers it needs #132's compare-and-set plus create-if-absent for
>    the first write (follow-up issue K).
> 4. **Flights** (`0e59a01`): entries store `flight_start` / `flight_end`,
>    and only entries that share at least one day with the requested flight
>    count. I'm treating `maximum_impressions` as a cap per overlapping
>    flight, with no per-period pacing, and that is documented. Your March
>    booking no longer affects an October quote. Entries without a flight
>    (pre-upgrade) count against every flight until they lapse.
> 5. **Disclosure** (`6234cb1`): anonymous callers (REST, MCP and anonymous
>    bulk) get the 409 without `available_impressions` or the count in the
>    message. Keyed callers still get the exact number, so they can re-quote.
>
> **Deferred:**
> - **`check_avails` still ignores the ledger** (follow-up issue I). It is
>   synchronous with several callers, and #108's MCP tool sits on it, so it
>   needs its own change.
> - **`DealsClientError` dropping `available_impressions`** is on the
>   buyer-agent side (follow-up issue J).
>
> Merge order: after #153 (settings and docs rows) and #156 (bulk and
> from-template). The resolution keeps the approval gate first and the
> ledger commit second, inside the per-quote booking lock.

## #160: Populate `wseat` / `wadomain` (#152) (note, no review yet)

> Rebuilt on the updated stack. The branch is now #157's head (`4a25208`),
> then #158's seven commits re-applied (`5bfdea0`…`0e581b7`), then the
> allow-list commit (`4e370e5`). It applied with no conflicts. 1986 unit
> tests pass.
>
> Since #158 now routes the AgentCore direct deal tool through the builder,
> that path gets `wseat` / `wadomain` too. It has no verified buyer identity
> to fill them from, so they stay empty there.
>
> Please review after #157 and #158; I'll rebase onto `main` once they land.
> It also conflicts with #153 (`deps.py`), #156 and #159 (deal booking), so
> it should merge last.

---

## Follow-up issue texts

### A. MCP buyer tools price keyed callers at the public tier

> **Title:** MCP `request_quote` / `create_order` ignore the caller's API key
> for pricing
>
> After #153, the MCP buyer-write tools require a valid key over HTTP
> (`_deny_unless_buyer_key`), but `request_quote` still prices with
> `_public_context()`. A keyed buyer therefore gets a tiered price over REST
> and the public price over MCP for the same product. Proposal: have the gate
> return the validated key record and build the context with
> `deps._verified_buyer_context` (key identity, same floor and registry
> rules as REST). stdio keeps the public context.

### B. Keyless callers get a registry-verified tier by naming an agent's URL

> **Title:** Keyless callers get a registry-verified tier by naming a trusted
> agent's URL (no proof of possession)
>
> `interfaces/api/deps.py::_verified_buyer_context` passes the body's
> `agent_url` to `AgentRegistryService.verify_buyer_trust` →
> `resolve_agent_access`, which looks the URL up in the local registry
> (`get_agent_by_url`). The ceiling then comes from
> `RegisteredAgent.effective_access_ceiling` (REGISTERED → SEAT, APPROVED or
> PREFERRED → ADVERTISER). That ceiling replaces the PUBLIC floor keyless
> callers otherwise get. Nothing ties the request to the agent: no
> signature, no client certificate, no key binding. Anonymous
> `GET /registry/agents` returns each agent's card URL and `trust_status`,
> so finding an approved URL is trivial.
>
> Impact: an anonymous request with an approved agent's URL plus an
> advertiser or agency claim gets agency-tier exact prices on
> `POST /discovery`, an advertiser-tier session on `POST /sessions`, and an
> advertiser-tier quote on `POST /api/v1/quotes` (12.75 instead of 15.00;
> same on `main`). Every caller of `_verified_buyer_context` is affected,
> and it bypasses the #148 floor.
>
> Proposal: keyless callers stay PUBLIC unless they prove possession, by one
> of:
> - HTTP Message Signatures (RFC 9421) verified against a key in the agent
>   card or registry record;
> - mTLS with the client certificate bound to the agent;
> - a seller-issued key bound to the registered agent (`agent_id` on the key
>   record, required to match `agent_url`).
>
> Interim mitigation: apply the registry ceiling only to key-authenticated
> callers (blocked agents still get 403), and consider dropping card URL and
> `trust_status` from the anonymous registry listing.

### C. The floor clamp reveals the floor when one capped step crosses it

> **Title:** Negotiation: clamping a capped counter at the floor reveals it
> when one step crosses it
>
> After #155, below-floor offers take the capped gap-split and are clamped
> with `max(counter, floor)`. When the floor lies within one capped step of
> the seller's position (e.g. advertiser CTV Premium: quote 29.75, 6% step,
> floor 28.00), the counter equals the floor. A buyer who knows the quote
> and the caps can tell the clamp fired, so it learns the floor, and a
> round or two sooner than an honest near-floor offer would. Options: hold
> the previous position instead of clamping (reveals only a bound),
> randomize a per-negotiation margin above the floor, or keep per-product
> cap configuration private. Each trades convergence against disclosure, so
> it needs a policy decision.

### D. buyer-agent: handle `202 pending_approval` on deal booking

> *(for IABTechLab/buyer-agent)*
>
> **Title:** Handle seller `202 pending_approval` from `POST /api/v1/deals`
> (approval-gated bookings)
>
> Seller-agent can now hold a booking for operator approval (seller-agent
> #156, #129). `POST /api/v1/deals` then returns **HTTP 202** with no deal:
>
> ```json
> {"status": "pending_approval", "quote_id": "<quote_id>",
>  "approval_id": "<approval_id>",
>  "message": "This booking requires seller approval. Re-submit the booking once it is approved."}
> ```
>
> `POST /api/v1/deals/from-template` returns
> `{"status", "approval_id", "product_id", "deal_type", "message"}` with 202.
> After the operator decides, re-submitting the same booking returns:
> - 200 with the deal (approved);
> - 409 `booking_rejected` (rejected);
> - 409 `proposal_rejected` (the source proposal was rejected).
>
> Today `DealsClient.book_deal` parses every 2xx as `DealBookingResponse`
> and raises `ValidationError: deal Field required`. `MultiSellerOrchestrator`
> records that as a failure and drops the `approval_id`.
>
> Proposal:
> 1. `DealsClient.book_deal` treats 202 / `status == "pending_approval"` as
>    a pending result carrying `approval_id` and `quote_id`.
> 2. The orchestrator keeps pending bookings in their own bucket and
>    re-submits the same `quote_id` with a new idempotency key on a schedule
>    or trigger. It stops on 200, on 409 `booking_rejected`,
>    `proposal_rejected` or `quote_already_booked`, or on 410
>    `quote_expired`.
> 3. iab-agentic-primitives gets a pending shape for `DealBookingResponse`
>    (optional `deal` plus `status: "pending_approval"` and `approval_id`, or
>    a sibling `DealBookingPending`).

### E. A negotiated price below the approved terms books without re-approval

> **Title:** Require a fresh approval when a negotiated price undercuts an
> approved proposal
>
> With the gate on for `proposal_decision`, an approved proposal followed by
> an accepted negotiation books at the negotiated price with no new
> approval. This happens on legacy `POST /deals` (#157) and on
> `POST /api/v1/deals` (#156). Proposal: when the agreed price (or other
> terms) is below what the operator approved, booking raises a
> `deal_booking` approval through #156's gate, carrying the `proposal_id`
> and both prices. Both routes share the rule.

### F. Rest of #128: persist legacy deals; no `deal.registered` after a failure

> **Title:** Legacy `POST /deals`: persist the deal and stop emitting
> `deal.registered` after a failed registration
>
> After #157, legacy `POST /deals` mints the deal from the caller's accepted
> proposal but still has two problems.
>
> 1. **The deal isn't stored as a deal.** It's written only to
>    `proposal["deal"]`, and `storage.set_deal` is never called. So
>    `GET /api/v1/deals/{deal_id}` returns 404 and the deal is missing from
>    the list and export. Store it with the same shape and status model as
>    quote bookings, and keep a reference on the proposal.
> 2. **`deal.registered` fires after a failed registration.**
>    `DealGenerationFlow.register_deal` emits the event even when
>    `_od_register_deal` raised; the failure only lands in `state.warnings`.
>    Emit it only on success (or with an explicit failure status), and
>    surface the registration outcome in the response.

### G. PA quotes and deals should carry `pricing_type: "floor"`

> **Title:** PA quotes and deals report `pricing_type: "fixed"`
>
> `QuotePricing.pricing_type` defaults to `PricingType.FIXED`
> (`models/quotes.py`), and `quote_service.create_quote` never sets it.
> Every quote, and every deal booked from one, reports `"fixed"`, including
> PA. Since #158 a booked PA deal returns `openrtb_params.at=1` (`bidfloor`
> is a floor) next to `pricing.pricing_type: "fixed"`.
>
> Proposal: `create_quote` sets `FLOOR` for PA, and the read mapper derives
> it for stored PA deals (as `at` is now derived). This affects:
> - quote responses (REST and MCP);
> - deals booked from quotes;
> - curated PA deals (#123);
> - buyer-agent logic that treats `final_cpm` as the price to pay.
>   `PricingType.FLOOR` already exists in the shared enum.

### H. Activation sends PD deals to the ad server as floor-priced

> **Title:** `ExecutionActivationFlow` treats PD deals as floor-priced
>
> `ExecutionActivationFlow` sets
> `is_fixed = deal.deal_type == DealType.PROGRAMMATIC_GUARANTEED`. PD deals
> therefore reach `ad_server.create_deal` with `floor_price_micros` and
> `fixed_price_micros=0`, and the execution order records
> `pricing_type: "floor"`. PD is fixed-price (`at=3`), as the builder and
> `build_deal_object` already treat it. Proposal: derive fixed vs floor from
> `deals_api.deal_type_code` (PG/PD fixed, PA floor), with a test per deal
> type over the CSV ad server.

### I. `check_avails` ignores PG holds and bookings

> **Title:** `check_avails` ignores PG holds and bookings, so avails disagree
> with quotes
>
> `catalog_service.check_avails` computes availability from
> `maximum_impressions` alone. Since #150/#159, PG capacity is tracked in
> the per-product ledger (live holds and booked volume, filtered by
> overlapping flight). Avails therefore report full capacity while PG quotes
> get 409 `insufficient_avails`. Affected:
> - `POST /products/avails`;
> - the availability agent;
> - `availability.inventory_available` on quotes;
> - #108's MCP `check_avails`.
>
> Proposal: an async variant that reads the ledger for the requested flight,
> with the same disclosure rule (exact remaining volume only for keyed
> callers).

### J. buyer-agent: keep structured 409 fields on `DealsClientError`

> *(for IABTechLab/buyer-agent)*
>
> **Title:** `DealsClientError` drops structured 409 fields such as
> `available_impressions`
>
> `DealsClientError` keeps only `code` and `message`. Seller-agent's 409
> `insufficient_avails` includes `available_impressions` for keyed buyers,
> so the buyer can't re-quote the remaining PG volume. Proposal: keep the
> full error `detail` on the exception, and let the orchestrator optionally
> re-quote `min(requested, available_impressions)`.

### K. Move check-then-act paths to storage compare-and-set

> **Title:** Use `storage.compare_and_set` for booking, legacy deal minting
> and the PG ledger (multi-worker races)
>
> #156, #157 and #159 serialize check-then-act sequences with per-resource
> `asyncio.Lock`s. These are correct within one worker process, but
> multiple workers or replicas can still race:
> - quote status `available` → `booked` in `book_deal` and bulk create;
> - the proposal's `deal` slot on legacy `POST /deals`;
> - the per-product `inventory_hold:{product_id}` ledger;
> - the per-URL `verified_trust` index append.
>
> Once #132's `storage.compare_and_set` lands, move each to a compare-and-set
> retry loop. The ledger and the index also need create-if-absent for the
> first write. Keep the locks as a cheap in-process fast path.

### L. Quote expiry vs approval timeout

> **Title:** Approved bookings can fail with 410 because the quote expired
> while awaiting approval; `APPROVAL_TIMEOUT_HOURS` is not wired
>
> Quote expiry is hard-coded at 24 h (`quote_service.create_quote`, plus
> the stored TTL). `APPROVAL_TIMEOUT_HOURS` exists in settings, but every
> `request_approval` call uses the 24 h default in `events/approval.py`. The
> approval clock starts at the first booking attempt, so the quote always
> expires first. A late approval then ends in 410 `quote_expired`, and
> re-quoting raises a new approval.
>
> Proposal:
> - pass `settings.approval_timeout_hours` to every `request_approval`;
> - cap a `deal_booking` approval's timeout at the quote's remaining
>   lifetime, or extend the quote's expiry when its booking approval is
>   granted;
> - document the interaction.
