// Reads the live CSS custom properties so uPlot (which needs concrete color
// strings, not CSS vars) draws in the current light/dark palette, and re-emits
// on system theme flips.
import { useEffect, useState } from 'react'

export function readColors() {
  const s = getComputedStyle(document.documentElement)
  const g = (n) => s.getPropertyValue(n).trim()
  return {
    fg: g('--fg'),
    muted: g('--muted'),
    border: g('--border'),
    grid: g('--grid'),
    accent: g('--accent'),
    danger: g('--danger'),
    good: g('--good'),
    surface: g('--surface'),
  }
}

// Bumps a counter whenever the OS theme changes, so plots can recreate.
export function useThemeColors() {
  const [colors, setColors] = useState(readColors)
  useEffect(() => {
    const mq = window.matchMedia('(prefers-color-scheme: dark)')
    const on = () => requestAnimationFrame(() => setColors(readColors()))
    mq.addEventListener('change', on)
    return () => mq.removeEventListener('change', on)
  }, [])
  return colors
}
