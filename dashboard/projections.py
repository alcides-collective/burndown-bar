"""Week-over-week comparisons and a trend-fit monthly OpenRouter projection.

Claude WoW uses real tokens (input+output, excluding the huge-but-cheap cache
reads) from the transcript scan. OpenRouter WoW + monthly projection use daily
spend reconstructed from the hourly archive's per-hour spend rate.

The monthly projection fits a least-squares line to recent full days to get the
*current* daily rate (so acceleration/easing is reflected) and extrapolates
that across the days left — rather than summing the fitted line, which would
overfit on the little history available.
"""
import calendar
import datetime as dt


def linfit(xs, ys):
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    sx, sy = sum(xs), sum(ys)
    sxx = sum(x * x for x in xs)
    sxy = sum(x * y for x, y in zip(xs, ys))
    denom = n * sxx - sx * sx
    if denom == 0:
        return 0.0, sy / n
    slope = (n * sxy - sx * sy) / denom
    return slope, (sy - slope * sx) / n


def daily_or_spend(archive):
    """Local-day -> $ spent, from the hourly archive's per-hour spend rate."""
    out = {}
    for r in archive or []:
        rate = r.get("or_rate")
        h = r.get("h")
        if rate is None or not h:
            continue
        try:
            day = dt.datetime.fromisoformat(h).astimezone().date().isoformat()
        except Exception:
            continue
        out[day] = out.get(day, 0.0) + float(rate)  # one hour each
    return out


def _between(day, a, b):
    return a.isoformat() <= day <= b.isoformat()


def week_over_week(token_daily, or_daily, now):
    today = now.date()
    tw = (today - dt.timedelta(days=6), today)            # this week (incl today)
    lw = (today - dt.timedelta(days=13), today - dt.timedelta(days=7))

    def claude(rng):
        return sum((r.get("in", 0) + r.get("out", 0)) for r in token_daily
                   if _between(r["day"], *rng))

    def orr(rng):
        return sum(v for d, v in or_daily.items() if _between(d, *rng))

    def block(this, last):
        delta = ((this - last) / last * 100) if last > 0 else None
        return {"this": round(this, 2), "last": round(last, 2),
                "delta_pct": (round(delta, 0) if delta is not None else None)}

    return {
        "claude_tokens": block(claude(tw), claude(lw)),
        "openrouter": block(orr(tw), orr(lw)),
    }


def month_or_projection(or_daily, now):
    today = now.date()
    ym = today.isoformat()[:7]
    mtd = sum(v for d, v in or_daily.items() if d.startswith(ym))

    full_days = sorted((d, v) for d, v in or_daily.items() if d < today.isoformat())[-30:]
    if len(full_days) >= 3:
        ys = [v for _, v in full_days]
        slope, intercept = linfit(list(range(len(ys))), ys)
        rate_now = max(0.0, intercept + slope * (len(ys) - 1))
        trend = "accelerating" if slope > 0.02 else "easing" if slope < -0.02 else "steady"
    else:
        ys = [v for _, v in full_days]
        rate_now = (sum(ys) / len(ys)) if ys else 0.0
        slope, trend = 0.0, "steady"

    days_in_month = calendar.monthrange(today.year, today.month)[1]
    days_left = days_in_month - today.day
    return {
        "mtd": round(mtd, 2),
        "rate_now": round(rate_now, 3),
        "projected": round(mtd + rate_now * days_left, 2),
        "days_left": days_left,
        "trend": trend,
    }


def compute(token_daily, archive, now):
    or_daily = daily_or_spend(archive)
    return {
        "wow": week_over_week(token_daily, or_daily, now),
        "month_or": month_or_projection(or_daily, now),
    }
