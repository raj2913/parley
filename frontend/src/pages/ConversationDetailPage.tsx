import type { ReactNode } from 'react'
import { Link, useParams } from 'react-router-dom'
import { api } from '../api'
import type { ConversationState, GuardTrace, LLMCall, Turn } from '../types'
import { label, money, ms, when } from '../format'
import {
  Badge,
  Field,
  FlagChip,
  JsonView,
  Loading,
  OutcomeBadge,
  ToolCallView,
  Violations,
  useAsync,
} from '../components/common'

function GuardView({ guard }: { guard: GuardTrace }) {
  const attempts = guard.attempts ?? []
  if (!attempts.length) return <div className="muted small">Scripted turn: no model draft to check.</div>
  return (
    <div className="stack-sm">
      {attempts.map((a) => {
        const rejected = a.violations.length > 0 || a.model_failed
        return (
          <div key={a.attempt} className={`draft ${rejected ? 'rejected' : 'ok'}`}>
            <div className="draft-head">
              <span className="muted small">draft {a.attempt + 1}</span>
              {rejected ? <Badge tone="bad">rejected — not spoken</Badge> : <Badge tone="good">passed</Badge>}
              {a.model_failed && <Badge tone="warn">model declined</Badge>}
            </div>
            <p className={rejected ? 'strike' : ''}>{a.draft || <em className="muted">(empty)</em>}</p>
            <Violations violations={a.violations} />
          </div>
        )
      })}
      {guard.fallback_used && (
        <div className="notice notice-warn small">
          Every draft failed the guard, so the controller's scripted fallback line was spoken instead.
          {guard.fallback_violations && guard.fallback_violations.length > 0 && (
            <Violations violations={guard.fallback_violations} />
          )}
        </div>
      )}
    </div>
  )
}

function StateView({ state }: { state: ConversationState }) {
  return (
    <div className="stack-sm">
      <div className="fields">
        <Field k="verified">{state.verified ? 'yes' : 'no'}</Field>
        <Field k="hardship">{state.hardship ? 'yes' : 'no'}</Field>
        <Field k="verify attempts">{state.verify_attempts}</Field>
        <Field k="disclosures">{state.disclosures_delivered.join(', ') || '—'}</Field>
        <Field k="active offer">{state.active_offer_id ?? '—'}</Field>
        <Field k="arrangement">{state.arrangement_offer_id ?? '—'}</Field>
      </div>
      {state.offers.length > 0 && (
        <table className="table compact">
          <thead>
            <tr>
              <th>Offer</th>
              <th>Kind</th>
              <th className="num">Monthly</th>
              <th className="num">Payments</th>
              <th className="num">Final</th>
              <th className="num">Lump sum</th>
              <th>Date</th>
              <th className="num">Total</th>
            </tr>
          </thead>
          <tbody>
            {state.offers.map((o) => (
              <tr key={o.id} className={o.id === state.active_offer_id ? 'highlight' : ''}>
                <td className="mono">
                  {o.id}
                  {o.id === state.active_offer_id && <Badge tone="accent">on table</Badge>}
                </td>
                <td>
                  {label(o.kind)}
                  {o.hardship && <Badge tone="info">hardship</Badge>}
                </td>
                <td className="num mono">{money(o.monthly_amount)}</td>
                <td className="num mono">{o.number_of_payments ?? '—'}</td>
                <td className="num mono">{money(o.final_payment)}</td>
                <td className="num mono">{money(o.lump_sum)}</td>
                <td className="mono">{o.first_payment_date}</td>
                <td className="num mono">{money(o.total)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <JsonView value={state} title="Full state JSON" />
    </div>
  )
}

function LLMCallView({ call }: { call: LLMCall }) {
  const u = call.usage
  const num = (k: string) => (typeof u[k] === 'number' ? (u[k] as number) : null)
  return (
    <div className="llmcall">
      <div className="llmcall-head">
        <code className="strong">{call.purpose}</code>
        <span className="mono small">{call.model}</span>
        {call.served_by && call.served_by !== call.model && (
          <Badge tone="warn">served by {call.served_by}</Badge>
        )}
        <span className="muted small">effort {call.effort}</span>
        <span className="muted small">{ms(call.latency_ms)}</span>
        <span className="muted small">stop: {call.stop_reason ?? '—'}</span>
      </div>
      <div className="small muted row-gap wrap">
        <span>in {num('input_tokens') ?? '—'}</span>
        <span>cache read {num('cache_read_input_tokens') ?? 0}</span>
        <span>cache write {num('cache_creation_input_tokens') ?? 0}</span>
        <span>out {num('output_tokens') ?? '—'}</span>
        <span>
          instructions{' '}
          <Link className="mono" to={`/policy#${encodeURIComponent(call.system_ref)}`}>
            {call.system_ref}
          </Link>
        </span>
        {call.request_id && <span className="mono">{call.request_id}</span>}
      </div>
      <JsonView value={call.request_messages} title={`Exact messages sent (${call.request_messages.length})`} />
      <JsonView value={call.response_content} title="Response content" />
    </div>
  )
}

function TurnView({ turn, disclosureTexts }: { turn: Turn; disclosureTexts: Record<string, string> }) {
  const injected = turn.disclosures
    .map((d) => ({ id: d, text: disclosureTexts[d] }))
    .filter((d) => d.text && turn.agent_text.includes(d.text))
  let agentBody: ReactNode = turn.agent_text
  if (injected.length) {
    let rest = turn.agent_text
    const parts: ReactNode[] = []
    for (const d of injected) {
      const at = rest.indexOf(d.text)
      if (at < 0) continue
      if (at > 0) parts.push(rest.slice(0, at))
      parts.push(
        <mark key={d.id} className="verbatim" title={`Verbatim from policy: ${d.id}`}>
          {d.text}
        </mark>,
      )
      rest = rest.slice(at + d.text.length)
    }
    parts.push(rest)
    agentBody = parts
  }

  return (
    <article className="turn">
      <div className="turn-index mono">#{turn.index}</div>
      <div className="turn-body">
        {turn.caller_text !== null && (
          <div className="bubble bubble-caller">
            <span className="bubble-who">Caller</span>
            <p>{turn.caller_text}</p>
          </div>
        )}
        <div className="bubble bubble-agent">
          <span className="bubble-who">
            Agent <span className="muted">· {label(turn.route)}</span>
            {turn.latency_ms > 0 && <span className="muted"> · {ms(turn.latency_ms)}</span>}
          </span>
          <p>{agentBody}</p>
        </div>

        <details className="why">
          <summary>Why did the agent say this?</summary>
          <div className="why-grid">
            <section>
              <h4>Intent</h4>
              {turn.intent ? (
                <>
                  <div className="row-gap wrap">
                    <code className="strong">{turn.intent.intent}</code>
                    {turn.intent.compliance_flags.map((f) => (
                      <FlagChip key={f} flag={f} />
                    ))}
                  </div>
                  <p className="small">{turn.intent.summary}</p>
                  <JsonView value={turn.intent} title="Intent JSON" />
                </>
              ) : (
                <div className="muted small">{turn.index === 0 ? 'Opening line (scripted).' : 'No intent (classifier unavailable).'}</div>
              )}
            </section>

            <section>
              <h4>Route</h4>
              <div className="fields">
                <Field k="route">
                  <code>{turn.route}</code>
                </Field>
                <Field k="stage">
                  {turn.stage_before} → {turn.stage_after}
                </Field>
                <Field k="reply">{turn.directive ? 'model-written, guarded' : 'scripted'}</Field>
              </div>
            </section>

            {turn.directive && (
              <section className="span2">
                <h4>Directive</h4>
                <p className="small">{turn.directive.goal}</p>
                {turn.directive.rules.length > 0 && (
                  <ul className="small">
                    {turn.directive.rules.map((r, i) => (
                      <li key={i}>{r}</li>
                    ))}
                  </ul>
                )}
                <div className="small muted">
                  Tools: {turn.directive.tools.length ? turn.directive.tools.join(', ') : 'none'} · Fallback:{' '}
                  <em>“{turn.directive.fallback_text}”</em>
                </div>
              </section>
            )}

            {turn.disclosures.length > 0 && (
              <section className="span2">
                <h4>Disclosures injected</h4>
                {turn.disclosures.map((d) => (
                  <div key={d} className="small">
                    <Badge tone="accent">{d}</Badge>{' '}
                    <span className="muted">read verbatim from the client policy — not written by the model:</span>
                    <blockquote>{disclosureTexts[d] ?? '(text not available)'}</blockquote>
                  </div>
                ))}
              </section>
            )}

            {turn.tool_calls.length > 0 && (
              <section className="span2">
                <h4>Offers checked against policy</h4>
                <div className="stack-sm">
                  {turn.tool_calls.map((c, i) => (
                    <ToolCallView key={i} call={c} />
                  ))}
                </div>
              </section>
            )}

            <section className="span2">
              <h4>Output guard</h4>
              <GuardView guard={turn.guard} />
            </section>

            <section className="span2">
              <h4>State after turn</h4>
              <StateView state={turn.state_after} />
            </section>

            {turn.redactions?.length > 0 && (
              <section className="span2">
                <h4>Redacted before processing</h4>
                <p className="small">
                  {turn.redactions.map((r) => label(r)).join(', ')} removed from the caller's words
                  before the model, the database, or any log saw them.
                </p>
              </section>
            )}

            {turn.crm_actions.length > 0 && (
              <section className="span2">
                <h4>CRM actions</h4>
                {turn.crm_actions.map((a, i) => (
                  <div key={i} className="stack-sm">
                    <Badge tone="info">{label(a.type)}</Badge>
                    <JsonView value={a.payload} title="Payload" />
                  </div>
                ))}
              </section>
            )}

            {turn.llm_calls.length > 0 && (
              <section className="span2">
                <h4>Model calls ({turn.llm_calls.length})</h4>
                <div className="stack-sm">
                  {turn.llm_calls.map((c, i) => (
                    <LLMCallView key={i} call={c} />
                  ))}
                </div>
              </section>
            )}
          </div>
        </details>
      </div>
    </article>
  )
}

export function ConversationDetailPage() {
  const { id = '' } = useParams()
  const conv = useAsync(() => api.conversation(id), [id])
  const policy = useAsync(() => api.policy(), [])
  const disclosureTexts: Record<string, string> = {}
  for (const [k, v] of Object.entries(policy.data?.policy.disclosures ?? {})) {
    disclosureTexts[k] = v.text.split(/\s+/).join(' ')
  }

  const c = conv.data
  return (
    <div className="page">
      <Loading error={conv.error} loading={conv.loading && !c} />
      {c && (
        <>
          <div className="page-head">
            <div>
              <div className="crumbs small">
                <Link to="/conversations">Conversations</Link> /{' '}
                <span className="mono">{c.id.slice(0, 8)}</span>
              </div>
              <h1>
                {c.account.first_name} {c.account.last_name}{' '}
                <span className="mono muted small">{c.account.reference}</span>
              </h1>
            </div>
            <div className="push row-gap">
              <Badge>{c.channel}</Badge>
              <OutcomeBadge outcome={c.outcome} />
              <button className="btn btn-ghost" onClick={conv.reload}>
                Refresh
              </button>
            </div>
          </div>
          <div className="meta-grid">
            <Field k="creditor">{c.account.original_creditor}</Field>
            <Field k="balance">{money(c.account.balance)}</Field>
            <Field k="status">{label(c.status)} · {label(c.stage)}</Field>
            <Field k="today">{c.today}</Field>
            <Field k="started">{when(c.started_at)}</Field>
            <Field k="policy">
              <span className="mono">{c.policy_version}</span>
            </Field>
            <Field k="agent prompt">
              <span className="mono">{c.agent_prompt_ref}</span>
            </Field>
            <Field k="classifier prompt">
              <span className="mono">{c.classifier_prompt_ref}</span>
            </Field>
            <Field k="model">
              <span className="mono">{c.agent_model}</span>
            </Field>
            {c.eval_run_id && (
              <Field k="eval run">
                <Link className="mono" to={`/evals/${c.eval_run_id}`}>
                  {c.eval_run_id.slice(0, 8)}
                </Link>
              </Field>
            )}
            {c.replay_of && (
              <Field k="replay of">
                <Link className="mono" to={`/conversations/${c.replay_of}`}>
                  {c.replay_of.slice(0, 8)}
                </Link>
              </Field>
            )}
          </div>
          <div className="timeline">
            {c.turns.map((t) => (
              <TurnView key={t.index} turn={t} disclosureTexts={disclosureTexts} />
            ))}
          </div>
        </>
      )}
    </div>
  )
}
