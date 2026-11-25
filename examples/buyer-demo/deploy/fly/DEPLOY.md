# Deploy the buyer demo UI to Fly.io

Public URL: <https://iab-buyer.agenticape.ai> (Fly app `iab-buyer-agenticape`,
region `syd`, one `shared-cpu-1x` / 512 MB machine that scales to zero).

Visitors see the buyer and seller agents talking and can run the fixed
scenarios against the **mock-only sandbox seller**
(<https://iab-seller.agenticape.ai>). Every run creates fictional quotes,
negotiations, a small deal and a held approval on that sandbox. The demo
never approves or rejects anything: it has no operator key, and the client
refuses any non-GET call under `/approvals`.

Run these from a machine that has `flyctl` and is logged in to the
agenticape Fly organisation. All commands run from `examples/buyer-demo`.

## 1. Create the app (once)

```bash
cd examples/buyer-demo
fly apps create iab-buyer-agenticape
```

## 2. Set secrets

Use the **buyer** keys from the seller's `keys.fly.json` (`buyers.advertiser`,
`buyers.agency`, `buyers.seat`). Never use the operator key. The values below
are placeholders.

```bash
fly secrets set --app iab-buyer-agenticape --stage \
  SELLER_URL=https://iab-seller.agenticape.ai \
  SELLER_API_KEY=<advertiser-buyer-key> \
  SELLER_API_KEY_AGENCY=<agency-buyer-key> \
  SELLER_API_KEY_SEAT=<seat-buyer-key>

# Optional: put the whole page behind basic auth (any username, this password).
fly secrets set --app iab-buyer-agenticape --stage DEMO_UI_PASSWORD=<choose-a-password>
```

PowerShell uses a backtick for line continuation:

```powershell
fly secrets set --app iab-buyer-agenticape --stage `
  SELLER_URL=https://iab-seller.agenticape.ai `
  SELLER_API_KEY=<advertiser-buyer-key> `
  SELLER_API_KEY_AGENCY=<agency-buyer-key> `
  SELLER_API_KEY_SEAT=<seat-buyer-key>
```

`--stage` stores them for the first deploy without restarting anything. To
remove the password later, run `fly secrets unset --app iab-buyer-agenticape DEMO_UI_PASSWORD`.

## 3. Deploy

```bash
fly deploy . --config deploy/fly/fly.toml --dockerfile deploy/fly/Dockerfile --ha=false
```

The build context is this folder (`.`), so the paths in the command matter.
`--ha=false` keeps a single machine: the run queue and the per-IP rate limit
live in that machine's memory.

Check it:

```bash
fly status --app iab-buyer-agenticape
curl https://iab-buyer-agenticape.fly.dev/health
# {"status": "ok", "mode": "hosted"}
```

## 4. Custom domain

```bash
fly certs add iab-buyer.agenticape.ai --app iab-buyer-agenticape
fly ips list --app iab-buyer-agenticape
```

In Cloudflare DNS for `agenticape.ai`, add **one** of these:

| Type | Name | Target | Proxy status |
|---|---|---|---|
| `CNAME` | `iab-buyer` | `iab-buyer-agenticape.fly.dev` | DNS only (grey cloud) |

or, using the addresses from `fly ips list`:

| Type | Name | Value | Proxy status |
|---|---|---|---|
| `A` | `iab-buyer` | the shared or dedicated IPv4 from `fly ips list` | DNS only (grey cloud) |
| `AAAA` | `iab-buyer` | the IPv6 from `fly ips list` | DNS only (grey cloud) |

If `fly ips list` shows no IPv6, run `fly ips allocate-v6 --app iab-buyer-agenticape`
first. Keep the record grey-clouded (DNS only): Fly issues and serves the
TLS certificate itself, which needs the name to resolve straight to Fly.

Then wait for the certificate:

```bash
fly certs check iab-buyer.agenticape.ai --app iab-buyer-agenticape
curl https://iab-buyer.agenticape.ai/health
```

## Settings

Secrets (set with `fly secrets set`):

| Name | Required | What |
|---|---|---|
| `SELLER_URL` | yes | Sandbox seller base URL, `https://iab-seller.agenticape.ai` |
| `SELLER_API_KEY` | yes | Advertiser-tier buyer key (quotes, booking, extras) |
| `SELLER_API_KEY_AGENCY` | recommended | Agency-tier buyer key (in-band negotiation) |
| `SELLER_API_KEY_SEAT` | optional | Seat-tier buyer key (pricing comparison) |
| `DEMO_UI_PASSWORD` | optional | Turns on basic auth for everything except `/health`. Off when unset |

Plain env (in `fly.toml` `[env]`; change there and redeploy):

| Name | Default (hosted) | What |
|---|---|---|
| `DEMO_RATE_LIMIT_RUNS` | `3` | Runs per visitor IP per window. `0` turns the limit off |
| `DEMO_RATE_LIMIT_WINDOW_SECONDS` | `600` | Rate-limit window |
| `DEMO_MAX_CONCURRENT_RUNS` | `1` | Runs at the same time (capped at 2); the rest queue with a visible "Waiting" state |
| `DEMO_MAX_QUEUED_RUNS` | `10` | Queue length before new runs get 503 |
| `DEMO_LIVE_FEED` | `true` | Lets visitors tick "Watch live feed" to follow everyone's runs |
| `DEMO_TRUST_PROXY` | `true` | Rate-limit by Fly's `Fly-Client-IP` header (set by the Fly proxy) |
| `PORT` | `8080` | Listen port inside the machine |

## What the hosted page guards

- The browser picks only one of the fixed scenarios (`discovery`, `pricing`,
  `negotiation`, `booking`, `anon-write`, `extras`, `all`). Runs go at full
  speed, and the page paces the playback itself. An optional server-side
  pause is still accepted but clamped to 0 to 3 seconds. Nothing the browser
  sends reaches the seller.
- `POST`s must be same-origin JSON with the page's `X-Demo-Client` header. No
  CORS headers are ever sent, so other sites cannot start runs.
- Each run streams to the viewer who started it, at an unguessable
  `/api/runs/<id>/events` URL. The optional live feed shows the same redacted
  events from every run.
- API keys stay in the server. Events are redacted when emitted, then scrubbed
  again as text before they are streamed. `/api/config` reports only whether
  each tier has a key. The machine's log shows run lifecycle lines only.
- `/health` is open (no auth) for the Fly health check.
- Run logs (`runs/*.md`, `runs/*.json`) are written inside the machine and are
  lost when it stops. They are redacted the same way.

## Updating

Pull `demo/buyer-demo`, then run the same `fly deploy` command. The image
copies `static/` (vendored three.js and the 3D scene) next to `ui.html`, so
use the Dockerfile from the branch. Rotating a
seller key is `fly secrets set ...` again; Fly restarts the machine.
