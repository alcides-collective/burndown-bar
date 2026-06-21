import { useEffect } from 'react'
import { motion, useSpring, useTransform, useReducedMotion } from 'motion/react'
import { fmtWhen } from './format.js'

// ── motion vocabulary (Pollar timings: ease-out / cubic-bezier(.32,.72,0,1)) ─
export const EASE = [0.32, 0.72, 0, 1]
export const gridV = { hidden: {}, show: { transition: { staggerChildren: 0.05, delayChildren: 0.05 } } }
export const cardV = {
  hidden: { opacity: 0, y: 12 },
  show: { opacity: 1, y: 0, transition: { duration: 0.4, ease: EASE } },
}

export function Card({ span = 'span4', className = '', children, ...rest }) {
  return (
    <motion.section className={`card ${span} ${className}`} variants={cardV} {...rest}>
      {children}
    </motion.section>
  )
}

// Spring-tweened number that counts up on mount and re-springs on change.
export function AnimatedNumber({ value, format = (v) => Math.round(v), className }) {
  const reduce = useReducedMotion()
  const spring = useSpring(0, { stiffness: 90, damping: 18, mass: 0.7 })
  useEffect(() => {
    const v = value ?? 0
    if (reduce) spring.jump(v)
    else spring.set(v)
  }, [value, reduce, spring])
  const text = useTransform(spring, (v) => format(v))
  if (value == null) return <span className={className}>—</span>
  return <motion.span className={className}>{text}</motion.span>
}

export function Stat({ label, children, foot, footClass = '', big = true }) {
  return (
    <div className="stat">
      <span className="label">{label}</span>
      <span className={`value tnum ${big ? '' : 'sm'}`}>{children}</span>
      {foot != null && <span className={`foot tnum ${footClass}`}>{foot}</span>}
    </div>
  )
}

export function Pill({ tone = '', children }) {
  return <span className={`pill ${tone}`}>{children}</span>
}

export function SurgeBanner({ surge }) {
  return (
    <motion.div
      className="surge"
      layout
      initial={{ opacity: 0, y: -8, scale: 0.99 }}
      animate={{ opacity: 1, y: 0, scale: 1 }}
      exit={{ opacity: 0, y: -8 }}
      transition={{ duration: 0.4, ease: EASE }}
    >
      <motion.span
        className="big"
        animate={{ scale: [1, 1.12, 1] }}
        transition={{ duration: 1.4, repeat: Infinity, ease: 'easeInOut' }}
      >
        🚨
      </motion.span>
      <span className="txt">
        <b>OpenRouter spend surge.</b> ${surge.rate.toFixed(2)}/h, ~{Math.round(surge.factor)}× your
        normal — balance empties in ~{surge.runway_h < 1
          ? `${Math.round(surge.runway_h * 60)} min`
          : `${surge.runway_h.toFixed(1)} h`}. Runaway process?
      </span>
    </motion.div>
  )
}

// weekday(0=Mon) × hour(0..23) baseline burn-rate heatmap
const WD = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
export function Heatmap({ grid, support }) {
  let max = 0
  grid.forEach((r) => r.forEach((v) => { if (v != null && v > max) max = v }))
  const reduce = useReducedMotion()
  return (
    <div className="heat" role="img" aria-label="typical burn by weekday and hour">
      {grid.map((row, wd) => (
        <div key={wd} style={{ display: 'contents' }}>
          <div className="hlab">{WD[wd]}</div>
          {row.map((v, hr) => {
            const has = v != null && max > 0 && (support?.[wd]?.[hr] || 0) > 0
            const intensity = has ? Math.min(1, v / max) : 0
            const bg = has
              ? `color-mix(in oklch, var(--accent) ${Math.round(8 + intensity * 82)}%, transparent)`
              : 'var(--surface)'
            const i = wd * 24 + hr
            return (
              <motion.div
                key={hr}
                className="hcell"
                title={`${WD[wd]} ${String(hr).padStart(2, '0')}:00 — ${has ? v.toFixed(2) + '%/h' : 'no data'}`}
                style={{ background: bg }}
                initial={reduce ? false : { opacity: 0, scale: 0.6 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ duration: 0.3, ease: EASE, delay: Math.min(0.5, i * 0.0015) }}
              />
            )
          })}
        </div>
      ))}
    </div>
  )
}

export function TrendRows({ rows }) {
  const items = [
    ['hour', 'vs typical this hour'],
    ['day', 'vs typical day'],
    ['week', 'vs typical week'],
  ].filter(([k]) => rows?.[k] != null)
  if (!items.length) return <div className="empty">Still building history</div>
  return (
    <div className="rows">
      {items.map(([k, label]) => {
        const v = rows[k]
        const cls = v.startsWith('+') && v !== '+0%' ? 'up' : v.startsWith('-') ? 'down' : ''
        return (
          <div className="row" key={k}>
            <span className="k">{label}</span>
            <span className={`v tnum ${cls}`}>{v}</span>
          </div>
        )
      })}
    </div>
  )
}

// horizontal bars (by-model, top projects). rows: [{label, frac, text}]
export function Bars({ rows }) {
  if (!rows?.length) return <div className="empty">No data yet</div>
  return (
    <div className="bars">
      {rows.map((r, i) => (
        <div className="barrow" key={i}>
          <span className="bl" title={r.label}>{r.label}</span>
          <span className="btrack">
            <motion.span
              className="bfill"
              initial={{ scaleX: 0 }}
              animate={{ scaleX: Math.max(0.01, r.frac) }}
              transition={{ duration: 0.6, ease: EASE, delay: i * 0.04 }}
              style={{ transformOrigin: 'left' }}
            />
          </span>
          <span className="bv tnum">{r.text}</span>
        </div>
      ))}
    </div>
  )
}

export function Delta({ pct, suffix = 'vs last wk' }) {
  if (pct == null) return <span className="foot">— {suffix}</span>
  const up = pct > 0
  return (
    <span className={`foot tnum ${up ? 'danger' : pct < 0 ? 'good' : ''}`}>
      {up ? '▲' : pct < 0 ? '▼' : '•'} {Math.abs(pct).toFixed(0)}% {suffix}
    </span>
  )
}

const INC = {
  surge: ['🚨', 'spend surge'],
  exhausted: ['⛔', 'weekly exhausted'],
  stale: ['⚠', 'data stale'],
  limit_change: ['↺', 'limit change'],
}
export function IncidentTimeline({ items }) {
  if (!items?.length) return <div className="empty">No incidents recorded — all calm.</div>
  const dur = (h) => (h == null ? '' : h < 1 ? `${Math.round(h * 60)}m` : `${h.toFixed(1)}h`)
  return (
    <div className="rows">
      {items.slice(0, 10).map((e, i) => {
        const [icon, label] = INC[e.type] || ['•', e.type]
        let detail = ''
        if (e.type === 'surge') detail = `peak $${(e.peak_rate || 0).toFixed(0)}/h · ${dur(e.duration_h)}${e.burned ? ` · -$${e.burned.toFixed(2)}` : ''}`
        else if (e.type === 'limit_change') detail = (e.kind || '').replace('_', ' ')
        else if (e.duration_h != null) detail = dur(e.duration_h)
        return (
          <motion.div
            className="evt" key={i}
            initial={{ opacity: 0, x: -6 }} animate={{ opacity: 1, x: 0 }}
            transition={{ duration: 0.3, ease: EASE, delay: i * 0.03 }}
          >
            <span className="badge">{icon} {e.label ? e.label : label}{e.open ? ' · live' : ''}</span>
            <span style={{ color: 'var(--muted)' }}>{detail}</span>
            <span className="tnum" style={{ marginLeft: 'auto', color: 'var(--muted)' }}>{fmtWhen(e.start)}</span>
          </motion.div>
        )
      })}
    </div>
  )
}

export function LimitEvents({ events }) {
  if (!events?.length) return <div className="empty">No limit changes detected</div>
  const kind = { reset_early: 'reset early', limit_raised: 'limit raised' }
  return (
    <div className="rows">
      {events.slice(0, 6).map((e, i) => (
        <div className="evt" key={i}>
          <span className="badge">{e.label}</span>
          <span>{kind[e.kind] || e.kind}</span>
          <span className="tnum" style={{ marginLeft: 'auto' }}>{fmtWhen(e.at)}</span>
        </div>
      ))}
    </div>
  )
}
