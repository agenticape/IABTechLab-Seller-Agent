# CLAUDE.md

Guidance for Claude Code (and other coding agents) working in this repository.

## What this is

A fork of [IABTechLab/seller-agent](https://github.com/IABTechLab/seller-agent):
the IAB Tech Lab reference **seller** (publisher/SSP) agent. Python 3.11+
FastAPI app plus CrewAI flows. Its main surfaces are the product catalog, tiered
pricing, IAB Deals API quotes and bookings, multi-round negotiation, proposals
with a human-in-the-loop approval gate, orders/change requests, and an MCP
server at `/mcp`. The companion buyer is
[IABTechLab/buyer-agent](https://github.com/IABTechLab/buyer-agent).

## Sandbox safety rules (non-negotiable)

Local work runs through the mock-only sandbox. See `SANDBOX.md`.

- Never point anything at a real ad server, SSP, DSP, deal-sync service, live
  agent registry or the hosted IAB agentic-direct server. Never use real company
  or customer data. Test data stays fictional (`data/csv/samples/aws_workshop`).
- Start the server with `sandbox/run.sh` / `sandbox/run.ps1` (or
  `uv run python sandbox/run_server.py`), not bare `uvicorn`. The launcher
  loads `sandbox/.env.sandbox` and refuses to boot unless the config is
  mock-only (`sandbox/sandbox_env.py::mock_only_violations`).
- Don't create or edit a repo-root `.env` for sandbox work. The repo's
  `.env.example` defaults to GAM, the hosted OpenDirect server and the live
  registry.
- Never commit secrets. Personal settings such as `ANTHROPIC_API_KEY` go in
  `sandbox/.local/sandbox.env` (git-ignored). API keys live in
  `sandbox/.local/keys.json` (git-ignored). Never print or paste their values
  into code, commits, PRs or logs. Refer to `keys.json` instead.
- If you add a setting that can reach an external system, add it to
  `PINNED_KEYS` and `mock_only_violations()` in `sandbox/sandbox_env.py` and
  give it a safe value in `sandbox/.env.sandbox` and in `sandbox/.env.fly`
  (the public Fly.io sandbox profile, see `docs/deploy-fly.md`).
- Don't change core behaviour in `src/` just to make the sandbox work. Fix it
  in `sandbox/`, or document the blocker in `SANDBOX.md`.

## Commands

Everything goes through `uv` with the lockfile. Don't `pip install`, and
don't use the system Python.

```bash
uv sync --locked --python 3.12 --extra dev           # install (what sandbox/setup.* does)

# Tests: same env vars as CI (.github/workflows/ci.yml)
export ANTHROPIC_API_KEY=test-key-for-ci CREWAI_TELEMETRY_OPT_OUT=true OTEL_SDK_DISABLED=true
uv run --locked pytest tests/unit/ -q --timeout=30 --timeout-method=thread
uv run --locked pytest tests/integration/ -q --timeout=60 --timeout-method=thread
uv run --locked pytest tests/unit/test_some_module.py -k name   # one test

# Lint / format: CI fails on either
uv run --locked ruff check src/
uv run --locked ruff format --check src/ tests/

# Sandbox
./sandbox/run.sh [--reload]          # setup + serve on http://localhost:8000
./sandbox/scenarios.sh [--only N]    # scripted mock buyers (needs the server)
```

Windows equivalents: `.\sandbox\run.ps1 [-Reload]`, `.\sandbox\scenarios.ps1`.
In PowerShell, set test env vars with `$env:ANTHROPIC_API_KEY = 'test-key-for-ci'`.

Gotcha: the unit suite can leave `./ad_seller.db` in the repo root (upstream
#82). Delete it if a second run fails. The sandbox DB is
`sandbox/.local/ad_seller_sandbox.db` and isn't affected.

## Layout

- `src/ad_seller/interfaces/api/main.py`: app assembly only. Endpoints live
  in `interfaces/api/routers/*.py` (thin), business logic in
  `ad_seller/services/*.py`, and request/response models in
  `interfaces/api/schemas.py` or the shared `iab_agentic_primitives` package.
- `ad_seller/engines/`: pricing rules, negotiation and yield.
  `ad_seller/flows/`: CrewAI flows. `ad_seller/crews/`: LLM crews.
- `ad_seller/clients/`: ad server (GAM, FreeWheel, CSV, S3), SSP, deal-sync,
  registry and OpenDirect clients. **These reach external systems**, so keep
  them off in the sandbox.
- `ad_seller/config/settings.py`: every env setting (pydantic-settings).
  Process env beats `.env`.
- `sandbox/`: the mock-only local harness (not shipped upstream).
- `tests/unit/`, `tests/integration/`: pytest with `asyncio_mode = "auto"`.

## Conventions

- Ruff, line length 100. Match the surrounding style; modules in `src/` carry
  the two-line `# Author: ... / # Donated to IAB Tech Lab` header.
- Money on the wire is the shared `Money` type (`amount_micros` + `currency`).
  Price-moving routes resolve the buyer through
  `deps._verified_buyer_context`: tier comes from a seller-issued API key or a
  registry-verified `agent_url`, never from body claims.
- Money-moving POSTs (`/api/v1/quotes`, `/api/v1/deals`,
  `/api/v1/negotiations/messages`) take a required `idempotency_key`, scoped
  per buyer.
- Buyer-facing responses must never expose the seller's floor, strategy or
  concession budget.
- Bug fixes come with a regression test. Keep `docs/` and `.env.example` in
  sync when you add settings.
- Commits: no internal tracker IDs (`ar-*`) and no `Claude-Session:` trailers
  (the `.githooks/commit-msg` hook and the CI hygiene job reject them). Run
  `./scripts/setup-hooks.sh` once after cloning.

## Upstream

This fork tracks `IABTechLab/seller-agent` `main`. See "Pulling upstream
updates" in `SANDBOX.md`. Keep fork-only changes inside `sandbox/`,
`SANDBOX.md` and this file so merges stay clean. Fixes meant for everyone
belong in an upstream PR.
