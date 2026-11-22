# Deploy the mock-only sandbox to Fly.io (public, `iab-seller.agenticape.ai`)

This guide puts the [local mock-only sandbox](../SANDBOX.md) on the internet
as a **public** seller agent at <https://iab-seller.agenticape.ai>, hosted on
[Fly.io](https://fly.io) in Sydney (`syd`), with DNS on Cloudflare. Every
command is written for **Windows PowerShell** (5.1 or 7).

It runs the same seller code and the same fictional CSV catalog
(`data/csv/samples/aws_workshop`, 15 `inv-*` products) as the local sandbox.

## What gets deployed

| Piece | File | Notes |
|---|---|---|
| Image | [`infra/fly/Dockerfile`](../infra/fly/Dockerfile) | Python 3.12 slim, locked `uv sync --no-dev`, only the aws_workshop CSV copied in. Server runs as the non-root `seller` user on `0.0.0.0:$PORT` (8080), one uvicorn worker. |
| Entry point | [`infra/fly/entrypoint.sh`](../infra/fly/entrypoint.sh) → [`sandbox/fly_server.py`](../sandbox/fly_server.py) | Starts as root just long enough to `chown` the volume, then drops to `seller`. Loads the mock-only profile, runs the safety guard, mints keys on first boot, then serves. |
| Mock-only profile | [`sandbox/.env.fly`](../sandbox/.env.fly) | Pins `AD_SERVER_TYPE=csv`, the aws_workshop catalog, GAM/FreeWheel off, blank `SSP_CONNECTORS` / `DEAL_SYNC_CONNECTORS`, OpenDirect and registry on dead local URLs, SQLite on `/data`. Neither `fly.toml` nor `fly secrets` can override these. |
| Fly config | [`fly.toml`](../fly.toml) | App `iab-seller-agenticape`, region `syd`, HTTPS forced, health check `GET /health`, scale-to-zero, 1 GB volume at `/data`, `shared-cpu-1x` with 512 MB. |

The server **refuses to boot** (exit code 2, reason in `fly logs`) if the
effective config isn't mock-only, or if `SELLER_AGENT_URL` isn't an `https://`
URL. No ad server, SSP or deal-sync credentials exist anywhere in this setup,
and none are needed.

What's on the volume (`/data`), and so survives deploys and restarts:

| File | What |
|---|---|
| `/data/ad_seller_sandbox.db` | SQLite: API keys (hashed), quotes, deals, negotiations, approvals, events. |
| `/data/keys.json` | Plain-text operator and buyer API keys (mode 600). Only key IDs are ever logged. |
| `/data/audit_fallback.jsonl` | Audit events the event bus couldn't persist (normally absent). |

!!! warning "Fully public"
    Anyone on the internet can call the anonymous endpoints (catalog, public
    pricing, avails, agent card, and the routes listed in upstream issue
    [#86](https://github.com/IABTechLab/seller-agent/issues/86) that accept
    unauthenticated writes). That's acceptable here because every byte of data
    is fictional, but don't put anything real on this deployment. Seller
    security behaviour is deliberately unchanged in this setup.

## Prerequisites

- A Fly.io account with a payment method (Fly bills pay-as-you-go).
- Access to the `agenticape.ai` zone in the Cloudflare dashboard.
- This repo cloned locally, e.g. `C:\dev\IABTechLab-Seller-Agent`. You don't
  need Docker locally: `fly deploy` builds on Fly's remote builder.

## 1. Install flyctl and log in

```powershell
# Official installer, per-user (no admin). Use `pwsh` instead of `powershell` on PowerShell 7.
powershell -Command "iwr https://fly.io/install.ps1 -useb | iex"
```

Open a **new** PowerShell window so `fly` is on your `PATH`, then:

```powershell
fly version
fly auth signup     # first time only (opens the browser), or:
fly auth login
```

## 2. Create the app from the repo's fly.toml (no deploy yet)

```powershell
cd C:\dev\IABTechLab-Seller-Agent
fly launch --no-deploy --copy-config --name iab-seller-agenticape --region syd
```

- If it asks **"Do you want to tweak these settings before proceeding?"**,
  answer **N**. If it offers Postgres, Redis or Tigris, decline.
- Afterwards run `git diff fly.toml`. Only the `app = ...` line should differ,
  if anything. Discard any other edits with `git checkout -- fly.toml` (after
  re-applying a new app name, if you chose one).
- **App name taken?** Fly app names are global. Pick another, e.g.
  `--name agenticape-iab-seller`, and keep it in `fly.toml`. Everywhere below,
  replace `iab-seller-agenticape` with your name, including the Cloudflare
  CNAME target (`<app>.fly.dev`).

If `fly launch` misbehaves, this creates the same empty app without touching
`fly.toml`:

```powershell
fly apps create iab-seller-agenticape
```

## 3. Create the volume

The name must match `source = "seller_data"` in `fly.toml`.

```powershell
fly volumes create seller_data --region syd --size 1 --yes
```

Fly warns that a single volume has no redundancy. That's expected: SQLite must
live on exactly one machine. Back up the DB with `fly volumes snapshots list`
(Fly takes daily snapshots by default).

## 4. Secrets (all optional)

**No secrets are required.** Every sandbox flow, including the scripted
scenarios, is deterministic and needs no LLM key.

Only if you want the CrewAI/LLM code paths:

```powershell
fly secrets set ANTHROPIC_API_KEY=sk-ant-your-own-key --stage
```

`--stage` stores it for the next `fly deploy` without restarting anything.

!!! warning "LLM spend on a public endpoint"
    With a key set, anonymous internet traffic can reach code paths that call
    Anthropic. `POST /proposals` stays on the deterministic evaluator
    (`PROPOSAL_FLOW_TIME_BUDGET=20` is below the crew's 120 s minimum), but
    give the key its own Anthropic workspace with a low monthly spend limit.
    Remove it with `fly secrets unset ANTHROPIC_API_KEY`.

Never put ad server, SSP or deal-sync credentials in Fly secrets. Even if you
did, `sandbox/.env.fly` pins those integrations off.

## 5. Deploy

```powershell
fly deploy --ha=false
```

`--ha=false` keeps it to **one** machine; a second machine would get its own
empty volume and SQLite file. The first build takes a few minutes (CrewAI's
dependency tree is large). Later deploys reuse cached layers unless
`uv.lock` changes.

Watch it boot:

```powershell
fly logs
```

On the first boot you'll see:

```text
[fly-sandbox] Minted new sandbox API keys in /data/keys.json
[fly-sandbox]   operator          key_id=key-xxxxxxxx
[fly-sandbox]   buyer (seat      ) key_id=key-xxxxxxxx
[fly-sandbox]   buyer (agency    ) key_id=key-xxxxxxxx
[fly-sandbox]   buyer (advertiser) key_id=key-xxxxxxxx
[fly-sandbox]   secrets are only in that file. Operator: fly ssh console -C 'cat /data/keys.json'
[fly-sandbox] Serving mock-only seller on 0.0.0.0:8080; agent card advertises https://iab-seller.agenticape.ai; ...
```

Later boots say `Reusing existing sandbox API keys`.

## 6. Verify on the fly.dev URL

```powershell
$base = "https://iab-seller-agenticape.fly.dev"

Invoke-RestMethod "$base/health"                       # status: healthy
(Invoke-RestMethod "$base/.well-known/agent.json").url  # https://iab-seller.agenticape.ai
(Invoke-RestMethod "$base/products?limit=500").products | Select-Object product_id, name
```

The agent card advertises `https://iab-seller.agenticape.ai` even before DNS
exists. It comes from `SELLER_AGENT_URL` in `fly.toml` `[env]`.

If the machine was idle and auto-stopped, the first request starts it, which
takes a few seconds.

### Get the API keys

```powershell
New-Item -ItemType Directory -Force sandbox\.local | Out-Null
fly ssh console -C "cat /data/keys.json" | Set-Content -Encoding utf8 sandbox\.local\keys.fly.json
Get-Content sandbox\.local\keys.fly.json -TotalCount 3   # should start with {
$k = Get-Content sandbox\.local\keys.fly.json -Raw | ConvertFrom-Json
```

`sandbox/.local/` is git-ignored. `fly ssh console` needs a running machine,
so hit `/health` first if it has auto-stopped. Give buyers only their own
`buyers.<tier>.api_key`. The `operator` key is admin: approvals, minting keys,
events.

### Get a quote and book it

```powershell
$adv = $k.buyers.advertiser.api_key
$h = @{ 'X-Api-Key' = $adv }

Invoke-RestMethod -Method Post "$base/pricing" -Headers $h -ContentType 'application/json' `
  -Body '{"product_id":"inv-ctv-apex-series"}'

$body = @{
  idempotency_key = "demo-$(Get-Random)"
  product_id      = "inv-dsp-gnn-richmedia"
  deal_type       = "PD"
  impressions     = 1000000
  flight_start    = "2027-01-01"
  flight_end      = "2027-01-31"
} | ConvertTo-Json
$q = Invoke-RestMethod -Method Post "$base/api/v1/quotes" -Headers $h -ContentType 'application/json' -Body $body
$q.quote | Select-Object quote_id, buyer_tier, expires_at

$book = @{ idempotency_key = "book-$(Get-Random)"; quote_id = $q.quote.quote_id } | ConvertTo-Json
(Invoke-RestMethod -Method Post "$base/api/v1/deals" -Headers $h -ContentType 'application/json' -Body $book).deal |
  Select-Object deal_id, status, deal_type
```

With `curl` (use `curl.exe` on Windows; plain `curl` is an alias for
`Invoke-WebRequest` in Windows PowerShell 5.1):

```powershell
curl.exe -s "$base/.well-known/agent.json"
```

### Run the full scenario suite against it (optional)

This needs the local sandbox installed once (`.\sandbox\setup.ps1`, see
[SANDBOX.md](../SANDBOX.md)). It doesn't need the local server running.

```powershell
.\sandbox\scenarios.ps1 --base-url $base --keys sandbox\.local\keys.fly.json `
  --expect-agent-url https://iab-seller.agenticape.ai
```

Expect scenarios 1-5 to PASS, with the same two WARNs as locally (product
floor undercut by stacked discounts, and PG `at=1`, upstream issue #147). Each
run adds a few fictional quotes, deals and approvals to the hosted DB.

## 7. Custom domain: `iab-seller.agenticape.ai`

### 7a. Ask Fly for a certificate

```powershell
fly certs add iab-seller.agenticape.ai
fly certs setup iab-seller.agenticape.ai    # prints the exact DNS records Fly wants
```

### 7b. Add the DNS records in Cloudflare

Cloudflare dashboard → `agenticape.ai` → **DNS → Records → Add record**:

| Type | Name | Target / content | Proxy status | TTL |
|---|---|---|---|---|
| `CNAME` | `iab-seller` | `iab-seller-agenticape.fly.dev` | **DNS only** (grey cloud) | Auto |
| `CNAME` | `_acme-challenge.iab-seller` | the value `fly certs setup` prints, shaped like `iab-seller.agenticape.ai.xxxxx.flydns.net` | **DNS only** (grey cloud) | Auto |

- Copy the `_acme-challenge` target **exactly** from `fly certs setup`; it's
  unique to your app. It lets Fly issue and renew the certificate by DNS
  challenge, which keeps working even if you later turn the Cloudflare proxy
  on.
- If `fly certs setup` suggests `A`/`AAAA` records instead of the CNAME, either
  works. The CNAME is simpler because it follows Fly if the app's IPs change.
- Only these two names are added. The existing `agenticape.ai` Workers & Pages
  site, the apex and `www` records stay untouched, so they're unaffected.

### 7c. Wait for the certificate, then test

```powershell
fly certs check iab-seller.agenticape.ai   # repeat until it reports the certificate as issued/ready
Invoke-RestMethod https://iab-seller.agenticape.ai/health
(Invoke-RestMethod https://iab-seller.agenticape.ai/.well-known/agent.json).url
```

Issuance usually takes a minute or two after DNS propagates. If
`fly certs check` complains about DNS, make sure both records are still grey.

### 7d. Optional: put Cloudflare's proxy in front (orange cloud)

Leaving it grey is fine: Fly terminates TLS with its own certificate. To get
Cloudflare caching, WAF and DDoS protection, do this only **after** the Fly
certificate is issued:

1. **Set SSL to Full (strict) for this hostname only.** Go to **Rules →
   Configuration Rules → Create rule**. Set *When incoming requests match*
   to `Hostname equals iab-seller.agenticape.ai`, then *SSL* to **Full
   (strict)**, and deploy it. This leaves the zone-wide SSL/TLS mode, which
   the rest of `agenticape.ai` uses, unchanged. If the zone is already on
   Full (strict), the rule is harmless.
   - Don't use **Flexible** for this host. Cloudflare would talk HTTP to Fly,
     `force_https` would redirect back to HTTPS, and browsers would show
     "too many redirects".
2. Switch the `iab-seller` CNAME to **Proxied** (orange). Leave
   `_acme-challenge.iab-seller` grey; renewals depend on it.
3. Check **Workers Routes** for a pattern such as `*agenticape.ai/*` or
   `*.agenticape.ai/*`. A wildcard route would send proxied `iab-seller`
   traffic to your Worker instead of Fly. Add an exclusion or a narrower route
   if so. A route limited to `agenticape.ai/*` or `www.agenticape.ai/*`
   doesn't match.
4. Re-test with `Invoke-RestMethod https://iab-seller.agenticape.ai/health`.

Cloudflare's proxy times out requests after 100 s. The sandbox flows answer
in seconds, but an LLM crew run (if you enable one) could exceed that.

## Day-2 operations

| Task | Command |
|---|---|
| Live logs | `fly logs` |
| Status, machine, region, health checks | `fly status` / `fly checks list` |
| Shell into the machine | `fly ssh console` |
| Show effective (mock-only) config | `fly ssh console -C "/app/infra/fly/entrypoint.sh check"` |
| **Rotate API keys** (revokes all `sandbox-*` keys, writes a new `/data/keys.json`, no restart needed) | `fly ssh console -C "/app/infra/fly/entrypoint.sh rekey"`, then re-download `keys.json` (step 6) |
| Redeploy after `git pull` | `fly deploy --ha=false` |
| Change the advertised URL | edit `SELLER_AGENT_URL` in `fly.toml` (must be `https://`), then `fly deploy --ha=false` |
| More memory (if `fly logs` shows OOM) | `fly scale memory 1024` |
| Keep it always on (no cold starts) | set `min_machines_running = 1` in `fly.toml`, then `fly deploy --ha=false` |
| Stop it (keeps app, volume, cert) | `fly scale count 0 --yes`. Bring it back with `fly scale count 1 --yes` |
| Wipe all sandbox data (fresh DB and keys) | `fly ssh console -C "sh -c 'rm -f /data/ad_seller_sandbox.db* /data/keys.json'"`, then `fly machine restart` |
| Destroy everything (app, machines, volume, cert) | `fly apps destroy iab-seller-agenticape`, then delete both `iab-seller` DNS records in Cloudflare |

Rotate the keys whenever a `keys.json` copy may have leaked. Revoking a single
buyer key works too: as the operator, call `DELETE /auth/api-keys/{key_id}`.

## Cost (rough, check <https://fly.io/calculator>)

Fly bills per second for running machines. Sydney prices are a little higher
than US regions. Approximate USD per month:

| Item | Scale-to-zero (`min_machines_running = 0`, default) | Always on (`= 1`) |
|---|---|---|
| `shared-cpu-1x`, 512 MB | cents to ~$1 for light, bursty use (billed only while running) | ~$3.5-4.5 |
| ...or 1 GB (`fly scale memory 1024`) | cents to ~$1.5 | ~$6-8 |
| Stopped-machine root filesystem (~0.4 GB compressed image) | ~$0.10 | n/a |
| Volume, 1 GB | $0.15 | $0.15 |
| Certificate (first 10 hostnames), shared IPv4, IPv6 | $0 | $0 |
| Outbound bandwidth (APAC) | ~$0.04/GB, negligible for API JSON | same |

**Trade-off**: with scale-to-zero, Fly stops the machine a few minutes after
the last request. The next request waits for a cold start, which took about
2 s for the server in local tests plus Fly's machine start, typically 2-5 s in
total. Always-on costs a few dollars a month and avoids that wait. A dedicated
IPv4 (`fly ips allocate-v4`) would add $2/month and isn't needed.

## Troubleshooting

- **`REFUSING TO START` in `fly logs`**: the listed setting isn't mock-only.
  The usual cause is `SELLER_AGENT_URL` in `fly.toml` not starting with
  `https://`.
- **Health check failing right after deploy**: run `fly logs`. The check has a
  60 s grace period; the server is normally healthy within a few seconds.
- **Two machines listed in `fly status`**: `fly scale count 1 --yes`, then
  delete the extra empty volume (`fly volumes list`, `fly volumes destroy <id>`).
- **`keys.json` looks empty or garbled**: the machine was stopped. Run
  `Invoke-RestMethod $base/health` first, then repeat the `fly ssh console`
  command.
- **Cloudflare 525/526 after turning the proxy on**: the Fly certificate isn't
  issued yet, or the SSL rule isn't Full (strict). Switch back to grey, wait
  for `fly certs check`, and retry.

## Testing the image locally (optional, needs Docker)

```powershell
docker build -f infra/fly/Dockerfile -t iab-seller .
docker run --rm -p 8080:8080 -v iab-seller-data:/data iab-seller
Invoke-RestMethod http://127.0.0.1:8080/health
docker run --rm -v iab-seller-data:/data iab-seller check    # prints the effective config
```
