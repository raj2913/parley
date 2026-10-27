const usd = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' })

export function money(value: string | number | null | undefined): string {
  if (value === null || value === undefined || value === '') return '—'
  const n = typeof value === 'number' ? value : Number(value)
  return Number.isFinite(n) ? usd.format(n) : String(value)
}

const dateTime = new Intl.DateTimeFormat('en-US', {
  month: 'short',
  day: 'numeric',
  hour: 'numeric',
  minute: '2-digit',
})

export function when(iso: string | null | undefined): string {
  if (!iso) return '—'
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : dateTime.format(d)
}

export function ms(value: number | null | undefined): string {
  if (value === null || value === undefined) return '—'
  return value >= 1000 ? `${(value / 1000).toFixed(1)}s` : `${value}ms`
}

export function label(value: string | null | undefined): string {
  if (!value) return '—'
  return value.replace(/_/g, ' ')
}

export function shortSha(sha: string | null | undefined): string {
  return sha ? sha.slice(0, 7) : '—'
}
