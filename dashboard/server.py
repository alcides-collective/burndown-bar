#!/usr/bin/env python3
"""Burndown Bar dashboard — a read-only web view of the plugin's caches.

Stdlib only. It imports the *already-tested* analytics from the plugin file
(``burndown-bar.5m.py``) rather than re-deriving any math, reads the three
cache files the plugin maintains, and serves:

  GET /            -> the built SPA (dist/index.html)
  GET /assets/*    -> SPA static assets
  GET /api/data    -> one JSON blob with every metric/series the UI needs
  GET /api/health  -> {"ok": true}

The plugin refreshes the caches every ~5 minutes; this server only ever reads
them, recomputing a fresh ``now`` per request (never the module's import-time
NOW), so the numbers track the wall clock. A small server-owned hourly archive
(see archive.py) extends history beyond the plugin's 21-day retention.
"""
import datetime as dt
import importlib.util
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DIST = os.path.join(HERE, "web", "dist")

PORT = int(os.environ.get("BURNDOWN_DASHBOARD_PORT", "3838"))

# The plugin writes its caches into SwiftBar's per-plugin cache dir. Default to
# that real location; override with BURNDOWN_CACHE_DIR (e.g. for testing).
DEFAULT_CACHE_DIR = os.path.expanduser(
    "~/Library/Caches/com.ameba.SwiftBar/Plugins/claude-burn.5m.py")
CACHE_DIR = os.environ.get("BURNDOWN_CACHE_DIR", DEFAULT_CACHE_DIR)

CLAUDE_CACHE = os.path.join(CACHE_DIR, "claude-burn-cache.json")
OR_CACHE = os.path.join(CACHE_DIR, "openrouter-burn-cache.json")
HISTORY = os.path.join(CACHE_DIR, "burndown-history.json")

PLUGIN_FILE = os.environ.get(
    "BURNDOWN_PLUGIN_FILE", os.path.join(REPO, "burndown-bar.5m.py"))


def _load_plugin(path):
    spec = importlib.util.spec_from_file_location("burndown_plugin", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bb = _load_plugin(PLUGIN_FILE)

# archive.py lives beside this file; import it the ordinary way.
sys.path.insert(0, HERE)
import archive  # noqa: E402
import incidents  # noqa: E402
import notify  # noqa: E402
import projections  # noqa: E402
import tokens  # noqa: E402

# Notification dedup state survives restarts so a reboot doesn't re-fire alerts.
NOTIFY_STATE_FILE = os.environ.get(
    "BURNDOWN_NOTIFY_STATE",
    os.path.expanduser("~/.local/share/burndown-bar/notify-state.json"))
TICK_SECONDS = float(os.environ.get("BURNDOWN_TICK_SECONDS", "45"))
TOKEN_SCAN_SECONDS = float(os.environ.get("BURNDOWN_TOKEN_SCAN_SECONDS", "180"))


# ── helpers ────────────────────────────────────────────────────────────────

def _read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _iso(t):
    return t.isoformat() if isinstance(t, dt.datetime) else t


def _downsample(points, target=600):
    """Evenly thin a list of points to at most ``target`` (keeps first/last)."""
    n = len(points)
    if n <= target:
        return points
    step = n / target
    out = [points[int(i * step)] for i in range(target)]
    out[-1] = points[-1]
    return out


# ── analytics: drive the plugin's pure functions with a fresh `now` ─────────

def _window_payload(a):
    """JSON-ify one analyze() result."""
    if a is None:
        return None
    return {
        "used": a["used"], "rate": a["rate"], "pace": a["pace"],
        "projected": a["projected"], "elapsed_h": a["elapsed_h"],
        "left_h": a["left_h"], "elapsed_frac": a["elapsed_frac"],
        "reset": _iso(a["reset"]), "start": _iso(a["start"]),
        "dry_at": _iso(a["dry_at"]), "early": a["early"],
        "exhausted": a["exhausted"], "will_run_out": a["will_run_out"],
    }


def _smart_path(used, now_local, reset_local, store, fallback):
    """Per-hour points of the baseline-aware projection (for charting).

    Mirrors plugin.smart_projection's walk so the drawn curve matches the
    headline number; uses the same SMART_MIN_SUPPORT gate and constants.
    """
    if reset_local <= now_local:
        return []
    pts = [[_iso(now_local.astimezone(dt.timezone.utc)), round(used, 2)]]
    proj, cursor = float(used), now_local
    min_support = getattr(bb, "SMART_MIN_SUPPORT", 3)
    while cursor < reset_local:
        boundary = (cursor + dt.timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
        nxt = min(reset_local, boundary)
        step_h = (nxt - cursor).total_seconds() / 3600.0
        if step_h <= 0:
            break
        cell = bb.baseline_query(store, cursor.weekday(), cursor.hour)
        rate = None
        if cell["mean"] is not None and cell.get("support", 0) >= min_support:
            rate = cell["mean"]
        elif fallback is not None:
            rate = fallback
        elif cell["mean"] is not None:
            rate = cell["mean"]
        if rate is not None:
            proj += max(0.0, rate) * step_h
        cursor = nxt
        pts.append([_iso(nxt.astimezone(dt.timezone.utc)), round(proj, 2)])
    return pts


def compute_claude(data, history, now):
    local_now = now.astimezone()
    baselines = history.get("baselines", {})
    claude_hist = history.get("claude", {})

    windows = {}
    for key, label, wh, _ in bb.WINDOWS:
        a = bb.analyze(data.get(key), wh, now)
        windows[key] = {"label": label, "window_h": wh, "data": _window_payload(a),
                        "_a": a}

    weekly = windows["seven_day"]["_a"]
    wk_store = baselines.get("seven_day", {})
    wk_snaps = claude_hist.get("seven_day", [])

    # trend (vs learned weekday/hour baseline) for the weekly window
    trend = bb.trend_for(
        lambda h, s=wk_snaps: bb.windowed_rate(s, now, h), wk_store, local_now)
    cls = trend["cls"]

    # smart projection (number) + drawable paths for naive and smart
    smart = None
    smart_path = naive_path = []
    if weekly is not None and not weekly["exhausted"]:
        fallback = bb.windowed_rate(wk_snaps, now, bb.TREND_DAY_H)
        if fallback is None:
            fallback = weekly["rate"]
        reset_local = weekly["reset"].astimezone()
        smart = bb.smart_projection(
            weekly["used"], local_now, reset_local, wk_store, fallback)
        smart_path = _smart_path(
            weekly["used"], local_now, reset_local, wk_store, fallback)
        naive_path = [
            [_iso(now), round(weekly["used"], 2)],
            [_iso(weekly["reset"]), round(weekly["projected"], 2)],
        ]
    if smart is not None:
        smart = {"projected": smart["projected"],
                 "used_baseline": smart["used_baseline"],
                 "dry_at": _iso(smart["dry_at"])}

    # current-window burn curve: snapshots whose reset matches the live window
    curve = []
    if weekly is not None:
        rkey = weekly["reset"]
        for s in wk_snaps:
            if len(s) != 3:
                continue
            try:
                if bb.same_reset(s[2], rkey.isoformat()):
                    curve.append([s[0], float(s[1])])
            except Exception:
                pass
    curve = _downsample(sorted(curve))

    # weekday × hour grid for the heatmap — RAW per-cell mean burn rate (%/h),
    # not the shrunk baseline, so only hours you've actually worked light up
    # (the shrunk mean smears every cell toward the pool and reads as uniform).
    cells = wk_store.get("cells", {})
    grid, support = [], []
    for wd in range(7):
        row, srow = [], []
        for hr in range(24):
            c = cells.get(f"{wd}-{hr}")
            if c and c[0] > 0:
                row.append(round(c[1] / c[0], 3)); srow.append(int(c[0]))
            else:
                row.append(None); srow.append(0)
        grid.append(row)
        support.append(srow)

    # plain-English summary (reuse the plugin's templates)
    weekly_status = bb.claude_summary_status(weekly, cls, smart) if weekly else "building"
    runway_h = None
    if smart and smart.get("dry_at"):
        runway_h = (dt.datetime.fromisoformat(smart["dry_at"]) - now).total_seconds() / 3600.0
    elif weekly is None:
        runway_h = None
    claude_sum = {
        "status": weekly_status, "dir": cls["dir"] if weekly else 0,
        "weekday": local_now.strftime("%A"),
        "pace": weekly["pace"] if weekly else 0.0,
        "used": weekly["used"] if weekly else 0.0,
        "runway_h": runway_h,
        "reset_in_h": weekly["left_h"] if weekly else None,
    }

    # limit events Anthropic made (early resets / quota bumps), recent first
    def _age_h(e):
        try:
            return (now - dt.datetime.fromisoformat(e["at"])).total_seconds() / 3600.0
        except Exception:
            return 1e9
    events = [
        {"label": e.get("label"), "kind": e.get("kind"), "at": e.get("at"),
         "age_h": round(_age_h(e), 1)}
        for e in reversed(history.get("limit_events", [])) if _age_h(e) <= 30 * 24.0
    ][:12]

    for w in windows.values():
        w.pop("_a", None)

    return {
        "ok": data is not None and weekly is not None,
        "windows": windows,
        "weekly_status": weekly_status,
        "trend": {"cls": cls, "rows": trend["rows"], "recent": trend["recent"]},
        "smart": smart, "smart_path": smart_path, "naive_path": naive_path,
        "curve": curve,
        "baseline_grid": grid, "baseline_support": support,
        "summary_claude": claude_sum,
        "limit_events": events,
        "weekday": local_now.strftime("%A"),
    }


def compute_openrouter(history, now):
    cache = _read_json(OR_CACHE)
    data = cache.get("data")
    samples = cache.get("samples") or []
    if not data:
        return {"configured": os.path.exists(OR_CACHE), "ok": False}
    a = bb.analyze_openrouter(data, samples, now)
    if a is None:
        return {"configured": True, "ok": False}
    # learned spend trend (for the arrow + the summary sentence)
    or_store = history.get("baselines", {}).get("openrouter", {})
    or_trend = bb.trend_for(
        lambda h: bb.spend_rate_over(samples, now, h), or_store, now.astimezone())
    or_dir = or_trend["cls"]["dir"]
    or_building = or_trend["cls"]["label"] == "building"
    surge = None
    if not a["depleted"]:
        s = bb.detect_spend_surge(samples, a["balance"], now)
        if s is not None:
            surge = {"rate": s["rate"], "baseline": s["baseline"],
                     "factor": s["factor"], "runway_h": s["runway_h"]}
    total = a["total"]
    bal_series = _downsample(
        [[ts, round(total - float(u), 4)] for ts, u in sorted(samples) if len([ts, u]) == 2])
    # flat drawdown projection to zero at the current rate
    proj = []
    if a["rate"] and a["rate"] > 0 and a["balance"] > 0:
        dry = now + dt.timedelta(hours=a["balance"] / a["rate"])
        proj = [[_iso(now), round(a["balance"], 4)], [_iso(dry), 0.0]]
    return {
        "configured": True, "ok": True,
        "balance": a["balance"], "total": total, "used": a["used"],
        "rate": a["rate"], "per_day": a["per_day"], "dry_in_h": a["dry_in_h"],
        "depleted": a["depleted"], "surge": surge,
        "dir": or_dir, "building": or_building,
        "balance_series": bal_series, "projection": proj,
    }


def _stale_info(now):
    """Has the plugin stopped refreshing its cache? (age of claude fetched_at)"""
    ts = cache_ts_age = None
    try:
        fa = _read_json(CLAUDE_CACHE).get("fetched_at")
        ts = dt.datetime.fromisoformat(fa) if fa else None
    except Exception:
        ts = None
    if ts is None:
        return False, None
    age_h = (now - ts).total_seconds() / 3600.0
    return age_h > notify.STALE_AFTER_H, round(age_h, 3)


class Engine:
    """Background worker: ticks on a timer so alerts fire without page loads.

    Each tick recomputes the payload, scans transcripts (throttled), tracks
    incidents, and pushes any due ntfy alerts. The HTTP layer just serves the
    cached payload from the most recent tick.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._latest = None
        self._notif = self._load_json(NOTIFY_STATE_FILE)
        self._tokens_state = None
        self._tokens_summary = {"daily": [], "by_model": [], "top_projects": [], "recent_sessions": []}
        self._n = 0

    @staticmethod
    def _load_json(path):
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return {}

    def _save_notif(self):
        try:
            os.makedirs(os.path.dirname(NOTIFY_STATE_FILE), exist_ok=True)
            with open(NOTIFY_STATE_FILE, "w") as f:
                json.dump(self._notif, f)
        except Exception:
            pass

    def _compute(self):
        now = dt.datetime.now(dt.timezone.utc)
        data = _read_json(CLAUDE_CACHE).get("data")
        history = _read_json(HISTORY)
        claude = compute_claude(data or {}, history, now)
        openrouter = compute_openrouter(history, now)
        stale, stale_age = _stale_info(now)

        or_sum = None
        if openrouter.get("ok"):
            or_sum = {
                "dir": openrouter.get("dir", 0), "balance": openrouter.get("balance", 0.0),
                "runway_h": openrouter.get("dry_in_h"), "depleted": openrouter.get("depleted", False),
                "idle": openrouter.get("rate") is not None and openrouter["rate"] <= 0,
                "building": openrouter.get("building", False),
            }
        summary = bb.summary_lines(claude["summary_claude"], or_sum, now.astimezone().hour)
        arch = archive.update(now, claude, openrouter, history, CACHE_DIR)

        # token history is scanned off the critical path (see _scan_tokens); the
        # main tick just reads the latest summary so it never blocks on I/O.
        with self._lock:
            tok = self._tokens_summary

        proj = projections.compute(tok.get("daily", []), arch, now)

        # incident lifecycle tracking
        wk = (claude.get("windows", {}).get("seven_day", {}) or {}).get("data") or {}
        obs = {
            "surge": openrouter.get("surge"),
            "or_balance": openrouter.get("balance"),
            "exhausted": bool(wk.get("used", 0) >= 100) or claude.get("weekly_status") == "exhausted",
            "stale": stale, "stale_age_h": stale_age,
            "limit_events": claude.get("limit_events", []),
        }
        inc_state = incidents.update(incidents.load(), obs, now)
        incidents.save(inc_state)

        payload = {
            "generated_at": _iso(now),
            "cache_dir": CACHE_DIR,
            "ntfy_topic": notify.NTFY_TOPIC,
            "stale": stale, "stale_age_h": stale_age,
            "summary": summary,
            "claude": claude,
            "openrouter": openrouter,
            "archive": arch,
            "tokens": tok,
            "projections": proj,
            "incidents": incidents.for_api(inc_state, now),
        }

        # push due alerts (the whole point of an always-on server)
        try:
            self._notif = notify.fire(payload, self._notif, bb, now)
            self._save_notif()
        except Exception:
            import traceback; traceback.print_exc()
        return payload

    def tick(self):
        p = self._compute()
        with self._lock:
            self._latest = p
            self._n += 1
        return p

    def scan_tokens(self):
        """Heavy transcript scan, run on its own thread (off the request path)."""
        now = dt.datetime.now(dt.timezone.utc)
        try:
            st = tokens.scan(now=now)
            summ = tokens.summarize(st, now)
            with self._lock:
                self._tokens_summary = summ
        except Exception:
            import traceback; traceback.print_exc()

    def latest(self):
        with self._lock:
            p = self._latest
        # Never block the request on a compute; the ticker fills this in ~1s.
        return p if p is not None else {"warming": True, "generated_at": _iso(dt.datetime.now(dt.timezone.utc))}


ENGINE = Engine()


# ── HTTP ────────────────────────────────────────────────────────────────────

_CT = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
       ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
       ".ico": "image/x-icon", ".woff2": "font/woff2", ".map": "application/json"}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # quiet by default
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            try:
                self.wfile.write(body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # client navigated away mid-response; harmless

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path == "/api/health":
            return self._send(200, json.dumps({"ok": True}))
        if path == "/api/data":
            try:
                return self._send(200, json.dumps(ENGINE.latest()))
            except Exception as e:
                import traceback
                traceback.print_exc()
                return self._send(500, json.dumps({"error": str(e)}))
        return self._serve_static(path)

    do_HEAD = do_GET

    def _serve_static(self, path):
        rel = "index.html" if path in ("/", "") else path.lstrip("/")
        full = os.path.normpath(os.path.join(DIST, rel))
        if not full.startswith(DIST):
            return self._send(403, "forbidden", "text/plain")
        if not os.path.isfile(full):
            # SPA fallback / build-missing hint
            if os.path.isfile(os.path.join(DIST, "index.html")):
                full = os.path.join(DIST, "index.html")
            else:
                return self._send(
                    200, _BUILD_HINT, "text/html; charset=utf-8")
        ext = os.path.splitext(full)[1]
        with open(full, "rb") as f:
            self._send(200, f.read(), _CT.get(ext, "application/octet-stream"))


_BUILD_HINT = """<!doctype html><meta charset=utf-8>
<title>Burndown dashboard</title>
<body style="font:16px/1.6 -apple-system,sans-serif;max-width:40rem;margin:4rem auto;padding:0 1rem;color:#222">
<h1>Burndown dashboard</h1>
<p>The backend is running, but the frontend hasn't been built yet.</p>
<pre style="background:#f4f4f4;padding:1rem;border-radius:8px">cd dashboard/web && npm install && npm run build</pre>
<p>The API is live at <a href="/api/data">/api/data</a>.</p>
</body>"""


def _ticker():
    import time
    while True:
        try:
            ENGINE.tick()
        except Exception:
            import traceback; traceback.print_exc()
        time.sleep(TICK_SECONDS)


def _token_loop():
    import time
    while True:
        ENGINE.scan_tokens()
        try:
            ENGINE.tick()  # refresh the cached payload with the fresh token data
        except Exception:
            import traceback; traceback.print_exc()
        time.sleep(TOKEN_SCAN_SECONDS)


def main():
    threading.Thread(target=_ticker, daemon=True).start()
    threading.Thread(target=_token_loop, daemon=True).start()
    httpd = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Burndown dashboard on http://localhost:{PORT}  (cache: {CACHE_DIR})",
          flush=True)
    print(f"  ntfy alerts → topic '{notify.NTFY_TOPIC}' on {notify.NTFY_SERVER}", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        httpd.shutdown()


if __name__ == "__main__":
    main()
