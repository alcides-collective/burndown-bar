# Burndown dashboard

A local web view of the Burndown Bar data — charts, metrics, projections,
trends, and long-range history at **http://localhost:3838**.

It is **read-only**: it never calls any API. It reuses the plugin's own
already-tested analytics (`../burndown-bar.5m.py`) and reads the three cache
files the plugin maintains, recomputing on each request. The plugin refreshes
those caches every ~5 minutes; the page polls every 30s.

## Architecture

- **Backend** — `server.py`, Python standard library only. Imports the plugin
  module by path (single source of truth for the math), serves a JSON API
  (`/api/data`) and the built SPA. No pip installs.
- **Frontend** — `web/`, a React + Motion (framer-motion) + uPlot SPA built
  with Vite. Visual language borrowed from Pollar: monochrome OKLCH, two
  weights, tabular numerals, restrained motion, light/dark by system theme.
- **Archive** — `archive.py`. The plugin only keeps ~21 days of history; the
  dashboard backfills from that once and then appends one rolled-up record per
  clock hour to `~/.local/share/burndown-bar/dashboard-archive.json`, so the
  history charts can grow to months. This file is the only state it *owns*.

## Run it

One-time, then start on login (LaunchAgent — starts at login, restarts if it
dies, logs to `~/Library/Logs/burndown-dashboard.log`):

```sh
cd dashboard
./install.sh
```

Or run it in the foreground without installing:

```sh
cd dashboard
(cd web && npm install && npm run build)   # first time
python3 server.py                          # http://localhost:3838
```

Uninstall the LaunchAgent: `./install.sh uninstall`.

## Configuration (env vars)

| Variable | Default | Purpose |
| --- | --- | --- |
| `BURNDOWN_DASHBOARD_PORT` | `3838` | Listen port |
| `BURNDOWN_CACHE_DIR` | SwiftBar plugin cache dir | Where the plugin's caches live |
| `BURNDOWN_PLUGIN_FILE` | `../burndown-bar.5m.py` | Analytics module to import |
| `BURNDOWN_ARCHIVE_FILE` | `~/.local/share/burndown-bar/dashboard-archive.json` | Long-term archive |
| `BURNDOWN_ARCHIVE_RETAIN_DAYS` | `180` | Archive retention |
