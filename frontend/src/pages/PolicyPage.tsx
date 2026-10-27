import { useEffect, useState } from 'react'
import { useLocation } from 'react-router-dom'
import { api } from '../api'
import type { PlanLimits } from '../types'
import { money, when } from '../format'
import { Badge, Field, Loading, useAsync } from '../components/common'

function pct(fraction: string): string {
  const n = Number(fraction)
  return Number.isFinite(n) ? `${Math.round(n * 100)}%` : fraction
}

function PlanCard({ title, plan }: { title: string; plan: PlanLimits }) {
  return (
    <div className="limit-card">
      <h4>{title}</h4>
      <Field k="min monthly">{money(plan.min_monthly_payment)}</Field>
      <Field k="max payments">{plan.max_payments}</Field>
      <Field k="first payment within">{plan.first_payment_within_days} days</Field>
    </div>
  )
}

export function PolicyPage() {
  const config = useAsync(() => api.config(), [])
  const policy = useAsync(() => api.policy(), [])
  const prompts = useAsync(() => api.prompts(), [])
  const { hash } = useLocation()
  const [openRef, setOpenRef] = useState<string | null>(null)

  useEffect(() => {
    if (hash) setOpenRef(decodeURIComponent(hash.slice(1)))
  }, [hash])

  const p = policy.data?.policy
  return (
    <div className="page">
      <div className="page-head">
        <h1>Policy & instructions</h1>
        {policy.data && <span className="mono small muted">{policy.data.fingerprint}</span>}
      </div>
      <Loading error={policy.error ?? config.error} loading={(policy.loading || config.loading) && !p} />

      {config.data && (
        <section className="panel">
          <div className="panel-head">
            <h2>Running configuration</h2>
          </div>
          <div className="meta-grid">
            <Field k="client">{config.data.client_name}</Field>
            <Field k="agent">{config.data.agent_name}</Field>
            <Field k="agent model">
              <span className="mono">
                {config.data.agent_model} · {config.data.agent_effort}
              </span>
            </Field>
            <Field k="classifier model">
              <span className="mono">{config.data.classifier_model}</span>
            </Field>
            <Field k="agent prompt">
              <span className="mono">{config.data.agent_prompt_ref}</span>
            </Field>
            <Field k="classifier prompt">
              <span className="mono">{config.data.classifier_prompt_ref}</span>
            </Field>
          </div>
        </section>
      )}

      {p && (
        <section className="panel">
          <div className="panel-head">
            <h2>Negotiation limits</h2>
            <span className="muted small">enforced in code by the offer validator; the model only proposes</span>
          </div>
          <div className="limit-grid">
            <PlanCard title="Payment plan" plan={p.negotiation.payment_plan} />
            <PlanCard title="Hardship plan" plan={p.negotiation.hardship_plan} />
            <div className="limit-card">
              <h4>
                Settlement {p.negotiation.settlement.enabled ? <Badge tone="good">enabled</Badge> : <Badge>off</Badge>}
              </h4>
              <Field k="floor">
                {pct(p.negotiation.settlement.min_fraction_of_balance)} of balance{' '}
                <span className="muted small">(hidden from model)</span>
              </Field>
              <Field k="counter">{pct(p.negotiation.settlement.counter_fraction_of_balance)} of balance</Field>
              <Field k="pay within">{p.negotiation.settlement.pay_within_days} days</Field>
            </div>
            <div className="limit-card">
              <h4>Verification</h4>
              <Field k="factors">{p.verification.factors.join(' + ')}</Field>
              <Field k="max attempts">{p.verification.max_attempts}</Field>
              <Field k="pay in full within">{p.negotiation.pay_in_full.pay_within_days} days</Field>
            </div>
          </div>
          <h4 className="section-title">Disclosures (read verbatim, never generated)</h4>
          {Object.entries(p.disclosures).map(([id, d]) => (
            <div key={id} className="disclosure">
              <Badge tone="accent">{id}</Badge> <span className="muted small">{d.when}</span>
              <blockquote>{d.text}</blockquote>
            </div>
          ))}
          <div className="two-col">
            <div>
              <h4 className="section-title">Prohibited phrases</h4>
              <div className="row-gap wrap">
                {p.prohibited_phrases.map((x) => (
                  <Badge key={x} tone="bad">
                    {x}
                  </Badge>
                ))}
              </div>
            </div>
            <div>
              <h4 className="section-title">Never said before verification</h4>
              <div className="row-gap wrap">
                {p.third_party_sensitive_terms.map((x) => (
                  <Badge key={x} tone="warn">
                    {x}
                  </Badge>
                ))}
              </div>
            </div>
          </div>
          <details className="json">
            <summary>Raw policy YAML</summary>
            <pre>{policy.data?.yaml}</pre>
          </details>
        </section>
      )}

      <section className="panel">
        <div className="panel-head">
          <h2>Instruction versions</h2>
          <span className="muted small">every text ever sent to a model, by content hash</span>
        </div>
        <Loading error={prompts.error} loading={prompts.loading && !prompts.data} />
        {prompts.data && prompts.data.length === 0 && (
          <div className="empty small">Recorded after the first call is started.</div>
        )}
        {prompts.data?.map((v) => (
          <details
            key={v.ref}
            id={v.ref}
            className="prompt"
            open={openRef === v.ref}
            onToggle={(e) => {
              if ((e.target as HTMLDetailsElement).open) setOpenRef(v.ref)
            }}
          >
            <summary>
              <span className="mono">{v.ref}</span>
              {v.current && <Badge tone="good">current</Badge>}
              <span className="muted small push">first seen {when(v.first_seen_at)}</span>
            </summary>
            <pre className="prompt-text">{v.text}</pre>
          </details>
        ))}
      </section>
    </div>
  )
}
