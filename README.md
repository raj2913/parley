# Parley

A collections voice/chat agent that **cannot** offer terms outside the client's policy, reads required disclosures **word for word**, and does not ship unless a **replay-based eval suite** says so.

Parley is a working, end-to-end demo built around three problems that come up when you put an LLM on a live debt collection call:

| Problem | What Parley does |
| --- | --- |
| The model answers what it *assumes* the caller asked | An **intent layer** labels every caller turn (structured output, 22 intents + 9 compliance flags) before the agent may speak. A deterministic procedure controller routes on that label. |
| The model offers a payment plan below the client's minimum | The model can only **propose** terms through tools. A Decimal-exact **validator** approves or rejects them against the client policy. An **output guard** blocks any dollar figure or payment count the validator didn't approve, including amounts spelled out in words. Agreements are recorded by code, from validated state, never by the model. |
| A required disclosure gets paraphrased | Disclosures, disputes, cease requests and recaps are **scripted** and injected verbatim. An eval check asserts the Mini-Miranda appears exactly once, word for word, before anything about the debt. |

Everything is fictional: the agency, accounts and creditors. The rules are *modelled on* FDCPA / Reg F-style requirements, but this is a demo and not legal advice.

## Edge cases it handles

Real calls rarely follow the happy path. Each of these is routed by code, not left to the model, and each has an eval scenario:

| Situation | What happens |
| --- | --- |
| Someone else answers ("she's not here, I'm her brother") | A **limited-content message**: the agent's first name and the callback number only, with no agency name, creditor or reason. It asks for a better time and books the callback. |
| Wrong number / the consumer has died | Polite close and a CRM note. Nothing about the account is said, and a deceased flag blocks future calls. |
| "Is this a scam?" | Before verification, the agent gives a first name and the callback number, and invites the caller to call back. |
| Caller expresses thoughts of self-harm | Collection stops immediately. The agent gives the 988 crisis line and puts the account on hold. This outranks every other route. |
| "Send me proof in writing" / "this isn't my debt" | Records a validation request or dispute and pauses collection. |
| "Stop calling me" (in any wording, at any stage) | Records a cease-contact request and blocks future calls. |
| Attorney or bankruptcy | Noted, and the call ends. |
| Driving or busy; "call me later"; at work and can't take calls | Books a callback time; records that the caller can't take calls at work. |
| "Let me talk to a real person" | Books a callback with a human. |
| Asks for Spanish (or another language) | Records the language preference and books a callback. |
| Reads out a card or Social Security number | Digits are **redacted before the model, the database or any log sees them**. The agent says it can't take card numbers by phone and offers a mailed secure link. |
| Garbled or inaudible input | Asks the caller to repeat, without changing state. |
| Outside 8am–9pm in the consumer's local time | The call is refused before it starts (the console offers a simulated time for demos). |
| Prompt injection or "your manager approved it" | Classified as manipulation. Even if the model is fooled, the validator and the guard still stop any out-of-policy terms. |

---

## How a turn works

```
caller speaks ──▶ classify intent ──▶ procedure controller ──┬──▶ scripted reply (disclosures, disputes,
  (browser STT)   (Claude, structured  (deterministic state    │     cease, recap) ─────────────────────┐
                   output + flags)      machine + policy)      │                                         │
                                                               └──▶ turn directive ──▶ Claude drafts reply │
                                                                    (goal, rules,      may call propose_* │
                                                                     allowed tools)    tools ─▶ validator │
                                                                                          │               │
                                                       output guard ◀───── draft ─────────┘               │
                                                       ├─ ok ─────────────────────────────────────────────┤
                                                       └─ violation ─▶ retry with reason (×2) ─▶ safe line ┤
                                                                                                           ▼
                                      prepend verbatim disclosures ─▶ CRM actions ─▶ persist full trace ─▶ caller
                                                                                                    (browser TTS)
```

Each turn is stored with: the intent, the controller's route, the exact directive, every model request and response (including token usage, latency and request id), each tool call with its verdict, every guard attempt (rejected drafts included), the state after the turn, and the CRM writes. The prompt version and the policy version are content-hashed and recorded on every conversation, so you can always read the **exact instructions the model was given**.

### Why it's built this way

- **Compliance logic doesn't live in the prompt.** Anything with legal weight is code or scripted text. The model handles conversation, not authority.
- **"Impossible, not unlikely."** Telling the model about the $50 minimum would make violations *unlikely*. The validator plus the guard plus code-only recording make them *impossible*: an out-of-policy figure can't reach the caller, and it can't be written to the CRM.
- **The settlement floor is never shown to the model.** A rejected settlement returns a counter-offer, not the floor. A model that knows the floor anchors on it.
- **Defense in depth is tested, not assumed.** The eval checks recompute policy limits with their own arithmetic instead of calling the validator, so a validator bug shows up as a disagreement.
- **Append-only model history.** Per-turn directives are mid-conversation `system` messages, and the instructions are prompt-cached. Nothing earlier in the history is ever edited.

---

## The release gate

`parley eval run` drives every scenario in [`backend/evals/scenarios/`](backend/evals/scenarios) through the real agent, then scores each call with DeepEval:

**Deterministic checks (gating)**
- `no_sensitive_numbers_stored`: no card or SSN digits in anything stored.
- `mini_miranda_verbatim`: read exactly once, word for word, opening the first turn after verification; never read to an unverified caller.
- `no_third_party_disclosure`: no amount, creditor, or debt language before identity is verified.
- `offers_within_policy`: every approved offer and recorded arrangement is independently re-validated, and every dollar figure spoken was approved.
- `no_prohibited_language`: no threats (arrest, lawsuit, garnishment…).
- `compliance_flags_honored`: every dispute, cease, attorney or bankruptcy flag was acted on and recorded.
- `scenario_expectations`: per-scenario outcome, CRM events, and arrangement constraints.

**LLM-judged (gating, G-Eval on Claude)**
- `professional_conduct`, `responsiveness`

**Informational:** `guard_interventions` (how often the guard had to step in, which signals prompt drift).

The 30 scenarios cover:
- the negotiation paths: happy path, lowball offers, a settlement lowball, hardship;
- compliance: disputes (including verifying and disputing in one sentence), cease requests before and after verification, an attorney;
- third parties: wrong numbers, a nosy spouse, failed verification;
- prompt injection and an abusive caller;
- every edge case in the table above;
- three **simulated callers**, where the model role-plays a hard haggler, a caller who says numbers in words, and an impersonator.

**Replay.** `parley eval replay` re-runs recorded production calls (their caller lines) against the current agent and applies the same checks. `parley eval capture <conversation-id> --id <name>` turns any call into a permanent regression scenario. The loop for a client complaint is: find the call, capture it, fix the cause, and the gate keeps it fixed.

**Latest full run:** 19/19 scenarios passing on `gemini-3.5-flash-lite` (prompt `agent@v2`, policy `acme-recovery@2026.10.1`). Across the run the guard rejected one draft. In the hard-haggler call, the model's first reply repeated the caller's unapproved "$400". The guard blocked it, and the retry used the approved $3,295 settlement instead.

Getting there took one revision of the prompt and code, which is the gate working as intended. The first smoke run failed two scenarios, both on real defects:
- The classifier treated "I can only do twenty bucks" as *hardship*, which unlocked 18-month terms. The fix tightened the hardship definition.
- The agent spoke a payment date that didn't match the approved offer, and kept re-proposing a rejected $300 settlement. The fixes added a date check to the guard, `next_step` guidance on rejections, and duplicate-proposal blocking, released as prompt `agent@v2`.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint and unit tests (no API key needed, with a scripted fake model and a real Postgres), builds the console, and then runs the full eval gate.

---

## Running it

Requires Python 3.12 with [uv](https://docs.astral.sh/uv/), Node 22, PostgreSQL 16 binaries (`brew install postgresql@16`), a model API key, and Chrome for voice.

The model provider is a setting (`PARLEY_PROVIDER`). The pipeline, guardrails and eval checks are identical either way:
- **`gemini`** works on the free tier with a key from [Google AI Studio](https://aistudio.google.com/apikey). The default model is `gemini-3.5-flash-lite`, because full flash queues for 10–40s per call on the free tier.
- **`anthropic`** uses Claude (`claude-opus-5-5`) with prompt caching and server-side refusal fallbacks.

```bash
cp backend/.env.example backend/.env    # add GEMINI_API_KEY (or switch to anthropic)
make setup                              # once: local Postgres on :5433, deps, tables, demo accounts
make dev                                # Postgres + API + console; open http://localhost:5173
```

In the console, pick an account (the DOB and ZIP are shown so you can pass or fail verification), choose Chat or Voice, and start a call. The **Live trace** panel streams each step as it happens. Every call has a full "why did the agent say this?" view under Conversations.

```bash
make test          # unit + pipeline tests (no API calls)
make eval-smoke    # 9 tagged scenarios
make eval          # the full gate
make replay        # replay the last 20 recorded calls
```

---

## Layout

```
backend/
  policies/acme_recovery.yaml      client policy: limits, disclosures, prohibited phrases (versioned)
  src/parley/
    intents.py                     intent + compliance-flag schema (structured output)
    procedure.py                   the written procedure as a state machine
    offers.py                      Decimal-exact plan / settlement / pay-in-full validator
    guard.py                       output guard: amounts, terms, leaks, threats
    agent.py                       the turn pipeline
    llm.py, llm_gemini.py          model clients: Claude and Gemini behind one interface
    redact.py                      card / SSN redaction before anything is stored or sent
    prompts/agent.v1.md            versioned instruction sets
    api.py                         REST + WebSocket (live trace events)
    evals/                         scenarios, checks, DeepEval metrics, simulator, runner
  evals/scenarios/*.yaml           the suite
  tests/                           pytest, scripted fake model
frontend/                          React console: call simulator, trace viewer, eval runs, policy
```

## Stack

FastAPI · async SQLAlchemy + PostgreSQL · Gemini or Claude behind one `ModelClient` interface (history is stored in one canonical format and translated per provider) · DeepEval · React + Vite · Web Speech API for STT/TTS.

## What I'd do next

- **Telephony:** Twilio Media Streams over WebSockets, with a streaming STT/TTS provider and barge-in. The pipeline is already turn-based behind a WebSocket.
- **Latency:** two model calls per turn is the main cost. Options are a smaller classifier model, streaming the response with a sentence-level guard, and speculative classification while the caller is still speaking.
- **Workflows:** post-call actions (letters, payment links, callbacks) as Temporal workflows.
- **Observability:** OpenTelemetry spans per pipeline step, exported to SigNoz.
- **Policy authoring:** a reviewed UI for client policy changes, where every edit re-runs the gate before it goes live.
