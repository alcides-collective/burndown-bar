"""Real token history from local Claude Code transcripts.

The OAuth usage endpoint only gives opaque %-of-limit windows. The transcripts
under ~/.claude/projects/*.jsonl carry the actual per-message `usage` (input,
output, cache create/read) and `model`, so we can build true token history,
broken down by day, model, project (from each line's cwd) and session.

Two correctness points the scanner handles:
  * One logical message is split across several jsonl lines with the *same*
    `message.id` and identical usage repeated — so we dedup by message id
    (a bounded recent set) to avoid double-counting (~2× otherwise).
  * Scanning is byte-offset incremental (transcripts are append-only), so each
    line is parsed exactly once across runs, and a partial trailing line is
    left for next time.
"""
import datetime as dt
import glob
import json
import os

PROJECTS_DIR = os.environ.get(
    "BURNDOWN_CLAUDE_PROJECTS", os.path.expanduser("~/.claude/projects"))
TOKEN_FILE = os.environ.get(
    "BURNDOWN_TOKEN_FILE",
    os.path.expanduser("~/.local/share/burndown-bar/token-archive.json"))
RETAIN_DAYS = int(os.environ.get("BURNDOWN_TOKEN_RETAIN_DAYS", "180"))
SEEN_CAP = 6000        # recent message ids kept to dedup across batch boundaries
MAX_SESSIONS = 400     # cap stored session rows
FIELDS = ("in", "out", "cc", "cr")


def parse_line(raw):
    """Extract a usage record from one transcript line, or None."""
    try:
        o = json.loads(raw)
    except Exception:
        return None
    if o.get("type") != "assistant":
        return None
    msg = o.get("message") or {}
    usage = msg.get("usage")
    model = msg.get("model")
    mid = msg.get("id")
    ts = o.get("timestamp")
    if not (usage and model and mid and ts) or model == "<synthetic>":
        return None
    try:
        day = dt.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone().date().isoformat()
    except Exception:
        return None
    cwd = o.get("cwd") or ""
    return {
        "id": mid, "day": day, "model": model,
        "project": os.path.basename(cwd) or "unknown",
        "in": int(usage.get("input_tokens") or 0),
        "out": int(usage.get("output_tokens") or 0),
        "cc": int(usage.get("cache_creation_input_tokens") or 0),
        "cr": int(usage.get("cache_read_input_tokens") or 0),
    }


def _add(bucket, rec):
    b = bucket
    for f in FIELDS:
        b[f] = b.get(f, 0) + rec[f]
    b["msgs"] = b.get("msgs", 0) + 1


def accumulate(state, rec, session):
    """Fold one (deduped) record into daily / project / session rollups."""
    day = state.setdefault("daily", {}).setdefault(rec["day"], {})
    _add(day.setdefault(rec["model"], {}), rec)
    proj = state.setdefault("projects", {}).setdefault(rec["project"], {})
    _add(proj.setdefault(rec["model"], {}), rec)
    sess = state.setdefault("sessions", {}).setdefault(
        session, {"project": rec["project"], "last": rec["day"]})
    sess["last"] = max(sess.get("last", ""), rec["day"])
    sess["project"] = rec["project"]
    _add(sess, rec)


def _load():
    try:
        with open(TOKEN_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state):
    try:
        os.makedirs(os.path.dirname(TOKEN_FILE), exist_ok=True)
        tmp = TOKEN_FILE + ".tmp"
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, TOKEN_FILE)
    except Exception:
        pass


def _read_new_lines(path, start):
    """Return (lines, new_offset) reading only complete lines past `start`."""
    try:
        size = os.path.getsize(path)
        if size < start:           # file rotated/truncated
            start = 0
        with open(path, "rb") as f:
            f.seek(start)
            data = f.read()
    except Exception:
        return [], start
    if not data:
        return [], start
    nl = data.rfind(b"\n")
    if nl < 0:                     # no complete line yet
        return [], start
    complete = data[:nl + 1]
    text = complete.decode("utf-8", "replace")
    return text.splitlines(), start + len(complete)


def scan(now=None, root=PROJECTS_DIR):
    """Incrementally scan transcripts; update + persist token rollups."""
    state = _load()
    offsets = state.setdefault("offsets", {})
    seen = state.setdefault("seen", [])
    seen_set = set(seen)
    for path in sorted(glob.glob(os.path.join(root, "*", "*.jsonl"))):
        try:
            mtime = os.path.getmtime(path)
        except Exception:
            continue
        prev = offsets.get(path)
        if prev and prev[0] == mtime and prev[1] == os.path.getsize(path):
            continue  # unchanged
        session = os.path.splitext(os.path.basename(path))[0]
        lines, new_off = _read_new_lines(path, prev[1] if prev else 0)
        for raw in lines:
            rec = parse_line(raw)
            if rec is None or rec["id"] in seen_set:
                continue
            seen_set.add(rec["id"])
            seen.append(rec["id"])
            accumulate(state, rec, session)
        offsets[path] = [mtime, new_off]

    if len(seen) > SEEN_CAP:
        del seen[:-SEEN_CAP]
        state["seen"] = seen
    # prune old daily + cap sessions
    cutoff = (now or dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=0)).date()
    cutoff = (dt.date.today() - dt.timedelta(days=RETAIN_DAYS)).isoformat()
    state["daily"] = {d: m for d, m in state.get("daily", {}).items() if d >= cutoff}
    sessions = state.get("sessions", {})
    if len(sessions) > MAX_SESSIONS:
        keep = sorted(sessions.items(), key=lambda kv: kv[1].get("last", ""), reverse=True)[:MAX_SESSIONS]
        state["sessions"] = dict(keep)
    _save(state)
    return state


def _sum(dst, src):
    for f in FIELDS:
        dst[f] = dst.get(f, 0) + src.get(f, 0)
    dst["msgs"] = dst.get("msgs", 0) + src.get("msgs", 0)
    return dst


def _total(b):
    return sum(b.get(f, 0) for f in FIELDS)


def summarize(state, now=None, window_days=30):
    """Shape the rollups for the API: daily series, by-model, top projects/sessions."""
    today = (now or dt.datetime.now()).date()
    since = (today - dt.timedelta(days=window_days)).isoformat()
    daily = state.get("daily", {})

    series, by_model = [], {}
    for d in sorted(daily):
        if d < since:
            continue
        day_tot = {}
        for model, b in daily[d].items():
            _sum(day_tot, b)
            _sum(by_model.setdefault(model, {}), b)
        series.append({"day": d, **{f: day_tot.get(f, 0) for f in FIELDS},
                       "total": _total(day_tot), "msgs": day_tot.get("msgs", 0)})

    projects = []
    for name, models in state.get("projects", {}).items():
        tot = {}
        for b in models.values():
            _sum(tot, b)
        projects.append({"name": name, **{f: tot.get(f, 0) for f in FIELDS},
                         "total": _total(tot), "msgs": tot.get("msgs", 0)})
    projects.sort(key=lambda r: r["total"], reverse=True)

    sessions = []
    for sid, s in state.get("sessions", {}).items():
        sessions.append({"id": sid, "project": s.get("project"), "last": s.get("last"),
                         **{f: s.get(f, 0) for f in FIELDS}, "total": _total(s),
                         "msgs": s.get("msgs", 0)})
    sessions.sort(key=lambda r: r["last"] or "", reverse=True)

    by_model_list = [{"model": m, **{f: b.get(f, 0) for f in FIELDS},
                      "total": _total(b), "msgs": b.get("msgs", 0)}
                     for m, b in by_model.items()]
    by_model_list.sort(key=lambda r: r["total"], reverse=True)

    return {
        "daily": series,
        "by_model": by_model_list,
        "top_projects": projects[:8],
        "recent_sessions": sessions[:10],
        "window_days": window_days,
    }
