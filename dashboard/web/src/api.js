// Poll the stdlib backend. The plugin rewrites the caches every ~5 min, so a
// 30s poll is plenty; we use no-store so we always see the latest compute.
export async function fetchData() {
  const r = await fetch('/api/data', { cache: 'no-store' })
  if (!r.ok) throw new Error(`api ${r.status}`)
  return r.json()
}

export const POLL_MS = 30_000
