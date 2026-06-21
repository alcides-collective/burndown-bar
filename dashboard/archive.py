"""Server-owned long-term history archive.

The plugin only retains ~21 days of snapshots (HISTORY_RETAIN_DAYS). To support
multi-month trend views, the dashboard keeps its own small append store: one
rolled-up record per clock hour. On first run it backfills from whatever the
plugin currently has (so charts aren't empty), then extends it hour by hour.

This is the only state the dashboard *owns*; everything else it just reads.
Records are intentionally tiny:  {"h": iso_hour, "wk_used", "wk_rate",
"or_bal", "or_rate"}.
"""
import datetime as dt
import json
import os

ARCHIVE_FILE = os.environ.get(
    "BURNDOWN_ARCHIVE_FILE",
    os.path.expanduser("~/.local/share/burndown-bar/dashboard-archive.json"))
RETAIN_DAYS = int(os.environ.get("BURNDOWN_ARCHIVE_RETAIN_DAYS", "180"))


def _hour_key(ts):
    t = dt.datetime.fromisoformat(ts) if isinstance(ts, str) else ts
    return (t.astimezone(dt.timezone.utc)
            .replace(minute=0, second=0, microsecond=0).isoformat())


def _load():
    try:
        with open(ARCHIVE_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(arch):
    try:
        os.makedirs(os.path.dirname(ARCHIVE_FILE), exist_ok=True)
        tmp = ARCHIVE_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(arch, f)
        os.replace(tmp, ARCHIVE_FILE)  # atomic, no torn reads
    except Exception:
        pass


def _backfill(by_hour, history, openrouter):
    """Seed the archive from the plugin's existing history (once)."""
    for s in history.get("claude", {}).get("seven_day", []):
        if len(s) != 3:
            continue
        try:
            k = _hour_key(s[0])
            by_hour.setdefault(k, {"h": k})["wk_used"] = float(s[1])
        except Exception:
            pass
    for ts, bal in (openrouter.get("balance_series") or []):
        try:
            k = _hour_key(ts)
            by_hour.setdefault(k, {"h": k})["or_bal"] = round(float(bal), 4)
        except Exception:
            pass


def update(now, claude, openrouter, history, cache_dir):
    """Upsert the current hour, backfill once, prune, and return the series."""
    arch = _load()
    by_hour = {r["h"]: r for r in arch.get("hourly", []) if isinstance(r, dict) and "h" in r}

    if not arch.get("backfilled"):
        _backfill(by_hour, history, openrouter)
        arch["backfilled"] = True

    rec = by_hour.setdefault(_hour_key(now), {"h": _hour_key(now)})
    wk = (claude.get("windows", {}).get("seven_day", {}) or {}).get("data")
    if wk:
        rec["wk_used"] = wk["used"]
    recent = claude.get("trend", {}).get("recent")
    if recent is not None:
        rec["wk_rate"] = round(recent, 3)
    if openrouter.get("ok"):
        if openrouter.get("balance") is not None:
            rec["or_bal"] = round(openrouter["balance"], 4)
        if openrouter.get("rate") is not None:
            rec["or_rate"] = round(openrouter["rate"], 4)

    cutoff = (now - dt.timedelta(days=RETAIN_DAYS)).isoformat()
    hourly = sorted((r for r in by_hour.values() if r["h"] >= cutoff),
                    key=lambda r: r["h"])
    arch["hourly"] = hourly
    _save(arch)
    return hourly
