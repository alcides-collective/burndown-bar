#!/usr/bin/env python3
# <xbar.title>Burndown Bar</xbar.title>
# <xbar.version>v1.0.0</xbar.version>
# <xbar.author>Jakub Dudek</xbar.author>
# <xbar.author.github>burndownbar</xbar.author.github>
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


def load_cache():
    try:
        with open(CACHE_PATH) as f:
            cached = json.load(f)
        return cached["data"], dt.datetime.fromisoformat(cached["fetched_at"])
    except Exception:
        return None, None


def save_cache(data, now):
    try:
        with open(CACHE_PATH, "w") as f:
            json.dump({"data": data, "fetched_at": now.isoformat()}, f)
    except Exception:
        pass


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
        return f"🔥 {a['pace']:.2f}× → dry {fmt_when(a['dry_at'], NOW)}"
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

    data, fetched_at, stale_err = None, NOW, None
    try:
        data = fetch_usage(token)
        save_cache(data, NOW)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            line("⚠️ Claude auth")
            line("---")
            line(f"Usage API returned {e.code} — token likely expired", color="red,red")
            line("Run any prompt in Claude Code to refresh it, then refresh here", size=12)
            line("Refresh | refresh=true")
            return
        data, fetched_at = load_cache()
        stale_err = f"HTTP {e.code}"
    except Exception as e:
        data, fetched_at = load_cache()
        stale_err = type(e).__name__

    if data is None:
        line("⚠️ Claude")
        line("---")
        line(f"Could not reach usage API ({stale_err}) and no cached data", color="red,red")
        line("Refresh | refresh=true")
        return

    results = {key: analyze(data.get(key), wh, NOW) for key, _, wh, _ in WINDOWS}

    weekly = results.get("seven_day")
    title = title_for(weekly)
    five = results.get("five_hour")
    if five and five["used"] >= 90:
        title += " ·5h⚠"
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

    line("---")
    note = f"Fetched {fetched_at.astimezone().strftime('%H:%M')}"
    if stale_err:
        note += f" (stale — last fetch failed: {stale_err})"
    line(note, size=11, color="gray,gray")
    line("Open claude.ai usage | href=https://claude.ai/settings/usage")
    line("Refresh | refresh=true")


NOW = dt.datetime.now(dt.timezone.utc)
main()
