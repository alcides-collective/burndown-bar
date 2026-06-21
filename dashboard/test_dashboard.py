"""Tests for the dashboard's pure logic (stdlib runner, no pytest).

    python3 test_dashboard.py
"""
import datetime as dt
import json
import os
import tempfile

import incidents
import notify
import projections
import tokens

UTC = dt.timezone.utc
def T(s): return dt.datetime.fromisoformat(s)


# ── tokens.parse_line / accumulate / scan ──────────────────────────────────

def _line(mid, model, inp, out, day="2026-06-20T12:00:00.000Z", cwd="/Users/x/proj"):
    return json.dumps({"type": "assistant", "timestamp": day, "cwd": cwd,
                       "message": {"id": mid, "model": model,
                                   "usage": {"input_tokens": inp, "output_tokens": out,
                                             "cache_creation_input_tokens": 0,
                                             "cache_read_input_tokens": 5}}})

def test_parse_line_extracts_usage():
    r = tokens.parse_line(_line("m1", "claude-opus-4-8", 100, 20))
    assert r["id"] == "m1" and r["model"] == "claude-opus-4-8"
    assert r["in"] == 100 and r["out"] == 20 and r["cr"] == 5
    assert r["project"] == "proj" and r["day"]  # local date

def test_parse_line_skips_synthetic_and_nonassistant():
    assert tokens.parse_line(_line("m", "<synthetic>", 1, 1)) is None
    assert tokens.parse_line(json.dumps({"type": "user", "message": {}})) is None
    assert tokens.parse_line("not json") is None

def test_accumulate_rolls_up_day_project_session():
    st = {}
    r = tokens.parse_line(_line("m1", "claude-opus-4-8", 100, 20))
    tokens.accumulate(st, r, "sess-1")
    day = next(iter(st["daily"].values()))["claude-opus-4-8"]
    assert day["in"] == 100 and day["msgs"] == 1
    assert st["projects"]["proj"]["claude-opus-4-8"]["out"] == 20
    assert st["sessions"]["sess-1"]["in"] == 100

def test_scan_dedups_repeated_message_id_and_is_incremental():
    d = tempfile.mkdtemp()
    proj = os.path.join(d, "-Users-x-proj"); os.makedirs(proj)
    f = os.path.join(proj, "sess.jsonl")
    # same message id written twice (split message) -> counts once
    with open(f, "w") as fh:
        fh.write(_line("dup", "claude-opus-4-8", 100, 20) + "\n")
        fh.write(_line("dup", "claude-opus-4-8", 100, 20) + "\n")
        fh.write(_line("two", "claude-opus-4-8", 5, 5) + "\n")
    tf = os.path.join(d, "tok.json")
    os.environ["BURNDOWN_TOKEN_FILE"] = tf
    import importlib; importlib.reload(tokens)
    st = tokens.scan(root=d)
    msgs = sum(m["msgs"] for m in tokens.summarize(st)["by_model"])
    assert msgs == 2  # 'dup' once + 'two'
    # append a new line -> incremental picks up only the new one
    with open(f, "a") as fh:
        fh.write(_line("three", "claude-opus-4-8", 1, 1) + "\n")
    st = tokens.scan(root=d)
    assert sum(m["msgs"] for m in tokens.summarize(st)["by_model"]) == 3
    del os.environ["BURNDOWN_TOKEN_FILE"]; importlib.reload(tokens)


# ── incidents lifecycle ────────────────────────────────────────────────────

def test_incident_surge_opens_tracks_peak_and_closes():
    s = {}
    s = incidents.update(s, {"surge": {"rate": 10, "factor": 8, "runway_h": 2}, "or_balance": 20}, T("2026-06-20T00:00:00+00:00"))
    s = incidents.update(s, {"surge": {"rate": 40, "factor": 60, "runway_h": 0.3}, "or_balance": 17}, T("2026-06-20T00:10:00+00:00"))
    openi = [e for e in s["log"] if e["type"] == "surge"][0]
    assert openi["open"] and openi["peak_rate"] == 40 and openi["peak_factor"] == 60
    assert openi["burned"] == 3.0
    s = incidents.update(s, {"or_balance": 17}, T("2026-06-20T00:20:00+00:00"))  # surge gone
    closed = [e for e in s["log"] if e["type"] == "surge"][0]
    assert not closed["open"]
    api = incidents.for_api(s, T("2026-06-20T00:30:00+00:00"))
    assert api[0]["duration_h"] is not None

def test_incident_limit_events_dedup():
    ev = {"label": "Weekly limit", "at": "2026-06-20T11:00:00+00:00", "kind": "reset_early"}
    s = incidents.update({}, {"limit_events": [ev]}, T("2026-06-20T12:00:00+00:00"))
    s = incidents.update(s, {"limit_events": [ev]}, T("2026-06-20T12:05:00+00:00"))
    assert len([e for e in s["log"] if e["type"] == "limit_change"]) == 1

def test_incident_stale_lifecycle():
    s = incidents.update({}, {"stale": True, "stale_age_h": 1.0}, T("2026-06-20T02:00:00+00:00"))
    assert [e for e in s["log"] if e["type"] == "stale"][0]["open"]
    s = incidents.update(s, {"stale": False}, T("2026-06-20T03:00:00+00:00"))
    assert not [e for e in s["log"] if e["type"] == "stale"][0]["open"]


# ── projections ────────────────────────────────────────────────────────────

def test_linfit_recovers_slope():
    slope, intercept = projections.linfit([0, 1, 2, 3], [1, 3, 5, 7])
    assert abs(slope - 2) < 1e-9 and abs(intercept - 1) < 1e-9

def test_daily_or_spend_groups_by_local_day():
    arch = [{"h": "2026-06-20T10:00:00+00:00", "or_rate": 0.5},
            {"h": "2026-06-20T11:00:00+00:00", "or_rate": 0.7}]
    d = projections.daily_or_spend(arch)
    assert round(sum(d.values()), 2) == 1.2

def test_week_over_week_claude_and_or():
    now = T("2026-06-20T12:00:00+00:00")
    td = [{"day": "2026-06-18", "in": 100, "out": 50},   # this week
          {"day": "2026-06-10", "in": 40, "out": 10}]    # last week
    ord_ = {"2026-06-18": 8.0, "2026-06-10": 4.0}
    wow = projections.week_over_week(td, ord_, now)
    assert wow["claude_tokens"]["this"] == 150 and wow["claude_tokens"]["last"] == 50
    assert wow["openrouter"]["this"] == 8.0 and wow["openrouter"]["delta_pct"] == 100

def test_month_or_projection_extrapolates():
    now = T("2026-06-10T12:00:00+00:00")
    ord_ = {f"2026-06-0{i}": 2.0 for i in range(1, 10)}  # 9 days @ $2
    m = projections.month_or_projection(ord_, now)
    assert m["days_left"] == 20 and m["rate_now"] > 0
    assert m["projected"] > m["mtd"]


# ── notify.build_alerts ────────────────────────────────────────────────────

def test_build_alerts_surge_is_urgent():
    p = {"openrouter": {"ok": True, "surge": {"rate": 40, "factor": 60, "runway_h": 0.3}},
         "claude": {}}
    ev = notify.build_alerts(p)
    surge = [e for e in ev if e["key"] == "or-surge"][0]
    assert surge["urgent"] and surge["priority"] == 5

def test_build_alerts_stale_and_exhausted():
    p = {"stale": True, "stale_age_h": 2.0,
         "claude": {"weekly_status": "exhausted", "windows": {"seven_day": {"data": {"used": 100}}}},
         "openrouter": {"ok": False}}
    keys = {e["key"] for e in notify.build_alerts(p)}
    assert "stale-cache" in keys and "claude-weekly-exhausted" in keys

def test_build_alerts_quiet_when_nothing_wrong():
    p = {"claude": {"weekly_status": "ok", "trend": {"cls": {"label": "steady"}},
                    "windows": {"seven_day": {"data": {"used": 30}}}},
         "openrouter": {"ok": True, "depleted": False, "dry_in_h": 200}}
    assert notify.build_alerts(p) == []


if __name__ == "__main__":
    import traceback
    tests = sorted((n, f) for n, f in globals().items() if n.startswith("test_") and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn(); print(f"  ok   {name}")
        except Exception:
            failed += 1; print(f"  FAIL {name}"); traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
