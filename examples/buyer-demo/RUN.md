# How to run the buyer-agent demo

Times in the logs are Australia/Sydney (AEDT/AEST). This demo only talks to
the seller over REST. It never prints or writes API key values.

In the seller fork this folder is `examples/buyer-demo/`. On the box it also
lives at `/workspace/buyer-agent`. The commands below assume you have already
`cd`'d into this folder.

## What you have to supply

1. **Seller URL** — optional. Defaults to the public mock sandbox
   `https://iab-seller.agenticape.ai`. Override with `SELLER_URL` or
   `--seller-url`.
2. **A buyer API key** — required for every scenario except `--check` and
   most of `anon-write`. Use one of the two methods below. Do not put the
   value in a file inside this folder.

### Where the key comes from

The Fly sandbox mints buyer and operator keys into `/data/keys.json` on the
seller volume. A local copy (gitignored) lives in the seller repo at:

`sandbox/.local/keys.fly.json`

(relative to the repo root; on the box the deploy clone has the same file at
`/workspace/iab-seller-deploy/sandbox/.local/keys.fly.json`).

That JSON has `buyers.advertiser`, `buyers.agency`, `buyers.seat`, and
`operator` entries (each with a `key_id` and `api_key`). Re-download
`/data/keys.json` from the Fly app if you do not already have a copy (see
`docs/deploy-fly.md` in the seller repo). Do not commit that file.

`--check` does not need a key.

### Preferred: point at the keys file

macOS / Linux:

```bash
export SELLER_URL=https://iab-seller.agenticape.ai
# From examples/buyer-demo in a seller-repo clone:
export KEYS_FILE=../../sandbox/.local/keys.fly.json
# On the box, if you are running /workspace/buyer-agent instead:
# export KEYS_FILE=/workspace/iab-seller-deploy/sandbox/.local/keys.fly.json
```

Windows PowerShell:

```powershell
$env:SELLER_URL = "https://iab-seller.agenticape.ai"
# From examples\buyer-demo in a seller-repo clone:
$env:KEYS_FILE = "..\..\sandbox\.local\keys.fly.json"
# Or an absolute path to your copy of keys.fly.json:
# $env:KEYS_FILE = "C:\path\to\sandbox\.local\keys.fly.json"
```

`KEYS_FILE` loads the advertiser, agency and seat buyer keys. The operator
key in that file is ignored (this demo never calls approve/reject).

### Or: export individual keys

macOS / Linux:

```bash
export SELLER_URL=https://iab-seller.agenticape.ai
export SELLER_API_KEY=          # advertiser-tier key (required for pricing quotes, booking, extras)
export SELLER_API_KEY_AGENCY=   # optional, but needed for a true in-band negotiation
export SELLER_API_KEY_SEAT=     # optional, used in the pricing tier comparison
```

Windows PowerShell:

```powershell
$env:SELLER_URL = "https://iab-seller.agenticape.ai"
$env:SELLER_API_KEY = ""            # advertiser-tier key
$env:SELLER_API_KEY_AGENCY = ""     # optional, in-band negotiation
$env:SELLER_API_KEY_SEAT = ""       # optional, pricing tier comparison
```

If you only set `SELLER_API_KEY`, discovery, pricing (advertiser), booking,
anon-write and extras still run. Negotiation will warn: on this mock catalog
the advertiser price already sits on the 85% floor, so a below-quote offer
is also a lowball. Set `SELLER_API_KEY_AGENCY` (or use `KEYS_FILE`) to see a
reasonable in-band counter as well.

Do not export the operator key here. This demo will not approve or reject
anything.

## Setup (once)

With [uv](https://docs.astral.sh/uv/) installed, from this folder
(`examples/buyer-demo` in a clone of
https://github.com/agenticape/IABTechLab-Seller-Agent, or
`/workspace/buyer-agent` on the box):

macOS / Linux:

```bash
git clone https://github.com/agenticape/IABTechLab-Seller-Agent.git
cd IABTechLab-Seller-Agent/examples/buyer-demo
uv sync
```

Windows PowerShell:

```powershell
git clone https://github.com/agenticape/IABTechLab-Seller-Agent.git
cd IABTechLab-Seller-Agent\examples\buyer-demo
uv sync
```

Python 3.11+ is required. The dependencies are `httpx` and `tzdata` (zone
data for the Australia/Sydney log times; Windows Python has none built in,
so no `--with tzdata` is needed any more).

The Fly app may be scaled to zero. The first request can take several
seconds (cold start). The default HTTP timeout is 60s.

Until the `demo/buyer-demo` branch is on the fork, the same files are on the
box at `/workspace/buyer-agent` (`cd /workspace/buyer-agent` then `uv sync`).

## Connectivity check (read-only, no key)

```bash
uv run python buyer_demo.py --check
```

```powershell
uv run python buyer_demo.py --check
```

This is a `GET /health` only. It does not create quotes, deals or approvals.

## One command per scenario

Run these from this folder after the exports above.

```bash
uv run python buyer_demo.py --scenario discovery
uv run python buyer_demo.py --scenario pricing
uv run python buyer_demo.py --scenario negotiation
uv run python buyer_demo.py --scenario booking
uv run python buyer_demo.py --scenario anon-write
uv run python buyer_demo.py --scenario extras
```

The PowerShell commands are the same. You can stack names:
`--scenario discovery --scenario pricing`.

**Writes:** `pricing` creates a quote; `negotiation` creates two quotes and
two negotiation messages (no deal); `booking` creates a small deal and a
high-value booking that should return `202 pending_approval` (leave it
pending); `extras` may create a small proposal that is not booked.
`discovery` is mostly reads, plus `POST /discovery` and `POST /products/avails`.
`anon-write` only sends unauthenticated writes that should be rejected.

## Full suite

```bash
uv run python buyer_demo.py --all
```

```powershell
uv run python buyer_demo.py --all
```

This runs all six scenarios in order. It **will** create a small booked deal
and a pending approval on the live seller. The demo does not decide that
approval. Do not approve or reject it unless you mean to.

The suite is not the default. Running the script with no arguments prints
help and exits.

## Watch it in the browser (`--ui`)

The same scenarios, shown as a buyer agent and a seller agent talking. The
default is a blocky 3D view: two box-built characters on a voxel island, a
glowing packet arcing between them for each call, gold deal blocks on the
seller's stall, padlock blocks for approval holds and a red X for refusals.
Drag to turn the camera, scroll to zoom. **View** switches to the flat 2D
view (remembered in the browser). Without WebGL the flat view is used
automatically, and with "reduce motion" set in the OS the scene stays still.
In both views each call is a pair of plain-language speech bubbles (green agreed, amber counter
or held for approval, red refused), with method, path, status and latency
underneath and the raw request/response one click away. A side panel shows
deal and approval cards and the running notes.

Windows PowerShell, first time:

```powershell
git clone https://github.com/agenticape/IABTechLab-Seller-Agent.git
cd IABTechLab-Seller-Agent
git checkout demo/buyer-demo
cd examples\buyer-demo
uv sync
```

Already cloned:

```powershell
cd IABTechLab-Seller-Agent
git fetch origin
git checkout demo/buyer-demo
git pull origin demo/buyer-demo
cd examples\buyer-demo
uv sync
```

Then set the seller and keys (as above) and start the UI:

```powershell
$env:SELLER_URL = "https://iab-seller.agenticape.ai"
$env:KEYS_FILE = "C:\path\to\keys.fly.json"     # or set $env:SELLER_API_KEY etc.
uv run python buyer_demo.py --ui
```

Your browser opens <http://127.0.0.1:8765/>. Pick a scenario (or `all`), set
the pause between steps (0 to 3 seconds; about 1.2 s is easy to follow),
press **Run**, and **Clear** to start over. Stop the server with Ctrl+C in
the PowerShell window. Options: `--port 9000` for another port,
`--no-browser` to only print the URL.

The page is served from your machine only (127.0.0.1) and works offline: no
CDN, no build step. three.js is vendored as `static/three.module.min.js`, and
only the files listed in `demo_ui.STATIC_FILES` are served under `/static/`. API keys stay in the Python process; the browser only
sees `***REDACTED***` and which tiers have a key. Every run still writes the
`runs/` markdown and JSON logs, exactly as the CLI does, and the terminal
shows the usual CLI lines as the run goes.

The hosted copy at <https://iab-buyer.agenticape.ai> is the same page in
`--serve` mode. See `deploy/fly/DEPLOY.md`.

## Tests

```powershell
uv run pytest
```

They use an in-process fake seller (no network) and cover the event stream,
key redaction, the CLI lines, and the web server's guardrails.

## Logs

Each scenario run writes two files under `runs/` (gitignored):

- `runs/YYYYMMDD-HHMMSS.md` — readable log
- `runs/YYYYMMDD-HHMMSS.json` — the same calls as JSON

API key headers and values are redacted. Keep these logs off git and off
any public paste if you are unsure.

Optional flags: `--seller-url URL`, `--timeout 60`, `--ui`, `--port`, `--no-browser`, `--serve`, `--host`.
