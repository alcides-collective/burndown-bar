#!/usr/bin/env python3
# <xbar.title>Burndown Bar</xbar.title>
# <xbar.version>v1.1.0</xbar.version>
# <xbar.author>Jakub Dudek</xbar.author>
# <xbar.author.github>alcides-collective</xbar.author.github>
# <xbar.desc>Not how much Claude quota you've used — how fast you're burning it. Pace vs sustainable, projected dry time vs reset.</xbar.desc>
# <xbar.dependencies>python3</xbar.dependencies>
# <xbar.abouturl>https://burndown.bar</xbar.abouturl>
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


def line(text, **params):
    if params:
        text += " | " + " ".join(f"{k}={v}" for k, v in params.items())
    print(text)


def title_for(a):
    if a is None:
        return "🟢 Claude"
    if a["exhausted"]:
        return f"⛔ 100% · resets {fmt_when(a['reset'], NOW)}"
    if a["will_run_out"]:
        # Relative runway, not a calendar date — on a weekly window the exact
        # day is noise; how much runway is left is the point.
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

    weekly = results.get("seven_day")
    title = title_for(weekly)
    five = results.get("five_hour")
    if five and five["used"] >= 90:
        title += " ·5h⚠"
    if or_a is not None:
        title += " · OR ⛔" if or_a["depleted"] else f" · OR ${or_a['balance']:.2f}"
    line(title)
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
        first = False

    extra = data.get("extra_usage") or {}
    if extra.get("is_enabled") and (extra.get("used_credits") or 0) > 0:
        line("---")
        line(f"Extra usage: {extra['used_credits']:.2f} of {extra.get('monthly_limit')} "
             f"{extra.get('currency', '')} used", size=12)

    if or_has_key:
        line("---")
        render_openrouter(or_a, or_stale_err)

    line("---")
    note = f"Fetched {fetched_at.astimezone().strftime('%H:%M')}"
    if stale_err:
        note += f" (cached — last fetch {stale_err})"
    line(note, size=11, color="gray,gray")
    line("Open claude.ai usage | href=https://claude.ai/settings/usage")
    line("Refresh | refresh=true")


NOW = dt.datetime.now(dt.timezone.utc)
main()
