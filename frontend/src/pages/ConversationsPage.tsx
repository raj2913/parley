import { useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { api } from '../api'
import { label, when } from '../format'
import { Badge, Loading, OutcomeBadge, useAsync } from '../components/common'

const CHANNELS = ['', 'chat', 'voice', 'eval', 'replay']

export function ConversationsPage() {
  const [channel, setChannel] = useState('')
  const list = useAsync(() => api.conversations({ channel: channel || undefined }), [channel])
  const navigate = useNavigate()

  return (
    <div className="page">
      <div className="page-head">
        <h1>Conversations</h1>
        <div className="segmented">
          {CHANNELS.map((c) => (
            <button key={c || 'all'} className={channel === c ? 'on' : ''} onClick={() => setChannel(c)}>
              {c || 'All'}
            </button>
          ))}
        </div>
        <button className="btn btn-ghost push" onClick={list.reload}>
          Refresh
        </button>
      </div>
      <Loading error={list.error} loading={list.loading && !list.data} />
      {list.data && list.data.length === 0 && <div className="empty">No conversations yet.</div>}
      {list.data && list.data.length > 0 && (
        <table className="table clickable">
          <thead>
            <tr>
              <th>Account</th>
              <th>Channel</th>
              <th>Stage</th>
              <th>Outcome</th>
              <th className="num">Turns</th>
              <th>Started</th>
              <th>Agent prompt</th>
              <th>Model</th>
            </tr>
          </thead>
          <tbody>
            {list.data.map((c) => (
              <tr key={c.id} onClick={() => navigate(`/conversations/${c.id}`)}>
                <td>
                  <strong>{c.account.name}</strong>{' '}
                  <span className="mono small muted">{c.account.reference}</span>
                </td>
                <td>
                  <Badge tone={c.channel === 'eval' || c.channel === 'replay' ? 'accent' : 'neutral'}>
                    {c.channel}
                  </Badge>
                </td>
                <td>
                  {label(c.stage)}
                  {c.status === 'active' && <span className="live-dot" title="active" />}
                </td>
                <td>
                  <OutcomeBadge outcome={c.outcome} />
                </td>
                <td className="num mono">{c.turn_count}</td>
                <td className="muted">{when(c.started_at)}</td>
                <td className="mono small">{c.agent_prompt_ref}</td>
                <td className="mono small">{c.agent_model}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
