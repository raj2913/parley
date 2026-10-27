import type { WsEvent } from '../types'
import { label, ms } from '../format'
import { Badge, FlagChip, ToolCallView, Violations } from './common'

export interface LiveTurn {
  key: number
  turnIndex: number | null
  callerText: string
  events: WsEvent[]
  done: boolean
}

function IntentFields({ e }: { e: Extract<WsEvent, { type: 'intent' }> }) {
  const i = e.intent
  const extracted: [string, string][] = []
  if (i.amount !== null) extracted.push(['amount', `$${i.amount}${i.amount_frequency ? ` (${label(i.amount_frequency)})` : ''}`])
  if (i.date_of_birth) extracted.push(['dob', i.date_of_birth])
  if (i.zip_code) extracted.push(['zip', i.zip_code])
  if (i.preferred_date) extracted.push(['date', i.preferred_date])
  return (
    <div className="ev">
      <div className="ev-head">
        <span className="ev-kind">intent</span>
        <code className="strong">{i.intent}</code>
        {i.compliance_flags.map((f) => (
          <FlagChip key={f} flag={f} />
        ))}
      </div>
      <div className="small">{i.summary}</div>
      {extracted.length > 0 && (
        <div className="small muted">
          {extracted.map(([k, v]) => (
            <span key={k} className="kvi">
              {k}: <span className="mono">{v}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  )
}

function EventView({ e }: { e: WsEvent }) {
  switch (e.type) {
    case 'turn_started':
      return null
    case 'intent':
      return <IntentFields e={e} />
    case 'route':
      return (
        <div className="ev">
          <div className="ev-head">
            <span className="ev-kind">route</span>
            <code>{e.route}</code>
            <span className="muted small">→ {e.stage}</span>
          </div>
        </div>
      )
    case 'tool_call':
      return (
        <div className="ev">
          <div className="ev-head">
            <span className="ev-kind">tool</span>
          </div>
          <ToolCallView call={e} />
        </div>
      )
    case 'guard':
      return (
        <div className="ev">
          <div className="ev-head">
            <span className="ev-kind">guard</span>
            {e.ok ? <Badge tone="good">passed</Badge> : <Badge tone="bad">draft rejected</Badge>}
            <span className="muted small">attempt {e.attempt + 1}</span>
          </div>
          <Violations violations={e.violations} />
        </div>
      )
    case 'agent_message':
      return (
        <div className="ev">
          <div className="ev-head">
            <span className="ev-kind">spoken</span>
            <span className="muted small">{label(e.route)}</span>
            <span className="muted small push">{ms(e.latency_ms)}</span>
          </div>
        </div>
      )
    case 'error':
      return (
        <div className="ev">
          <div className="ev-head">
            <span className="ev-kind">error</span>
            <span className="bad-text small">{e.message}</span>
          </div>
        </div>
      )
  }
}

export function LiveTrace({ turns }: { turns: LiveTurn[] }) {
  if (!turns.length) {
    return (
      <div className="muted small pad">
        Each caller turn appears here as it is processed: the classified intent, the
        controller's route, any offers checked against policy, and the output guard's verdict.
      </div>
    )
  }
  return (
    <div className="live-turns">
      {[...turns].reverse().map((t) => (
        <section key={t.key} className="live-turn">
          <header>
            <span className="mono small muted">turn {t.turnIndex ?? '…'}</span>
            <span className="quote small">“{t.callerText}”</span>
            {!t.done && <span className="spinner" aria-label="processing" />}
          </header>
          {t.events.map((e, i) => (
            <EventView key={i} e={e} />
          ))}
        </section>
      ))}
    </div>
  )
}
