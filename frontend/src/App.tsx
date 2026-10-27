import { BrowserRouter, NavLink, Route, Routes } from 'react-router-dom'
import { api } from './api'
import { useAsync } from './components/common'
import { CallPage } from './pages/CallPage'
import { ConversationDetailPage } from './pages/ConversationDetailPage'
import { ConversationsPage } from './pages/ConversationsPage'
import { EvalRunPage, EvalsPage } from './pages/EvalsPage'
import { PolicyPage } from './pages/PolicyPage'

function Nav() {
  const config = useAsync(() => api.config(), [])
  return (
    <header className="nav">
      <NavLink to="/" className="wordmark">
        <svg viewBox="0 0 32 32" width="20" height="20" aria-hidden="true">
          <rect width="32" height="32" rx="7" fill="var(--accent)" />
          <path
            d="M9 23V9h7.5a4.5 4.5 0 0 1 0 9H13"
            fill="none"
            stroke="#fff"
            strokeWidth="3"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
        Parley
      </NavLink>
      <nav>
        <NavLink to="/" end>
          Call
        </NavLink>
        <NavLink to="/conversations">Conversations</NavLink>
        <NavLink to="/evals">Evals</NavLink>
        <NavLink to="/policy">Policy</NavLink>
      </nav>
      <div className="nav-meta mono">
        {config.error ? (
          <span className="bad-text">backend offline</span>
        ) : config.data ? (
          <>
            <span title="agent instructions">{config.data.agent_prompt_ref}</span>
            <span title="client policy">{config.data.policy_version}</span>
          </>
        ) : null}
      </div>
    </header>
  )
}

export default function App() {
  return (
    <BrowserRouter>
      <Nav />
      <main>
        <Routes>
          <Route path="/" element={<CallPage />} />
          <Route path="/conversations" element={<ConversationsPage />} />
          <Route path="/conversations/:id" element={<ConversationDetailPage />} />
          <Route path="/evals" element={<EvalsPage />} />
          <Route path="/evals/:id" element={<EvalRunPage />} />
          <Route path="/policy" element={<PolicyPage />} />
          <Route path="*" element={<div className="page empty">Not found.</div>} />
        </Routes>
      </main>
    </BrowserRouter>
  )
}
