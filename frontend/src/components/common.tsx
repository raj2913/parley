import { useCallback, useEffect, useState, type ReactNode } from 'react'
import type { Json, Stage, ToolCall, Violation } from '../types'
import { STAGES } from '../types'
import { label } from '../format'

export function useAsync<T>(load: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const run = useCallback(load, deps)
  const reload = useCallback(() => {
    setLoading(true)
    run()
      .then((d) => {
        setData(d)
        setError(null)
      })
      .catch((e: unknown) => setError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [run])
  useEffect(() => {
    reload()
  }, [reload])
  return { data, error, loading, reload }
}

export function Loading({ error, loading }: { error: string | null; loading: boolean }) {
  if (error) return <div className="notice notice-error">Couldn't load: {error}</div>
  if (loading) return <div className="muted pad">Loading…</div>
  return null
}

type Tone = 'neutral' | 'good' | 'bad' | 'warn' | 'info' | 'accent'

export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return <span className={`badge badge-${tone}`}>{children}</span>
}

const FLAG_TONE: Record<string, Tone> = {
  cease_contact: 'bad',
  dispute: 'warn',
  attorney_represented: 'bad',
  bankruptcy: 'bad',
  hardship: 'info',
}

export function FlagChip({ flag }: { flag: string }) {
  return <Badge tone={FLAG_TONE[flag] ?? 'neutral'}>{label(flag)}</Badge>
}

const OUTCOME_TONE: Record<string, Tone> = {
  arrangement: 'good',
  callback: 'info',
  no_contact: 'neutral',
  wrong_number: 'neutral',
  disputed: 'warn',
  cease_contact: 'bad',
  attorney_represented: 'warn',
  bankruptcy: 'warn',
  verification_failed: 'bad',
  verification_refused: 'warn',
  abusive: 'bad',
  max_turns: 'warn',
}

export function OutcomeBadge({ outcome }: { outcome: string | null }) {
  if (!outcome) return <span className="muted">—</span>
  return <Badge tone={OUTCOME_TONE[outcome] ?? 'neutral'}>{label(outcome)}</Badge>
}

export function StatusBadge({ status }: { status: string }) {
  const tone: Tone =
    status === 'active' ? 'good' : status === 'arrangement' ? 'info' : 'warn'
  return <Badge tone={tone}>{label(status)}</Badge>
}

export function PassBadge({ passed }: { passed: boolean | null }) {
  if (passed === null) return <Badge tone="neutral">running</Badge>
  return passed ? <Badge tone="good">pass</Badge> : <Badge tone="bad">fail</Badge>
}

export function StageProgress({ stage, visited }: { stage: Stage; visited?: Stage[] }) {
  const current = STAGES.indexOf(stage)
  const reached = new Set(visited ?? STAGES.slice(0, current + 1))
  return (
    <ol className="stages">
      {STAGES.map((s, i) => {
        // A call can close early (wrong number, cease request...): stages it never reached
        // are shown as skipped rather than completed.
        const cls =
          i === current ? 'current' : i < current ? (reached.has(s) ? 'done' : 'skipped') : ''
        return (
          <li key={s} className={cls} title={cls === 'skipped' ? 'skipped' : undefined}>
            {s}
          </li>
        )
      })}
    </ol>
  )
}

export function JsonView({
  value,
  title = 'JSON',
  open = false,
}: {
  value: Json | object | null | undefined
  title?: string
  open?: boolean
}) {
  return (
    <details className="json" open={open}>
      <summary>{title}</summary>
      <pre>{JSON.stringify(value, null, 2)}</pre>
    </details>
  )
}

export function Violations({ violations }: { violations: Violation[] }) {
  if (!violations.length) return null
  return (
    <ul className="violations">
      {violations.map((v, i) => (
        <li key={i}>
          <code>{v.rule}</code> {v.detail}
        </li>
      ))}
    </ul>
  )
}

function inlineArgs(input: Record<string, Json>): string {
  return Object.entries(input)
    .map(([k, v]) => `${k}=${typeof v === 'string' ? v : JSON.stringify(v)}`)
    .join(', ')
}

export function ToolCallView({ call }: { call: ToolCall }) {
  const approved = call.result?.approved === true
  const rest = Object.entries(call.result ?? {}).filter(
    ([k]) => !['approved', 'reason', 'terms', 'offer_id'].includes(k),
  )
  return (
    <div className={`toolcall ${approved ? 'ok' : 'rejected'}`}>
      <div className="toolcall-head">
        <code>
          {call.name}({inlineArgs(call.input ?? {})})
        </code>
        {approved ? (
          <Badge tone="good">approved</Badge>
        ) : (
          <Badge tone="bad">{call.error ? 'not allowed' : 'rejected'}</Badge>
        )}
        {call.attempt > 0 && <span className="muted small">attempt {call.attempt + 1}</span>}
      </div>
      {approved && typeof call.result.terms === 'string' && (
        <div className="small">
          <span className="muted">{String(call.result.offer_id)}:</span> {call.result.terms}
        </div>
      )}
      {!approved && typeof call.result?.reason === 'string' && (
        <div className="small bad-text">{call.result.reason}</div>
      )}
      {rest.length > 0 && (
        <dl className="kv small">
          {rest.map(([k, v]) => (
            <div key={k}>
              <dt>{label(k)}</dt>
              <dd>{Array.isArray(v) ? v.join(', ') : String(v)}</dd>
            </div>
          ))}
        </dl>
      )}
    </div>
  )
}

export function Field({ k, children }: { k: string; children: ReactNode }) {
  return (
    <div className="field">
      <span className="field-k">{k}</span>
      <span className="field-v">{children}</span>
    </div>
  )
}
