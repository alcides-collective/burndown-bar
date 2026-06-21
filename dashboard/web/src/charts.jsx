import { useMemo } from 'react'
import UPlot from './UPlot.jsx'
import { useThemeColors } from './theme.js'
import { toEpoch, align } from './format.js'

const FONT = '11px "Helvetica Neue", Arial, sans-serif'

function axes(colors, extra = {}) {
  return {
    stroke: colors.muted,
    grid: { stroke: colors.grid, width: 1 },
    ticks: { stroke: colors.grid, width: 1, size: 4 },
    font: FONT,
    ...extra,
  }
}

// horizontal reference line drawn straight onto the canvas (e.g. the 100% cap)
function refLine(yval, color, scale = 'y') {
  return (u) => {
    const y = u.valToPos(yval, scale, true)
    if (y < u.bbox.top || y > u.bbox.top + u.bbox.height) return
    const ctx = u.ctx
    ctx.save()
    ctx.strokeStyle = color
    ctx.globalAlpha = 0.55
    ctx.setLineDash([3, 3])
    ctx.lineWidth = 1
    ctx.beginPath()
    ctx.moveTo(u.bbox.left, y)
    ctx.lineTo(u.bbox.left + u.bbox.width, y)
    ctx.stroke()
    ctx.restore()
  }
}

const baseOpts = (colors, h) => ({
  height: h,
  legend: { show: false },
  cursor: { y: false, points: { size: 6 } },
  padding: [10, 8, 0, 0],
})

// ── Weekly burn curve: actual + smart projection + naive projection ─────────
export function BurnChart({ curve, smartPath, naivePath, height = 220 }) {
  const colors = useThemeColors()
  const { data, ymax } = useMemo(() => {
    const used = toEpoch(curve)
    const smart = toEpoch(smartPath)
    const naive = toEpoch(naivePath)
    const d = align([used, smart, naive])
    let mx = 110
    ;[...smart, ...naive, ...used].forEach(([, v]) => { if (v != null && v > mx) mx = v })
    return { data: d, ymax: Math.min(mx * 1.05, 500) }
  }, [curve, smartPath, naivePath])

  const makeOpts = (w, h) => ({
    ...baseOpts(colors, h),
    width: w,
    scales: { x: { time: true }, y: { range: [0, ymax] } },
    axes: [axes(colors), axes(colors, { size: 42, values: (u, vs) => vs.map((v) => v + '%') })],
    series: [
      {},
      { label: 'used', stroke: colors.fg, width: 2, points: { show: false } },
      { label: 'smart', stroke: colors.accent, width: 2.5, points: { show: false } },
      { label: 'naive', stroke: colors.muted, width: 1.5, dash: [6, 4], points: { show: false } },
    ],
    hooks: { draw: [refLine(100, colors.danger)] },
  })

  return (
    <>
      <UPlot makeOpts={makeOpts} data={data} height={height} rev={JSON.stringify(colors)} />
      <div className="chartcap">
        <span><i className="swatch" style={{ background: colors.fg }} /> actual</span>
        <span style={{ color: colors.accent }}><i className="swatch" style={{ background: colors.accent }} /> <b style={{ color: colors.accent }}>smart projection</b></span>
        <span><i className="swatch dash" style={{ color: colors.muted }} /> naive (flat pace)</span>
        <span style={{ color: colors.danger }}><i className="swatch dash" style={{ color: colors.danger }} /> 100% limit</span>
      </div>
    </>
  )
}

// ── OpenRouter balance drawdown + flat projection to zero ───────────────────
export function ORChart({ series, projection, height = 200 }) {
  const colors = useThemeColors()
  const { data, ymax } = useMemo(() => {
    const bal = toEpoch(series)
    const proj = toEpoch(projection)
    let mx = 1
    ;[...bal, ...proj].forEach(([, v]) => { if (v != null && v > mx) mx = v })
    return { data: align([bal, proj]), ymax: mx * 1.08 }
  }, [series, projection])

  const makeOpts = (w, h) => ({
    ...baseOpts(colors, h),
    width: w,
    scales: { x: { time: true }, y: { range: [0, ymax] } },
    axes: [axes(colors), axes(colors, { size: 46, values: (u, vs) => vs.map((v) => '$' + v) })],
    series: [
      {},
      { label: 'balance', stroke: colors.fg, width: 2, points: { show: false } },
      { label: 'projection', stroke: colors.danger, width: 1.5, dash: [6, 4], points: { show: false } },
    ],
    hooks: { draw: [refLine(0, colors.danger)] },
  })

  return (
    <>
      <UPlot makeOpts={makeOpts} data={data} height={height} rev={JSON.stringify(colors)} />
      <div className="chartcap">
        <span><i className="swatch" style={{ background: colors.fg }} /> balance</span>
        <span style={{ color: colors.danger }}><i className="swatch dash" style={{ color: colors.danger }} /> projection to $0</span>
      </div>
    </>
  )
}

// ── Real token usage from transcripts: input+output vs cache, per day ───────
export function TokenChart({ daily, height = 180 }) {
  const colors = useThemeColors()
  const data = useMemo(() => {
    const rows = daily || []
    const xs = rows.map((r) => Math.floor(new Date(r.day).getTime() / 1000))
    const io = rows.map((r) => (r.in + r.out) / 1e6)
    const cache = rows.map((r) => (r.cc + r.cr) / 1e6)
    return [xs, io, cache]
  }, [daily])

  const makeOpts = (w, h) => ({
    ...baseOpts(colors, h),
    width: w,
    scales: { x: { time: true }, y: { range: (u, lo, hi) => [0, (hi || 1) * 1.1] } },
    axes: [axes(colors), axes(colors, { size: 44, values: (u, vs) => vs.map((v) => v + 'M') })],
    series: [
      {},
      { label: 'I/O', scale: 'y', stroke: colors.accent, width: 2, points: { show: false } },
      { label: 'cache', scale: 'y', stroke: colors.muted, width: 1.5, dash: [4, 3], points: { show: false } },
    ],
  })

  if (!daily?.length) return <div className="empty">Scanning transcripts…</div>
  return (
    <>
      <UPlot makeOpts={makeOpts} data={data} height={height} rev={JSON.stringify(colors)} />
      <div className="chartcap">
        <span style={{ color: colors.accent }}><i className="swatch" style={{ background: colors.accent }} /> input+output</span>
        <span><i className="swatch dash" style={{ color: colors.muted }} /> cache (read+create)</span>
        <span>millions of tokens / day</span>
      </div>
    </>
  )
}

// ── Long-range archive: weekly utilization % (left) + OR balance $ (right) ───
export function HistoryChart({ archive, height = 200 }) {
  const colors = useThemeColors()
  const data = useMemo(() => {
    const rows = (archive || []).filter((r) => r && r.h)
    const xs = rows.map((r) => Math.floor(new Date(r.h).getTime() / 1000))
    const wk = rows.map((r) => (r.wk_used != null ? r.wk_used : null))
    const or = rows.map((r) => (r.or_bal != null ? r.or_bal : null))
    return [xs, wk, or]
  }, [archive])

  const makeOpts = (w, h) => ({
    ...baseOpts(colors, h),
    width: w,
    scales: { x: { time: true }, '%': { range: [0, 110] }, $: {} },
    axes: [
      axes(colors),
      axes(colors, { scale: '%', size: 42, values: (u, vs) => vs.map((v) => v + '%') }),
      axes(colors, { scale: '$', side: 1, size: 46, grid: { show: false }, values: (u, vs) => vs.map((v) => '$' + v) }),
    ],
    series: [
      {},
      { label: 'weekly %', scale: '%', stroke: colors.accent, width: 1.5, points: { show: false } },
      { label: 'OR $', scale: '$', stroke: colors.fg, width: 1.5, dash: [4, 3], points: { show: false } },
    ],
  })

  if (!archive?.length) return <div className="empty">Accumulating history…</div>
  return (
    <>
      <UPlot makeOpts={makeOpts} data={data} height={height} rev={JSON.stringify(colors)} />
      <div className="chartcap">
        <span style={{ color: colors.accent }}><i className="swatch" style={{ background: colors.accent }} /> weekly utilization</span>
        <span><i className="swatch dash" style={{ color: colors.fg }} /> OpenRouter balance</span>
      </div>
    </>
  )
}
