export type Stage = 'opening' | 'verification' | 'negotiation' | 'closed'
export const STAGES: Stage[] = ['opening', 'verification', 'negotiation', 'closed']

export const BLOCKING_STATUSES = ['cease_contact', 'attorney_represented', 'bankruptcy', 'disputed']

export type Json = string | number | boolean | null | Json[] | { [key: string]: Json }

export interface Account {
  id: string
  reference: string
  first_name: string
  last_name: string
  date_of_birth: string
  zip_code: string
  phone: string
  original_creditor: string
  balance: string
  status: string
  timezone: string
}

export interface AppConfig {
  policy_version: string
  agent_prompt_ref: string
  classifier_prompt_ref: string
  agent_model: string
  agent_effort: string
  classifier_model: string
  agent_name: string
  client_name: string
}

export interface TurnOutcome {
  conversation_id: string
  turn_index: number
  agent_text: string
  stage: Stage
  route: string
  end_call: boolean
  outcome: string | null
}

export interface IntentResult {
  intent: string
  compliance_flags: string[]
  summary: string
  amount: number | null
  amount_frequency: 'monthly' | 'one_time' | null
  date_of_birth: string | null
  zip_code: string | null
  preferred_date: string | null
}

export interface Directive {
  stage: Stage
  goal: string
  rules: string[]
  tools: string[]
  fallback_text: string
}

export interface ToolCall {
  attempt: number
  name: string
  input: Record<string, Json>
  result: Record<string, Json>
  approved?: boolean
  error?: boolean
}

export interface Violation {
  rule: string
  detail: string
}

export interface GuardAttempt {
  attempt: number
  draft: string
  violations: Violation[]
  model_failed: boolean
}

export interface GuardTrace {
  attempts?: GuardAttempt[]
  fallback_used?: boolean
  fallback_violations?: Violation[]
}

export interface LLMCall {
  purpose: string
  model: string
  served_by: string | null
  effort: string
  system_ref: string
  request_messages: Json[]
  tools: string[]
  response_content: Json[]
  stop_reason: string | null
  usage: Record<string, Json>
  latency_ms: number
  request_id: string | null
}

export interface Offer {
  id: string
  kind: 'plan' | 'settlement' | 'pay_in_full'
  hardship: boolean
  monthly_amount: string | null
  number_of_payments: number | null
  final_payment: string | null
  lump_sum: string | null
  first_payment_date: string
  total: string
}

export interface ConversationState {
  stage: Stage
  turn_count: number
  identity_confirmed: boolean
  verified: boolean
  verify_attempts: number
  verify_refusals: number
  hardship: boolean
  disclosures_delivered: string[]
  offers: Offer[]
  active_offer_id: string | null
  arrangement_offer_id: string | null
  outcome: string | null
  [key: string]: Json | Offer[] | undefined
}

export interface CrmActionLog {
  type: string
  payload: Record<string, Json>
}

export interface Turn {
  index: number
  caller_text: string | null
  agent_text: string
  route: string
  stage_before: Stage
  stage_after: Stage
  intent: IntentResult | null
  directive: Directive | null
  state_after: ConversationState
  disclosures: string[]
  tool_calls: ToolCall[]
  guard: GuardTrace
  llm_calls: LLMCall[]
  crm_actions: CrmActionLog[]
  redactions: string[]
  latency_ms: number
  created_at: string
}

export interface ConversationSummary {
  id: string
  account: { id: string; reference: string; name: string }
  channel: string
  status: string
  stage: Stage
  outcome: string | null
  turn_count: number
  eval_run_id: string | null
  replay_of: string | null
  policy_version: string
  agent_prompt_ref: string
  agent_model: string
  started_at: string
  ended_at: string | null
}

export interface ConversationDetail extends Omit<ConversationSummary, 'account'> {
  account: Account
  today: string
  classifier_prompt_ref: string
  state: ConversationState
  turns: Turn[]
}

export interface EvalRun {
  id: string
  kind: string
  label: string | null
  git_sha: string | null
  policy_version: string
  agent_prompt_ref: string
  classifier_prompt_ref: string
  agent_model: string
  status: string
  passed: boolean | null
  summary: Record<string, Json>
  started_at: string
  finished_at: string | null
}

export interface EvalMetric {
  name: string
  score: number | null
  threshold: number | null
  success: boolean
  reason: string | null
  [key: string]: Json
}

export interface EvalResult {
  id: string
  scenario_id: string
  conversation_id: string | null
  passed: boolean
  error: string | null
  metrics: EvalMetric[]
  duration_ms: number
}

export interface EvalRunDetail extends EvalRun {
  results: EvalResult[]
}

export interface Scenario {
  id: string
  description: string
  tags: string[]
  caller_mode: string
}

export interface PlanLimits {
  min_monthly_payment: string
  max_payments: number
  first_payment_within_days: number
}

export interface Policy {
  client_id: string
  version: string
  client_name: string
  agent_name: string
  callback_number: string
  verification: { factors: string[]; max_attempts: number }
  disclosures: Record<string, { when: string; text: string }>
  negotiation: {
    payment_plan: PlanLimits
    hardship_plan: PlanLimits
    settlement: {
      enabled: boolean
      min_fraction_of_balance: string
      counter_fraction_of_balance: string
      pay_within_days: number
    }
    pay_in_full: { pay_within_days: number }
  }
  prohibited_phrases: string[]
  third_party_sensitive_terms: string[]
}

export interface PolicyResponse {
  fingerprint: string
  policy: Policy
  yaml: string
}

export interface PromptVersion {
  ref: string
  name: string
  version: string
  text: string
  current: boolean
  first_seen_at: string
}

// --- WebSocket events (see backend agent.py emit calls) ---------------------------------

export type WsEvent =
  | { type: 'turn_started'; turn_index: number }
  | { type: 'intent'; intent: IntentResult }
  | { type: 'route'; route: string; stage: Stage }
  | ({ type: 'tool_call' } & ToolCall)
  | { type: 'guard'; attempt: number; ok: boolean; violations: Violation[] }
  | {
      type: 'agent_message'
      turn_index: number
      text: string
      stage: Stage
      route: string
      end_call: boolean
      outcome: string | null
      latency_ms: number
    }
  | { type: 'error'; message: string; closed?: boolean }
