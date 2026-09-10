import type { EventsPage, SessionState } from './types'

async function j<T>(r: Response): Promise<T> {
  if (!r.ok) {
    let detail = `${r.status}`
    try {
      detail = (await r.json()).detail ?? detail
    } catch {
      /* ignore */
    }
    throw new Error(detail)
  }
  return (await r.json()) as T
}

export const sessionApi = {
  get: () => fetch('/api/session').then((r) => j<SessionState>(r)),
  control: (action: string, extra: Record<string, number | undefined> = {}) =>
    fetch('/api/session', { method: 'POST', headers: { 'content-type': 'application/json' }, body: JSON.stringify({ action, ...extra }) }).then((r) => j<SessionState>(r)),
  events: (windowS: number, until: number | null, after: string | null, limit = 500, backfillS = 1800) => {
    const q = new URLSearchParams({ window: `${windowS}s`, limit: String(limit), backfill_s: String(backfillS) })
    if (until !== null) q.set('until', String(Math.round(until)))
    if (after) q.set('after', after)
    return fetch(`/api/events?${q}`).then((r) => j<EventsPage>(r))
  },
}
