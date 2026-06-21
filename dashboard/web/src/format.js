// Formatting helpers — mirror the plugin's wording where it matters.

export const pct = (v, d = 0) => (v == null ? '—' : `${v.toFixed(d)}%`)
export const money = (v, d = 2) => (v == null ? '—' : `$${v.toFixed(d)}`)
export const mult = (v, d = 2) => (v == null ? '—' : `${v.toFixed(d)}×`)

export function tok(n) {
  if (n == null) return '—'
  if (n >= 1e9) return (n / 1e9).toFixed(2) + 'B'
  if (n >= 1e6) return (n / 1e6).toFixed(1) + 'M'
  if (n >= 1e3) return (n / 1e3).toFixed(0) + 'k'
  return String(Math.round(n))
}

export function fmtDur(h) {
  if (h == null) return '—'
  if (h < 1) return `${Math.round(h * 60)} min`
  if (h < 48) return `${h.toFixed(1)} h`
  return `${(h / 24).toFixed(1)} d`
}

export function fmtWhen(iso) {
  if (!iso) return '—'
  const t = new Date(iso)
  return t.toLocaleString(undefined, {
    weekday: 'short', hour: '2-digit', minute: '2-digit',
  })
}

// iso list [[iso, val], ...] -> [[epochSeconds, val], ...] for uPlot
export const toEpoch = (series) =>
  (series || []).map(([t, v]) => [Math.floor(new Date(t).getTime() / 1000), v])

// Align several [[x,y]] series onto one shared, sorted x axis (nulls fill gaps).
export function align(seriesList) {
  const xset = new Set()
  seriesList.forEach((s) => s.forEach(([x]) => xset.add(x)))
  const xs = [...xset].sort((a, b) => a - b)
  const idx = new Map(xs.map((x, i) => [x, i]))
  const ys = seriesList.map((s) => {
    const arr = new Array(xs.length).fill(null)
    s.forEach(([x, y]) => { arr[idx.get(x)] = y })
    return arr
  })
  return [xs, ...ys]
}
