<div align="center">

# Burndown Bar

**Not how much Claude quota you've used — how fast you're burning it.**

[![License: MIT](https://img.shields.io/badge/license-MIT-blue?style=flat)](LICENSE)
![Platform](https://img.shields.io/badge/platform-macOS-lightgrey?style=flat)
![SwiftBar plugin](https://img.shields.io/badge/SwiftBar-plugin-orange?style=flat)
![Dependencies](https://img.shields.io/badge/dependencies-none-brightgreen?style=flat)

<!-- TODO before publishing: replace this comment with the hero screenshot.
Tight crop of the menu bar + open dropdown, dark wallpaper, other icons hidden,
~1400px wide. Dark mode first; add a light variant if you can:
<picture>
  <source media="(prefers-color-scheme: dark)" srcset="assets/hero-dark.png">
  <img alt="Burndown Bar in the macOS menu bar" src="assets/hero-light.png" width="700">
</picture>
-->

</div>

Every Claude usage monitor tells you the level: `76% used`. Burndown Bar tells you the slope:

```
🔥 1.12× → dry tomorrow 18:50
─────────────────────────────────────────────────────
Weekly limit — 77% used
Elapsed: 4.8 d of 7.0 d window (69% of the time)
Pace: 1.12× sustainable (0.67%/h ≈ 16.0%/day)
Runs dry tomorrow 18:50 — 18.2 h BEFORE reset
Resets Sat Jun 13, 13:00 (in 2.2 d)
─────────────────────────────────────────────────────
5-hour session — 29% used
Pace: 0.78× sustainable
Projected at reset: 78% — fits with 22% headroom
Resets today 11:30 (in 3.1 h)
```

76% used means nothing on its own. 76% used with only 69% of the window elapsed means you run dry Friday night and the reset isn't until Saturday afternoon. That second sentence is the one you need, and it's the one your menu bar shows at a glance: 🟢 sustainable pace, 🟡 tight, 🔥 you won't make it to the reset (and when you'll hit the wall), ⛔ already hit.

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

For each window: percent used vs percent of time elapsed, burn rate in %/hour and %/day, pace as a multiple of the sustainable rate, and the verdict — either *projected usage at reset with headroom*, or *the hour you run dry and how long before the reset that is*.

## How it works

Burndown Bar reads your Claude Code OAuth token from the macOS Keychain and polls the same usage endpoint that Claude Code's `/usage` command uses, every 5 minutes. Since the API reports each window's reset time, the window start is just `reset − 168h` (or 5 h) — which is what makes trajectory math possible at all. Nothing leaves your machine except that one HTTPS request to `api.anthropic.com`. No analytics, no third-party servers, no token refreshing (it never touches your refresh token — if the access token expires, it just tells you to run any Claude Code prompt).

The projection is a deliberate simplification: it extrapolates your *average* pace across the whole window. Usage is bursty — you don't burn quota while you sleep — so treat "dry tomorrow 18:50" as a trend line, not a countdown clock.

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

**Why a single Python file instead of a real app?**
Because your quota monitor shouldn't have a quota of its own. One file, stdlib only, auditable in five minutes.

## Contributing

PRs welcome — it's one file, so read it first. Bug reports with the dropdown's error text are the most useful thing you can send.

## License

MIT — see [LICENSE](LICENSE).
