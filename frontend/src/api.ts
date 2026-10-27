import type {
  Account,
  AppConfig,
  ConversationDetail,
  ConversationSummary,
  EvalRun,
  EvalRunDetail,
  PolicyResponse,
  PromptVersion,
  Scenario,
  TurnOutcome,
} from './types'

export class ApiError extends Error {
  status: number
  code: string | null
  constructor(status: number, message: string, code: string | null = null) {
    super(message)
    this.status = status
    this.code = code
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(init?.headers ?? {}) },
  })
  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    let code: string | null = null
    try {
      const body = await res.json()
      if (typeof body?.detail === 'string') message = body.detail
      else if (typeof body?.detail?.message === 'string') {
        message = body.detail.message
        code = typeof body.detail.code === 'string' ? body.detail.code : null
      }
    } catch {
      // keep status text
    }
    throw new ApiError(res.status, message, code)
  }
  return res.json() as Promise<T>
}

export const api = {
  config: () => request<AppConfig>('/api/config'),
  policy: () => request<PolicyResponse>('/api/policy'),
  prompts: () => request<PromptVersion[]>('/api/prompts'),
  accounts: () => request<Account[]>('/api/accounts'),
  resetAccount: (id: string) =>
    request<Account>(`/api/accounts/${id}/reset`, { method: 'POST' }),
  startConversation: (accountId: string, channel: string, simulateLocalTime?: string) =>
    request<TurnOutcome>('/api/conversations', {
      method: 'POST',
      body: JSON.stringify({
        account_id: accountId,
        channel,
        ...(simulateLocalTime ? { simulate_local_time: simulateLocalTime } : {}),
      }),
    }),
  hangUp: (conversationId: string) =>
    request<{ outcome: string | null }>(`/api/conversations/${conversationId}/hangup`, {
      method: 'POST',
    }),
  conversations: (params: { channel?: string; eval_run_id?: string } = {}) => {
    const q = new URLSearchParams()
    if (params.channel) q.set('channel', params.channel)
    if (params.eval_run_id) q.set('eval_run_id', params.eval_run_id)
    const qs = q.toString()
    return request<ConversationSummary[]>(`/api/conversations${qs ? `?${qs}` : ''}`)
  },
  conversation: (id: string) => request<ConversationDetail>(`/api/conversations/${id}`),
  evalRuns: () => request<EvalRun[]>('/api/eval-runs'),
  evalRun: (id: string) => request<EvalRunDetail>(`/api/eval-runs/${id}`),
  scenarios: () => request<Scenario[]>('/api/scenarios'),
}

export function wsUrl(path: string): string {
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  return `${proto}//${window.location.host}${path}`
}
