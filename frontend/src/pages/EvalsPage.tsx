import { Link, useNavigate, useParams } from 'react-router-dom'
import { api } from '../api'
import type { EvalMetric, EvalRun, Json } from '../types'
import { label, ms, shortSha, when } from '../format'
import { Badge, Field, Loading, PassBadge, useAsync } from '../components/common'

function SummaryCounts({ summary }: { summary: Record<string, Json> }) {
  const entries = Object.entries(summary).filter(([, v]) => typeof v === 'number' || typeof v === 'string')
  if (!entries.length) return <span className="muted">—</span>
  return (
    <span className="row-gap wrap small">
      {entries.map(([k, v]) => (
        <span key={k}>
          <span className="muted">{label(k)}</span> <span className="mono">{String(v)}</span>
        </span>
      ))}
    </span>
  )
}

export function EvalsPage() {
  const runs = useAsync(() => api.evalRuns(), [])
  const scenarios = useAsync(() => api.scenarios(), [])
  const navigate = useNavigate()

  return (
    <div className="page">
      <div className="page-head">
        <h1>Evals</h1>
        <span className="muted small">
          Release gate: every change to instructions, policy or model runs this suite in CI.
        </span>
        <button className="btn btn-ghost push" onClick={runs.reload}>
          Refresh
        </button>
      </div>

      <section className="panel">
        <div className="panel-head">
          <h2>Suite: {scenarios.data?.length ?? '…'} scenarios</h2>
        </div>
        <Loading error={scenarios.error} loading={scenarios.loading && !scenarios.data} />
        {scenarios.data && (
          <table className="table compact">
            <tbody>
              {scenarios.data.map((s) => (
                <tr key={s.id}>
                  <td className="mono">{s.id}</td>
                  <td>{s.description}</td>
                  <td>
                    <Badge tone={s.caller_mode === 'simulated' ? 'accent' : 'neutral'}>{s.caller_mode}</Badge>
                  </td>
                  <td className="row-gap wrap">
                    {s.tags.map((t) => (
                      <Badge key={t}>{t}</Badge>
                    ))}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className="panel">
        <div className="panel-head">
          <h2>Runs</h2>
        </div>
        <Loading error={runs.error} loading={runs.loading && !runs.data} />
        {runs.data && runs.data.length === 0 && (
          <div className="empty">
            No runs yet. Run <code>uv run parley eval run</code> in <code>backend/</code>.
          </div>
        )}
        {runs.data && runs.data.length > 0 && (
          <table className="table clickable">
            <thead>
              <tr>
                <th>Result</th>
                <th>Kind</th>
                <th>Label</th>
                <th>Commit</th>
                <th>Agent prompt</th>
                <th>Model</th>
                <th>Summary</th>
                <th>Started</th>
              </tr>
            </thead>
            <tbody>
              {runs.data.map((r: EvalRun) => (
                <tr key={r.id} onClick={() => navigate(`/evals/${r.id}`)}>
                  <td>
                    {r.status === 'running' ? <Badge tone="info">running</Badge> : <PassBadge passed={r.passed} />}
                  </td>
                  <td>{r.kind}</td>
                  <td>{r.label ?? <span className="muted">—</span>}</td>
                  <td className="mono">{shortSha(r.git_sha)}</td>
                  <td className="mono small">{r.agent_prompt_ref}</td>
                  <td className="mono small">{r.agent_model}</td>
                  <td>
                    <SummaryCounts summary={r.summary} />
                  </td>
                  <td className="muted">{when(r.started_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}

function MetricRow({ m }: { m: EvalMetric }) {
  return (
    <li className={`metric ${m.success ? 'ok' : 'fail'}`}>
      <div className="metric-head">
        {m.success ? <Badge tone="good">pass</Badge> : <Badge tone="bad">fail</Badge>}
        <span className="strong">{m.name}</span>
        {m.score !== null && m.score !== undefined && (
          <span className="mono small muted">
            {Number(m.score).toFixed(2)}
            {m.threshold !== null && m.threshold !== undefined ? ` / ${m.threshold}` : ''}
          </span>
        )}
      </div>
      {m.reason && <div className={`small ${m.success ? 'muted' : ''}`}>{m.reason}</div>}
    </li>
  )
}

export function EvalRunPage() {
  const { id = '' } = useParams()
  const run = useAsync(() => api.evalRun(id), [id])
  const r = run.data
  return (
    <div className="page">
      <Loading error={run.error} loading={run.loading && !r} />
      {r && (
        <>
          <div className="page-head">
            <div>
              <div className="crumbs small">
                <Link to="/evals">Evals</Link> / <span className="mono">{r.id.slice(0, 8)}</span>
              </div>
              <h1>
                {r.label ?? `${r.kind} run`}{' '}
                {r.status === 'running' ? <Badge tone="info">running</Badge> : <PassBadge passed={r.passed} />}
              </h1>
            </div>
            <button className="btn btn-ghost push" onClick={run.reload}>
              Refresh
            </button>
          </div>
          <div className="meta-grid">
            <Field k="commit">
              <span className="mono">{shortSha(r.git_sha)}</span>
            </Field>
            <Field k="policy">
              <span className="mono">{r.policy_version}</span>
            </Field>
            <Field k="agent prompt">
              <span className="mono">{r.agent_prompt_ref}</span>
            </Field>
            <Field k="classifier prompt">
              <span className="mono">{r.classifier_prompt_ref}</span>
            </Field>
            <Field k="model">
              <span className="mono">{r.agent_model}</span>
            </Field>
            <Field k="started">{when(r.started_at)}</Field>
            <Field k="finished">{when(r.finished_at)}</Field>
            <Field k="summary">
              <SummaryCounts summary={r.summary} />
            </Field>
          </div>
          <div className="results">
            {[...r.results]
              .sort((a, b) => Number(a.passed) - Number(b.passed))
              .map((res) => {
                const failing = res.metrics.filter((m) => !m.success)
                const passing = res.metrics.filter((m) => m.success)
                return (
                  <section key={res.id} className={`result ${res.passed ? 'ok' : 'fail'}`}>
                    <header>
                      <PassBadge passed={res.passed} />
                      <span className="mono strong">{res.scenario_id}</span>
                      <span className="muted small">{ms(res.duration_ms)}</span>
                      <span className="muted small">
                        {passing.length}/{res.metrics.length} checks
                      </span>
                      {res.conversation_id && (
                        <Link className="push small" to={`/conversations/${res.conversation_id}`}>
                          Conversation →
                        </Link>
                      )}
                    </header>
                    {res.error && <div className="notice notice-error small mono">{res.error}</div>}
                    {failing.length > 0 && (
                      <ul className="metrics">
                        {failing.map((m, i) => (
                          <MetricRow key={i} m={m} />
                        ))}
                      </ul>
                    )}
                    {passing.length > 0 && (
                      <details className="passing">
                        <summary className="small muted">{passing.length} passing checks</summary>
                        <ul className="metrics">
                          {passing.map((m, i) => (
                            <MetricRow key={i} m={m} />
                          ))}
                        </ul>
                      </details>
                    )}
                  </section>
                )
              })}
          </div>
        </>
      )}
    </div>
  )
}
