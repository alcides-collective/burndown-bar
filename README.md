<div align="center">

# Burndown Bar

**Not how much Claude quota you've used — how fast you're burning it.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey?style=flat)
![SwiftBar plugin](https://img.shields.io/badge/SwiftBar-plugin-orange?style=flat)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen?style=flat)

</div>

Every Claude usage monitor tells you the level: `76% used`. Burndown Bar tells you the slope — and now, whether that slope is unusual *for you*:

```
🟡 proj 88%↑ 5h↑ · OR $12.34↓
─────────────────────────────────────────────────────
You're heavier than your typical Friday on Claude, but
there's still room — only 77% of the week gone. Spend's
eased on OpenRouter; $12.34 now stretches ~15 d.
─────────────────────────────────────────────────────
Weekly limit — 77% used
Elapsed: 4.8 d of 7.0 d window (69% of the time)
Pace: 1.12× sustainable (0.67%/h ≈ 16.0%/day)
Runs dry tomorrow 18:50 — 18.2 h BEFORE reset
Resets Sat Jun 13, 13:00 (in 2.2 d)
Smart projection: ~88% at reset — fits, 12% headroom (learned rhythm)
vs typical this hour: +38%
vs typical Friday:    +12%
vs typical week:      -4%
─────────────────────────────────────────────────────
5-hour session — 29% used
Pace: 0.78× sustainable
Projected at reset: 78% — fits with 22% headroom
Resets today 11:30 (in 3.1 h)
```

76% used means nothing on its own. 76% with only 69% of the window elapsed *sounds* like you run dry before the reset — but a naïve straight-line projection assumes you keep burning at that pace straight through the night and the weekend, which you don't. So Burndown Bar leads with a **smart projection**: it walks the hours left in the window and applies *your own* learned burn for each weekday and hour, so a busy afternoon isn't mistaken for a runaway week. That projected-at-reset figure is the headline your menu bar shows at a glance: 🟢 sustainable, 🟡 tight, 🔥 you won't make it (and when you'll hit the wall), ⛔ already hit.

The arrows are the next layer: `↑`/`↓` next to each signal mean today's burn is running heavier or lighter than *your own typical* for this weekday and hour. A plain-English, two-sentence summary sits at the top of the dropdown, and a macOS notification fires when something genuinely shifts — no LLM, just your history and some arithmetic.

## Install

Requires macOS, [SwiftBar](https://github.com/swiftbar/SwiftBar), and a Claude Pro/Max subscription with [Claude Code](https://code.claude.com) logged in.

```sh
brew install swiftbar   # if you don't have it yet
curl -fsSL https://raw.githubusercontent.com/alcides-collective/burndown-bar/main/burndown-bar.5m.py \
  -o "$(defaults read com.ameba.SwiftBar PluginDirectory)/burndown-bar.5m.py" \
  && chmod +x "$(defaults read com.ameba.SwiftBar PluginDirectory)/burndown-bar.5m.py"
```

The first run triggers a macOS Keychain prompt (the plugin reads your Claude Code token) — click **Always Allow** once and you're done. No configuration, no dependencies beyond the system Python.

## What it tracks

- **Weekly limit (168 h)** — the one that actually ends your week. Drives the menu bar verdict.
- **5-hour session** — shown alongside with its own mini-trajectory.
- **Per-model weekly buckets** (Sonnet/Opus) and **extra-usage credits** — appear automatically when your plan reports them.
- **OpenRouter credits** *(optional)* — your prepaid balance plus a spend trajectory, when you add a key (see below).

For each window: percent used vs percent of time elapsed, burn rate in %/hour and %/day, pace as a multiple of the sustainable rate, and the verdict — either *projected usage at reset with headroom*, or *the hour you run dry and how long before the reset that is*. The weekly window adds the **smart projection** — a separate, baseline-aware estimate of where you'll land at reset that models your daily rhythm rather than holding the current pace flat. Both are shown side by side, so you can see the simple straight-line read and the rhythm-aware one at once.

## Trends, in plain English

Levels and pace tell you where you are. Trends tell you whether *this* is normal for you. Burndown Bar quietly records a snapshot every run and learns your rhythm — what a typical Tuesday 3pm looks like, separate from a typical Saturday morning — then reports the present against it:

- **A two-sentence summary** at the top of the dropdown, written from hand-built templates (no LLM, nothing leaves your machine). The wording rotates so it doesn't read like a robot, but the numbers are exact.
- **Trend arrows in the menu bar** — `↑`/`↓` on the weekly pace, the 5-hour session, and OpenRouter spend, each comparing now to your typical for this weekday and hour.
- **Baseline-relative rows** under the weekly window: `vs typical this hour`, `vs typical <weekday>`, `vs typical week`.
- **Notifications** when a shift is genuinely notable — a heavy day, a spend spike, a balance about to run dry. They respect a per-event cooldown, collapse duplicates, and stay quiet overnight (22:00–08:00 local).

A trend is only called when the current rate is statistically out of line with your own history (a z-score past ~1.5σ), so normal day-to-day noise doesn't trip it. Everything degrades gracefully: on a fresh install it says *"still building history,"* hour- and day-level trends appear within a day, and week-over-week kicks in after about two weeks of data. History lives in a single local cache file and is capped and aged automatically.

## When Anthropic moves your limits

Anthropic sometimes resets a window early, or quietly raises a limit (special events, holiday grants). Burndown Bar spots both from the usage stream — an **early reset** (the window rolls over before its scheduled time) or a **limit raised** (your utilization drops with no scheduled reset) — and:

- logs them in a **Limit events (Anthropic)** section in the dropdown,
- fires a one-time heads-up notification (*"Fresh quota — Anthropic reset your weekly limit early"*),
- and, crucially, **excludes those abnormal periods from your learned baseline**, so a Christmas binge or a surprise grant never becomes your new "typical." Readings that are wildly out of range, or that land within ~48 h of a detected event, are recorded but not learned from.

This is fully automatic — there's nothing to configure.

## OpenRouter credits (optional)

If you also burn through [OpenRouter](https://openrouter.ai) credits, Burndown Bar can show those too. OpenRouter credits don't reset on a window — they're a prepaid balance that only drains — so the plugin snapshots your cumulative spend across runs and extrapolates **when the balance hits zero**:

```
OpenRouter — $12.34 left
Pace: $0.85/day ($0.035/h)
Balance empties in ~14.5 d at this pace
```

It also reports your remaining balance as a compact `· OR $12.34` chip in the menu bar.

### Runaway-spend alarm

OpenRouter credit is *real prepaid money*, so a buggy or runaway process can quietly burn through it far faster than you'd ever spend on purpose. Burndown Bar watches for exactly that — it compares your spend over the last half hour against your trailing-24h normal, and fires a **🚨 spend surge** alarm when the rate jumps well above normal *and* would empty the balance fast:

```
🚨 OpenRouter spend surge
$48.20/h, ~70× your normal — balance empties in ~14 min. Runaway process?
```

It's deliberately hard to false-trip: a brief burst (a one-off batch job) gets averaged out over the 30-minute window, so the alarm only fires on a *sustained* surge that's both abnormal and materially fast. It needs no learned history — it works off the raw spend trajectory — so it can catch a bug the very first time it happens. And because a money leak at 3am is precisely when you can't see it, the surge alarm is the one notification that **overrides quiet hours** (with a short cooldown so it nudges rather than nags). A 🚨 also appears on the menu-bar chip while it's surging. The thresholds (`OR_SURGE_FACTOR`, `OR_SURGE_RUNWAY_H`, and friends) are constants near the top of the file if you want to tune the sensitivity.

Point it at a key one of two ways (it checks them in this order):

```sh
# 1. environment variable
export OPENROUTER_API_KEY=sk-or-v1-...

# 2. a one-line key file (recommended for SwiftBar — see note below)
mkdir -p ~/.config/burndown-bar
printf '%s\n' "sk-or-v1-..." > ~/.config/burndown-bar/openrouter-key
chmod 600 ~/.config/burndown-bar/openrouter-key
```

> **Use the key file for the menu bar.** SwiftBar launches plugins from a GUI context that doesn't read your `~/.zshrc`, so an `export`ed `OPENROUTER_API_KEY` won't reach the plugin there — the key file always will. The env var is handy when running the script yourself in a terminal.

The key needs no special scope — a normal inference key works (it reads `/api/v1/credits`). Nothing is written anywhere except `openrouter.ai`. With no key configured, the OpenRouter section simply doesn't appear. The spend trajectory needs a little history to warm up; until then it just shows the balance.

## Web dashboard (optional)

The menu bar is the glance; the dashboard is the full picture. `dashboard/` is a small local web app at **`http://localhost:3838`** that reads the same caches and renders the analysis in depth — charts, projections, trends, and history you can actually pore over. It's **read-only** and reuses the plugin's own tested analytics (no math is reimplemented).

```sh
cd dashboard && ./install.sh      # builds the UI, starts it on login
```

What it adds on top of the menu bar:

- **Charts for everything** — the weekly burn curve with the naive *and* smart projection drawn out, the OpenRouter balance drawdown, a weekday × hour baseline heatmap, and long-range history.
- **Real token history** — it scans your local Claude Code transcripts (`~/.claude/projects`) for actual per-message token counts, so you get true usage by **model, project, and day** — not just the opaque %-of-limit windows. It dedups split messages and scans incrementally.
- **Week-over-week & a projected monthly OpenRouter bill**, from a hourly archive it keeps itself (the plugin only retains ~21 days; the dashboard backfills from that and then grows to months).
- **An incident timeline** — spend surges, weekly exhaustion, stale data, and Anthropic limit changes, tracked as episodes with duration and peak.
- **Phone alerts via [ntfy](https://ntfy.sh)** — because the dashboard runs continuously (a macOS LaunchAgent), it's a reliable watchdog: a spend surge, a balance about to run dry, or the plugin going stale can push to your phone. The surge alert is urgent and overrides quiet hours, since a runaway bill at 3am is exactly when you can't see it. Subscribe to the topic printed at startup (override it with `$BURNDOWN_NTFY_TOPIC`).

Built with a small React + Motion + uPlot front-end (Vite) over a standard-library Python backend — no framework, styled to be quiet and editorial. It never calls any API; it only reads what the plugin already fetched, plus your local transcripts. See [`dashboard/README.md`](dashboard/README.md) for configuration and the env vars.

## How it works

Burndown Bar reads your Claude Code OAuth token from the macOS Keychain and polls the same usage endpoint that Claude Code's `/usage` command uses, every 5 minutes. Since the API reports each window's reset time, the window start is just `reset − 168h` (or 5 h) — which is what makes trajectory math possible at all. Nothing leaves your machine except that one HTTPS request to `api.anthropic.com` (plus one to `openrouter.ai` if you've added an OpenRouter key). No analytics, no third-party servers, no token refreshing (it never touches your refresh token — if the access token expires, it just tells you to run any Claude Code prompt).

OpenRouter has no reset window, so its trajectory works differently: each run records your account's cumulative spend (`total_usage`) with a timestamp in a local cache, and the dry-date is extrapolated from the spend rate over the last 24 h of those snapshots. Because cumulative spend only ever rises, the rate survives credit top-ups. Both fetches share the same throttle — fresh-enough cache is served without touching the network, and after an error the plugin backs off for five minutes.

There are two projections, by design. The plain one extrapolates your *average* pace across the whole window — simple and transparent, but it over-counts, because usage is bursty and you don't burn quota while you sleep. The **smart projection** corrects for exactly that: it walks each remaining clock-hour to the reset and adds your *learned typical burn* for that weekday and hour, so quiet nights and weekends are modelled from your own history instead of assumed away. Until a given hour-of-day has enough readings to trust, that hour falls back to your recent pace — the dropdown labels which it's using (*learned rhythm* vs *recent pace*), and the menu-bar headline follows the smart figure. On a fresh install, with no rhythm learned yet, the two projections agree; they diverge as the baseline fills in.

The trend layer adds one more local file: a rolling history of utilization snapshots plus a learned baseline of your burn rate, bucketed by weekday and hour. Sparse buckets borrow strength from broader ones (an empirical-Bayes shrinkage toward the hour-of-day and overall averages), so trends are usable long before every weekday/hour cell is full. Burn rate is reconstructed from the snapshot deltas, stitching across window resets so a reset never looks like a usage cliff — and the reset boundary is matched with a small tolerance, because the usage API recomputes each window's reset time on every poll and it jitters by a second or two, which would otherwise read as a fresh reset on most intervals and starve this math. Notifications are sent via `osascript` (a standard macOS notification); the same file remembers what's already been announced so nothing nags you twice. None of this touches the network — it's all derived from the readings you'd be fetching anyway.

## Why

I kept getting the "you've reached your weekly limit" wall mid-task, despite glancing at `/usage` regularly. The number was never the problem — 76% sounds fine. What I could never do in my head was the second step: *76% of budget in 69% of the window means 16 hours of darkness before Saturday's reset*. So I made the menu bar do that math, permanently.

## FAQ

**Why does it want Keychain access?**
Claude Code stores its OAuth token in the macOS Keychain under `Claude Code-credentials`. The plugin reads it via the system `security` tool — that's the prompt you see once. It never writes, never refreshes, never sends the token anywhere but `api.anthropic.com`.

**Does it work with an API key / Console account?**
No — it reads the subscription limits (Pro/Max 5-hour and weekly windows), which only exist for claude.ai subscribers logged into Claude Code.

**The menu shows "token likely expired."**
Run any prompt in Claude Code; it refreshes the token itself, then hit Refresh in the dropdown.

**Can this break?**
Yes. It rides the same undocumented endpoint Claude Code uses internally. If Anthropic changes it, the plugin will show an error until patched — open an issue and it'll get fixed.

**Can I turn off the notifications (or change quiet hours)?**
They only fire on genuinely notable shifts, with a cooldown, and stay silent 22:00–08:00 — with one exception: an OpenRouter spend surge overrides quiet hours, because a money leak overnight is the whole point of catching it. To tune or disable them, edit the constants near the top of the file — `NOTIFY_COOLDOWN_H`, `QUIET_HOURS`, the `*_Z` thresholds, and the `OR_SURGE_*` thresholds — or mute *Burndown Bar* in macOS System Settings → Notifications.

**The trends say "still building history" — is something wrong?**
No. The baseline learns from your own usage, so it needs data first. Hour- and day-level trends appear within a day; week-over-week after about two weeks.

**Why a single Python file instead of a real app?**
Because your quota monitor shouldn't have a quota of its own. One file, stdlib only, auditable in five minutes.

## Contributing

PRs welcome — it's one file, so read it first. Bug reports with the dropdown's error text are the most useful thing you can send.

## License

MIT — see [LICENSE](LICENSE).
