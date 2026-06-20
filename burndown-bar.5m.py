#!/usr/bin/env python3
# <xbar.title>Burndown Bar</xbar.title>
# <xbar.version>v1.3.0</xbar.version>
# <xbar.author>Jakub Dudek</xbar.author>
# <xbar.author.github>alcides-collective</xbar.author.github>
# <xbar.desc>Not how much Claude quota you've used — how fast you're burning it. Pace vs sustainable, projected dry time vs reset.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.abouturl>https://alcides-collective.github.io/burndown-bar/</xbar.abouturl>
# <swiftbar.hideRunInTerminal>true</swiftbar.hideRunInTerminal>
# <swiftbar.refreshOnOpen>true</swiftbar.refreshOnOpen>

import datetime as dt
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
KEYCHAIN_SERVICE = "Claude Code-credentials"
CREDS_FILE = os.path.expanduser("~/.claude/.credentials.json")
CACHE_PATH = os.path.join(
    os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH", "/tmp"), "claude-burn-cache.json"
)

# OpenRouter is optional: only shown when a key is configured. Read from
# $OPENROUTER_API_KEY first, then a one-line key file.
OPENROUTER_CREDITS_URL = "https://openrouter.ai/api/v1/credits"
OPENROUTER_KEY_FILE = os.path.expanduser("~/.config/burndown-bar/openrouter-key")
OR_CACHE_PATH = os.path.join(
    os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH", "/tmp"), "openrouter-burn-cache.json"
)
OR_SAMPLE_MAX = 300          # cap on stored spend snapshots
OR_SAMPLE_WINDOW_H = 24.0    # compute the current pace over this trailing window
OR_SAMPLE_MIN_SPAN_H = 0.25  # need this much history before projecting a dry date

# Trend history: utilization snapshots + learned weekday/hour baselines,
# notification state. Kept in a single file alongside the other caches.
HISTORY_PATH = os.path.join(
    os.environ.get("SWIFTBAR_PLUGIN_CACHE_PATH", "/tmp"), "burndown-history.json"
)
HISTORY_RETAIN_DAYS = 21     # enough to learn a weekly rhythm + week-over-week
HISTORY_MAX = 6000           # hard cap on stored snapshots per series
TREND_RECENT_H = 1.0         # "right now" burn window for the trend arrow
TREND_DAY_H = 24.0
TREND_WEEK_H = 168.0
NOTIFY_COOLDOWN_H = 6.0      # same event won't re-notify within this span
QUIET_HOURS = (22, 8)        # local-time window where notifications are muted
OR_RUNWAY_WARN_H = 72.0      # ⚠ once the OpenRouter balance empties this soon

WINDOWS = [
    ("seven_day", "Weekly limit", 168.0, True),
    ("five_hour", "5-hour session", 5.0, False),
    ("seven_day_sonnet", "Weekly Sonnet", 168.0, False),
    ("seven_day_opus", "Weekly Opus", 168.0, False),
]


def get_token():
    try:
        raw = subprocess.run(
            ["security", "find-generic-password", "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip()
        if raw:
            return json.loads(raw)["claudeAiOauth"]["accessToken"]
    except Exception:
        pass
    try:
        with open(CREDS_FILE) as f:
            return json.load(f)["claudeAiOauth"]["accessToken"]
    except Exception:
        return None


def fetch_usage(token):
    req = urllib.request.Request(USAGE_URL, headers={
        "Authorization": f"Bearer {token}",
        "anthropic-beta": "oauth-2025-04-20",
        "Content-Type": "application/json",
        # without a claude-cli UA the endpoint 429s aggressively
        "User-Agent": "claude-cli/2.0.0 (external, cli)",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def read_cache(path=CACHE_PATH):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def write_cache(cache, path=CACHE_PATH):
    try:
        with open(path, "w") as f:
            json.dump(cache, f)
    except Exception:
        pass


def cache_ts(cache, key):
    try:
        return dt.datetime.fromisoformat(cache[key])
    except Exception:
        return None


# ── OpenRouter: prepaid credit balance + spend trajectory ─────────────────
# OpenRouter credits don't reset like Claude's windows — they're a balance
# that only drains. So instead of a window we snapshot cumulative spend
# (total_usage) across runs and extrapolate when the balance hits zero.

def get_openrouter_key():
    key = os.environ.get("OPENROUTER_API_KEY", "").strip()
    if key:
        return key
    try:
        with open(OPENROUTER_KEY_FILE) as f:
            return f.read().strip() or None
    except Exception:
        return None


def _openrouter_get(url, key):
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode()).get("data") or {}


def fetch_openrouter(key):
    """Account credit balance (total purchased minus total spent)."""
    return {"credits": _openrouter_get(OPENROUTER_CREDITS_URL, key)}


def analyze_openrouter(data, samples, now):
    """Account balance + a spend trajectory toward zero."""
    if not data:
        return None
    credits = data.get("credits") or {}
    total = float(credits.get("total_credits") or 0.0)
    used = float(credits.get("total_usage") or 0.0)
    balance = total - used

    # Spend rate from cumulative-usage snapshots in the trailing window.
    # total_usage only ever rises, so a delta over time survives top-ups.
    pts = sorted((s for s in samples if len(s) == 2), key=lambda s: s[0])
    rate = None  # $/h; None means not enough history to say
    if pts:
        cutoff = (now - dt.timedelta(hours=OR_SAMPLE_WINDOW_H)).isoformat()
        window = [p for p in pts if p[0] >= cutoff] or pts
        first_ts = dt.datetime.fromisoformat(window[0][0])
        span_h = (now - first_ts).total_seconds() / 3600.0
        if span_h >= OR_SAMPLE_MIN_SPAN_H:
            rate = max(0.0, (used - float(window[0][1])) / span_h)

    dry_in_h = balance / rate if (rate and balance > 0) else None
    return {
        "total": total, "used": used, "balance": balance,
        "rate": rate, "per_day": (rate * 24) if rate is not None else None,
        "dry_in_h": dry_in_h, "depleted": balance <= 0,
    }


def render_openrouter(a, stale_err=None):
    ok, hot, bad = "green,lime", "orange,orange", "red,red"
    if a is None:
        line("OpenRouter — no data", size=13, color=bad)
        line("Could not reach the credits API"
             + (f" ({stale_err})" if stale_err else ""), size=12, color="gray,gray")
        line("Set $OPENROUTER_API_KEY or write ~/.config/burndown-bar/openrouter-key",
             size=11, color="gray,gray")
        return

    if a["depleted"]:
        line("OpenRouter — depleted", size=13, color=bad)
    else:
        line(f"OpenRouter — ${a['balance']:.2f} left", size=13)

    if a["rate"] is None:
        line("Spend rate: warming up — need a little history", size=12, color="gray,gray")
    elif a["rate"] <= 0:
        line("No recent spend — balance steady", size=12, color=ok)
    else:
        runway = a["dry_in_h"]
        rc = ok if runway is None else (
            bad if runway < 48 else hot if runway < 7 * 24 else ok)
        line(f"Pace: ${a['per_day']:.2f}/day (${a['rate']:.3f}/h)", size=12, color=rc)
        if runway is not None:
            line(f"Balance empties in ~{fmt_dur(runway)} at this pace", size=12, color=rc)

    if stale_err:
        line(f"(cached — last fetch {stale_err})", size=11, color="gray,gray")


def openrouter_status():
    """Fetch + analyze OpenRouter, mirroring the Claude cache/backoff dance.

    Returns (analysis_or_None, stale_err_or_None, key_is_configured).
    """
    key = get_openrouter_key()
    if not key:
        return None, None, False

    cache = read_cache(OR_CACHE_PATH)
    cached = cache.get("data")
    cached_at = cache_ts(cache, "fetched_at")
    failed_at = cache_ts(cache, "failed_at")
    cache_age = (NOW - cached_at).total_seconds() if cached_at else None
    fail_age = (NOW - failed_at).total_seconds() if failed_at else None
    samples = cache.get("samples") or []

    data, stale_err = None, None
    skip_fetch = cached is not None and (
        (cache_age is not None and cache_age < 90)
        or (fail_age is not None and fail_age < 300)
    )
    if skip_fetch:
        data = cached
        if not (cache_age is not None and cache_age < 90):
            stale_err = "rate-limited — backing off"
    else:
        try:
            data = fetch_openrouter(key)
            used_now = float((data.get("credits") or {}).get("total_usage") or 0.0)
            samples.append([NOW.isoformat(), used_now])
            cutoff = (NOW - dt.timedelta(days=7)).isoformat()
            samples = [s for s in samples
                       if len(s) == 2 and s[0] >= cutoff][-OR_SAMPLE_MAX:]
            write_cache({"data": data, "fetched_at": NOW.isoformat(),
                         "samples": samples}, OR_CACHE_PATH)
        except urllib.error.HTTPError as e:
            cache["failed_at"] = NOW.isoformat()
            write_cache(cache, OR_CACHE_PATH)
            data = cached
            stale_err = (f"auth (HTTP {e.code})" if e.code in (401, 403)
                         else "rate-limited (HTTP 429)" if e.code == 429
                         else f"HTTP {e.code}")
        except Exception as e:
            cache["failed_at"] = NOW.isoformat()
            write_cache(cache, OR_CACHE_PATH)
            data = cached
            stale_err = type(e).__name__

    return analyze_openrouter(data, samples, NOW), stale_err, True


# ── Trend history: burn-rate series from utilization snapshots ────────────
# Snapshots are [iso_ts, utilization_pct, resets_at_iso]. Utilization rises
# within a window and drops to ~0 when the window resets, so an interval that
# straddles a reset can't be attributed cleanly — we skip it.

RESET_JITTER_TOL_S = 120.0  # resets_at wobble below this = same window


def same_reset(a, b):
    """True if two resets_at timestamps denote the same window.

    The usage API recomputes resets_at as (now + window) on every poll, so the
    stored value jitters by up to a second or two. That wobble can straddle a
    minute/hour boundary (10:59:59.9 vs 11:00:00.1) and masquerade as a reset,
    which previously made ~two-thirds of intervals look like rollovers. A real
    reset moves resets_at by the whole window (hours to days), so a small
    tolerance cleanly separates jitter from genuine rollovers.
    """
    try:
        delta = dt.datetime.fromisoformat(b) - dt.datetime.fromisoformat(a)
        return abs(delta.total_seconds()) <= RESET_JITTER_TOL_S
    except Exception:
        return a == b


def rates_from_snapshots(snaps):
    """Per-interval burn rate (%/h), keyed by the interval's end timestamp."""
    pts = [s for s in snaps if len(s) == 3]
    out = []
    for a, b in zip(pts, pts[1:]):
        t0 = dt.datetime.fromisoformat(a[0])
        t1 = dt.datetime.fromisoformat(b[0])
        dt_h = (t1 - t0).total_seconds() / 3600.0
        if dt_h <= 0:
            continue
        # A reset between the two samples (window rolled over, or utilization
        # fell) makes the delta meaningless — drop that interval.
        if not same_reset(a[2], b[2]) or float(b[1]) < float(a[1]):
            continue
        out.append((t1, (float(b[1]) - float(a[1])) / dt_h))
    return out


# ── Unexpected limit changes: early resets and mid-window quota bumps ──────
# Anthropic sometimes resets a window before its scheduled time, or quietly
# raises a limit (special events, holidays). Both show up in the snapshot
# stream: an early reset changes resets_at while the old reset was still in the
# future; a quota bump drops utilization sharply with resets_at unchanged.

LIMIT_DROP_EPS = 3.0  # % utilization drop below which a dip is just noise


def detect_limit_events(snaps):
    """Scan a series' snapshots for early resets and mid-window quota bumps."""
    pts = [s for s in snaps if len(s) == 3]
    events = []
    for a, b in zip(pts, pts[1:]):
        if not same_reset(a[2], b[2]):
            t_b = dt.datetime.fromisoformat(b[0])
            old_reset = dt.datetime.fromisoformat(a[2])
            if t_b < old_reset:  # rolled over before it was due
                events.append({"at": b[0], "kind": "reset_early"})
        elif float(b[1]) < float(a[1]) - LIMIT_DROP_EPS:
            events.append({"at": b[0], "kind": "limit_raised"})
    return events


LIMIT_EVENT_MAX = 50          # cap on the stored event log
SUPPRESS_AFTER_EVENT_H = 48.0  # don't learn baselines for this long after one


def record_limit_events(history, label, events):
    """Append newly seen events to the log (deduped). Returns the new ones."""
    log = history.setdefault("limit_events", [])
    seen = {(e["label"], e["at"], e["kind"]) for e in log}
    fresh = []
    for e in events:
        key = (label, e["at"], e["kind"])
        if key in seen:
            continue
        seen.add(key)
        rec = {"label": label, "at": e["at"], "kind": e["kind"]}
        log.append(rec)
        fresh.append(rec)
    history["limit_events"] = sorted(log, key=lambda r: r["at"])[-LIMIT_EVENT_MAX:]
    return fresh


def pending_limit_notifications(history, events_recent, local_now):
    """Limit events that should notify now — fire-once-ever, muted in quiet hours."""
    if in_quiet_hours(local_now.hour, QUIET_HOURS):
        return []
    notified = set(history.get("limit_notified", []))
    fire = []
    for e in events_recent:
        k = f"{e['label']}|{e['at']}|{e['kind']}"
        if k in notified:
            continue
        notified.add(k)
        fire.append(e)
    history["limit_notified"] = sorted(notified)[-LIMIT_EVENT_MAX:]
    return fire


def event_active(history, label, now, within_h):
    """True if a logged event for ``label`` falls within the trailing window."""
    cutoff = now - dt.timedelta(hours=within_h)
    for e in history.get("limit_events", []):
        if e["label"] != label:
            continue
        try:
            if dt.datetime.fromisoformat(e["at"]) >= cutoff:
                return True
        except Exception:
            pass
    return False


def windowed_rate(snaps, now, hours):
    """Average burn rate over the trailing ``hours``, ignoring reset gaps.

    Duration-weighted across clean intervals. Returns None when there's no
    clean (non-reset, positive-span) history inside the window.
    """
    cutoff = now - dt.timedelta(hours=hours)
    pts = [s for s in snaps if len(s) == 3]
    burn = span = 0.0
    for a, b in zip(pts, pts[1:]):
        t1 = dt.datetime.fromisoformat(b[0])
        if t1 <= cutoff:
            continue
        t0 = dt.datetime.fromisoformat(a[0])
        dt_h = (t1 - t0).total_seconds() / 3600.0
        if dt_h <= 0 or not same_reset(a[2], b[2]) or float(b[1]) < float(a[1]):
            continue
        burn += float(b[1]) - float(a[1])
        span += dt_h
    return (burn / span) if span > 0 else None


# ── Baselines: what's *typical* for this weekday + hour ───────────────────
# Each series keeps per-(weekday, hour) cells of [count, sum, sum_of_squares].
# A single weekday/hour cell is sparse (a couple of weeks gives only a handful
# of readings), so a query shrinks the exact cell toward the broader
# hour-of-day pool — thin cells borrow strength, well-populated cells stand on
# their own. Variance comes from the pool, which is far more stable.

BASELINE_SHRINK_K = 4.0  # pseudo-observations of the pool mixed into each cell


def baseline_update(store, weekday, hour, value):
    cells = store.setdefault("cells", {})
    cell = cells.get(f"{weekday}-{hour}") or [0, 0.0, 0.0]
    cell[0] += 1
    cell[1] += value
    cell[2] += value * value
    cells[f"{weekday}-{hour}"] = cell
    return store


def _agg(cells, keys):
    n = s = ss = 0.0
    for k in keys:
        c = cells.get(k)
        if c:
            n += c[0]
            s += c[1]
            ss += c[2]
    if n == 0:
        return 0, None, None
    mean = s / n
    var = (ss - n * mean * mean) / (n - 1) if n >= 2 else None
    return n, mean, var


def baseline_means(store, weekday, hour):
    """Typical (raw, unshrunk) means at three pool widths for the data rows."""
    cells = store.get("cells", {})
    _, m_hour, _ = _agg(cells, [f"{weekday}-{hour}"])
    _, m_wd, _ = _agg(cells, [f"{weekday}-{h}" for h in range(24)])
    _, m_all, _ = _agg(cells, list(cells.keys()))
    return {"hour": m_hour, "weekday": m_wd, "overall": m_all}


def rel_pct(cur, ref):
    """Percent change of cur vs ref as a signed string, or None if undefined."""
    if cur is None or ref is None or ref <= 0:
        return None
    return f"{(cur - ref) / ref * 100:+.0f}%"


def baseline_query(store, weekday, hour):
    """Typical value (shrunk) + spread (std) for one weekday/hour cell.

    ``support`` is the observation count behind the shrunk mean (the hour-of-day
    pool, which includes the exact cell). Because the mean borrows from the
    global pool, it is non-None as soon as *any* cell exists — callers that need
    to know whether the estimate is trustworthy should gate on ``support``.
    """
    cells = store.get("cells", {})
    n_e, mean_e, _ = _agg(cells, [f"{weekday}-{hour}"])
    n_h, mean_h, var_h = _agg(cells, [f"{wd}-{hour}" for wd in range(7)])
    _, mean_g, var_g = _agg(cells, list(cells.keys()))

    pool_mean = mean_h if mean_h is not None else mean_g
    if mean_e is None:
        mean = pool_mean
    elif pool_mean is None:
        mean = mean_e
    else:
        mean = (n_e * mean_e + BASELINE_SHRINK_K * pool_mean) / (n_e + BASELINE_SHRINK_K)

    var = var_h if var_h is not None else var_g
    std = (var ** 0.5) if var is not None else None
    return {"mean": mean, "std": std, "n": int(n_e), "support": int(n_h)}


# ── Trend classification: is the current rate unusual for this cell? ───────
# A z-score against the learned baseline. The std gets a relative floor so a
# very regular series (std≈0) doesn't turn a trivial wiggle into a huge score.

TREND_Z_NOTABLE = 1.5  # beyond this many sigma we call it up/down
TREND_Z_SHARP = 3.0    # beyond this, sharply up/down
ANOMALY_Z = 3.5        # beyond this, a reading is too weird to learn from


def classify_trend(current, baseline):
    """Return {label, dir, z, current, mean} comparing current to baseline.

    dir is +1/0/-1. label is one of: sharply up, up, steady, down,
    sharply down, building (not enough history), unknown (no current rate).
    """
    if current is None:
        return {"label": "unknown", "dir": 0, "z": None,
                "current": None, "mean": baseline.get("mean")}
    mean = baseline.get("mean")
    std = baseline.get("std")
    if mean is None or std is None:
        return {"label": "building", "dir": 0, "z": None,
                "current": current, "mean": mean}

    eff_std = max(std, abs(mean) * 0.10, 1e-9)
    z = (current - mean) / eff_std
    if z >= TREND_Z_SHARP:
        label, d = "sharply up", 1
    elif z >= TREND_Z_NOTABLE:
        label, d = "up", 1
    elif z <= -TREND_Z_SHARP:
        label, d = "sharply down", -1
    elif z <= -TREND_Z_NOTABLE:
        label, d = "down", -1
    else:
        label, d = "steady", 0
    return {"label": label, "dir": d, "z": z, "current": current, "mean": mean}


def is_anomalous(rate, baseline, z_thresh=ANOMALY_Z):
    """True when a reading is so far from the baseline it shouldn't be learned."""
    if rate is None:
        return False
    mean, std = baseline.get("mean"), baseline.get("std")
    if mean is None or std is None:
        return False
    eff_std = max(std, abs(mean) * 0.10, 1e-9)
    return abs((rate - mean) / eff_std) > z_thresh


# ── Plain-English summary: two sentences, baseline-relative, with character ─
# No LLM — a small library of hand-written templates, picked deterministically
# by a `variant` (the hour of day) so the wording rotates without being random.

def _pick(options, variant):
    return options[variant % len(options)]


def _rel(direction, weekday):
    if direction > 0:
        return f"heavier than your typical {weekday}"
    if direction < 0:
        return f"lighter than your typical {weekday}"
    return f"about typical for a {weekday}"


def _claude_sentence(c, variant):
    status = c.get("status")
    weekday = c.get("weekday", "day")
    used = c.get("used", 0.0)
    runway = fmt_dur(c["runway_h"]) if c.get("runway_h") is not None else None
    rel = _rel(c.get("dir", 0), weekday)

    if status == "exhausted":
        return _pick([
            f"Weekly limit's tapped out at {used:.0f}% — nothing to do but wait for the reset.",
            f"You've hit the weekly ceiling ({used:.0f}%); it's reset-o'clock now.",
        ], variant)
    if status == "building":
        return _pick([
            "Still learning your rhythm — give it a few days and I'll tell you how today stacks up.",
            "I'm a few days short of knowing your usual pace, so no verdict on today yet.",
        ], variant)
    if status == "idle":
        return _pick([
            "Claude's quiet — nothing burned on the weekly window yet.",
            "Not a dent in the weekly window so far today.",
        ], variant)
    if status == "dry" and runway:
        return _pick([
            f"You're burning {rel} and, at this clip, the weekly window runs dry in {runway}.",
            f"Hot streak: weekly burn is {rel} and headed for empty in {runway}.",
            f"This pace won't last the week — {rel}, dry in about {runway}.",
        ], variant)
    if status == "hot":
        return _pick([
            f"Running warm on the weekly window, {rel} — worth half an eye.",
            f"Weekly burn's {rel} and pushing the pace; not dangerous yet.",
        ], variant)
    # ok
    d = c.get("dir", 0)
    if d > 0:
        return _pick([
            f"You're {rel} on Claude, but there's still room — only {used:.0f}% of the week gone.",
            f"Claude's running {rel}; nothing alarming, you're {used:.0f}% into the week.",
            f"A touch {rel} today, yet comfortably on track at {used:.0f}% of the weekly budget.",
        ], variant)
    if d < 0:
        return _pick([
            f"Quieter than usual — you're {rel}, just {used:.0f}% of the week used.",
            f"Easy does it: {rel} on Claude, only {used:.0f}% in.",
        ], variant)
    return _pick([
        f"Right on your usual {weekday} pace for Claude, {used:.0f}% of the week used.",
        f"Textbook {weekday} so far — Claude's at {used:.0f}% of the weekly budget.",
    ], variant)


def _openrouter_sentence(o, variant):
    bal = o.get("balance", 0.0)
    runway = fmt_dur(o["runway_h"]) if o.get("runway_h") is not None else None
    if o.get("depleted"):
        return _pick([
            "OpenRouter's out of credit.",
            "OpenRouter balance is spent.",
        ], variant)
    if o.get("building"):
        return _pick([
            f"OpenRouter's at ${bal:.2f}; still gauging your spend rhythm.",
            f"${bal:.2f} on OpenRouter — give it a day to read your spend.",
        ], variant)
    if o.get("idle") or runway is None:
        return _pick([
            f"OpenRouter's steady at ${bal:.2f} with no real spend lately.",
            f"${bal:.2f} sitting on OpenRouter, barely moving.",
        ], variant)
    d = o.get("dir", 0)
    if d > 0:
        return _pick([
            f"Spend's up there too — that ${bal:.2f} empties in about {runway}.",
            f"OpenRouter's draining faster; ${bal:.2f} left, ~{runway} of runway.",
        ], variant)
    if d < 0:
        return _pick([
            f"OpenRouter spend has eased; ${bal:.2f} now stretches ~{runway}.",
            f"Lighter on OpenRouter — ${bal:.2f} lasts about {runway}.",
        ], variant)
    return _pick([
        f"OpenRouter's spending at its usual clip; ${bal:.2f} lasts about {runway}.",
        f"Steady on OpenRouter — ${bal:.2f}, roughly {runway} to go.",
    ], variant)


def _tail_sentence(c, variant):
    if c.get("reset_in_h") is not None:
        reset = fmt_dur(c["reset_in_h"])
        return _pick([
            f"The weekly window resets in {reset}.",
            f"Reset's about {reset} out.",
        ], variant)
    return "All quiet otherwise."


def summary_lines(claude, openrouter, variant):
    """The two sentences as a list (Claude's burn vs typical, then OpenRouter)."""
    first = _claude_sentence(claude, variant)
    if openrouter is not None:
        second = _openrouter_sentence(openrouter, variant)
    else:
        second = _tail_sentence(claude, variant)
    return [first, second]


def compose_summary(claude, openrouter, variant):
    """Two sentences: Claude's burn vs typical, then OpenRouter (or a tail)."""
    return " ".join(summary_lines(claude, openrouter, variant))


# ── Title glyph: encode several trend signals compactly ────────────────────

def trend_arrow(direction):
    return "↑" if direction == 1 else "↓" if direction == -1 else ""


def compose_title(weekly, five, openrouter):
    """Menu-bar title: weekly pace + (5h) + (OpenRouter), each with a trend arrow.

    5h shows only when it's warning or has a notable trend; OpenRouter shows
    its balance (or ⛔ when depleted) with a ⚠ when runway is short.
    """
    title = f"{weekly['emoji']} {weekly['core']}{trend_arrow(weekly.get('dir', 0))}"
    if five and (five.get("warn") or five.get("dir")):
        title += f" 5h{'⚠' if five.get('warn') else ''}{trend_arrow(five.get('dir', 0))}"
    if openrouter is not None:
        if openrouter.get("depleted"):
            title += " · OR ⛔"
        else:
            title += f" · OR ${openrouter['balance']:.2f}{trend_arrow(openrouter.get('dir', 0))}"
            if openrouter.get("warn"):
                title += "⚠"
    return title


# ── Notifications: which notable events earn an actual macOS pop-up ────────
# Trends show passively in the menu; only genuinely notable shifts fire a
# notification. A per-event cooldown stops the same event nagging every 5 min,
# duplicates in a batch collapse to one, and quiet hours mute everything.

def in_quiet_hours(hour, quiet):
    start, end = quiet
    if start == end:
        return False
    if start < end:
        return start <= hour < end
    return hour >= start or hour < end


def decide_notifications(events, state, now, quiet=(22, 8), cooldown_h=6.0):
    """Return (events_to_fire, new_state). State maps event key -> last-fired iso."""
    state = dict(state or {})
    if in_quiet_hours(now.hour, quiet):
        return [], state
    fire, seen = [], set()
    for e in events:
        k = e["key"]
        if k in seen:
            continue
        last = state.get(k)
        if last is not None:
            try:
                if (now - dt.datetime.fromisoformat(last)).total_seconds() < cooldown_h * 3600:
                    continue
            except Exception:
                pass
        seen.add(k)
        fire.append(e)
        state[k] = now.isoformat()
    return fire, state


def spend_rate_over(samples, now, hours):
    """OpenRouter spend rate ($/h) over the trailing ``hours``.

    Samples are cumulative ``total_usage`` ([iso_ts, usage]) and never reset,
    so the rate is just the rise across the window. Anchors at the earliest
    sample inside the window (or all of them if none fall inside).
    """
    cutoff = (now - dt.timedelta(hours=hours)).isoformat()
    pts = sorted((s for s in samples if len(s) == 2), key=lambda s: s[0])
    win = [p for p in pts if p[0] >= cutoff] or pts
    if len(win) < 2:
        return None
    t0 = dt.datetime.fromisoformat(win[0][0])
    span_h = (now - t0).total_seconds() / 3600.0
    if span_h <= 0:
        return None
    return max(0.0, (float(win[-1][1]) - float(win[0][1])) / span_h)


def analyze(entry, window_h, now):
    """Compute the burn trajectory for one limit window."""
    if not entry or entry.get("resets_at") is None:
        return None
    used = float(entry.get("utilization") or 0.0)
    reset = dt.datetime.fromisoformat(entry["resets_at"])
    start = reset - dt.timedelta(hours=window_h)
    elapsed_h = max(0.0, min((now - start).total_seconds() / 3600.0, window_h))
    left_to_reset_h = max(0.0, (reset - now).total_seconds() / 3600.0)
    elapsed_frac = elapsed_h / window_h
    rate = used / elapsed_h if elapsed_h > 0 else 0.0  # %/h
    pace = (used / 100.0) / elapsed_frac if elapsed_frac > 0 else 0.0
    dry_at = None
    if rate > 0 and used < 100.0:
        dry_at = now + dt.timedelta(hours=(100.0 - used) / rate)
    projected = used + rate * left_to_reset_h  # % at reset if pace holds
    return {
        "used": used, "reset": reset, "start": start,
        "elapsed_h": elapsed_h, "left_h": left_to_reset_h,
        "elapsed_frac": elapsed_frac, "rate": rate, "pace": pace,
        "dry_at": dry_at, "projected": projected,
        "early": elapsed_frac < 0.05,
        "exhausted": used >= 100.0,
        "will_run_out": used >= 100.0 or (dry_at is not None and dry_at < reset),
    }


# ── Smart projection: integrate the learned per-hour baseline to the reset ─
# The naive projection (analyze's "projected") holds the whole-window-average
# rate flat — a front-loaded morning spike makes that read 4.7× and "dry in
# 28h" even when the rest of the day is quiet. The smart projection instead
# walks every remaining clock hour and adds that (weekday, hour) cell's learned
# typical burn rate, so overnight/weekend lulls are modelled from your own
# history. Thin cells fall back to a supplied recent rate; with no history at
# all and no fallback it returns None (nothing to project from).

SMART_MIN_SUPPORT = 3  # learned-cell observations needed before trusting it


def smart_projection(used, now, reset, store, fallback_rate):
    """Project utilization at ``reset`` by integrating learned per-hour rates.

    ``now``/``reset`` are wall-clock-aware datetimes whose .weekday()/.hour are
    used directly for baseline lookup (the caller passes local time, matching
    how baselines are recorded). Returns {projected, used_baseline, dry_at} or
    None when no rate is available for any remaining hour.

    Each hour uses its learned baseline only once that hour-of-day has enough
    support (``SMART_MIN_SUPPORT`` observations); otherwise it uses the supplied
    fallback rate. This matters because ``baseline_query`` borrows from the
    global pool, so a single early reading would otherwise project every hour at
    that lone value.
    """
    if reset <= now:
        return None
    proj = float(used)
    cursor = now
    used_baseline = rate_known = False
    dry_at = None
    while cursor < reset:
        # step to the next clock-hour boundary (first step may be partial)
        boundary = (cursor + dt.timedelta(hours=1)).replace(
            minute=0, second=0, microsecond=0)
        nxt = min(reset, boundary)
        step_h = (nxt - cursor).total_seconds() / 3600.0
        if step_h <= 0:
            break
        cell = baseline_query(store, cursor.weekday(), cursor.hour)
        rate = None
        if cell["mean"] is not None and cell.get("support", 0) >= SMART_MIN_SUPPORT:
            rate = cell["mean"]
            used_baseline = True
        elif fallback_rate is not None:
            rate = fallback_rate
        elif cell["mean"] is not None:
            rate = cell["mean"]  # thin, but better than nothing with no fallback
        if rate is not None:
            rate_known = True
            r = max(0.0, rate)
            if dry_at is None and proj < 100.0 <= proj + r * step_h and r > 0:
                frac = (100.0 - proj) / (r * step_h)
                dry_at = cursor + dt.timedelta(hours=step_h * frac)
            proj += r * step_h
        cursor = nxt
    if not rate_known:
        return None
    return {"projected": proj, "used_baseline": used_baseline, "dry_at": dry_at}


def fmt_when(t, now):
    t = t.astimezone()  # local time
    t = (t + dt.timedelta(seconds=30)).replace(second=0, microsecond=0)
    if t.date() == now.astimezone().date():
        return t.strftime("today %H:%M")
    if t.date() == (now.astimezone() + dt.timedelta(days=1)).date():
        return t.strftime("tomorrow %H:%M")
    return t.strftime("%a %b %-d, %H:%M")


def fmt_dur(h):
    if h < 1.0:
        return f"{round(h * 60)} min"
    if h < 48.0:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} d"


def fmt_limit_event(e, now):
    kind = {"reset_early": "reset early",
            "limit_raised": "limit raised"}.get(e["kind"], e["kind"])
    try:
        when = fmt_when(dt.datetime.fromisoformat(e["at"]), now)
    except Exception:
        when = e["at"]
    return f"{e['label']} — {kind} ({when})"


def line(text, **params):
    if params:
        text += " | " + " ".join(f"{k}={v}" for k, v in params.items())
    print(text)


def title_for(a, smart=None):
    if a is None:
        return "🟢 Claude"
    if a["exhausted"]:
        return f"⛔ 100% · resets {fmt_when(a['reset'], NOW)}"
    if smart is not None:
        # Smart projection headlines: it models your diurnal rhythm, so a
        # front-loaded spike no longer screams 4.7×. Runway is relative — on a
        # weekly window the exact day is noise; how much is left is the point.
        proj = smart["projected"]
        if smart.get("dry_at") is not None:
            runway = fmt_dur((smart["dry_at"] - NOW).total_seconds() / 3600.0)
            return f"🔥 proj {proj:.0f}% · dry in {runway}"
        band = "🟡" if proj >= 85.0 else "🟢"
        return f"{band} proj {proj:.0f}%"
    # No baselines yet (or projection unavailable): legacy naive title.
    if a["will_run_out"]:
        return f"🔥 {a['pace']:.2f}× → dry in {fmt_dur((a['dry_at'] - NOW).total_seconds() / 3600.0)}"
    if a["pace"] >= 0.85:
        return f"🟡 {a['pace']:.2f}× · proj {a['projected']:.0f}%"
    return f"🟢 {a['pace']:.2f}× · proj {a['projected']:.0f}%"


def render_window(label, a, lead=False):
    color_ok = "green,lime"
    color_hot = "orange,orange"
    color_bad = "red,red"
    size = 13 if lead else 12
    line(f"{label} — {a['used']:.0f}% used", size=size)
    line(
        f"Elapsed: {fmt_dur(a['elapsed_h'])} of {fmt_dur(a['elapsed_h'] + a['left_h'])}"
        f" window ({a['elapsed_frac'] * 100:.0f}% of the time)",
        size=12, color="gray,gray",
    )
    if a["used"] <= 0:
        line("Nothing used yet — no trajectory", size=12, color="gray,gray")
    else:
        per_day = a["rate"] * 24
        rate_str = (
            f"Pace: {a['pace']:.2f}× sustainable ({a['rate']:.2f}%/h"
            + (f" ≈ {per_day:.1f}%/day)" if a["elapsed_h"] + a["left_h"] > 24 else ")")
        )
        if a["early"]:
            rate_str += " — early in window, low confidence"
        line(rate_str, size=12,
             color=color_bad if a["will_run_out"] else (color_hot if a["pace"] >= 0.85 else color_ok))
        if a["exhausted"]:
            line(f"Limit hit — resets {fmt_when(a['reset'], NOW)} (in {fmt_dur(a['left_h'])})",
                 size=12, color=color_bad)
        elif a["will_run_out"]:
            short_by = (a["reset"] - a["dry_at"]).total_seconds() / 3600.0
            line(f"Runs dry {fmt_when(a['dry_at'], NOW)} — {fmt_dur(short_by)} BEFORE reset",
                 size=12, color=color_bad)
        else:
            line(f"Projected at reset: {a['projected']:.0f}% — fits with {100 - a['projected']:.0f}% headroom",
                 size=12, color=color_ok)
    line(f"Resets {fmt_when(a['reset'], NOW)} (in {fmt_dur(a['left_h'])})",
         size=12, color="gray,gray")


def render_smart_projection(smart):
    """One line: where the baseline-aware projection lands, vs the naive one."""
    ok, hot, bad = "green,lime", "orange,orange", "red,red"
    if smart is None:
        return
    proj = smart["projected"]
    basis = "learned rhythm" if smart.get("used_baseline") else "recent pace"
    if smart.get("dry_at") is not None:
        runway = fmt_dur((smart["dry_at"] - NOW).total_seconds() / 3600.0)
        line(f"Smart projection: dry in ~{runway} (≈{proj:.0f}% by reset, {basis})",
             size=12, color=bad)
    elif proj >= 85.0:
        line(f"Smart projection: ~{proj:.0f}% at reset — tight, {max(0, 100 - proj):.0f}% headroom ({basis})",
             size=12, color=hot)
    else:
        line(f"Smart projection: ~{proj:.0f}% at reset — fits, {100 - proj:.0f}% headroom ({basis})",
             size=12, color=ok)


# ── Integration glue: record history, derive trends, notify ───────────────

def record_claude_snapshot(claude_hist, data, now):
    """Append the current utilization reading for each window and trim old ones."""
    cutoff = (now - dt.timedelta(days=HISTORY_RETAIN_DAYS)).isoformat()
    for key, _, _, _ in WINDOWS:
        entry = data.get(key) or {}
        if entry.get("resets_at") is None or entry.get("utilization") is None:
            continue
        series = claude_hist.setdefault(key, [])
        series.append([now.isoformat(), float(entry["utilization"]), entry["resets_at"]])
        claude_hist[key] = [s for s in series
                            if len(s) == 3 and s[0] >= cutoff][-HISTORY_MAX:]


def commit_baseline(history, series, rate, local_now, suppress=False):
    """Fold one rate reading into the baseline, at most once per clock hour.

    Skips readings that are anomalous or that land in a flagged event window
    (``suppress``) so holiday spikes and quota bumps never become "typical".
    The hour slot is consumed either way, so a single weird reading doesn't get
    a do-over later in the same hour.
    """
    if rate is None:
        return
    marks = history.setdefault("baseline_marks", {})
    hr_iso = local_now.replace(minute=0, second=0, microsecond=0).isoformat()
    if marks.get(series) == hr_iso:
        return
    marks[series] = hr_iso  # consume this hour's slot
    if suppress:
        return
    store = history.setdefault("baselines", {}).setdefault(series, {})
    if is_anomalous(rate, baseline_query(store, local_now.weekday(), local_now.hour)):
        return
    baseline_update(store, local_now.weekday(), local_now.hour, rate)


def trend_for(rate_fn, store, local_now):
    """Bundle a series' current rates, classification and baseline-relative rows.

    rate_fn(hours) -> rate over the trailing window (or None).
    """
    recent = rate_fn(TREND_RECENT_H)
    day = rate_fn(TREND_DAY_H)
    week = rate_fn(TREND_WEEK_H)
    cls = classify_trend(recent, baseline_query(store, local_now.weekday(), local_now.hour))
    means = baseline_means(store, local_now.weekday(), local_now.hour)
    rows = {
        "hour": rel_pct(recent, means["hour"]),
        "day": rel_pct(day, means["weekday"]),
        "week": rel_pct(week, means["overall"]),
    }
    return {"cls": cls, "rows": rows, "recent": recent}


def claude_summary_status(weekly, cls, smart=None):
    if weekly is None:
        return "building"
    if weekly["exhausted"]:
        return "exhausted"
    # When a smart projection exists it supersedes the spike-prone naive
    # "will_run_out"/pace signals for the dry/hot verdict.
    if smart is not None and smart.get("dry_at") is not None:
        return "dry"
    if smart is None and weekly["will_run_out"]:
        return "dry"
    if weekly["used"] <= 0:
        return "idle"
    if cls["label"] == "building":
        return "building"
    if smart is not None:
        return "hot" if smart["projected"] >= 85.0 else "ok"
    return "hot" if weekly["pace"] >= 0.85 else "ok"


def render_trend_rows(rows, weekday_name):
    """Baseline-relative rows under the weekly window (only what we can compute)."""
    labels = [("hour", "vs typical this hour"),
              ("day", f"vs typical {weekday_name}"),
              ("week", "vs typical week")]
    shown = [(lbl, rows.get(k)) for k, lbl in labels if rows.get(k) is not None]
    if not shown:
        line("Trend: still building history", size=11, color="gray,gray")
        return
    for lbl, val in shown:
        color = "gray,gray"
        if val.startswith("+") and val not in ("+0%",):
            color = "orange,orange"
        elif val.startswith("-"):
            color = "green,lime"
        line(f"{lbl}: {val}", size=11, color=color)


def build_events(weekly, weekly_status, weekly_cls, or_a, or_cls, local_now,
                 weekly_smart=None):
    """Candidate notifications; the state machine decides which actually fire."""
    events = []
    wd = local_now.strftime("%A")
    if weekly is not None:
        if weekly_status == "exhausted":
            events.append({"key": "claude-weekly-exhausted",
                           "title": "Claude weekly limit hit",
                           "body": "You're at 100% — waiting on the reset."})
        elif weekly_status == "dry":
            # Prefer the smart projection's runway; fall back to the naive one.
            dry_at = ((weekly_smart or {}).get("dry_at")) or weekly.get("dry_at")
            runway = (fmt_dur((dry_at - NOW).total_seconds() / 3600.0)
                      if dry_at else "soon")
            events.append({"key": "claude-weekly-dry",
                           "title": "Claude burning hot",
                           "body": f"At this pace the weekly limit runs dry in {runway}."})
        elif weekly_cls["label"] == "sharply up":
            events.append({"key": "claude-weekly-spike",
                           "title": "Heavy Claude day",
                           "body": f"Weekly burn is well above your typical {wd}."})
    if or_a is not None and or_cls is not None:
        if or_a["depleted"]:
            events.append({"key": "or-depleted",
                           "title": "OpenRouter out of credit",
                           "body": "Balance is spent."})
        else:
            if or_cls["label"] == "sharply up":
                events.append({"key": "or-spend-spike",
                               "title": "OpenRouter spend spiked",
                               "body": f"Spending well above your typical {wd}."})
            if or_a.get("dry_in_h") is not None and or_a["dry_in_h"] < OR_RUNWAY_WARN_H:
                events.append({"key": "or-runway-low",
                               "title": "OpenRouter running low",
                               "body": f"Balance empties in ~{fmt_dur(or_a['dry_in_h'])} at this pace."})
    return events


def send_notification(title, body):
    try:
        subprocess.run(
            ["osascript", "-e",
             f"display notification {json.dumps(body)} with title {json.dumps(title)}"],
            capture_output=True, timeout=10,
        )
    except Exception:
        pass


def fire_notifications(history, events, local_now):
    state = history.get("notif_state", {})
    to_fire, new_state = decide_notifications(
        events, state, local_now, quiet=QUIET_HOURS, cooldown_h=NOTIFY_COOLDOWN_H)
    history["notif_state"] = new_state
    for e in to_fire:
        send_notification(e["title"], e["body"])


def main():
    token = get_token()
    if not token:
        line("⚠️ Claude")
        line("---")
        line("No Claude Code credentials found", color="red,red")
        line("Open Claude Code and log in, then refresh", size=12)
        line("Refresh | refresh=true")
        return

    cache = read_cache()
    cached = cache.get("data")
    cached_at = cache_ts(cache, "fetched_at")
    failed_at = cache_ts(cache, "failed_at")
    cache_age = (NOW - cached_at).total_seconds() if cached_at else None
    fail_age = (NOW - failed_at).total_seconds() if failed_at else None

    # The endpoint is shared with Claude Code's own polling and 429s when
    # hammered: serve fresh-enough cache without touching the API, and after
    # a failure back off for 5 minutes before trying again.
    data, fetched_at, stale_err = None, NOW, None
    skip_fetch = cached is not None and (
        (cache_age is not None and cache_age < 90)
        or (fail_age is not None and fail_age < 300)
    )
    if skip_fetch:
        data, fetched_at = cached, cached_at or NOW
        if not (cache_age is not None and cache_age < 90):
            stale_err = "rate-limited — backing off"
    else:
        try:
            data = fetch_usage(token)
            write_cache({"data": data, "fetched_at": NOW.isoformat()})
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                line("⚠️ Claude auth")
                line("---")
                line(f"Usage API returned {e.code} — token likely expired", color="red,red")
                line("Run any prompt in Claude Code to refresh it, then refresh here", size=12)
                line("Refresh | refresh=true")
                return
            cache["failed_at"] = NOW.isoformat()
            write_cache(cache)
            data, fetched_at = cached, cached_at or NOW
            stale_err = "rate-limited (HTTP 429)" if e.code == 429 else f"HTTP {e.code}"
        except Exception as e:
            cache["failed_at"] = NOW.isoformat()
            write_cache(cache)
            data, fetched_at = cached, cached_at or NOW
            stale_err = type(e).__name__

    if data is None:
        line("⚠️ Claude")
        line("---")
        line(f"Could not reach usage API ({stale_err}) and no cached data", color="red,red")
        line("Refresh | refresh=true")
        return

    results = {key: analyze(data.get(key), wh, NOW) for key, _, wh, _ in WINDOWS}
    or_a, or_stale_err, or_has_key = openrouter_status()
    or_samples = (read_cache(OR_CACHE_PATH).get("samples") or []) if or_has_key else []

    local_now = NOW.astimezone()

    # ── Trends: record this reading, learn baselines, classify vs typical ──
    history = read_cache(HISTORY_PATH)
    claude_hist = history.setdefault("claude", {})
    record_claude_snapshot(claude_hist, data, NOW)
    baselines = history.setdefault("baselines", {})

    trends = {}
    for key, label, _, _ in WINDOWS:
        snaps = claude_hist.get(key, [])
        store = baselines.setdefault(key, {})
        # Spot early resets / quota bumps and log them before learning baselines.
        record_limit_events(history, label, detect_limit_events(snaps))
        suppress = event_active(history, label, NOW, SUPPRESS_AFTER_EVENT_H)
        t = trend_for(lambda h, s=snaps: windowed_rate(s, NOW, h), store, local_now)
        commit_baseline(history, key, t["recent"], local_now, suppress=suppress)
        trends[key] = t

    or_trend = None
    if or_has_key:
        or_store = baselines.setdefault("openrouter", {})
        or_trend = trend_for(lambda h: spend_rate_over(or_samples, NOW, h), or_store, local_now)
        commit_baseline(history, "openrouter", or_trend["recent"], local_now)

    weekly = results.get("seven_day")
    weekly_cls = trends["seven_day"]["cls"]
    five = results.get("five_hour")
    five_cls = trends["five_hour"]["cls"]

    # ── Smart projection: walk the time left in the weekly window hour by hour,
    # adding each hour's learned typical burn; fall back to the trailing-24h
    # rate (then the raw window rate) when baselines are still thin. ──
    weekly_smart = None
    if weekly is not None and not weekly["exhausted"]:
        wk_snaps = claude_hist.get("seven_day", [])
        fallback = windowed_rate(wk_snaps, NOW, TREND_DAY_H)
        if fallback is None:
            fallback = weekly["rate"]
        weekly_smart = smart_projection(
            weekly["used"], local_now, weekly["reset"].astimezone(),
            baselines.setdefault("seven_day", {}), fallback)

    # ── Title: one compact glyph carrying weekly + 5h + OpenRouter trends ──
    emoji, core = title_for(weekly, weekly_smart).split(" ", 1)
    weekly_t = {"emoji": emoji, "core": core, "dir": weekly_cls["dir"] if weekly else 0}
    five_t = ({"warn": five["used"] >= 90, "dir": five_cls["dir"]}
              if five is not None else None)
    or_t = None
    if or_a is not None:
        or_t = {"depleted": or_a["depleted"], "balance": or_a["balance"],
                "dir": (or_trend["cls"]["dir"] if or_trend else 0),
                "warn": (or_a.get("dry_in_h") is not None
                         and or_a["dry_in_h"] < OR_RUNWAY_WARN_H)}
    line(compose_title(weekly_t, five_t, or_t))
    line("---")

    # ── Plain-English, baseline-relative summary up top ──
    weekly_status = claude_summary_status(weekly, weekly_cls, weekly_smart)
    if weekly_smart is not None and weekly_smart.get("dry_at") is not None:
        runway_h = (weekly_smart["dry_at"] - NOW).total_seconds() / 3600.0
    elif weekly_smart is None and weekly and weekly["will_run_out"] and weekly["dry_at"]:
        runway_h = (weekly["dry_at"] - NOW).total_seconds() / 3600.0
    else:
        runway_h = None
    claude_sum = {
        "status": weekly_status,
        "dir": weekly_cls["dir"] if weekly else 0,
        "weekday": local_now.strftime("%A"),
        "pace": weekly["pace"] if weekly else 0.0,
        "used": weekly["used"] if weekly else 0.0,
        "runway_h": runway_h,
        "reset_in_h": weekly["left_h"] if weekly else None,
    }
    or_sum = None
    if or_a is not None and or_trend is not None:
        or_sum = {
            "dir": or_trend["cls"]["dir"], "balance": or_a["balance"],
            "runway_h": or_a.get("dry_in_h"), "depleted": or_a["depleted"],
            "idle": (or_a.get("rate") is not None and or_a["rate"] <= 0),
            "building": or_trend["cls"]["label"] == "building",
        }
    for sentence in summary_lines(claude_sum, or_sum, local_now.hour):
        line(sentence, size=12)
    line("---")

    first = True
    for key, label, _wh, lead in WINDOWS:
        a = results.get(key)
        if a is None:
            continue
        if key == "seven_day_sonnet" and a["used"] <= 0:
            continue
        if not first:
            line("---")
        render_window(label, a, lead=lead)
        if key == "seven_day":
            render_smart_projection(weekly_smart)
            render_trend_rows(trends["seven_day"]["rows"], local_now.strftime("%A"))
        first = False

    extra = data.get("extra_usage") or {}
    if extra.get("is_enabled") and (extra.get("used_credits") or 0) > 0:
        line("---")
        line(f"Extra usage: {extra['used_credits']:.2f} of {extra.get('monthly_limit')} "
             f"{extra.get('currency', '')} used", size=12)

    if or_has_key:
        line("---")
        render_openrouter(or_a, or_stale_err)

    def _event_age_h(e):
        try:
            return (NOW - dt.datetime.fromisoformat(e["at"])).total_seconds() / 3600.0
        except Exception:
            return 1e9

    # ── Unexpected limit changes Anthropic made (early resets, quota bumps) ──
    recent_events = [e for e in reversed(history.get("limit_events", []))
                     if _event_age_h(e) <= 14 * 24.0][:4]
    if recent_events:
        line("---")
        line("Limit events (Anthropic)", size=12)
        for e in recent_events:
            line(f"· {fmt_limit_event(e, NOW)}", size=11, color="gray,gray")

    # ── Notify on notable shifts + one-time limit events, then persist ──
    fire_notifications(history, build_events(
        weekly, weekly_status, weekly_cls, or_a,
        (or_trend["cls"] if or_trend else None), local_now,
        weekly_smart=weekly_smart), local_now)
    notify_recent = [e for e in history.get("limit_events", []) if _event_age_h(e) <= 24.0]
    for e in pending_limit_notifications(history, notify_recent, local_now):
        title = ("Fresh quota" if e["kind"] == "reset_early" else "Limit raised")
        send_notification(title, fmt_limit_event(e, local_now))
    write_cache(history, HISTORY_PATH)

    line("---")
    note = f"Fetched {fetched_at.astimezone().strftime('%H:%M')}"
    if stale_err:
        note += f" (cached — last fetch {stale_err})"
    line(note, size=11, color="gray,gray")
    line("Open claude.ai usage | href=https://claude.ai/settings/usage")
    line("Refresh | refresh=true")


NOW = dt.datetime.now(dt.timezone.utc)

if __name__ == "__main__":
    main()
