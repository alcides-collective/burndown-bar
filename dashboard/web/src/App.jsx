import { useEffect, useState } from 'react'
import { motion, AnimatePresence } from 'motion/react'
import { fetchData, POLL_MS } from './api.js'
import {
  Card, Stat, Pill, AnimatedNumber, SurgeBanner, Heatmap, TrendRows,
  Bars, Delta, IncidentTimeline, gridV,
} from './components.jsx'
import { BurnChart, ORChart, HistoryChart, TokenChart } from './charts.jsx'
import { pct, money, fmtDur, fmtWhen, tok } from './format.js'

const STATUS_TONE = { dry: 'hot', exhausted: 'hot', hot: 'hot', ok: 'ok', idle: '', building: '' }
const STATUS_LABEL = {
  dry: 'on track to run dry', exhausted: 'limit hit', hot: 'running warm',
  ok: 'sustainable', idle: 'idle', building: 'learning',
}
const prettyModel = (m) => (m || '').replace(/^claude-/, '')

export default function App() {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)

  useEffect(() => {
    let alive = true
    const tick = () =>
      fetchData().then((d) => alive && (setData(d), setErr(null))).catch((e) => alive && setErr(String(e)))
    tick()
    const id = setInterval(tick, POLL_MS)
    return () => { alive = false; clearInterval(id) }
  }, [])

  if (err && !data) return <main className="boot"><div>⚠ {err}</div><div>Is the backend running on :3838?</div></main>
  if (!data || data.warming) return <main className="boot">Warming up — scanning your usage…</main>

  const c = data.claude
  const wk = c?.windows?.seven_day?.data
  const smart = c?.smart
  const or = data.openrouter
  const status = c?.weekly_status || 'building'
  const t = data.tokens || {}
  const wow = data.projections?.wow
  const mo = data.projections?.month_or
  const today = t.daily?.[t.daily.length - 1]
  const ioMax = Math.max(1, ...(t.by_model || []).map((m) => m.in + m.out))
  const projMax = Math.max(1, ...(t.top_projects || []).map((p) => p.in + p.out))

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <h1>Burndown</h1>
          <span className="sub">how fast you're burning it</span>
        </div>
        <div className="chips">
          {data.stale && <span className="chip stale">⚠ data stale {fmtDur(data.stale_age_h)}</span>}
          <span className="chip alert">🔔 alerts → <code>{data.ntfy_topic}</code></span>
          <span className="chip"><span className="dot" style={{ background: 'var(--good)' }} />updated {fmtWhen(data.generated_at)}</span>
        </div>
      </header>

      <motion.div className="grid" variants={gridV} initial="hidden" animate="show">
        <Card span="span12" className="pad-tight">
          <div className="summary">
            <span className="lede">{data.summary?.[0]} </span>
            <span className="tail">{data.summary?.[1]}</span>
          </div>
        </Card>

        <AnimatePresence>
          {or?.surge && <SurgeBanner key="surge" surge={or.surge} />}
        </AnimatePresence>

        {/* headline row */}
        <Card span="span3">
          <Stat label="Weekly used" foot={<Pill tone={STATUS_TONE[status]}>{STATUS_LABEL[status]}</Pill>}>
            <AnimatedNumber value={wk?.used} format={(v) => `${Math.round(v)}%`} />
          </Stat>
        </Card>
        <Card span="span3">
          <Stat
            label="Smart projection at reset"
            foot={smart ? (smart.dry_at ? `dry in ${fmtDur(c.summary_claude.runway_h)} · ${smart.used_baseline ? 'learned' : 'recent pace'}` : `fits · ${smart.used_baseline ? 'learned' : 'recent pace'}`) : '—'}
            footClass={smart?.dry_at ? 'danger' : 'good'}
          >
            {smart ? <AnimatedNumber value={smart.projected} format={(v) => `${Math.round(v)}%`} /> : '—'}
          </Stat>
        </Card>
        <Card span="span3">
          <Stat label="Pace vs sustainable" foot={wk ? `proj ${pct(wk.projected)} naive` : '—'}>
            <AnimatedNumber value={wk?.pace} format={(v) => `${v.toFixed(2)}×`} />
          </Stat>
        </Card>
        <Card span="span3">
          <Stat
            label="OpenRouter balance"
            foot={or?.ok ? (or.dry_in_h != null ? `empties in ${fmtDur(or.dry_in_h)}` : 'steady') : 'not configured'}
            footClass={or?.surge ? 'danger' : ''}
          >
            {or?.ok ? <AnimatedNumber value={or.balance} format={(v) => money(v)} /> : '—'}
          </Stat>
        </Card>

        {/* second stat row: tokens + comparisons */}
        <Card span="span3">
          <Stat label="Tokens today (I/O)" big={false} foot={`${tok(today ? today.cc + today.cr : 0)} cache`}>
            <AnimatedNumber value={today ? today.in + today.out : 0} format={(v) => tok(v)} />
          </Stat>
        </Card>
        <Card span="span3">
          <Stat label="Claude tokens this week" big={false} foot={<Delta pct={wow?.claude_tokens?.delta_pct} />}>
            <AnimatedNumber value={wow?.claude_tokens?.this} format={(v) => tok(v)} />
          </Stat>
        </Card>
        <Card span="span3">
          <Stat label="OpenRouter spend this week" big={false} foot={<Delta pct={wow?.openrouter?.delta_pct} />}>
            {wow ? <AnimatedNumber value={wow.openrouter.this} format={(v) => money(v)} /> : '—'}
          </Stat>
        </Card>
        <Card span="span3">
          <Stat label="OpenRouter — projected month" big={false} foot={mo ? `$${mo.mtd} so far · ${mo.trend}` : '—'}>
            {mo ? <AnimatedNumber value={mo.projected} format={(v) => money(v)} /> : '—'}
          </Stat>
        </Card>

        {/* burn curve + window facts */}
        <Card span="span8">
          <h2>Weekly burn — actual vs projections</h2>
          <BurnChart curve={c?.curve} smartPath={c?.smart_path} naivePath={c?.naive_path} />
        </Card>
        <Card span="span4">
          <h2>This window</h2>
          <div className="rows">
            <div className="row"><span className="k">Used</span><span className="v tnum">{pct(wk?.used)}</span></div>
            <div className="row"><span className="k">Elapsed</span><span className="v tnum">{wk ? `${fmtDur(wk.elapsed_h)} (${Math.round(wk.elapsed_frac * 100)}%)` : '—'}</span></div>
            <div className="row"><span className="k">Burn rate</span><span className="v tnum">{wk ? `${wk.rate.toFixed(2)}%/h` : '—'}</span></div>
            <div className="row"><span className="k">Resets</span><span className="v tnum">{fmtWhen(wk?.reset)}</span></div>
            <div className="row"><span className="k">Reset in</span><span className="v tnum">{fmtDur(wk?.left_h)}</span></div>
          </div>
          <h2 style={{ marginTop: 16 }}>Trend vs your typical</h2>
          <TrendRows rows={c?.trend?.rows} />
        </Card>

        {/* real token usage */}
        <Card span="span8">
          <h2>Token usage (real, from transcripts)</h2>
          <TokenChart daily={t.daily} />
        </Card>
        <Card span="span4">
          <h2>By model · last {t.window_days || 30}d</h2>
          <Bars rows={(t.by_model || []).slice(0, 6).map((m) => ({
            label: prettyModel(m.model), frac: (m.in + m.out) / ioMax, text: tok(m.in + m.out),
          }))} />
        </Card>

        {/* heatmap + OpenRouter */}
        <Card span="span8">
          <h2>Typical Claude burn by weekday × hour</h2>
          <Heatmap grid={c?.baseline_grid} support={c?.baseline_support} />
          <div className="chartcap" style={{ marginTop: 10 }}>
            <span>columns 00:00 → 23:00 · darker = heavier · fills in as history builds</span>
          </div>
        </Card>
        <Card span="span4">
          <h2>OpenRouter spend</h2>
          {or?.ok ? (
            <>
              <div className="rows" style={{ marginBottom: 12 }}>
                <div className="row"><span className="k">Balance</span><span className="v tnum">{money(or.balance)}</span></div>
                <div className="row"><span className="k">Pace</span><span className="v tnum">{money(or.per_day)}/day</span></div>
                <div className="row"><span className="k">Empties in</span><span className="v tnum">{fmtDur(or.dry_in_h)}</span></div>
              </div>
              <ORChart series={or.balance_series} projection={or.projection} height={150} />
            </>
          ) : (
            <div className="empty">{or?.configured ? 'No OpenRouter data yet.' : 'Add an OpenRouter key to track prepaid spend.'}</div>
          )}
        </Card>

        {/* long-range history + top projects */}
        <Card span="span8">
          <h2>History — weekly utilization & OpenRouter balance</h2>
          <HistoryChart archive={data.archive} />
        </Card>
        <Card span="span4">
          <h2>Top projects · tokens</h2>
          <Bars rows={(t.top_projects || []).slice(0, 7).map((p) => ({
            label: p.name, frac: (p.in + p.out) / projMax, text: tok(p.in + p.out),
          }))} />
        </Card>

        {/* incident timeline */}
        <Card span="span12">
          <h2>Incident timeline — surges, exhaustion, stale data, Anthropic limit changes</h2>
          <IncidentTimeline items={data.incidents} />
        </Card>
      </motion.div>
    </div>
  )
}
