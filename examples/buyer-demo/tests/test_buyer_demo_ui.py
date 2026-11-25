"""Event emission, key redaction and the --ui / --serve guardrails.

Everything runs against an in-process fake seller (httpx.MockTransport) with
fictional data. No network.
"""

from __future__ import annotations

import base64
import http.client
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import buyer_demo  # noqa: E402
import demo_ui  # noqa: E402

SECRET = "sk-test-NOT-A-REAL-KEY-0123456789"


def money(v):
    return {"amount_micros": int(v * 1_000_000), "currency": "USD"}


def fake_seller(request: httpx.Request) -> httpx.Response:
    """Answers like the sandbox seller; echoes the key back to test redaction."""
    key = request.headers.get("X-Api-Key")
    path = request.url.path
    if request.method == "POST" and key is None:
        return httpx.Response(401, json={"detail": {"error": "authentication_required"}})
    if path == "/approvals":
        return httpx.Response(401, json={"detail": "Authentication required"})
    body = json.loads(request.content or b"{}")
    if path == "/api/v1/quotes":
        imps = body["impressions"]
        cpm = 44.20 if imps > 1_000_000 else 11.90
        return httpx.Response(
            200,
            json={
                "quote": {
                    "quote_id": f"qt-{imps}",
                    "buyer_tier": "advertiser",
                    "product": {"product_id": body["product_id"], "name": "Fictional"},
                    "pricing": {
                        "base_cpm": money(cpm / 0.85),
                        "final_cpm": money(cpm),
                        "tier_discount_pct": 15.0,
                    },
                    "terms": {"impressions": imps},
                    "debug_echo": f"key was {key}",
                }
            },
        )
    if path == "/api/v1/deals":
        if body["quote_id"] == "qt-8000000":
            return httpx.Response(
                202,
                json={
                    "status": "pending_approval",
                    "quote_id": body["quote_id"],
                    "approval_id": "appr-1",
                },
            )
        return httpx.Response(
            200,
            json={
                "deal": {
                    "deal_id": "DEMO-TEST1",
                    "status": "booked",
                    "pricing": {"final_cpm": money(11.90)},
                    "openrtb_params": {
                        "id": "DEMO-TEST1",
                        "bidfloor": money(11.90),
                        "at": 3,
                        "wseat": ["seat-sandbox-dsp-001"],
                        "wadomain": ["sandbox-outdoor.example"],
                    },
                }
            },
        )
    return httpx.Response(200, json={})


class FakeKeys(buyer_demo.Keys):
    def __init__(self, tiers=("advertiser",)):
        super().__init__()
        for tier in tiers:
            self.by_tier[tier] = SECRET
        self.source = "env"


def run_booking(listeners, keep_going=False, transport=None):
    return buyer_demo.run_scenarios(
        "http://seller.test",
        ["booking", "anon-write"],
        FakeKeys(),
        5.0,
        listeners=listeners,
        transport=transport or httpx.MockTransport(fake_seller),
        keep_going=keep_going,
    )


@pytest.fixture(autouse=True)
def tmp_runs(tmp_path, monkeypatch):
    monkeypatch.setattr(buyer_demo, "RUNS_DIR", tmp_path / "runs")
    return tmp_path / "runs"


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


def test_every_call_is_a_start_end_pair_with_captions(tmp_runs):
    events: list[dict] = []
    run_booking([events.append])

    kinds = [e["type"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    starts = [e for e in events if e["type"] == "call_start"]
    ends = [e for e in events if e["type"] == "call_end"]
    assert [e["seq"] for e in starts] == [e["seq"] for e in ends] == list(range(1, len(starts) + 1))
    assert all(e["say"] for e in starts)
    assert all(e["reply"] and e["tone"] for e in ends)

    replies = {e["seq"]: (e["reply"], e["tone"]) for e in ends}
    assert replies[1] == (
        "$11.90 CPM, 15% advertiser discount off $14.00 list. Quote qt-200000.",
        "ok",
    )
    assert replies[2] == ("Booked: DEMO-TEST1 at $11.90 CPM. Ready to target.", "ok")
    assert replies[4] == ("Held for approval: $353,600 is over the $250k gate.", "pending")
    assert replies[5] == ("401: no key, no write.", "refused")
    assert "Book quote qt-8000000" in starts[3]["say"]
    assert "Apr 2027" in starts[0]["say"]
    assert sorted(tmp_runs.iterdir())  # md + json still written


def test_booking_emits_deal_and_approval_cards():
    events: list[dict] = []
    run_booking([events.append])

    cards = {e["kind"]: e for e in events if e["type"] == "card"}
    deal = cards["deal"]["fields"]
    assert deal == {
        "deal_id": "DEMO-TEST1",
        "status": "booked",
        "final_cpm": "$11.90",
        "bidfloor": "$11.90",
        "at": 3,
        "wseat": ["seat-sandbox-dsp-001"],
        "wadomain": ["sandbox-outdoor.example"],
    }
    approval = cards["approval"]["fields"]
    assert approval["approval_id"] == "appr-1"
    assert approval["gross"] == "$353,600"


def test_cli_printer_lines_are_unchanged(capsys):
    run_booking([buyer_demo.cli_printer])
    out = capsys.readouterr().out
    assert "Buyer demo against http://seller.test" in out
    assert "\n=== booking: Small deal under $250k, then one over (leave 202 pending) ===" in out
    assert "  [200] POST /api/v1/quotes as advertiser  (" in out
    assert "         http=202, status=pending_approval, approval_id=appr-1" in out
    assert "  [NOTE] Large booking held: HTTP 202" in out
    assert "\nWrote " in out
    assert "say" not in out and "tone" not in out


def test_missing_key_skips_the_scenario_only_with_keep_going():
    keys = FakeKeys(tiers=())
    events: list[dict] = []
    with pytest.raises(SystemExit):
        buyer_demo.run_scenarios(
            "http://seller.test",
            ["booking"],
            keys,
            5.0,
            listeners=[events.append],
            transport=httpx.MockTransport(fake_seller),
        )
    events.clear()
    buyer_demo.run_scenarios(
        "http://seller.test",
        ["booking", "anon-write"],
        keys,
        5.0,
        listeners=[events.append],
        transport=httpx.MockTransport(fake_seller),
        keep_going=True,
    )
    assert any(e["type"] == "finding" and "skipped" in e["text"] for e in events)
    assert events[-1]["type"] == "run_end"


def test_client_refuses_approval_decisions():
    rec = buyer_demo.Recorder([], listeners=[])
    client = buyer_demo.SellerClient(
        "http://seller.test", FakeKeys(), rec, 5.0, transport=httpx.MockTransport(fake_seller)
    )
    with pytest.raises(ValueError):
        client.call("POST", "/approvals/appr-1/decide", body={"decision": "approve"})
    assert client.call("GET", "/approvals").status_code == 401


# ---------------------------------------------------------------------------
# Redaction
# ---------------------------------------------------------------------------


def test_keys_never_reach_events_or_logs(tmp_runs):
    events: list[dict] = []
    run_booking([events.append])

    text = json.dumps(events)
    assert SECRET not in text
    starts = [e for e in events if e["type"] == "call_start" and e["request_headers"]]
    assert starts and all(e["request_headers"] == {"X-Api-Key": "***REDACTED***"} for e in starts)
    assert "key was ***REDACTED***" in text
    for path in tmp_runs.iterdir():
        assert SECRET not in path.read_text(encoding="utf-8")


def test_redact_handles_header_names_and_embedded_values():
    out = buyer_demo.redact(
        {"Authorization": "Bearer x", "x-api-key": "y", "nested": [f"pre {SECRET} post"]},
        [SECRET],
    )
    assert out == {
        "Authorization": "***REDACTED***",
        "x-api-key": "***REDACTED***",
        "nested": ["pre ***REDACTED*** post"],
    }


def test_scrubber_and_browser_safe_strip_what_redact_missed():
    scrub = demo_ui.Scrubber([SECRET])
    assert SECRET not in scrub.dumps({"weird": {"k": SECRET * 2}})
    safe = demo_ui.browser_safe(
        {"type": "run_start", "key_source": "KEYS_FILE (C:/Users/someone/keys.json)"}
    )
    assert safe["key_source"] == "KEYS_FILE"
    safe = demo_ui.browser_safe(
        {"type": "run_end", "md_path": "/home/x/runs/a.md", "json_path": "/home/x/runs/a.json"}
    )
    assert safe["md_path"] == "runs/a.md"


# ---------------------------------------------------------------------------
# Web server guardrails
# ---------------------------------------------------------------------------


class Server:
    def __init__(self, settings: demo_ui.Settings, run=None, keys=None):
        settings.port = 0
        self.app = demo_ui.DemoApp(
            settings,
            "http://seller.test",
            keys or FakeKeys(),
            5.0,
            run or buyer_demo.run_scenarios,
            buyer_demo.SCENARIO_HELP,
            buyer_demo.SCENARIO_ORDER,
            transport=httpx.MockTransport(fake_seller),
        )
        self.httpd = demo_ui.build_server(self.app)
        settings.port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = demo_ui.make_handler(self.app)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.port = settings.port

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=20)
        hdrs = {"Host": f"127.0.0.1:{self.port}", **(headers or {})}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            hdrs.setdefault("Content-Type", "application/json")
            hdrs.setdefault("X-Demo-Client", "1")
        conn.request(method, path, body=data, headers=hdrs)
        resp = conn.getresponse()
        return resp.status, dict(resp.getheaders()), resp.read().decode()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def test_run_stream_is_redacted_and_ends(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=True))
    try:
        status, _, body = srv.request("POST", "/api/runs", {"scenario": "booking", "pace": 0})
        assert status == 202
        url = json.loads(body)["events_url"]
        status, headers, stream = srv.request("GET", url)
        assert status == 200 and headers["Content-Type"].startswith("text/event-stream")
        assert SECRET not in stream
        assert '"type": "run_end"' in stream and "stream_end" in stream
        assert "Held for approval" in stream
        assert "Access-Control-Allow-Origin" not in headers
    finally:
        srv.close()


def test_only_fixed_scenarios_and_same_origin_json(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=True))
    try:
        assert srv.request("POST", "/api/runs", {"scenario": "rm -rf"})[0] == 400
        assert (
            srv.request(
                "POST", "/api/runs", {"scenario": "booking"}, {"Origin": "https://evil.example"}
            )[0]
            == 403
        )
        assert (
            srv.request("POST", "/api/runs", {"scenario": "booking"}, {"X-Demo-Client": "0"})[0]
            == 415
        )
    finally:
        srv.close()


def test_rate_limit_per_ip(tmp_runs, monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(1)

    srv = Server(demo_ui.Settings(hosted=True, rate_limit_runs=2, trust_proxy=True), run=fake_run)
    try:
        ip_a = {"Fly-Client-IP": "203.0.113.5"}
        ip_b = {"Fly-Client-IP": "203.0.113.6"}
        assert srv.request("POST", "/api/runs", {"scenario": "pricing"}, ip_a)[0] == 202
        assert srv.request("POST", "/api/runs", {"scenario": "pricing"}, ip_a)[0] == 202
        status, headers, body = srv.request("POST", "/api/runs", {"scenario": "pricing"}, ip_a)
        assert status == 429 and int(headers["Retry-After"]) > 0
        assert json.loads(body)["error"] == "rate_limited"
        assert srv.request("POST", "/api/runs", {"scenario": "pricing"}, ip_b)[0] == 202
    finally:
        srv.close()


def test_rate_limiter_window_slides():
    limiter = demo_ui.RateLimiter(limit=1, window=600)
    assert limiter.check("a", now=1000) == 0
    assert limiter.check("a", now=1100) > 0
    assert limiter.check("a", now=1601) == 0


def test_runs_queue_one_at_a_time(tmp_runs):
    gate = threading.Event()
    active = []

    def slow_run(*args, listeners, **kwargs):
        active.append(1)
        assert len(active) == 1
        gate.wait(5)
        active.pop()

    srv = Server(demo_ui.Settings(hosted=True, max_concurrent_runs=1), run=slow_run)
    try:
        first = srv.app.submit("pricing", 0, "a")
        second = srv.app.submit("pricing", 0, "b")
        time.sleep(0.2)
        events = [json.loads(t) for _, t in second.log.read_after(0, 0.1)[0]]
        assert [e["position"] for e in events if e["type"] == "run_queued"][-1] == 1
        assert second.state == "queued" and first.state == "running"
        gate.set()
        deadline = time.time() + 5
        while second.state != "done" and time.time() < deadline:
            time.sleep(0.05)
        assert second.state == "done"
    finally:
        gate.set()
        srv.close()


def test_password_gate_and_open_health(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=True, password="s3cret-demo"))
    try:
        assert srv.request("GET", "/health")[0] == 200
        status, headers, _ = srv.request("GET", "/")
        assert status == 401 and "Basic" in headers["WWW-Authenticate"]
        good = "Basic " + base64.b64encode(b"viewer:s3cret-demo").decode()
        bad = "Basic " + base64.b64encode(b"viewer:nope").decode()
        assert srv.request("GET", "/", headers={"Authorization": bad})[0] == 401
        assert srv.request("GET", "/", headers={"Authorization": good})[0] == 200
    finally:
        srv.close()


def test_local_mode_rejects_foreign_host_header(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=False))
    try:
        assert srv.request("GET", "/api/config")[0] == 200
        assert srv.request("GET", "/api/config", headers={"Host": "evil.example"})[0] == 421
    finally:
        srv.close()


def test_config_exposes_key_presence_not_values(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=True))
    try:
        status, _, body = srv.request("GET", "/api/config")
        assert status == 200 and SECRET not in body
        assert json.loads(body)["keys"] == {"advertiser": True, "agency": False, "seat": False}
    finally:
        srv.close()


# ---------------------------------------------------------------------------
# Static assets and offline page
# ---------------------------------------------------------------------------


def test_vendored_three_is_served_as_javascript(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=True))
    try:
        status, headers, body = srv.request("GET", "/static/three.module.min.js")
        assert status == 200
        assert headers["Content-Type"].startswith("text/javascript")
        assert body.startswith("/**\n * @license") and "SPDX-License-Identifier: MIT" in body[:200]
        assert srv.request("GET", "/static/../demo_ui.py")[0] == 404
        assert srv.request("GET", "/static/keys.json")[0] == 404
    finally:
        srv.close()


def test_static_files_need_the_password_too(tmp_runs):
    srv = Server(demo_ui.Settings(hosted=True, password="s3cret-demo"))
    try:
        assert srv.request("GET", "/static/three.module.min.js")[0] == 401
    finally:
        srv.close()


def test_page_references_no_external_urls():
    import re

    root = Path(__file__).resolve().parents[1]
    for name in ("ui.html", "static/voxel_scene.js"):
        path = root / name
        if not path.exists():
            continue
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"(?i)\b(?:https?:)?//[a-z0-9.-]+\.[a-z]{2,}", text), name
        assert not re.search(r"(?i)\b(?:src|href)\s*=\s*[\"']?(?:https?:|//)", text), name


def test_voxel_scene_is_served_and_imports_only_vendored_three(tmp_runs):
    import re

    srv = Server(demo_ui.Settings(hosted=True))
    try:
        status, headers, body = srv.request("GET", "/static/voxel_scene.js")
        assert status == 200
        assert headers["Content-Type"].startswith("text/javascript")
        imports = re.findall(r"""^\s*import\b[^;]*?from\s+["']([^"']+)["']""", body, re.M)
        assert imports == ["./three.module.min.js"]
        page = srv.request("GET", "/")[2]
        assert 'import("/static/voxel_scene.js")' in page
        assert "buyerDemoView" in page and "webglAvailable" in page
    finally:
        srv.close()
