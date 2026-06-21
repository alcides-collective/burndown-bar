"""Incident timeline with lifecycle tracking.

The dashboard runs continuously, so unlike the plugin it can watch a condition
open and close. Each lifecycle incident (spend surge, weekly exhaustion, stale
data) is recorded once with start/end, duration, and — for surges — the peak
rate and dollars burned over the episode. Anthropic limit changes (early
resets, quota bumps) come in as point incidents, deduped.

`update()` is a pure state transition over the prior log + the current
observations; the IO wrappers just persist it.
"""
import datetime as dt
import json
import os

INCIDENT_FILE = os.environ.get(
    "BURNDOWN_INCIDENT_FILE",
    os.path.expanduser("~/.local/share/burndown-bar/incidents.json"))
LOG_CAP = 250


def _open_of(log, typ):
    for e in log:
        if e["type"] == typ and e.get("open"):
            return e
    return None


def update(state, obs, now):
    """Fold the current observations into the incident log. Pure."""
    state = dict(state or {})
    log = list(state.get("log", []))
    seen = set(state.get("seen_points", []))
    iso = now.isoformat()

    # ── surge (lifecycle, with peak + burned) ──
    surge = obs.get("surge")
    cur = _open_of(log, "surge")
    if surge:
        bal = obs.get("or_balance")
        if cur is None:
            log.append({
                "type": "surge", "start": iso, "end": None, "open": True,
                "start_balance": bal, "peak_rate": surge["rate"],
                "peak_factor": surge["factor"], "min_runway_h": surge["runway_h"],
                "burned": 0.0,
            })
        else:
            cur["peak_rate"] = max(cur.get("peak_rate", 0), surge["rate"])
            cur["peak_factor"] = max(cur.get("peak_factor", 0), surge["factor"])
            cur["min_runway_h"] = min(cur.get("min_runway_h", 1e9), surge["runway_h"])
            if cur.get("start_balance") is not None and bal is not None:
                cur["burned"] = round(max(0.0, cur["start_balance"] - bal), 4)
            cur["end"] = iso
    elif cur is not None:
        cur["open"] = False
        cur["end"] = iso

    # ── exhausted + stale (lifecycle, duration only) ──
    for typ, active, extra in (
        ("exhausted", obs.get("exhausted"), {}),
        ("stale", obs.get("stale"), {"age_h": obs.get("stale_age_h")}),
    ):
        cur = _open_of(log, typ)
        if active:
            if cur is None:
                log.append({"type": typ, "start": iso, "end": iso, "open": True, **extra})
            else:
                cur["end"] = iso
                cur.update(extra)
        elif cur is not None:
            cur["open"] = False
            cur["end"] = iso

    # ── Anthropic limit changes (point incidents, deduped) ──
    for e in obs.get("limit_events", []):
        key = f"{e.get('label')}|{e.get('at')}|{e.get('kind')}"
        if key in seen:
            continue
        seen.add(key)
        log.append({"type": "limit_change", "start": e.get("at"), "end": e.get("at"),
                    "open": False, "label": e.get("label"), "kind": e.get("kind")})

    log.sort(key=lambda e: (e.get("start") or ""))
    # keep all open incidents; cap the closed tail
    closed = [e for e in log if not e.get("open")][-LOG_CAP:]
    openi = [e for e in log if e.get("open")]
    state["log"] = sorted(closed + openi, key=lambda e: (e.get("start") or ""))
    state["seen_points"] = sorted(seen)[-LOG_CAP:]
    return state


def for_api(state, now, limit=40):
    """Recent incidents, newest first, with computed durations."""
    out = []
    for e in reversed(state.get("log", [])):
        d = dict(e)
        try:
            start = dt.datetime.fromisoformat(e["start"])
            end = now if e.get("open") else dt.datetime.fromisoformat(e.get("end") or e["start"])
            d["duration_h"] = round((end - start).total_seconds() / 3600.0, 3)
        except Exception:
            d["duration_h"] = None
        out.append(d)
        if len(out) >= limit:
            break
    return out


# ── IO wrappers ─────────────────────────────────────────────────────────────

def load():
    try:
        with open(INCIDENT_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save(state):
    try:
        os.makedirs(os.path.dirname(INCIDENT_FILE), exist_ok=True)
        tmp = INCIDENT_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, INCIDENT_FILE)
    except Exception:
        pass
