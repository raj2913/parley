import { useCallback, useEffect, useRef, useState } from 'react'
import { Link } from 'react-router-dom'
import { api, ApiError, wsUrl } from '../api'
import type { Account, Stage, WsEvent } from '../types'
import { BLOCKING_STATUSES } from '../types'
import { money } from '../format'
import { Loading, OutcomeBadge, StageProgress, StatusBadge, useAsync } from '../components/common'
import { LiveTrace, type LiveTurn } from '../components/LiveTrace'
import { useVoice, voiceSupported } from '../voice'

type Channel = 'chat' | 'voice'

interface Line {
  role: 'agent' | 'caller' | 'system'
  text: string
}

function localTime(timeZone: string): string {
  try {
    return new Intl.DateTimeFormat('en-US', {
      timeZone,
      hour: 'numeric',
      minute: '2-digit',
    }).format(new Date())
  } catch {
    return '?'
  }
}

export function CallPage() {
  const accounts = useAsync(() => api.accounts(), [])
  const [selected, setSelected] = useState<string | null>(null)
  const [channel, setChannel] = useState<Channel>('chat')
  const [startError, setStartError] = useState<string | null>(null)

  const [conversationId, setConversationId] = useState<string | null>(null)
  const [lines, setLines] = useState<Line[]>([])
  const [turns, setTurns] = useState<LiveTurn[]>([])
  const [stage, setStageRaw] = useState<Stage>('opening')
  const [visited, setVisited] = useState<Stage[]>(['opening'])
  const [blockedCode, setBlockedCode] = useState<string | null>(null)
  const setStage = useCallback((s: Stage) => {
    setStageRaw(s)
    setVisited((v) => (v.includes(s) ? v : [...v, s]))
  }, [])
  const [outcome, setOutcome] = useState<string | null>(null)
  const [ended, setEnded] = useState(false)
  const [inFlight, setInFlight] = useState(false)
  const [draft, setDraft] = useState('')

  const wsRef = useRef<WebSocket | null>(null)
  const turnKey = useRef(0)
  const transcriptEnd = useRef<HTMLDivElement | null>(null)
  const channelRef = useRef(channel)
  channelRef.current = channel

  const send = useCallback((text: string) => {
    const ws = wsRef.current
    const clean = text.trim()
    if (!ws || ws.readyState !== WebSocket.OPEN || !clean) return
    turnKey.current += 1
    setLines((l) => [...l, { role: 'caller', text: clean }])
    setTurns((t) => [
      ...t,
      { key: turnKey.current, turnIndex: null, callerText: clean, events: [], done: false },
    ])
    setInFlight(true)
    ws.send(JSON.stringify({ type: 'caller_message', text: clean }))
  }, [])

  const voice = useVoice(send)
  const voiceRef = useRef(voice)
  voiceRef.current = voice

  const onEvent = useCallback((e: WsEvent) => {
    setTurns((all) => {
      if (!all.length) return all
      const last = all[all.length - 1]
      const updated: LiveTurn = {
        ...last,
        events: [...last.events, e],
        turnIndex: e.type === 'turn_started' || e.type === 'agent_message' ? e.turn_index : last.turnIndex,
        done: last.done || e.type === 'agent_message' || e.type === 'error',
      }
      return [...all.slice(0, -1), updated]
    })
    if (e.type === 'route') setStage(e.stage)
    if (e.type === 'agent_message') {
      setInFlight(false)
      setStage(e.stage)
      setLines((l) => [...l, { role: 'agent', text: e.text }])
      if (e.end_call) {
        setEnded(true)
        setOutcome(e.outcome)
      }
      if (channelRef.current === 'voice') voiceRef.current.speak(e.text, !e.end_call)
    }
    if (e.type === 'error') {
      setInFlight(false)
      setLines((l) => [...l, { role: 'system', text: e.message }])
      if (e.closed) setEnded(true)
    }
  }, [])

  useEffect(() => {
    transcriptEnd.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [lines, inFlight])

  useEffect(() => () => wsRef.current?.close(), [])

  const resetCall = () => {
    wsRef.current?.close()
    wsRef.current = null
    voice.stopAll()
    setConversationId(null)
    setLines([])
    setTurns([])
    setStageRaw('opening')
    setVisited(['opening'])
    setOutcome(null)
    setEnded(false)
    setInFlight(false)
    setDraft('')
  }

  const start = async (simulateLocalTime?: string) => {
    if (!selected) return
    resetCall()
    setStartError(null)
    setBlockedCode(null)
    try {
      const res = await api.startConversation(selected, channel, simulateLocalTime)
      setConversationId(res.conversation_id)
      setLines([{ role: 'agent', text: res.agent_text }])
      setStage(res.stage)
      const ws = new WebSocket(wsUrl(`/ws/conversations/${res.conversation_id}`))
      ws.onmessage = (m) => onEvent(JSON.parse(m.data) as WsEvent)
      ws.onclose = () => setInFlight(false)
      ws.onerror = () =>
        setLines((l) => [...l, { role: 'system', text: 'Connection to the agent was lost.' }])
      wsRef.current = ws
      if (channel === 'voice') voice.speak(res.agent_text, true)
    } catch (err) {
      setBlockedCode(err instanceof ApiError ? err.code : null)
      setStartError(
        err instanceof ApiError && err.status === 409
          ? `Call blocked: ${err.message}`
          : err instanceof Error
            ? err.message
            : String(err),
      )
    }
  }

  const hangUp = async () => {
    voice.stopAll()
    wsRef.current?.close()
    setEnded(true)
    if (conversationId) {
      try {
        const res = await api.hangUp(conversationId)
        setOutcome(res.outcome)
        setStage('closed')
      } catch {
        // The call is already over on screen; the server will show it as active.
      }
    }
  }

  const reset = async (a: Account) => {
    await api.resetAccount(a.id)
    accounts.reload()
  }

  const account = accounts.data?.find((a) => a.id === selected) ?? null
  const active = conversationId !== null && !ended
  const canVoice = voiceSupported()

  return (
    <div className="call-layout">
      <aside className="panel accounts-panel">
        <div className="panel-head">
          <h2>Accounts</h2>
          <span className="muted small">fictional demo data</span>
        </div>
        <Loading error={accounts.error} loading={accounts.loading && !accounts.data} />
        <ul className="account-list">
          {accounts.data?.map((a) => {
            const blocked = BLOCKING_STATUSES.includes(a.status)
            return (
              <li
                key={a.id}
                className={`account ${selected === a.id ? 'selected' : ''} ${blocked ? 'blocked' : ''}`}
                onClick={() => !active && setSelected(a.id)}
              >
                <div className="account-row">
                  <strong>
                    {a.first_name} {a.last_name}
                  </strong>
                  <span className="mono small">{money(a.balance)}</span>
                </div>
                <div className="account-row small muted">
                  <span>
                    <span className="mono">{a.reference}</span> · {a.original_creditor}
                  </span>
                  <StatusBadge status={a.status} />
                </div>
                <div className="account-row small muted">
                  <span>
                    DOB <span className="mono">{a.date_of_birth}</span> · ZIP{' '}
                    <span className="mono">{a.zip_code}</span> ·{' '}
                    <span title={a.timezone}>local {localTime(a.timezone)}</span>
                  </span>
                  {blocked && (
                    <button
                      className="btn btn-xs"
                      onClick={(ev) => {
                        ev.stopPropagation()
                        void reset(a)
                      }}
                    >
                      Reset
                    </button>
                  )}
                </div>
              </li>
            )
          })}
        </ul>
        <div className="start-box">
          <div className="segmented" role="radiogroup" aria-label="Channel">
            {(['chat', 'voice'] as Channel[]).map((c) => (
              <button
                key={c}
                role="radio"
                aria-checked={channel === c}
                className={channel === c ? 'on' : ''}
                disabled={active}
                onClick={() => setChannel(c)}
              >
                {c === 'chat' ? 'Chat' : 'Voice'}
              </button>
            ))}
          </div>
          {channel === 'voice' && !canVoice && (
            <div className="notice notice-warn small">
              Voice isn't supported in this browser. Use Chrome, or switch to Chat.
            </div>
          )}
          <button
            className="btn btn-primary btn-block"
            disabled={!selected || active || (channel === 'voice' && !canVoice)}
            onClick={() => void start()}
          >
            {account ? `Call ${account.first_name}` : 'Select an account'}
          </button>
          {startError && (
            <div className="notice notice-error small">
              {startError}
              {blockedCode === 'outside_calling_hours' && (
                <button className="btn btn-xs simulate-btn" onClick={() => void start('10:00')}>
                  Simulate this call at 10:00 AM their time
                </button>
              )}
            </div>
          )}
          <p className="muted small hint">
            You play the consumer. The account's DOB and ZIP are shown above so you can pass
            identity verification — or deliberately fail it.
          </p>
        </div>
      </aside>

      <section className="panel transcript-panel">
        <div className="panel-head">
          <h2>Call</h2>
          {conversationId && <StageProgress stage={stage} visited={visited} />}
          <div className="push row-gap">
            {ended && <OutcomeBadge outcome={outcome} />}
            {conversationId && (
              <Link className="small" to={`/conversations/${conversationId}`}>
                Open full trace →
              </Link>
            )}
          </div>
        </div>

        <div className="transcript">
          {!conversationId && (
            <div className="empty">
              <p>Pick an account and start a call. The agent speaks first.</p>
            </div>
          )}
          {lines.map((l, i) => (
            <div key={i} className={`bubble bubble-${l.role}`}>
              <span className="bubble-who">
                {l.role === 'agent' ? 'Agent' : l.role === 'caller' ? 'You' : 'System'}
              </span>
              <p>{l.text}</p>
            </div>
          ))}
          {inFlight && (
            <div className="bubble bubble-agent thinking">
              <span className="bubble-who">Agent</span>
              <p>
                thinking<span className="dots" />
              </p>
            </div>
          )}
          {ended && conversationId && <div className="call-ended">Call ended</div>}
          <div ref={transcriptEnd} />
        </div>

        {active && channel === 'chat' && (
          <form
            className="composer"
            onSubmit={(ev) => {
              ev.preventDefault()
              if (inFlight) return
              send(draft)
              setDraft('')
            }}
          >
            <input
              autoFocus
              value={draft}
              placeholder={inFlight ? 'Waiting for the agent…' : 'Say something as the caller…'}
              onChange={(ev) => setDraft(ev.target.value)}
            />
            <button className="btn btn-primary" disabled={inFlight || !draft.trim()}>
              Send
            </button>
            <button type="button" className="btn btn-ghost" onClick={hangUp}>
              Hang up
            </button>
          </form>
        )}
        {active && channel === 'voice' && (
          <div className="composer voice-bar">
            <button
              className={`mic ${voice.listening ? 'live' : ''}`}
              disabled={inFlight || voice.speaking}
              onClick={() => (voice.listening ? voice.stopListening() : voice.startListening())}
              aria-label={voice.listening ? 'Stop listening' : 'Start listening'}
            >
              <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true">
                <path
                  d="M12 15a3 3 0 0 0 3-3V6a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3Zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-2.08A7 7 0 0 0 19 12h-2Z"
                  fill="currentColor"
                />
              </svg>
            </button>
            <div className="voice-status">
              {voice.speaking
                ? 'Agent speaking…'
                : inFlight
                  ? 'Agent thinking…'
                  : voice.listening
                    ? voice.interim || 'Listening…'
                    : 'Mic off — click to talk'}
              {voice.error && <span className="bad-text small"> {voice.error}</span>}
            </div>
            <button type="button" className="btn btn-ghost" onClick={hangUp}>
              Hang up
            </button>
          </div>
        )}
      </section>

      <aside className="panel trace-panel">
        <div className="panel-head">
          <h2>Live trace</h2>
        </div>
        <LiveTrace turns={turns} />
      </aside>
    </div>
  )
}
