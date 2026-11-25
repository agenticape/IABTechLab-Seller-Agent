# Buyer-agent demo

A small, standalone buyer that calls a live AAMP seller over REST so you can
see how the seller responds to discovery, pricing, negotiation and booking.

It talks to the public mock-only sandbox at
<https://iab-seller.agenticape.ai> by default (Fly app `iab-seller-agenticape`).
It does **not** use the IAB buyer-agent MCP or A2A clients (those paths failed
against this host in earlier probes). It never approves or rejects seller
approvals, and it never writes API keys to logs or files.

This is mock data only. Do not point it at a real ad server, SSP, or any
environment with real publisher data.

## Quick start

See **[RUN.md](RUN.md)** for env vars (names only), setup, one command per
scenario, and the full suite.

```bash
# In the seller fork this is examples/buyer-demo. On the box: /workspace/buyer-agent.
cd examples/buyer-demo   # or: cd /workspace/buyer-agent
uv sync
export SELLER_URL=https://iab-seller.agenticape.ai
# export KEYS_FILE=../../sandbox/.local/keys.fly.json   (see RUN.md)
# PowerShell: $env:SELLER_URL = "..."; $env:KEYS_FILE = "...\keys.fly.json"

uv run python buyer_demo.py --check          # read-only health check
uv run python buyer_demo.py --scenario discovery
uv run python buyer_demo.py --all            # creates a small deal + a pending approval
```

Each run writes a timestamped JSON log and a readable markdown log under
`runs/` (gitignored). Request headers that carry keys are redacted.

## What it covers

| Scenario | What it shows |
|---|---|
| `discovery` | Anonymous vs keyed catalog and `POST /discovery` (price ranges vs exact prices, `access_tier`) |
| `pricing` | `POST /pricing` by tier, then a keyed `POST /api/v1/quotes` |
| `negotiation` | A reasonable counter, then a below-floor lowball on a fresh quote (no booking) |
| `booking` | A small deal under the $250k approval gate, then one over (expect `202 pending_approval`, left pending) |
| `anon-write` | Anonymous quotes, bookings and proposals (expect `401`) |
| `extras` | Agent card, `GET /mcp` reachability, media kit, a small proposal (not booked) |

## Safety

- Do not change the seller app, its Fly machine, volume, secrets or config.
- Do not post anything from this demo upstream to IABTechLab.
- Leave any pending approval untouched. This script has no decide/resume calls.
