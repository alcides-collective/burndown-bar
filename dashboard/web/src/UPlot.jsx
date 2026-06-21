import { useEffect, useRef } from 'react'
import uPlot from 'uplot'
import { motion, useReducedMotion } from 'motion/react'

// Thin React wrapper: create the plot once, setData on change, resize to the
// container, and recreate when `rev` changes (e.g. theme flip rebuilds opts).
// The whole thing is wrapped in a left-to-right clip reveal for the draw-on
// feel, honouring reduced-motion.
export default function UPlot({ makeOpts, data, height = 200, rev = 0 }) {
  const host = useRef(null)
  const plot = useRef(null)
  const reduce = useReducedMotion()

  useEffect(() => {
    if (!host.current) return
    const w = host.current.clientWidth || 600
    const opts = makeOpts(w, height)
    plot.current = new uPlot(opts, data, host.current)
    const ro = new ResizeObserver((es) => {
      const cw = Math.floor(es[0].contentRect.width)
      if (cw > 0) plot.current?.setSize({ width: cw, height })
    })
    ro.observe(host.current)
    return () => { ro.disconnect(); plot.current?.destroy(); plot.current = null }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rev, height])

  useEffect(() => {
    if (plot.current && data) plot.current.setData(data)
  }, [data])

  return (
    <motion.div
      className="uplot-host"
      initial={reduce ? false : { clipPath: 'inset(0 100% 0 0)', opacity: 0.4 }}
      animate={{ clipPath: 'inset(0 0% 0 0)', opacity: 1 }}
      transition={{ duration: 0.7, ease: [0.32, 0.72, 0, 1] }}
    >
      <div ref={host} />
    </motion.div>
  )
}
