"""ntfy push alerts.

Publishes to a public ntfy.sh topic (the data isn't sensitive). The topic is
unguessable-by-default and overridable via $BURNDOWN_NTFY_TOPIC; subscribe to
it in the ntfy app/website to receive alerts on your phone.

Alert *which* and *when* reuse the plugin's already-tested
``decide_notifications`` state machine: per-event cooldown, in-batch dedup,
quiet hours, and an ``urgent`` flag that lets the spend surge bypass quiet
hours (a money leak at 3am shouldn't wait until morning).

Titles/bodies are plain text; emoji come from ntfy ``Tags`` (shortcodes), so
HTTP headers stay ASCII-clean.
"""
import datetime as dt
import os
import urllib.request

NTFY_SERVER = os.environ.get("BURNDOWN_NTFY_SERVER", "https://ntfy.sh").rstrip("/")
NTFY_TOPIC = os.environ.get("BURNDOWN_NTFY_TOPIC", "burndown-61d1751f1d")
ENABLED = os.environ.get("BURNDOWN_NTFY", "1").lower() not in ("0", "false", "no", "")
STALE_AFTER_H = float(os.environ.get("BURNDOWN_STALE_AFTER_H", "0.5"))


def publish(title, message, priority=3, tags=None, topic=None):
    topic = topic or NTFY_TOPIC
    if not (ENABLED and topic):
        return False
    headers = {"Title": title, "Priority": str(priority)}
    if tags:
        headers["Tags"] = ",".join(tags)
    req = urllib.request.Request(
        f"{NTFY_SERVER}/{topic}", data=message.encode("utf-8"),
        method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _fmt_dur(h):
    if h is None:
        return "—"
    if h < 1:
        return f"{round(h * 60)} min"
    if h < 48:
        return f"{h:.1f} h"
    return f"{h / 24:.1f} d"


def build_alerts(payload):
    """Turn the current payload into candidate ntfy events (urgent ones flagged)."""
    events = []
    c = payload.get("claude") or {}
    o = payload.get("openrouter") or {}
    wk = ((c.get("windows") or {}).get("seven_day") or {}).get("data") or {}

    surge = o.get("surge")
    if surge:
        events.append({
            "key": "or-surge", "urgent": True, "cooldown_h": 1.0,
            "priority": 5, "tags": ["rotating_light"],
            "title": "OpenRouter spend surge",
            "message": (f"${surge['rate']:.2f}/h, ~{round(surge['factor'])}x your normal "
                        f"— balance empties in ~{_fmt_dur(surge['runway_h'])}. Runaway process?"),
        })

    if c.get("weekly_status") == "exhausted" or (wk.get("used", 0) >= 100):
        events.append({
            "key": "claude-weekly-exhausted", "priority": 4, "tags": ["no_entry"],
            "title": "Claude weekly limit hit",
            "message": "You're at 100% of the weekly limit — waiting on the reset.",
        })

    if payload.get("stale"):
        events.append({
            "key": "stale-cache", "priority": 4, "tags": ["warning"],
            "title": "Burndown data is stale",
            "message": (f"Usage data hasn't updated in ~{_fmt_dur(payload.get('stale_age_h'))}. "
                        "Is SwiftBar / the plugin still running?"),
        })

    if o.get("ok") and not o.get("depleted") and o.get("dry_in_h") is not None and o["dry_in_h"] < 72:
        events.append({
            "key": "or-runway-low", "priority": 3, "tags": ["money_with_wings"],
            "title": "OpenRouter running low",
            "message": f"Balance ${o.get('balance', 0):.2f} empties in ~{_fmt_dur(o['dry_in_h'])} at this pace.",
        })

    if (c.get("trend") or {}).get("cls", {}).get("label") == "sharply up":
        events.append({
            "key": "claude-weekly-spike", "priority": 3, "tags": ["chart_increasing"],
            "title": "Heavy Claude day",
            "message": "Weekly burn is well above your typical for this weekday.",
        })

    # one-time Anthropic limit changes seen in the last hour
    for e in (c.get("limit_events") or []):
        if e.get("age_h") is not None and e["age_h"] <= 1.0:
            kind = {"reset_early": "reset early", "limit_raised": "limit raised"}.get(e.get("kind"), e.get("kind"))
            events.append({
                "key": f"limit-{e.get('label')}-{e.get('at')}-{e.get('kind')}",
                "cooldown_h": 24.0, "priority": 3, "tags": ["arrows_counterclockwise"],
                "title": "Anthropic changed a limit",
                "message": f"{e.get('label')} — {kind}.",
            })

    return events


def fire(payload, state, bb, now):
    """Decide (via the plugin's state machine) and publish. Returns new state."""
    events = build_alerts(payload)
    to_fire, new_state = bb.decide_notifications(
        events, state or {}, now.astimezone(),
        quiet=bb.QUIET_HOURS, cooldown_h=bb.NOTIFY_COOLDOWN_H)
    for e in to_fire:
        publish(e["title"], e["message"], e.get("priority", 3), e.get("tags"))
    return new_state
