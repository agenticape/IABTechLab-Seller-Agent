# Local mock-only sandbox

A one-step local seller agent for working through buyer scenarios on your own
machine. It runs the real seller code (FastAPI app, pricing, quotes, deal
booking, negotiation, approval gate) against a **fictional** CSV catalog and a
local SQLite file. It never talks to a real ad server, SSP, DSP, deal-sync
service, agent registry or LLM, and it needs no secrets to boot.

Everything lives in [`sandbox/`](sandbox/). Nothing in `src/` was changed to
make it work.

**Hosted version:** the same mock-only sandbox can run publicly on Fly.io at
<https://iab-seller.agenticape.ai>. See [docs/deploy-fly.md](docs/deploy-fly.md)
for the step-by-step guide (Windows PowerShell), including Cloudflare DNS.

| You want to... | Windows (PowerShell) | macOS / Linux |
|---|---|---|
| Set up and start the seller (one step) | `.\sandbox\run.ps1` | `./sandbox/run.sh` |
| Run the mock-buyer scenarios (second terminal) | `.\sandbox\scenarios.ps1` | `./sandbox/scenarios.sh` |
| Only (re)do setup | `.\sandbox\setup.ps1` | `./sandbox/setup.sh` |
| Re-mint the API keys | `.\sandbox\setup.ps1 -Rekey` | `./sandbox/setup.sh --rekey` |
| Wipe the sandbox DB (stop the server first) | `.\sandbox\setup.ps1 -Reset` | `./sandbox/setup.sh --reset` |

---

## Quickstart: Windows (PowerShell)

Requirements: Windows 10/11, `git`, internet access for the first install.
You do **not** need Python, Docker or admin rights.

```powershell
git clone https://github.com/agenticape/IABTechLab-Seller-Agent.git
cd IABTechLab-Seller-Agent

# Allow local scripts for your user, once (skip if you already did this):
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned

.\sandbox\run.ps1
```

The first run installs `uv` into `%USERPROFILE%\.local\bin`, a uv-managed
Python 3.12, and the locked dependencies into `.venv` (a few minutes). Later
runs take a few seconds. Leave that window open. The seller is at
<http://localhost:8000>, with interactive API docs at <http://localhost:8000/docs>.

In a **second** PowerShell window:

```powershell
cd IABTechLab-Seller-Agent
.\sandbox\scenarios.ps1
```

Windows notes:

- If you'd rather not change the execution policy, run the scripts as
  `powershell -ExecutionPolicy Bypass -File .\sandbox\run.ps1`.
- If `uv` was just installed and a later command can't find it, open a new
  terminal; the installer updates your user `PATH` for new sessions. The
  scripts also add it for the current session.
- The server binds to `127.0.0.1` only, so Windows Firewall shouldn't prompt.
- If port 8000 is busy, the launcher says so and exits. Find the owner with
  `Get-NetTCPConnection -LocalPort 8000 | Select OwningProcess`.
- Avoid cloning into a OneDrive-synced folder (e.g. `Documents`). The
  `.venv` holds thousands of files, and sync or Defender scans make the first
  install slow. Something like `C:\dev\` works well.
- Both Windows PowerShell 5.1 and PowerShell 7 work. `.gitattributes` keeps the
  `.ps1` files CRLF and the `.sh` files LF.

## Quickstart: macOS (and Linux)

Requirements: `git` and `curl`. On macOS you need **Apple Silicon (M1 or
later) on macOS 14 Sonoma or newer**. The locked `onnxruntime` 1.27 (pulled
in through CrewAI and chromadb) ships no wheel for Intel Macs or for macOS 13
and older, so `uv sync` fails there. See [Known blockers](#known-blockers).

```bash
git clone https://github.com/agenticape/IABTechLab-Seller-Agent.git
cd IABTechLab-Seller-Agent
./sandbox/run.sh
```

Second terminal:

```bash
./sandbox/scenarios.sh
```

If you prefer Homebrew for uv, run `brew install uv` before the first run and
the script will use it.

## What the scripts do (all idempotent)

`setup` (also run by `run` unless you pass `-SkipSetup` / `--skip-setup`):

1. Installs `uv` if it's missing. It uses the official astral.sh installer,
   per-user with no sudo or admin, and prints manual instructions if that
   fails.
2. `uv python install 3.12`. The system Python is never used.
3. `uv sync --locked --python 3.12 --extra dev`, the same locked install CI
   uses.
4. Copies `sandbox/.env.sandbox` to `sandbox/.local/sandbox.env` if that file
   doesn't exist yet. An existing copy is never overwritten.
5. `sandbox/mint_keys.py` mints one operator key and three fictional buyer
   keys (seat, agency, advertiser) directly into the sandbox SQLite DB. This
   is the same bootstrap path as `ad-seller create-operator-key`. The keys go
   to `sandbox/.local/keys.json` and only their key IDs are printed. On later
   runs the keys are re-validated and reused.

`run` then starts `sandbox/run_server.py`, which:

- loads `sandbox/.env.sandbox`, then your `sandbox/.local/sandbox.env`, on top
  of the environment;
- **refuses to start** (exit code 2, with a list of reasons) unless the
  effective settings are mock-only: `AD_SERVER_TYPE=csv` with a catalog under
  `data/csv/samples/`, GAM and FreeWheel off, no SSP or deal-sync connectors,
  no S3, no Redis, no AAMP registry URL, OpenDirect, registry and seller URLs
  on localhost, and SQLite storage. Safety-relevant keys are pinned from the
  sandbox files even when blank, so a value exported in your shell or a stray
  repo-root `.env` can't switch a real integration back on;
- detaches stdin. CrewAI otherwise shows a "view your execution traces?
  [y/N] (20s timeout)" prompt after every flow when the server runs in a
  terminal, which stalls each `POST /proposals` and `POST /discovery` for 20s;
- serves on `127.0.0.1:8000` and hard-exits on Ctrl+C, the same workaround as
  the CLI's `force_exit_after`, so CrewAI's atexit handlers can't hang
  shutdown.

### Files

| Path | Committed? | What |
|---|---|---|
| `sandbox/.env.sandbox` | yes | Mock-only config template. No secrets, ever. |
| `sandbox/.local/sandbox.env` | **no** | Your copy. Put personal settings such as `ANTHROPIC_API_KEY` here. |
| `sandbox/.local/keys.json` | **no** | Operator and buyer API keys (plain text; `chmod 600` on macOS/Linux). |
| `sandbox/.local/ad_seller_sandbox.db` | **no** | All sandbox state: keys, quotes, deals, negotiations, approvals. |
| `sandbox/.local/audit_fallback.jsonl` | **no** | Audit events the event bus couldn't persist (normally absent). |

`sandbox/.local/` is in `.gitignore`. Check with
`git check-ignore -v sandbox/.local/keys.json`.

### Using the keys by hand

PowerShell:

```powershell
$k = Get-Content sandbox\.local\keys.json | ConvertFrom-Json
$adv = $k.buyers.advertiser.api_key
Invoke-RestMethod -Method Post http://127.0.0.1:8000/pricing `
  -Headers @{ 'X-Api-Key' = $adv } -ContentType 'application/json' `
  -Body '{"product_id":"inv-ctv-apex-series"}'
```

bash:

```bash
ADV=$(python3 -c "import json;print(json.load(open('sandbox/.local/keys.json'))['buyers']['advertiser']['api_key'])")
curl -s -X POST http://127.0.0.1:8000/pricing -H "X-Api-Key: $ADV" \
  -H 'content-type: application/json' -d '{"product_id":"inv-ctv-apex-series"}'
```

The operator key (`$k.operator.api_key`) is for admin routes: `/approvals`,
`/auth/api-keys`, `/events`, rate card and packages. To mint more buyers,
for example a second agency to probe #130:

```bash
curl -s -X POST http://127.0.0.1:8000/auth/api-keys -H "X-Api-Key: $OPERATOR" \
  -H 'content-type: application/json' \
  -d '{"agency_id":"agency-sandbox-002","agency_name":"Second Agency (fictional)","label":"sandbox-buyer-agency-b"}'
```

Keys whose label starts with `sandbox-` are revoked by `setup --rekey`.

---

## What's mocked (and what isn't)

| Area | In the sandbox |
|---|---|
| Inventory / ad server | `AD_SERVER_TYPE=csv` with `data/csv/samples/aws_workshop`: 15 products from a fictional publisher (Apex Streaming, GNN, Crestline, SportsPulse, Horizon Discovery). No GAM, FreeWheel or S3. The baked-in default catalog (with real-brand demo SKUs) is **not** used. |
| Storage | One local SQLite file. No Redis or Postgres. |
| SSP / deal sync | `SSP_CONNECTORS` and `DEAL_SYNC_CONNECTORS` blank. Booking returns `DEMO-…` deal IDs, OpenRTB params and DSP activation *text*; nothing is pushed anywhere. |
| OpenDirect | `OPENDIRECT_BASE_URL=http://localhost:3000`. Nothing listens there, so anything that would register with OpenDirect fails locally (only legacy `POST /deals` tries; see #128). |
| Agent registry | `AGENT_REGISTRY_ENABLED=false`, URL pinned to a dead local port, `AAMP_REGISTRY_URL` blank. The legacy registry client is a stub that never makes HTTP calls. A buyer `agent_url` is still fetched for its agent card, so only use local buyer URLs. |
| Buyers | Four API keys with fictional identities (`sandbox/mint_keys.py`). |
| Approval gate | Opt-in gate off. `APPROVAL_REQUIRED_ABOVE_VALUE=250000` holds any proposal worth at least $250k gross for an operator. |
| LLM / CrewAI | No key. Every scripted scenario is deterministic: `POST /proposals` uses the rule-based evaluator because `PROPOSAL_FLOW_TIME_BUDGET` (20s) is below `PROPOSAL_CREW_MIN_BUDGET` (120s). |
| Telemetry | CrewAI, OTEL, PostHog and Chroma telemetry are opted out by `ad_seller/_telemetry_shim.py`. CrewAI tracing is off. |
| Docker | Not used locally. The hosted sandbox image (`infra/fly/Dockerfile`, profile `sandbox/.env.fly`) is covered in [docs/deploy-fly.md](docs/deploy-fly.md). |

Pricing quirks to know about with this catalog:

- The CSV's `floor_price_cpm` column becomes each product's **list price**. The
  seller's internal floor is 85% of that (`catalog_service.product_from_inventory_item`).
- Tier discounts come from the pricing engine defaults: seat 5%, agency 10%,
  advertiser 15%. `data/csv/samples/aws_workshop/rate_card.json` (agency 12%)
  and `media_kits.json` are not read by any code path, so `/media-kit` is
  empty.
- Products report `seller_organization_id: seller-premium-pub-001` regardless
  of `SELLER_ORGANIZATION_ID`.

## Adding an LLM key (optional, Anthropic/Claude)

Only needed for the CrewAI paths (`ad-seller chat`, the proposal-review crew,
natural-language flows). Edit **`sandbox/.local/sandbox.env`**, which is
git-ignored. Never edit `sandbox/.env.sandbox`.

```dotenv
ANTHROPIC_API_KEY=sk-ant-your-own-key
DEFAULT_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
MANAGER_LLM_MODEL=anthropic/claude-sonnet-4-5-20250929
# To let POST /proposals actually run the review crew (slow: minutes):
# PROPOSAL_FLOW_TIME_BUDGET=900
# PROPOSAL_CREW_MIN_BUDGET=120
```

Restart the server. A non-blank `ANTHROPIC_API_KEY` already exported in your
shell is also picked up when the file leaves it blank. The safety guard
doesn't restrict the LLM provider, so the key only ever goes to Anthropic.
With a key set, proposal text reaches the model. Keep buyer inputs fictional
(see #126).

---

## The scenarios

`sandbox/scenarios.py` is a scripted mock buyer. It uses only HTTP and plain
Python, so it behaves the same on Windows and macOS. Each check prints
`[PASS]`, `[FAIL]`, `[WARN]`, `[INFO]` or `[SKIP]`, followed by a per-scenario
summary.

- **FAIL** means the seller or the sandbox is broken, and the exit code is 1.
- **WARN** means current upstream behaviour worth a sell-side tester's
  attention, usually a known issue. It doesn't fail the run; `--strict` makes
  it fail.
- Every run uses fresh idempotency keys, so re-running never collides with
  earlier data.

```text
scenarios.ps1 / scenarios.sh [--only 2,4] [--verbose] [--strict] [--buyer-agent PATH]
```

`--verbose` prints every request and response body. That's the quickest way
to learn the wire format.

| # | Scenario | What it checks |
|---|---|---|
| 1 | Discovery | `/health`, `/.well-known/agent.json`, `GET /products` (exactly the 15 fictional `inv-*` products), `GET /products/{id}`, 404 on unknown ids, `POST /products/avails`, no floor or reserve fields on the catalog. `POST /discovery` with a tier claimed in the body but no key is answered at the public tier; with the agency key, at the agency tier. Media-kit search exposes no floor fields. |
| 2 | Tiered pricing | `POST /pricing` as public, seat, agency and advertiser keys: discounts of 0, 5, 10 and 15%, strictly better per tier, never below the floor, and no floor field or "floor" wording in responses. Also checks that an anonymous body claiming advertiser tier gets public price, a bad key gets 401, and a 50M-impression advertiser order stays at or above the product floor. |
| 3 | Quote then book | An anonymous quote gets 401. Advertiser quotes PD, and replaying the `idempotency_key` returns the same quote, while the same key with a different body gets 409. A seat `target_cpm` at the floor is not honoured (the quote stays at the seat price, and the rationale names that price), while an agency target inside its negotiation band is. Anonymous booking gets 401 and another buyer's booking gets 403. Booking returns the **Deal ID**, OpenRTB `id`, `bidfloor`, `at=3` (fixed price) and `wseat`/`wadomain` from the advertiser key's seat ids and domains, plus the TTD, DV360 and Xandr activation text. A booking replay returns the same Deal ID, a new key on a booked quote is refused, and the deal reads back. Then the same flow on a PG deal, which must also carry `at=3`. |
| 4 | Negotiation | Agency quotes, then makes counters via `POST /api/v1/negotiations/messages` (below floor, then into the band between floor and quote) until the seller accepts. It sends an explicit `accept` and books the quote. The deal's `final_cpm` and OpenRTB `bidfloor` must equal the agreed CPM. Each round's seller price must be at or above the floor and never named as such, a below-floor offer must be countered strictly above the floor, and a round replay must not use up a round. |
| 5 | Approval gate | A $5k proposal is decided automatically, while a $500k one comes back `pending_approval`. `/approvals` gives 401 to anonymous callers and 403 to buyers, and a buyer can't approve itself. The operator approves, then `/resume` returns `accepted`. An agency proposal between the floor and its tier price is countered. Legacy `POST /deals` gives 401 without a key, 404 for an unknown proposal and 409 to another buyer, and for the advertiser's approved proposal returns a PG deal at the proposal's $50 with its 10M guaranteed impressions. A second $360k proposal is rejected by the operator, resumes as `rejected`, and `POST /deals` refuses it with 409. Finally, booking a $354k quote returns 202 `pending_approval`; once the operator approves, the re-submitted booking books. |
| 6 | IAB buyer-agent (optional) | With `--buyer-agent PATH`, runs `sandbox/buyer_agent_probe.py` inside your local [buyer-agent](https://github.com/IABTechLab/buyer-agent) clone's own environment (`uv run --project`). The probe uses the buyer's `DealsClient` to fetch the agent card, discover products, quote and book against this seller only. |

### Scenario 6 setup

```bash
git clone https://github.com/IABTechLab/buyer-agent.git ../buyer-agent
./sandbox/scenarios.sh --only 6 --buyer-agent ../buyer-agent
```

The first run syncs the buyer's locked dependencies, which takes a minute.
The probe's environment pins `SELLER_ENDPOINTS` and `IAB_SERVER_URL` to
`http://127.0.0.1:8000` and `OPENDIRECT_BASE_URL` to localhost, because the
buyer's own `.env.example` defaults `IAB_SERVER_URL` to the hosted IAB
agentic-direct service. If you run the buyer's server or CLI by hand, copy
that override into the buyer's `.env` first. Its CrewAI flows also need an
LLM key.

### Last verified result (Linux VM, uv-managed CPython 3.12.16, uv 0.13.9)

This was a fresh clone with an empty `HOME` and no uv on `PATH`, so
`./sandbox/run.sh` installed uv, Python and all dependencies from scratch.
Then `./sandbox/scenarios.sh --buyer-agent <fresh buyer-agent clone>` ran.
`strace -e connect` on the seller process for a full scenario run recorded
**no** outbound connections.

```text
[PASS] 1. Discovery: agent card, products, avails  (10 pass, 0 fail)
[PASS] 2. Tiered pricing public/seat/agency/advertiser + floor protection  (19 pass, 0 fail, 1 warn)
[PASS] 3. Quote then book with idempotency (Deal ID + OpenRTB params)  (14 pass, 0 fail, 1 warn)
[PASS] 4. Multi-round negotiation, then book at the agreed CPM  (15 pass, 0 fail)
[PASS] 5. High-value proposal held by the approval gate  (13 pass, 0 fail)
[PASS] 6. IAB buyer-agent (local clone) against this seller  (5 pass, 0 fail)
```

That run's two WARNs (product-floor undercut on large volume orders, and PG
deals with OpenRTB `at=1`) are fixed on this fork, along with the other
issues written up in `docs/upstream-issues.md`. A later run of scenarios 1-5
on a Linux VM (scenario 6 not run) gave:

```text
[PASS] 1. Discovery: agent card, products, avails  (15 pass, 0 fail)
[PASS] 2. Tiered pricing public/seat/agency/advertiser + floor protection  (20 pass, 0 fail)
[PASS] 3. Quote then book with idempotency (Deal ID + OpenRTB params)  (23 pass, 0 fail)
[PASS] 4. Multi-round negotiation, then book at the agreed CPM  (20 pass, 0 fail)
[PASS] 5. High-value proposal held by the approval gate  (26 pass, 0 fail)
```

INFO-level observations:

- A below-floor offer is countered above the floor, at
  `NEGOTIATION_BELOW_FLOOR_COUNTER_SHARE` of the way back to the seller's last
  position (default: the tier strategy's own share); `0` restores the old
  counter at the floor.
- Seat-tier keys can negotiate too; the endpoint doesn't apply
  `BuyerContext.eligible_for_negotiation`.
- PG soft holds apply only to products with a declared `maximum_impressions`.
  The CSV catalog declares none, so they're covered by unit tests rather than
  a scenario.
- If you re-mint keys after pulling this change (`sandbox/mint_keys.py` does
  it automatically when the fictional identities change), the advertiser key
  carries `seat_ids` and `advertiser_domains` for the `wseat`/`wadomain` check.

---

## Known upstream issues relevant to sell-side testing

Checked against [IABTechLab/seller-agent](https://github.com/IABTechLab/seller-agent/issues)
on 2026-11-21. All were open at that time. This fork fixes #86, #128, #129
and #147 (see `docs/upstream-issues.md`).

| Issue | Title | How it shows up in the sandbox |
|---|---|---|
| [#86](https://github.com/IABTechLab/seller-agent/issues/86) | fix: six mutating routes accept unauthenticated callers Fixed here: those routes now need an operator key (curated, deprecate, migrate, bulk) or the owning buyer key (sessions), `POST /deals` needs a key, and `/agentic-audience/match` stays public as a stateless score. |
| [#126](https://github.com/IABTechLab/seller-agent/issues/126) | Buyer-controlled proposal text can produce an `accept` decision inconsistent with the structured bid price | LLM path only. It doesn't show up in the default (no-key) sandbox; it can once you add `ANTHROPIC_API_KEY` and raise the proposal time budget. |
| [#129](https://github.com/IABTechLab/seller-agent/issues/129) | Accepting a proposal-led negotiation and booking its quote doesn't check the proposal's approval decision Fixed here: quote booking applies the approval gate (scenario 5 checks the 202 and the approved re-submit). |
| [#130](https://github.com/IABTechLab/seller-agent/issues/130) | A second authenticated buyer can accept another buyer's proposal-led negotiation and apply its price to their own quote | Needs two buyers at the same tier. Mint a second agency key (see "Using the keys by hand"). |
| [#131](https://github.com/IABTechLab/seller-agent/issues/131) | `POST /proposals` has no idempotency key, so a retry after a client timeout creates a second proposal and approval request | Re-sending a scenario 5 body creates a second pending approval. |
| [#142](https://github.com/IABTechLab/seller-agent/issues/142) | A product's supported_deal_types is never enforced: proposals, quotes and template bookings accept deal types the product doesn't offer | e.g. quote `deal_type: "PG"` on `inv-dsp-gnn-richmedia`, whose types are PD/PA; it's accepted. |
| [#146](https://github.com/IABTechLab/seller-agent/issues/146) | A CSV catalog priced in AUD is listed and quoted as USD, and `DEFAULT_CURRENCY` does not change the label | aws_workshop is all USD, so it doesn't trigger here. Point `CSV_DATA_DIR` at a copy with `currency=AUD` under `data/csv/samples/` to reproduce. |
| [#147](https://github.com/IABTechLab/seller-agent/issues/147) | Quote-backed PG deal returns at=1 despite fixed, guaranteed terms Fixed here: PG/PD `at=3`, PA `at=1` (scenario 3). |

Also worth knowing:

- [#118](https://github.com/IABTechLab/seller-agent/issues/118): `POST /proposals` silently falls back to the
  deterministic evaluation after a crew failure or timeout. In the sandbox the
  fallback is always used, and nothing in the response says so.
- [#128](https://github.com/IABTechLab/seller-agent/issues/128): legacy `POST /deals` returned fixed demo terms ($15
  display) for any `proposal_id`. Fixed here: it needs a key and the caller's
  own accepted proposal, and uses the proposal's terms. It still tries to
  register with `OPENDIRECT_BASE_URL`, which is localhost here.
- [#82](https://github.com/IABTechLab/seller-agent/issues/82): the unit suite can leave `./ad_seller.db` behind,
  which makes a second local run fail. Delete it between runs (it's not the
  sandbox DB).

## Pulling upstream updates into this fork

The sandbox only adds `sandbox/`, `SANDBOX.md`, `CLAUDE.md` and three lines to
`.gitignore`, plus the hosted-sandbox files `fly.toml`, `.dockerignore`,
`infra/fly/` and `docs/deploy-fly.md`, so upstream merges should be clean.

```bash
# once
git remote add upstream https://github.com/IABTechLab/seller-agent.git

# each time
git checkout main
git fetch upstream
git merge upstream/main          # resolve .gitignore by keeping both sides if it conflicts
git push origin main

# then re-sync deps and re-run the scenarios against the new code
./sandbox/run.sh                 # Windows: .\sandbox\run.ps1
./sandbox/scenarios.sh           # second terminal
```

GitHub's **Sync fork** button on the fork's page does the same fetch and merge.
If a scenario starts failing after a merge, that's the point: it caught a
wire-format or behaviour change. Run it with `--verbose` and compare against
the upstream changelog. If a setting the sandbox relies on gets renamed
upstream, update `sandbox/.env.sandbox`, `sandbox/.env.fly` and the
`PINNED_KEYS` / `mock_only_violations()` lists in `sandbox/sandbox_env.py`
together.

## Continuing development locally with Claude Code

[`CLAUDE.md`](CLAUDE.md) at the repo root gives Claude Code the project
conventions, the test and lint commands CI runs, and the sandbox safety rules.
A typical loop:

1. `./sandbox/run.sh --reload` (Windows: `.\sandbox\run.ps1 -Reload`). The
   server restarts when you edit `src/`.
2. Ask Claude Code for a change, and have it add or adjust a unit test under
   `tests/unit/`.
3. `uv run --locked pytest tests/unit/ -q` and `uv run --locked ruff check src/`.
4. `./sandbox/scenarios.sh` to check the end-to-end buyer flows still hold.

Claude Code itself uses your Anthropic account. That's separate from the
optional `ANTHROPIC_API_KEY` the seller's CrewAI paths read from
`sandbox/.local/sandbox.env`.

## Known blockers

- **Intel Macs, and macOS 13 or older**: `uv sync --locked` can't install
  `onnxruntime==1.27.0` (CrewAI → chromadb), which has wheels only for macOS 14+
  on arm64. `cryptography==49.0.0` also lacks an Intel-mac wheel. Use an Apple
  Silicon Mac on Sonoma or later, or run the sandbox in a Linux VM or WSL2.
- **Windows**: the locked dependency set resolves to binary wheels for
  `win_amd64` (checked with `uv pip compile --python-platform
  x86_64-pc-windows-msvc --only-binary :all:`), and the `.ps1` scripts were run
  under PowerShell 7 on Linux. They haven't yet been run on a real Windows
  desktop, so please report anything odd.
