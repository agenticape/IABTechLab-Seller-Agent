"""Live web view of the buyer demo: two agents talking, one step at a time.

Two modes share this server:

* ``buyer_demo.py --ui``: local. Binds 127.0.0.1, opens the browser, no
  rate limit.
* ``buyer_demo.py --serve``: hosted (Fly.io). Same page, no browser, keys
  from env (Fly secrets), per-IP rate limit, a run queue, optional basic
  auth (``DEMO_UI_PASSWORD``) and an opt-in live feed of everyone's runs.

The browser only ever chooses one of the fixed scenarios and a pacing value;
nothing it sends reaches the seller. API keys stay in this process: events
are redacted before they are stored, and scrubbed again as serialized text
before they are streamed.

Stdlib only (``http.server`` + Server-Sent Events), so it runs offline on
Windows with no build step.
"""

from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import os
import queue
import secrets
import sys
import threading
import time
import webbrowser
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent
PAGE = ROOT / "ui.html"
STATIC_DIR = ROOT / "static"
# Only these files are served from /static/ (no directory listing, no traversal).
STATIC_FILES = {
    "three.module.min.js": "text/javascript; charset=utf-8",
    "voxel_scene.js": "text/javascript; charset=utf-8",
    "three.LICENSE": "text/plain; charset=utf-8",
}

PACE_MIN = 0.0
PACE_MAX = 3.0
PACE_DEFAULT = 0.0  # the browser paces playback; the server runs at full speed
MAX_BODY = 1024
SSE_PING_SECONDS = 15
RUN_RETENTION_SECONDS = 15 * 60
MAX_RETAINED_RUNS = 30
LIVE_BACKLOG = 400
MAX_STREAMS = 60

REDACTED = "***REDACTED***"


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise SystemExit(f"{name} must be an integer")


@dataclass
class Settings:
    host: str = "127.0.0.1"
    port: int = 8765
    hosted: bool = False
    open_browser: bool = True
    # 0 disables the per-IP limit.
    rate_limit_runs: int = 0
    rate_limit_window: int = 600
    max_concurrent_runs: int = 1
    max_queued_runs: int = 10
    password: str = ""
    live_feed: bool = True
    trust_proxy: bool = False


def settings_from_env(hosted: bool, host: str, port: int, open_browser: bool) -> Settings:
    """Settings for ``--ui`` (local) or ``--serve`` (hosted), from DEMO_* env."""
    return Settings(
        host=host,
        port=port,
        hosted=hosted,
        open_browser=open_browser,
        rate_limit_runs=max(0, _env_int("DEMO_RATE_LIMIT_RUNS", 3 if hosted else 0)),
        rate_limit_window=max(1, _env_int("DEMO_RATE_LIMIT_WINDOW_SECONDS", 600)),
        max_concurrent_runs=min(2, max(1, _env_int("DEMO_MAX_CONCURRENT_RUNS", 1))),
        max_queued_runs=max(1, _env_int("DEMO_MAX_QUEUED_RUNS", 10)),
        password=os.environ.get("DEMO_UI_PASSWORD", ""),
        live_feed=_env_bool("DEMO_LIVE_FEED", True),
        trust_proxy=_env_bool("DEMO_TRUST_PROXY", hosted),
    )


# ---------------------------------------------------------------------------
# Redaction for anything that leaves the process
# ---------------------------------------------------------------------------


class Scrubber:
    """Last line of defence: no key value survives serialization."""

    def __init__(self, secret_values: list[str]) -> None:
        self.secrets = [s for s in secret_values if s]

    def dumps(self, event: dict) -> str:
        text = json.dumps(event, default=str)
        for value in self.secrets:
            text = text.replace(value, REDACTED)
        return text


def browser_safe(event: dict) -> dict:
    """Drop local-only detail (file paths, key file location) from an event."""
    out = dict(event)
    kind = out.get("type")
    if kind == "run_start":
        source = str(out.get("key_source") or "none")
        out["key_source"] = "KEYS_FILE" if source.startswith("KEYS_FILE") else source
    elif kind == "run_end":
        out["md_path"] = "runs/" + Path(str(out.get("md_path", ""))).name
        out["json_path"] = "runs/" + Path(str(out.get("json_path", ""))).name
    elif kind == "call_start":
        headers = out.get("request_headers") or {}
        out["request_headers"] = {k: REDACTED for k in headers}
    return out


# ---------------------------------------------------------------------------
# Runs, live feed, rate limit
# ---------------------------------------------------------------------------


class EventLog:
    """Append-only serialized events with blocking reads (one per stream)."""

    def __init__(self, maxlen: Optional[int] = None) -> None:
        self._events: collections.deque[tuple[int, str]] = collections.deque(maxlen=maxlen)
        self._next = 1
        self._cond = threading.Condition()
        self.closed = False

    def append(self, text: str) -> None:
        with self._cond:
            self._events.append((self._next, text))
            self._next += 1
            self._cond.notify_all()

    def last_id(self) -> int:
        with self._cond:
            return self._next - 1

    def close(self) -> None:
        with self._cond:
            self.closed = True
            self._cond.notify_all()

    def read_after(self, last_id: int, timeout: float) -> tuple[list[tuple[int, str]], bool]:
        """Events with id > ``last_id``; waits up to ``timeout`` if none."""
        with self._cond:
            items = [e for e in self._events if e[0] > last_id]
            if not items and not self.closed:
                self._cond.wait(timeout)
                items = [e for e in self._events if e[0] > last_id]
            return items, self.closed


@dataclass
class Run:
    run_id: str
    label: str
    scenario: str
    wanted: list[str]
    client_key: str
    pace: float = PACE_DEFAULT
    state: str = "queued"
    created: float = field(default_factory=time.time)
    finished: Optional[float] = None
    log: EventLog = field(default_factory=EventLog)


class RateLimiter:
    def __init__(self, limit: int, window: int) -> None:
        self.limit = limit
        self.window = window
        self._hits: dict[str, collections.deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, client: str, now: Optional[float] = None) -> int:
        """Record a run for ``client``; 0 if allowed, else seconds to wait."""
        if self.limit <= 0:
            return 0
        now = time.time() if now is None else now
        with self._lock:
            hits = self._hits.setdefault(client, collections.deque())
            while hits and hits[0] <= now - self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return max(1, int(hits[0] + self.window - now) + 1)
            hits.append(now)
            return 0


class RunError(Exception):
    def __init__(self, status: int, error: str, message: str, **extra: Any) -> None:
        super().__init__(message)
        self.status = status
        self.body = {"error": error, "message": message, **extra}


class DemoApp:
    """Run queue + workers. Each run streams to its own viewer."""

    def __init__(
        self,
        settings: Settings,
        base_url: str,
        keys: Any,
        timeout: float,
        run: Callable[..., Any],
        scenario_help: dict[str, str],
        scenario_order: tuple[str, ...],
        printer: Optional[Callable[[dict], None]] = None,
        transport: Any = None,
    ) -> None:
        self.settings = settings
        self.base_url = base_url
        self.keys = keys
        self.timeout = timeout
        self.run_fn = run
        self.scenario_help = scenario_help
        self.scenario_order = scenario_order
        self.choices = list(scenario_order) + ["all"]
        self.printer = printer
        self.transport = transport
        self.scrubber = Scrubber(keys.secrets())
        self.limiter = RateLimiter(settings.rate_limit_runs, settings.rate_limit_window)
        self.live = EventLog(maxlen=LIVE_BACKLOG)
        self.runs: dict[str, Run] = {}
        self.waiting: list[Run] = []
        self.queue: queue.Queue[Run] = queue.Queue()
        self.lock = threading.Lock()
        self.counter = 0
        self.streams = threading.BoundedSemaphore(MAX_STREAMS)
        self.workers = [
            threading.Thread(target=self._worker, name=f"demo-run-{i}", daemon=True)
            for i in range(settings.max_concurrent_runs)
        ]
        for worker in self.workers:
            worker.start()

    # -- public API used by the HTTP handler --------------------------------

    def config(self) -> dict:
        return {
            "mode": "hosted" if self.settings.hosted else "local",
            "seller_url": self.base_url,
            "scenarios": [
                {"name": name, "help": self.scenario_help.get(name, "Every scenario in order")}
                for name in self.choices
            ],
            "keys": {tier: self.keys.has(tier) for tier in ("advertiser", "agency", "seat")},
            "pace": {"min": PACE_MIN, "max": PACE_MAX, "default": PACE_DEFAULT},
            "live_feed": self.settings.live_feed,
            "rate_limit": {
                "runs": self.settings.rate_limit_runs,
                "window_seconds": self.settings.rate_limit_window,
            },
            "max_concurrent_runs": self.settings.max_concurrent_runs,
        }

    def submit(self, scenario: Any, pace: Any, client_key: str) -> Run:
        if not isinstance(scenario, str) or scenario not in self.choices:
            raise RunError(400, "unknown_scenario", "Pick one of the listed scenarios.")
        pace_value = _clamp_pace(pace)
        with self.lock:
            self._expire_old_runs()
            if len(self.waiting) >= self.settings.max_queued_runs:
                raise RunError(503, "queue_full", "Too many runs are waiting. Try again shortly.")
        retry = self.limiter.check(client_key)
        if retry:
            raise RunError(
                429,
                "rate_limited",
                f"Run limit reached ({self.settings.rate_limit_runs} per "
                f"{self.settings.rate_limit_window // 60} min). Try again later.",
                retry_after=retry,
            )
        wanted = list(self.scenario_order) if scenario == "all" else [scenario]
        with self.lock:
            self.counter += 1
            run = Run(
                run_id=secrets.token_urlsafe(18),
                label=f"#{self.counter}",
                scenario=scenario,
                wanted=wanted,
                client_key=client_key,
                pace=pace_value,
            )
            self.runs[run.run_id] = run
            self.waiting.append(run)
            position = len(self.waiting)
        self._publish(run, {"type": "run_queued", "position": position, "scenario": scenario})
        self._log(
            f"run {run.label} queued: {scenario} (position {position}, "
            f"client {client_fingerprint(client_key)})"
        )
        self.queue.put(run)
        return run

    def get(self, run_id: str) -> Optional[Run]:
        with self.lock:
            return self.runs.get(run_id)

    def set_pace(self, run_id: str, pace: Any) -> float:
        run = self.get(run_id)
        if run is None:
            raise RunError(404, "run_not_found", "No such run.")
        run.pace = _clamp_pace(pace)
        return run.pace

    # -- internals ------------------------------------------------------------

    def _log(self, message: str) -> None:
        if self.settings.hosted or self.printer is None:
            print(f"[buyer-demo-ui] {message}", flush=True)

    def _publish(self, run: Run, event: dict) -> None:
        event = {**browser_safe(event), "run_label": run.label}
        text = self.scrubber.dumps(event)
        run.log.append(text)
        if self.settings.live_feed:
            self.live.append(text)

    def _expire_old_runs(self) -> None:
        now = time.time()
        finished = sorted(
            (r for r in self.runs.values() if r.finished is not None), key=lambda r: r.finished
        )
        for run in finished:
            too_many = len(self.runs) > MAX_RETAINED_RUNS
            if too_many or now - run.finished > RUN_RETENTION_SECONDS:
                self.runs.pop(run.run_id, None)

    def _worker(self) -> None:
        while True:
            run = self.queue.get()
            with self.lock:
                if run in self.waiting:
                    self.waiting.remove(run)
                still_waiting = list(self.waiting)
            for position, other in enumerate(still_waiting, start=1):
                self._publish(other, {"type": "run_queued", "position": position})
            try:
                self._execute(run)
            finally:
                self.queue.task_done()

    def _execute(self, run: Run) -> None:
        run.state = "running"
        self._log(f"run {run.label} started: {run.scenario}")
        first = {"done": False}

        def pacer() -> None:
            if first["done"]:
                time.sleep(run.pace)
            first["done"] = True

        listeners = [lambda event: self._publish(run, event)]
        if self.printer is not None and not self.settings.hosted:
            listeners.append(self.printer)
        try:
            self.run_fn(
                self.base_url,
                run.wanted,
                self.keys,
                self.timeout,
                listeners=listeners,
                pacer=pacer,
                transport=self.transport,
                keep_going=True,
            )
            run.state = "done"
        except (Exception, SystemExit) as exc:
            run.state = "error"
            self._publish(
                run,
                {"type": "run_error", "message": f"Run stopped: {type(exc).__name__}"},
            )
            self._log(f"run {run.label} failed: {type(exc).__name__}")
        finally:
            run.finished = time.time()
            run.log.close()
            self._log(f"run {run.label} {run.state}")


def _clamp_pace(pace: Any) -> float:
    try:
        value = float(pace)
    except (TypeError, ValueError):
        value = PACE_DEFAULT
    if value != value:  # NaN
        value = PACE_DEFAULT
    return round(min(PACE_MAX, max(PACE_MIN, value)), 2)


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; script-src 'self' 'unsafe-inline'; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; "
        "base-uri 'none'; form-action 'none'; frame-ancestors 'none'"
    ),
}


def make_handler(app: DemoApp) -> type[BaseHTTPRequestHandler]:
    settings = app.settings
    local_hosts = {f"127.0.0.1:{settings.port}", f"localhost:{settings.port}"}

    class Handler(BaseHTTPRequestHandler):
        server_version = "agenticape-buyer-demo"
        sys_version = ""
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
            return

        # -- helpers --------------------------------------------------------

        def _send(
            self,
            status: int,
            body: bytes,
            content_type: str,
            headers: Optional[dict] = None,
        ) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            merged = {"Cache-Control": "no-store", **SECURITY_HEADERS, **(headers or {})}
            for key, value in merged.items():
                self.send_header(key, value)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, status: int, payload: dict, headers: Optional[dict] = None) -> None:
            body = app.scrubber.dumps(payload).encode("utf-8")
            self._send(status, body, "application/json; charset=utf-8", headers)

        def _client_key(self) -> str:
            ip = self.client_address[0]
            if settings.trust_proxy:
                forwarded = self.headers.get("Fly-Client-IP") or (
                    self.headers.get("X-Forwarded-For", "").split(",")[0].strip()
                )
                ip = forwarded or ip
            return ip

        def _host_ok(self) -> bool:
            if settings.hosted:
                return True
            # Local mode: refuse other Host names (DNS rebinding).
            return self.headers.get("Host", "") in local_hosts

        def _auth_ok(self) -> bool:
            if not settings.password:
                return True
            header = self.headers.get("Authorization", "")
            if not header.startswith("Basic "):
                return False
            try:
                decoded = base64.b64decode(header[6:], validate=True).decode("utf-8")
            except (ValueError, UnicodeDecodeError):
                return False
            _, _, password = decoded.partition(":")
            return hmac.compare_digest(password.encode(), settings.password.encode())

        def _same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if origin is None:
                return True
            return urlsplit(origin).netloc == self.headers.get("Host", "")

        def _guard(self, write: bool = False) -> bool:
            if not self._host_ok():
                self._json(421, {"error": "misdirected_request"})
                return False
            if not self._auth_ok():
                self._json(
                    401,
                    {"error": "auth_required"},
                    {"WWW-Authenticate": 'Basic realm="buyer-demo", charset="UTF-8"'},
                )
                return False
            if write:
                if not self._same_origin():
                    self._json(403, {"error": "cross_origin"})
                    return False
                ctype = self.headers.get("Content-Type", "").split(";")[0].strip()
                if ctype != "application/json" or self.headers.get("X-Demo-Client") != "1":
                    self._json(415, {"error": "json_from_the_demo_page_only"})
                    return False
            return True

        def _read_json(self) -> dict:
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length <= 0 or length > MAX_BODY:
                raise RunError(400, "bad_body", "Expected a small JSON body.")
            try:
                data = json.loads(self.rfile.read(length).decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                raise RunError(400, "bad_body", "Expected a small JSON body.")
            if not isinstance(data, dict):
                raise RunError(400, "bad_body", "Expected a JSON object.")
            return data

        # -- routes -----------------------------------------------------------

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if path == "/health":
                self._json(200, {"status": "ok", "mode": app.config()["mode"]})
                return
            if not self._guard():
                return
            if path == "/":
                self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
            elif path.startswith("/static/"):
                name = path[len("/static/") :]
                ctype = STATIC_FILES.get(name)
                if ctype is None or not (STATIC_DIR / name).is_file():
                    self._json(404, {"error": "not_found"})
                    return
                self._send(
                    200,
                    (STATIC_DIR / name).read_bytes(),
                    ctype,
                    {"Cache-Control": "public, max-age=3600"},
                )
            elif path == "/api/config":
                self._json(200, app.config())
            elif path == "/api/live/events":
                if not settings.live_feed:
                    self._json(404, {"error": "live_feed_disabled"})
                    return
                self._stream(app.live, from_now=True)
            elif path.startswith("/api/runs/") and path.endswith("/events"):
                run = app.get(path[len("/api/runs/") : -len("/events")])
                if run is None:
                    self._json(404, {"error": "run_not_found"})
                    return
                self._stream(run.log)
            else:
                self._json(404, {"error": "not_found"})

        def do_POST(self) -> None:  # noqa: N802
            path = urlsplit(self.path).path
            if not self._guard(write=True):
                return
            try:
                if path == "/api/runs":
                    data = self._read_json()
                    run = app.submit(data.get("scenario"), data.get("pace"), self._client_key())
                    self._json(
                        202,
                        {
                            "run_id": run.run_id,
                            "label": run.label,
                            "events_url": f"/api/runs/{run.run_id}/events",
                        },
                    )
                elif path.startswith("/api/runs/") and path.endswith("/pace"):
                    data = self._read_json()
                    run_id = path[len("/api/runs/") : -len("/pace")]
                    self._json(200, {"pace": app.set_pace(run_id, data.get("pace"))})
                else:
                    self._json(404, {"error": "not_found"})
            except RunError as exc:
                headers = {}
                if "retry_after" in exc.body:
                    headers["Retry-After"] = str(exc.body["retry_after"])
                self._json(exc.status, exc.body, headers)

        def _stream(self, log: EventLog, from_now: bool = False) -> None:
            if not app.streams.acquire(blocking=False):
                self._json(503, {"error": "too_many_viewers"})
                return
            try:
                try:
                    last = int(self.headers.get("Last-Event-ID") or 0)
                except ValueError:
                    last = 0
                if from_now and not last:
                    last = log.last_id()
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Accel-Buffering", "no")
                for key, value in SECURITY_HEADERS.items():
                    self.send_header(key, value)
                self.send_header("Connection", "close")
                self.end_headers()
                self.close_connection = True
                self.wfile.write(b"retry: 3000\n\n")
                self.wfile.flush()
                while True:
                    items, closed = log.read_after(last, SSE_PING_SECONDS)
                    for event_id, text in items:
                        self.wfile.write(f"id: {event_id}\ndata: {text}\n\n".encode("utf-8"))
                        last = event_id
                    if not items:
                        if closed:
                            self.wfile.write(b'event: end\ndata: {"type": "stream_end"}\n\n')
                            self.wfile.flush()
                            return
                        self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                return
            finally:
                app.streams.release()

    return Handler


def build_server(app: DemoApp) -> ThreadingHTTPServer:
    server = ThreadingHTTPServer((app.settings.host, app.settings.port), make_handler(app))
    server.daemon_threads = True
    return server


def serve(
    settings: Settings,
    base_url: str,
    keys: Any,
    timeout: float,
    run: Callable[..., Any],
    scenario_help: dict[str, str],
    scenario_order: tuple[str, ...],
    printer: Optional[Callable[[dict], None]] = None,
) -> int:
    """Serve the page until Ctrl+C. Returns a process exit code."""
    if settings.hosted and not keys.has("advertiser"):
        print(
            "[buyer-demo-ui] warning: no advertiser key (SELLER_API_KEY); keyed scenarios "
            "will be skipped.",
            file=sys.stderr,
            flush=True,
        )
    app = DemoApp(settings, base_url, keys, timeout, run, scenario_help, scenario_order, printer)
    try:
        server = build_server(app)
    except OSError as exc:
        raise SystemExit(f"Cannot listen on {settings.host}:{settings.port}: {exc}")
    shown_host = "127.0.0.1" if settings.host in ("0.0.0.0", "") else settings.host
    url = f"http://{shown_host}:{settings.port}/"
    mode = "hosted" if settings.hosted else "local"
    print(f"Buyer demo UI ({mode}) for seller {base_url}", flush=True)
    print(f"Open {url}  (Ctrl+C to stop)", flush=True)
    if settings.password:
        print("Basic auth is on (DEMO_UI_PASSWORD).", flush=True)
    if settings.open_browser:
        threading.Timer(0.5, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\nStopping.", flush=True)
    finally:
        server.server_close()
    return 0


def client_fingerprint(ip: str) -> str:
    """Short, non-reversible label for logs."""
    return hashlib.sha256(ip.encode()).hexdigest()[:10]
