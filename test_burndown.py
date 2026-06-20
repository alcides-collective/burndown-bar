"""Tests for burndown-bar pure logic.

The plugin lives in a SwiftBar-named file (``burndown-bar.5m.py``) that isn't a
valid module name, so we load it by path with importlib. main() is guarded by
``if __name__ == "__main__"`` so importing here doesn't fire the menu render.
"""
import datetime as dt
import importlib.util
import os

_PATH = os.path.join(os.path.dirname(__file__), "burndown-bar.5m.py")
_spec = importlib.util.spec_from_file_location("burndown", _PATH)
bb = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bb)


def T(s):
    """Shorthand: ISO string -> aware UTC datetime."""
    return dt.datetime.fromisoformat(s)


# ── rates_from_snapshots ──────────────────────────────────────────────────

def test_rate_from_monotonic_snapshots():
    snaps = [
        ["2026-06-20T10:00:00+00:00", 10.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 13.0, "2026-06-25T00:00:00+00:00"],
    ]
    rates = bb.rates_from_snapshots(snaps)
    assert rates == [(T("2026-06-20T11:00:00+00:00"), 3.0)]


def test_rate_skips_interval_that_straddles_a_reset():
    # resets_at changes and utilization drops: the window reset mid-interval,
    # so we can't attribute burn to it. That one interval is dropped; the
    # clean intervals on either side survive.
    snaps = [
        ["2026-06-20T10:00:00+00:00", 90.0, "2026-06-20T12:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 95.0, "2026-06-20T12:00:00+00:00"],
        ["2026-06-20T13:00:00+00:00", 4.0, "2026-06-20T17:00:00+00:00"],
        ["2026-06-20T14:00:00+00:00", 6.0, "2026-06-20T17:00:00+00:00"],
    ]
    rates = bb.rates_from_snapshots(snaps)
    assert rates == [
        (T("2026-06-20T11:00:00+00:00"), 5.0),
        (T("2026-06-20T14:00:00+00:00"), 2.0),
    ]


def test_rate_skips_zero_and_negative_intervals():
    # duplicate timestamp -> div-by-zero guard; clean pair still emitted.
    snaps = [
        ["2026-06-20T10:00:00+00:00", 10.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T10:00:00+00:00", 10.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T12:00:00+00:00", 14.0, "2026-06-25T00:00:00+00:00"],
    ]
    rates = bb.rates_from_snapshots(snaps)
    assert rates == [(T("2026-06-20T12:00:00+00:00"), 2.0)]


# ── windowed_rate (reset-aware, duration-weighted) ─────────────────────────

def test_windowed_rate_duration_weighted():
    snaps = [
        ["2026-06-20T10:00:00+00:00", 10.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 13.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T12:00:00+00:00", 16.0, "2026-06-25T00:00:00+00:00"],
    ]
    # 6% of clean burn over 2 clean hours = 3%/h
    assert bb.windowed_rate(snaps, T("2026-06-20T12:00:00+00:00"), 24.0) == 3.0


def test_windowed_rate_excludes_intervals_before_window():
    snaps = [
        ["2026-06-20T08:00:00+00:00", 0.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T09:00:00+00:00", 50.0, "2026-06-25T00:00:00+00:00"],  # old burst
        ["2026-06-20T10:00:00+00:00", 50.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 52.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T12:00:00+00:00", 54.0, "2026-06-25T00:00:00+00:00"],
    ]
    # trailing 2h (cutoff 10:00): the burst ends at/by the cutoff and is
    # excluded; only the two 2%/h intervals after 10:00 count.
    assert bb.windowed_rate(snaps, T("2026-06-20T12:00:00+00:00"), 2.0) == 2.0


def test_windowed_rate_none_when_no_clean_history():
    snaps = [["2026-06-20T12:00:00+00:00", 5.0, "2026-06-25T00:00:00+00:00"]]
    assert bb.windowed_rate(snaps, T("2026-06-20T12:00:00+00:00"), 24.0) is None


# ── spend_rate_over (OpenRouter cumulative samples) ────────────────────────

def test_spend_rate_over_window():
    samples = [
        ["2026-06-20T08:00:00+00:00", 0.0],
        ["2026-06-20T10:00:00+00:00", 1.0],
        ["2026-06-20T12:00:00+00:00", 3.0],
    ]
    # trailing 2h: $1 -> $3 over 2h = $1.00/h
    assert bb.spend_rate_over(samples, T("2026-06-20T12:00:00+00:00"), 2.0) == 1.0
    # trailing 24h: anchors at earliest sample, $0 -> $3 over 4h = $0.75/h
    assert bb.spend_rate_over(samples, T("2026-06-20T12:00:00+00:00"), 24.0) == 0.75


def test_spend_rate_over_none_without_two_points():
    samples = [["2026-06-20T12:00:00+00:00", 3.0]]
    assert bb.spend_rate_over(samples, T("2026-06-20T12:00:00+00:00"), 24.0) is None


# ── baselines (weekday/hour buckets, shrinkage pooling, variance) ──────────

def test_baseline_query_mean_for_populated_bucket():
    store = {}
    for _ in range(12):
        bb.baseline_update(store, weekday=4, hour=15, value=2.0)
    q = bb.baseline_query(store, weekday=4, hour=15)
    assert abs(q["mean"] - 2.0) < 1e-9
    assert q["n"] == 12


def test_baseline_thin_bucket_shrinks_toward_hour_pool():
    store = {}
    # hour-of-day 15 across the other weekdays averages ~1.0
    for wd in range(7):
        if wd == 4:
            continue
        for _ in range(8):
            bb.baseline_update(store, weekday=wd, hour=15, value=1.0)
    # Friday 15:00 has a single fluky high reading
    bb.baseline_update(store, weekday=4, hour=15, value=9.0)
    q = bb.baseline_query(store, weekday=4, hour=15)
    # a thin cell is pulled hard toward the ~1.0 pool (from 9 down well under
    # 3), but isn't ignored entirely (stays above the pure-pool value).
    assert 1.0 < q["mean"] < 3.0


def test_baseline_std_available_from_pool_for_thin_exact_bucket():
    store = {}
    vals = [1.0, 2.0, 3.0, 4.0, 5.0]
    for wd in range(7):
        for v in vals:
            bb.baseline_update(store, weekday=wd, hour=9, value=v)
    # brand-new bucket: Friday 9am has no own history beyond the pool
    q = bb.baseline_query(store, weekday=4, hour=9)
    assert q["std"] is not None and q["std"] > 0


def test_baseline_std_none_when_globally_thin():
    store = {}
    bb.baseline_update(store, weekday=1, hour=10, value=3.0)
    q = bb.baseline_query(store, weekday=1, hour=10)
    assert q["std"] is None


# ── classify_trend (z-score vs baseline) ───────────────────────────────────

def test_classify_steady_within_band():
    c = bb.classify_trend(2.5, {"mean": 2.0, "std": 1.0, "n": 20})
    assert c["dir"] == 0 and c["label"] == "steady"


def test_classify_up_and_sharply_up():
    up = bb.classify_trend(4.0, {"mean": 2.0, "std": 1.0, "n": 20})
    assert up["dir"] == 1 and up["label"] == "up"
    sharp = bb.classify_trend(6.0, {"mean": 2.0, "std": 1.0, "n": 20})
    assert sharp["dir"] == 1 and sharp["label"] == "sharply up"


def test_classify_down_and_sharply_down():
    down = bb.classify_trend(0.0, {"mean": 2.0, "std": 1.0, "n": 20})
    assert down["dir"] == -1 and down["label"] == "down"
    sharp = bb.classify_trend(-4.0, {"mean": 2.0, "std": 1.0, "n": 20})
    assert sharp["dir"] == -1 and sharp["label"] == "sharply down"


def test_classify_building_when_no_std():
    c = bb.classify_trend(5.0, {"mean": 1.0, "std": None, "n": 1})
    assert c["dir"] == 0 and c["label"] == "building"


def test_classify_unknown_when_no_current():
    c = bb.classify_trend(None, {"mean": 1.0, "std": 1.0, "n": 20})
    assert c["label"] == "unknown"


def test_classify_relative_floor_suppresses_tiny_std_noise():
    # std is ~0 (very regular), but a small move shouldn't read as a huge
    # z-score: a 5% bump around a mean of 10 stays steady.
    c = bb.classify_trend(10.5, {"mean": 10.0, "std": 0.0, "n": 20})
    assert c["label"] == "steady"


# ── compose_summary (two-sentence, baseline-relative, personality) ─────────

def _sentence_count(s):
    return sum(s.count(c) for c in ".!")


def test_summary_heavier_than_typical_but_within_pace():
    claude = {"status": "ok", "dir": 1, "weekday": "Friday",
              "pace": 0.6, "used": 42.0, "runway_h": None, "reset_in_h": 90.0}
    s = bb.compose_summary(claude, None, variant=0)
    assert "Friday" in s
    assert "typical" in s.lower()
    assert _sentence_count(s) >= 2


def test_summary_dry_warning_mentions_runway():
    claude = {"status": "dry", "dir": 1, "weekday": "Monday",
              "pace": 1.8, "used": 88.0, "runway_h": 30.0, "reset_in_h": 60.0}
    s = bb.compose_summary(claude, None, variant=0)
    assert "dry" in s.lower()
    assert "30" in s or "1.2" in s  # 30h shows as "30 h" (fmt_dur < 48)


def test_summary_building_history():
    claude = {"status": "building", "dir": 0, "weekday": "Tuesday",
              "pace": 0.0, "used": 5.0, "runway_h": None, "reset_in_h": 100.0}
    s = bb.compose_summary(claude, None, variant=0)
    assert any(w in s.lower() for w in ["rhythm", "learning", "history", "few days"])


def test_summary_includes_openrouter_balance_and_trend():
    claude = {"status": "ok", "dir": 0, "weekday": "Wednesday",
              "pace": 0.5, "used": 30.0, "runway_h": None, "reset_in_h": 80.0}
    openrouter = {"dir": 1, "balance": 24.0, "runway_h": 5 * 24.0,
                  "depleted": False, "idle": False, "building": False}
    s = bb.compose_summary(claude, openrouter, variant=0)
    assert "OpenRouter" in s or "$24" in s
    assert "$24" in s


def test_summary_no_openrouter_falls_back_without_mentioning_it():
    claude = {"status": "ok", "dir": 0, "weekday": "Sunday",
              "pace": 0.3, "used": 12.0, "runway_h": None, "reset_in_h": 48.0}
    s = bb.compose_summary(claude, None, variant=0)
    assert "OpenRouter" not in s
    assert _sentence_count(s) >= 2


def test_summary_is_deterministic_and_variant_changes_wording():
    claude = {"status": "ok", "dir": 1, "weekday": "Friday",
              "pace": 0.6, "used": 42.0, "runway_h": None, "reset_in_h": 90.0}
    a0 = bb.compose_summary(claude, None, variant=0)
    a0_again = bb.compose_summary(claude, None, variant=0)
    a1 = bb.compose_summary(claude, None, variant=1)
    assert a0 == a0_again          # deterministic
    assert a0 != a1                # personality rotates with variant


# ── title glyph (multi-signal: weekly + 5h + OpenRouter) ───────────────────

def test_trend_arrow():
    assert bb.trend_arrow(1) == "↑"
    assert bb.trend_arrow(-1) == "↓"
    assert bb.trend_arrow(0) == ""
    assert bb.trend_arrow(None) == ""


def test_title_weekly_only_steady():
    weekly = {"emoji": "🟢", "core": "0.62× · proj 78%", "dir": 0}
    assert bb.compose_title(weekly, None, None) == "🟢 0.62× · proj 78%"


def test_title_multi_signal():
    weekly = {"emoji": "🟢", "core": "0.62× · proj 78%", "dir": 1}
    five = {"warn": False, "dir": -1}
    # OpenRouter dir is the spend trend (↑ = spending more than typical),
    # consistent with Claude's burn arrow; ⚠ flags short runway.
    openrouter = {"depleted": False, "balance": 24.0, "dir": 1, "warn": True}
    title = bb.compose_title(weekly, five, openrouter)
    assert "🟢 0.62× · proj 78%↑" in title
    assert "5h↓" in title
    assert "OR $24.00↑⚠" in title


def test_title_five_hour_warn_shows_even_when_trend_flat():
    weekly = {"emoji": "🔥", "core": "1.50× → dry in 3.0 h", "dir": 1}
    five = {"warn": True, "dir": 0}
    title = bb.compose_title(weekly, five, None)
    assert "5h⚠" in title


def test_title_openrouter_depleted():
    weekly = {"emoji": "🟢", "core": "0.62×", "dir": 0}
    openrouter = {"depleted": True, "balance": 0.0, "dir": 0, "warn": False}
    title = bb.compose_title(weekly, None, openrouter)
    assert "· OR ⛔" in title


# ── notification state machine (dedup, cooldown, quiet hours) ──────────────

def N(hour, minute=0):
    return dt.datetime(2026, 6, 20, hour, minute)  # naive local-ish for tests


def test_notify_fresh_event_fires_and_records_state():
    ev = [{"key": "claude-hot", "title": "t", "body": "b"}]
    fire, state = bb.decide_notifications(ev, {}, N(12), quiet=(22, 8), cooldown_h=6.0)
    assert [e["key"] for e in fire] == ["claude-hot"]
    assert "claude-hot" in state


def test_notify_suppressed_within_cooldown():
    state = {"claude-hot": N(12).isoformat()}
    ev = [{"key": "claude-hot", "title": "t", "body": "b"}]
    fire, _ = bb.decide_notifications(ev, state, N(15), quiet=(22, 8), cooldown_h=6.0)
    assert fire == []


def test_notify_fires_again_after_cooldown():
    state = {"claude-hot": N(2).isoformat()}
    ev = [{"key": "claude-hot", "title": "t", "body": "b"}]
    fire, _ = bb.decide_notifications(ev, state, N(12), quiet=(22, 8), cooldown_h=6.0)
    assert [e["key"] for e in fire] == ["claude-hot"]


def test_notify_dedup_same_key_in_one_batch():
    ev = [
        {"key": "or-spike", "title": "t", "body": "b1"},
        {"key": "or-spike", "title": "t", "body": "b2"},
    ]
    fire, _ = bb.decide_notifications(ev, {}, N(12), quiet=(22, 8), cooldown_h=6.0)
    assert len(fire) == 1


def test_notify_quiet_hours_suppress_all():
    ev = [{"key": "claude-hot", "title": "t", "body": "b"}]
    fire, state = bb.decide_notifications(ev, {}, N(23), quiet=(22, 8), cooldown_h=6.0)
    assert fire == []
    assert "claude-hot" not in state  # not consumed; can fire after quiet hours


def test_notify_quiet_hours_wraparound_midnight():
    assert bb.in_quiet_hours(23, (22, 8)) is True
    assert bb.in_quiet_hours(3, (22, 8)) is True
    assert bb.in_quiet_hours(12, (22, 8)) is False
    assert bb.in_quiet_hours(8, (22, 8)) is False  # end is exclusive
    assert bb.in_quiet_hours(5, (0, 0)) is False   # disabled


# ── baseline_means + rel_pct (for the baseline-relative data rows) ─────────

def test_baseline_means_pools():
    store = {}
    # Friday(4) 15:00 = 5.0, other Friday hours = 1.0, other days = 3.0
    bb.baseline_update(store, 4, 15, 5.0)
    bb.baseline_update(store, 4, 9, 1.0)
    bb.baseline_update(store, 1, 10, 3.0)
    m = bb.baseline_means(store, weekday=4, hour=15)
    assert abs(m["hour"] - 5.0) < 1e-9          # exact weekday+hour cell
    assert abs(m["weekday"] - 3.0) < 1e-9        # all Friday cells: (5+1)/2
    assert abs(m["overall"] - 3.0) < 1e-9        # all cells: (5+1+3)/3


def test_baseline_means_none_when_empty():
    m = bb.baseline_means({}, weekday=2, hour=11)
    assert m == {"hour": None, "weekday": None, "overall": None}


def test_rel_pct_formatting():
    assert bb.rel_pct(1.4, 1.0) == "+40%"
    assert bb.rel_pct(0.92, 1.0) == "-8%"
    assert bb.rel_pct(1.0, 1.0) == "+0%"
    assert bb.rel_pct(2.0, None) is None
    assert bb.rel_pct(None, 1.0) is None
    assert bb.rel_pct(1.0, 0.0) is None


# ── detect_limit_events (unexpected resets / quota bumps) ──────────────────

def test_detect_early_reset():
    # resets_at jumps to a new time while the OLD reset was still in the future
    snaps = [
        ["2026-06-20T10:00:00+00:00", 90.0, "2026-06-20T14:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 5.0, "2026-06-27T11:00:00+00:00"],
    ]
    ev = bb.detect_limit_events(snaps)
    assert ev == [{"at": "2026-06-20T11:00:00+00:00", "kind": "reset_early"}]


def test_detect_scheduled_reset_is_not_flagged():
    # the old reset time had already arrived, so this is a normal rollover
    snaps = [
        ["2026-06-20T10:00:00+00:00", 90.0, "2026-06-20T11:00:00+00:00"],
        ["2026-06-20T12:00:00+00:00", 5.0, "2026-06-27T12:00:00+00:00"],
    ]
    assert bb.detect_limit_events(snaps) == []


def test_detect_limit_raised():
    # utilization drops sharply with resets_at unchanged -> bigger denominator
    snaps = [
        ["2026-06-20T10:00:00+00:00", 80.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 40.0, "2026-06-25T00:00:00+00:00"],
    ]
    ev = bb.detect_limit_events(snaps)
    assert ev == [{"at": "2026-06-20T11:00:00+00:00", "kind": "limit_raised"}]


def test_detect_ignores_noise_and_normal_growth():
    snaps = [
        ["2026-06-20T10:00:00+00:00", 80.0, "2026-06-25T00:00:00+00:00"],
        ["2026-06-20T11:00:00+00:00", 79.7, "2026-06-25T00:00:00+00:00"],  # rounding
        ["2026-06-20T12:00:00+00:00", 83.0, "2026-06-25T00:00:00+00:00"],  # growth
    ]
    assert bb.detect_limit_events(snaps) == []


# ── is_anomalous + commit_baseline exclusion ───────────────────────────────

def test_is_anomalous():
    bl = {"mean": 1.0, "std": 0.5, "n": 30}
    assert bb.is_anomalous(5.0, bl) is True
    assert bb.is_anomalous(1.2, bl) is False
    assert bb.is_anomalous(5.0, {"mean": 1.0, "std": None, "n": 1}) is False
    assert bb.is_anomalous(None, bl) is False


def _seed_baseline(history, series, mean, n_per_cell=20):
    store = history.setdefault("baselines", {}).setdefault(series, {})
    for wd in range(7):
        for hr in range(24):
            # tight spread around `mean` so std is small and available
            for v in (mean - 0.2, mean, mean + 0.2):
                bb.baseline_update(store, wd, hr, v)


def test_commit_baseline_skips_anomalous_reading():
    history = {}
    _seed_baseline(history, "s", mean=1.0)
    store = history["baselines"]["s"]
    before = bb.baseline_query(store, 5, 15)["n"]
    bb.commit_baseline(history, "s", 12.0, N(15), suppress=False)
    assert bb.baseline_query(store, 5, 15)["n"] == before  # not folded in


def test_commit_baseline_records_normal_reading():
    history = {}
    _seed_baseline(history, "s", mean=1.0)
    store = history["baselines"]["s"]
    before = bb.baseline_query(store, 5, 16)["n"]
    bb.commit_baseline(history, "s", 1.1, N(16), suppress=False)
    assert bb.baseline_query(store, 5, 16)["n"] == before + 1


def test_commit_baseline_suppressed_during_event():
    history = {}
    _seed_baseline(history, "s", mean=1.0)
    store = history["baselines"]["s"]
    before = bb.baseline_query(store, 5, 17)["n"]
    bb.commit_baseline(history, "s", 1.1, N(17), suppress=True)
    assert bb.baseline_query(store, 5, 17)["n"] == before  # event window: excluded


def test_commit_baseline_consumes_hour_slot():
    # a skipped (anomalous) reading still consumes the hour, so a later normal
    # reading in the same clock hour isn't folded in either.
    history = {}
    _seed_baseline(history, "s", mean=1.0)
    store = history["baselines"]["s"]
    before = bb.baseline_query(store, 5, 18)["n"]
    bb.commit_baseline(history, "s", 12.0, N(18, 5), suppress=False)   # anomalous
    bb.commit_baseline(history, "s", 1.1, N(18, 40), suppress=False)   # same hour
    assert bb.baseline_query(store, 5, 18)["n"] == before


# ── record_limit_events (log + dedup) and event_active (suppression) ───────

def test_record_limit_events_dedup():
    history = {}
    ev = [{"at": "2026-06-20T11:00:00+00:00", "kind": "reset_early"}]
    new1 = bb.record_limit_events(history, "Weekly limit", ev)
    assert len(new1) == 1 and new1[0]["label"] == "Weekly limit"
    new2 = bb.record_limit_events(history, "Weekly limit", ev)  # same event again
    assert new2 == []
    assert len(history["limit_events"]) == 1


def test_record_limit_events_adds_distinct():
    history = {}
    bb.record_limit_events(history, "Weekly limit",
                           [{"at": "2026-06-20T11:00:00+00:00", "kind": "reset_early"}])
    new = bb.record_limit_events(history, "Weekly Opus",
                                 [{"at": "2026-06-20T11:00:00+00:00", "kind": "limit_raised"}])
    assert len(new) == 1
    assert len(history["limit_events"]) == 2


def test_event_active_within_window():
    history = {}
    bb.record_limit_events(history, "Weekly limit",
                           [{"at": "2026-06-20T02:00:00+00:00", "kind": "reset_early"}])
    now = T("2026-06-20T12:00:00+00:00")
    assert bb.event_active(history, "Weekly limit", now, 48.0) is True
    assert bb.event_active(history, "Weekly limit", now, 6.0) is False   # 10h ago
    assert bb.event_active(history, "Weekly Opus", now, 48.0) is False   # other series


def test_pending_limit_notifications_fire_once():
    history = {"limit_events": []}
    ev = [{"label": "Weekly limit", "at": "2026-06-20T11:00:00+00:00", "kind": "reset_early"}]
    fire = bb.pending_limit_notifications(history, ev, N(12))
    assert len(fire) == 1
    again = bb.pending_limit_notifications(history, ev, N(12))
    assert again == []  # already notified, never again


def test_pending_limit_notifications_muted_in_quiet_hours():
    history = {"limit_events": []}
    ev = [{"label": "Weekly limit", "at": "2026-06-20T11:00:00+00:00", "kind": "reset_early"}]
    fire = bb.pending_limit_notifications(history, ev, N(23))
    assert fire == []
    assert "limit_notified" not in history or not history["limit_notified"]  # retry later


# ── same_reset: resets_at jitter tolerance (the bug fix) ───────────────────
# The usage API recomputes resets_at as (now + window) on every poll, so the
# stored value carries sub-second-to-second jitter — which can straddle a
# minute/hour boundary (10:59:59.9 vs 11:00:00.1) and look like a fresh reset.

def test_same_reset_collapses_subsecond_jitter():
    assert bb.same_reset("2026-06-27T10:59:59.892718+00:00",
                         "2026-06-27T11:00:00.763660+00:00") is True


def test_same_reset_distinguishes_real_rollover():
    assert bb.same_reset("2026-06-20T11:00:00+00:00",
                         "2026-06-27T11:00:00+00:00") is False


def test_windowed_rate_survives_resets_at_jitter():
    # same window throughout, but resets_at jitters across the minute boundary
    # each poll. Pre-fix every interval is dropped as a "reset" -> None.
    snaps = [
        ["2026-06-20T10:00:00+00:00", 10.0, "2026-06-27T10:59:59.900000+00:00"],
        ["2026-06-20T11:00:00+00:00", 13.0, "2026-06-27T11:00:00.100000+00:00"],
        ["2026-06-20T12:00:00+00:00", 16.0, "2026-06-27T10:59:59.800000+00:00"],
    ]
    assert bb.windowed_rate(snaps, T("2026-06-20T12:00:00+00:00"), 24.0) == 3.0


def test_rates_from_snapshots_survive_jitter():
    snaps = [
        ["2026-06-20T10:00:00+00:00", 10.0, "2026-06-27T10:59:59.900000+00:00"],
        ["2026-06-20T11:00:00+00:00", 13.0, "2026-06-27T11:00:00.100000+00:00"],
    ]
    assert bb.rates_from_snapshots(snaps) == [(T("2026-06-20T11:00:00+00:00"), 3.0)]


def test_detect_limit_events_ignores_resets_at_jitter():
    # jitter must not masquerade as an early reset
    snaps = [
        ["2026-06-20T11:00:00+00:00", 18.0, "2026-06-27T10:59:59.900000+00:00"],
        ["2026-06-20T11:05:00+00:00", 19.0, "2026-06-27T11:00:00.100000+00:00"],
    ]
    assert bb.detect_limit_events(snaps) == []


# ── smart_projection: baseline-aware integral to reset ─────────────────────
# Instead of holding the whole-window average rate flat (which a front-loaded
# spike inflates), walk each remaining hour and add that (weekday, hour) cell's
# learned typical rate. Falls back to a supplied recent rate for thin cells.

def _flat_baseline(rate, n_per_cell=10):
    store = {}
    for wd in range(7):
        for hr in range(24):
            for _ in range(n_per_cell):
                bb.baseline_update(store, wd, hr, rate)
    return store


def test_smart_projection_uses_baseline_rate():
    store = _flat_baseline(0.5)  # uniform 0.5%/h learned everywhere
    now = T("2026-06-20T00:00:00+00:00")
    reset = T("2026-06-22T00:00:00+00:00")  # 48h left
    s = bb.smart_projection(50.0, now, reset, store, fallback_rate=None)
    assert abs(s["projected"] - 74.0) < 0.5   # 50 + 0.5*48
    assert s["used_baseline"] is True
    assert s["dry_at"] is None


def test_smart_projection_falls_back_when_no_baseline():
    s = bb.smart_projection(20.0, T("2026-06-20T00:00:00+00:00"),
                            T("2026-06-20T10:00:00+00:00"), {}, fallback_rate=1.0)
    assert abs(s["projected"] - 30.0) < 0.2   # 20 + 1.0*10
    assert s["used_baseline"] is False


def test_smart_projection_none_without_any_rate_source():
    s = bb.smart_projection(20.0, T("2026-06-20T00:00:00+00:00"),
                            T("2026-06-20T10:00:00+00:00"), {}, fallback_rate=None)
    assert s is None


def test_smart_projection_reports_dry_when_it_crosses_100():
    store = _flat_baseline(2.0)  # 2%/h
    now = T("2026-06-20T00:00:00+00:00")
    reset = T("2026-06-25T00:00:00+00:00")  # 120h
    s = bb.smart_projection(80.0, now, reset, store, fallback_rate=None)
    assert s["projected"] > 100
    assert s["dry_at"] is not None
    dry_h = (s["dry_at"] - now).total_seconds() / 3600.0
    assert abs(dry_h - 10.0) < 1.0   # (100-80)/2 = 10h


def test_smart_projection_thin_baseline_does_not_poison_to_zero():
    # regression: a lone near-empty cell must not drag the whole projection to
    # ~0 via the shrink pool. With thin support we use the fallback rate.
    store = {}
    bb.baseline_update(store, weekday=5, hour=20, value=0.0)  # single reading
    now = T("2026-06-20T00:00:00+00:00")
    reset = T("2026-06-20T10:00:00+00:00")  # 10h
    s = bb.smart_projection(20.0, now, reset, store, fallback_rate=1.0)
    assert abs(s["projected"] - 30.0) < 0.5   # 20 + 1.0*10, NOT ~20
    assert s["used_baseline"] is False


def test_smart_projection_respects_diurnal_rhythm():
    # busy 08:00-20:00 (3%/h), quiet overnight (0%/h). Over a full day from
    # 08:00 that is 12*3 = 36%, far below holding the daytime 3%/h flat (72%).
    store = {}
    for wd in range(7):
        for hr in range(24):
            r = 3.0 if 8 <= hr < 20 else 0.0
            for _ in range(10):
                bb.baseline_update(store, wd, hr, r)
    now = T("2026-06-20T08:00:00+00:00")
    reset = T("2026-06-21T08:00:00+00:00")  # 24h
    s = bb.smart_projection(0.0, now, reset, store, fallback_rate=None)
    assert 30.0 < s["projected"] < 42.0


# ── title prefers smart projection; summary status follows it ──────────────

def test_title_prefers_smart_projection_over_spiky_pace():
    a = {"exhausted": False, "will_run_out": True, "pace": 4.72,
         "projected": 474.0, "dry_at": T("2026-06-21T00:00:00+00:00"),
         "reset": T("2026-06-27T11:00:00+00:00")}
    smart = {"projected": 95.0, "used_baseline": True, "dry_at": None}
    title = bb.title_for(a, smart)
    assert "95%" in title
    assert "4.72" not in title          # the spiky pace no longer headlines
    assert title[0] in ("🟢", "🟡")


def test_title_smart_dry_shows_runway():
    a = {"exhausted": False, "will_run_out": True, "pace": 2.0,
         "projected": 200.0, "dry_at": None, "reset": T("2026-06-27T11:00:00+00:00")}
    smart = {"projected": 180.0, "used_baseline": True,
             "dry_at": bb.NOW + dt.timedelta(hours=50)}
    title = bb.title_for(a, smart)
    assert title.startswith("🔥") and "dry in" in title


def test_title_falls_back_to_naive_without_smart():
    a = {"exhausted": False, "will_run_out": False, "pace": 0.62,
         "projected": 78.0, "dry_at": None, "reset": T("2026-06-27T11:00:00+00:00")}
    title = bb.title_for(a, None)
    assert "0.62×" in title and "78%" in title


def test_summary_status_dry_from_smart_projection():
    weekly = {"exhausted": False, "will_run_out": False, "used": 20.0, "pace": 0.5}
    smart = {"projected": 130.0, "dry_at": T("2026-06-25T00:00:00+00:00")}
    assert bb.claude_summary_status(weekly, {"label": "steady"}, smart) == "dry"


def test_summary_status_ok_when_smart_fits_despite_spiky_pace():
    # naive will_run_out + pace 4.7 (spike), but the smart projection fits.
    weekly = {"exhausted": False, "will_run_out": True, "used": 20.0, "pace": 4.7}
    smart = {"projected": 60.0, "dry_at": None}
    assert bb.claude_summary_status(weekly, {"label": "steady"}, smart) == "ok"


def test_summary_status_unchanged_without_smart():
    weekly = {"exhausted": False, "will_run_out": True, "used": 20.0, "pace": 4.7,
              "dry_at": T("2026-06-21T00:00:00+00:00")}
    assert bb.claude_summary_status(weekly, {"label": "steady"}, None) == "dry"


# ── detect_spend_surge: OpenRouter runaway-spend guard ─────────────────────
# Baseline-free: compares the trailing-30min spend rate to the trailing-24h
# rate and checks the surge would drain the balance fast. No learned history
# needed, so it works the first time a bug strikes.

def _steady(now, hours, rate, step_min=5, start=100.0):
    """Cumulative-usage samples climbing at `rate` $/h up to `now`."""
    base = now - dt.timedelta(hours=hours)
    out, u, n = [], start, int(hours * 60 / step_min)
    for i in range(n + 1):
        out.append([(base + dt.timedelta(minutes=step_min * i)).isoformat(), u])
        u += rate * (step_min / 60.0)
    return out


def test_detect_spend_surge_fires_on_runaway():
    now = T("2026-06-20T12:00:00+00:00")
    samples = _steady(now, 24.0, rate=0.5)          # steady ~$0.5/h
    u = samples[-1][1]
    for j in range(1, 7):                            # last 30 min: ~$40/h
        samples.append([(now - dt.timedelta(minutes=30) + dt.timedelta(minutes=5 * j)).isoformat(),
                         u + 3.3 * j])
    s = bb.detect_spend_surge(samples, balance=50.0, now=now)
    assert s is not None
    assert s["factor"] >= 6.0 and s["runway_h"] <= 8.0


def test_detect_spend_surge_quiet_on_steady_spend():
    now = T("2026-06-20T12:00:00+00:00")
    assert bb.detect_spend_surge(_steady(now, 24.0, rate=0.5), balance=50.0, now=now) is None


def test_detect_spend_surge_ignores_brief_blip():
    # one $1 blip inside the 30-min window averages to ~$2.4/h (<6x) -> quiet
    now = T("2026-06-20T12:00:00+00:00")
    samples = _steady(now, 23.5, rate=0.5)
    u = samples[-1][1]
    t0 = now - dt.timedelta(minutes=30)
    for m, du in [(0, 0), (5, 1.0), (10, 1.04), (15, 1.08), (20, 1.12), (25, 1.16), (30, 1.20)]:
        samples.append([(t0 + dt.timedelta(minutes=m)).isoformat(), u + du])
    assert bb.detect_spend_surge(samples, balance=50.0, now=now) is None


def test_detect_spend_surge_respects_min_rate_floor():
    # big ratio but pennies/h and a tiny balance: still below the $/h floor
    now = T("2026-06-20T12:00:00+00:00")
    samples = _steady(now, 24.0, rate=0.01)
    u = samples[-1][1]
    for j in range(1, 7):
        samples.append([(now - dt.timedelta(minutes=30) + dt.timedelta(minutes=5 * j)).isoformat(),
                         u + 0.01 * j])
    assert bb.detect_spend_surge(samples, balance=0.5, now=now) is None


def test_detect_spend_surge_none_without_history():
    now = T("2026-06-20T12:00:00+00:00")
    assert bb.detect_spend_surge([["2026-06-20T11:55:00+00:00", 100.0]], 50.0, now) is None


# ── notifications: urgent bypass + per-event cooldown ──────────────────────

def test_notify_urgent_event_bypasses_quiet_hours():
    ev = [{"key": "or-spend-surge", "urgent": True, "title": "t", "body": "b"}]
    fire, state = bb.decide_notifications(ev, {}, N(3), quiet=(22, 8), cooldown_h=6.0)
    assert [e["key"] for e in fire] == ["or-spend-surge"]
    assert "or-spend-surge" in state


def test_notify_nonurgent_still_muted_in_quiet_hours():
    ev = [{"key": "claude-hot", "title": "t", "body": "b"}]
    fire, _ = bb.decide_notifications(ev, {}, N(3), quiet=(22, 8), cooldown_h=6.0)
    assert fire == []


def test_notify_per_event_cooldown_overrides_default():
    state = {"or-spend-surge": N(3).isoformat()}
    ev = [{"key": "or-spend-surge", "urgent": True, "cooldown_h": 1.0, "title": "t", "body": "b"}]
    within = bb.decide_notifications(ev, state, N(3, 30), quiet=(22, 8), cooldown_h=6.0)[0]
    assert within == []                                   # 30 min < 1h cooldown
    after = bb.decide_notifications(ev, state, N(4, 13), quiet=(22, 8), cooldown_h=6.0)[0]
    assert [e["key"] for e in after] == ["or-spend-surge"]  # 73 min > 1h


def test_title_shows_surge_glyph():
    weekly = {"emoji": "🟢", "core": "proj 40%", "dir": 0}
    openrouter = {"depleted": False, "balance": 8.0, "dir": 1, "warn": True, "surge": True}
    title = bb.compose_title(weekly, None, openrouter)
    assert "🚨" in title and "OR" in title


# ── runner (stdlib only, no pytest) ───────────────────────────────────────

if __name__ == "__main__":
    import traceback

    tests = sorted((n, f) for n, f in globals().items()
                   if n.startswith("test_") and callable(f))
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  ok   {name}")
        except Exception:
            failed += 1
            print(f"  FAIL {name}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
