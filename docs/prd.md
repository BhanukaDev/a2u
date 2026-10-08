# A2U — Product Requirements (v1)

Status: draft · 2026-10-09 · Owner: Bhanuka Dassanayake

Related: [architecture.md](architecture.md) · [agent-config-spec.md](agent-config-spec.md) · [milestones.md](milestones.md) · [decisions.md](decisions.md) · [Blueprint diagrams](https://claude.ai/artifact/WA2RpmQpHjSRvR5DdTKyxN)

---

## 1. Summary

A2U is a self-serve platform for building AI agents that talk to customers over **phone calls, web voice, web chat, WhatsApp and SMS**. Each agent has one brain and shared customer memory, whatever the channel. Developers sign up, describe an agent in config, connect their systems via webhooks, MCP or built-in connectors, and pay for usage.

It competes with Retell AI and Vapi. The difference is in what is built in:

1. **Background work while the conversation continues.** Slow tools run as durable tasks. The agent keeps talking, and the result is delivered on whatever channel can reach the user, even after the call ends.
2. **Deterministic where it matters.** Typed flows (`collect` → `confirm` → `call_tool`) for actions with consequences. The LLM understands and speaks, code decides.
3. **One persona across channels.** A single front agent talks; specialist workers do the work behind it. No "transfer" feeling, one place for safety checks.
4. **Channels working together.** Collect an email address over WhatsApp during a call, send a payment link mid-call, or call back from chat with full context.
5. **Data controls on every tier,** without needing an enterprise contract: retention, no-recording mode, PII masking, region.
6. **Built for South Asia first.** WhatsApp-heavy behaviour, local numbers, and local languages where speech quality allows.

## 2. Goals and non-goals

### Goals (v1)
- A developer can sign up, build, test and deploy an inbound phone + web agent **in under 1 hour** without talking to us.
- Voice feels natural: **p50 ≤ 900 ms, p95 ≤ 1.5 s** from the end of user speech to the first agent audio, measured on calls in Sri Lanka.
- Actions are safe: no tool with side effects runs without its flow's `confirm` or approval step having passed.
- Every agent change is versioned, diffable, tested by evals and reversible.
- Gross margin on usage of **≥ 40%** at list price.

### Non-goals (v1)
- Customers uploading or running their own code. Custom logic goes to webhooks, MCP or code agents built by the A2U team.
- A drag-and-drop flow builder. The canvas *visualises* agents and runs tests; it does not edit flows.
- Speech-to-speech as the default voice mode. It is optional per agent (see §6.6).
- Self-hosted (air-gapped) control plane. Enterprise gets a dedicated data plane; the console stays hosted.
- Video, email channel, mobile SDKs.

## 3. Users

| Persona | Who | What they need |
|---|---|---|
| **Builder** (primary) | A developer at a company or agency building a customer-facing agent | Fast setup, clear config, good debugging (traces, transcripts), webhooks that work, predictable pricing |
| **Operator** | Support or ops lead at the customer | Read transcripts, edit prompts and knowledge safely, see outcomes, take over a live conversation |
| **Admin** | Account owner | Team, roles, billing, API keys, data settings, SSO (Business+) |
| **End user** | The customer's customer: caller, chatter, WhatsApp user | Fast answers, no repeating themselves, a human when needed |
| **A2U team** | Us | Build code agents for managed accounts; operate the platform |

## 4. Core concepts

| Concept | Definition |
|---|---|
| **Agent** | A versioned definition: front agent, workers, router, triggers, channels, knowledge, policies |
| **Front agent** | The only component that talks to the end user. Fast model, persona, channel-aware rendering |
| **Worker** | Does work behind the front agent: an LLM specialist, a deterministic **flow**, or a single tool. Never talks to the user |
| **Worker contract** | Workers return exactly one of `Result`, `NeedsInput`, `Progress`, `Failed` |
| **Router** | Picks which worker the front agent delegates to: `handoff` (front agent decides), `classifier`, or `rules_then_classifier` |
| **Task** | A durable execution of a worker, owned by the **conversation**, not by the call or the agent turn |
| **Trigger** | What starts a conversation: inbound channel, API, webhook event, schedule. Carries context and an optional goal |
| **Customer** | An end user within one tenant, with one or more **identities** (phone, WhatsApp, web user ID, email) |
| **Conversation** | A thread with one customer, which may have several active channels at once |
| **Config agent / Code agent** | Built by customers in config / built by the A2U team in Python with the SDK. Same runtime |

## 5. v1 scope

All of the following are in v1, delivered in milestone order (see [milestones.md](milestones.md)). Public self-serve launch happens at M5.

| Area | v1 |
|---|---|
| Channels | Web chat, web voice, phone (inbound + outbound), WhatsApp (inbound + outbound templates), SMS (inbound + outbound) |
| Builder surface | Console config editor, YAML import/export, REST API, CLI (`a2u deploy`) |
| Agent features | Front agent, workers, capped flows, routing modes, knowledge (RAG), memory, background tasks, approvals, human handoff |
| Triggers | Inbound per channel, API, webhook events, schedules (campaigns with pacing) |
| Tools | Webhooks, MCP servers, built-in connectors (v1 set: Google Calendar, Microsoft Calendar, HubSpot, Google Sheets, generic REST) |
| Testing | Playground (chat, browser voice, simulated WhatsApp/SMS), evals with simulated users, routing accuracy |
| Operate | Transcripts, traces with per-stage latency, recordings (opt-in), live monitor and takeover, analytics |
| Account | Sign-up, orgs, roles, API keys, usage billing, spend caps, Business-tier data settings |
| Languages | English first; Sinhala and Tamil subject to the speech quality gate (§10, M0) |

## 6. Functional requirements

Priority: **P0** blocks launch, **P1** should ship in v1, **P2** may slip past launch.

### 6.1 Agent definition
- **P0** Agents are defined in YAML/JSON per [agent-config-spec.md](agent-config-spec.md), editable in the console, via API, or with the CLI.
- **P0** Every save creates an immutable version. Deploy promotes a version to an environment (`draft`, `live`). Rollback is one action.
- **P0** The console shows a plain-language diff between versions before deploy.
- **P0** Schema validation with clear errors, before save.
- **P1** Templates: receptionist, lead qualifier, support triage, appointment reminders.

### 6.2 Front agent
- **P0** Exactly one front agent per agent. It is the only component allowed to produce user-facing output.
- **P0** Per-channel profiles: model, reply length, formatting (voice: short, spoken; WhatsApp: buttons/lists; web: cards).
- **P0** Answers knowledge questions directly, using read-only retrieval, without delegating.
- **P0** Speaks or shows filler while a delegated task is pending ("let me check that").
- **P0** **Result check:** before speaking, values from `Result.must_say` and typed fields (amounts, dates, times) must match the reply. On mismatch, regenerate once, then fall back to a template sentence.
- **P1** Language detection and per-conversation language switching among the agent's configured languages.

### 6.3 Workers and flows
- **P0** Worker kinds: `llm` (prompt, model, scoped tools), `flow` (capped steps), `tool` (single tool call).
- **P0** Flow steps: `collect`, `choose`, `confirm`, `verify`, `call_tool`, `delegate`, `handoff`, `if`. No loops, no free-form expressions beyond the condition language (spec §5).
- **P0** Tool permissions are scoped per worker. The front agent has no side-effecting tools.
- **P0** Workers return only the four contract types. Anything else is a `Failed`.
- **P1** Approvals: a policy like `refund: amount > 100` pauses the task until a human approves in the console or via webhook.

### 6.4 Routing
- **P0** Modes: `handoff` (default), `classifier`, `rules_then_classifier`.
- **P0** Sticky routing: a conversation stays with a worker until it returns `Result`/`Failed` or the user changes topic.
- **P1** Routing evals: confusion matrix per scope in the console.

### 6.5 Background tasks
- **P0** Any worker or tool can be marked `background: true`. Delegation returns immediately; the conversation continues.
- **P0** Tasks are durable (survive restarts and deploys), cancellable, with timeouts and idempotency keys for side effects.
- **P0** Delivery: speak at the next turn boundary if the conversation is live; otherwise WhatsApp (inside the 24-hour window), otherwise an approved WhatsApp template or SMS. Configurable per agent.
- **P0** The front agent sees task state in context and must not claim completion of a pending task (enforced by the result check).
- **P1** A task can return `NeedsInput` after the call ended; the question goes out on WhatsApp/SMS and the answer resumes the task.

### 6.6 Voice
- **P0** Pipeline: speech-to-text → front agent → text-to-speech, streaming end to end. Interruptions (barge-in), end-of-turn detection, filler.
- **P0** Choice of speech-to-text and text-to-speech providers and voices per agent (v1: Deepgram, ElevenLabs, Cartesia, Google; final list after the M0 bake-off).
- **P0** DTMF input, transfer to a human number, voicemail detection on outbound.
- **P1** Speech-to-speech mode (OpenAI Realtime or Gemini Live) per agent, with the documented trade-off: no pre-speech result check, fixed provider voices.
- **P1** Call recording, opt-in per agent, with a spoken disclosure.

### 6.7 Channels
- **P0** **Web widget**: chat and voice in one embeddable component; anonymous and verified modes (§6.9); a headless JS client.
- **P0** **Phone**: buy or port numbers through A2U, or bring a SIP trunk. Local Sri Lankan numbers subject to the M0 carrier check.
- **P0** **WhatsApp**: Meta Cloud API, embedded signup for the customer's WhatsApp Business account, template management, 24-hour window enforcement.
- **P0** **SMS**: inbound and outbound, sender ID handling per country.
- **P1** Cross-channel inside one conversation: `NeedsInput` can prefer a channel (e.g. ask for an email address over WhatsApp during a call); "call me" from chat starts an outbound call with context.
- **P1** WhatsApp voice notes, transcribed and answered as text or voice.

### 6.8 Triggers and outbound
- **P0** Inbound triggers per channel, with context injected before the first turn (caller ID, page, user traits).
- **P0** API trigger: start a call/message to a number with context and a goal.
- **P1** Webhook triggers (e.g. CRM "new lead" → call within 60 s).
- **P1** Schedule triggers: campaigns over a list with pacing, calling hours per timezone, retries, and WhatsApp-then-call sequences.
- **P0** Outbound guardrails: opt-out list per tenant, calling-hours enforcement, per-tenant rate limits, number verification before outbound is enabled.

### 6.9 Customers, identity and memory
- **P0** Customer record per tenant, with identities: `phone`, `whatsapp`, `web_user`, `email`, `web_visitor`. Never shared across tenants.
- **P0** Phone/SMS/WhatsApp: identify by number. Sensitive flows require `verify` (one-time code over SMS/WhatsApp, or knowledge check).
- **P0** Web, anonymous: publishable key + allowed origins + bot check before voice; visitor ID; upgrade by verifying phone or email.
- **P0** Web, verified: the client's backend calls `POST /v1/sessions` with a secret key and user info; the widget uses the short-lived session token. Optional `act_as_token`, forwarded to the client's webhooks only.
- **P0** Identity linking: a web user is linked to a phone identity only if the client marks the phone verified or the user verifies it in-chat.
- **P0** Memory: per-conversation transcripts, rolling summaries, and customer facts with source and expiry. New conversations start with a summary, not full transcripts.
- **P1** Client OIDC: accept the client's own ID tokens instead of a backend call.

### 6.10 Knowledge
- **P0** Upload PDFs, DOCX, URLs and plain text; parse, chunk, embed into pgvector; cite sources in traces.
- **P1** Scheduled re-crawl of URLs.

### 6.11 Testing and evals
- **P0** Playground: chat, browser voice, simulated WhatsApp and SMS, choose a test customer identity.
- **P0** Evals: scripted and simulated-user conversations with assertions on tool calls, flow steps, `must_say` values and routing. They run on every version; deploy is blocked on failure unless overridden.
- **P1** Replay a real conversation (with PII masked) against a new version.

### 6.12 Operate
- **P0** Conversation list with transcripts, channel, outcome, cost and per-stage latency trace.
- **P0** Live monitor; human takeover for chat and WhatsApp; warm transfer for calls.
- **P1** Analytics: volume, containment, outcomes per goal, latency percentiles, cost per conversation.
- **P1** Post-conversation analysis: summary, sentiment and custom extracted fields, pushed by webhook.

### 6.13 Accounts, billing and plans
- **P0** Sign-up (email, Google, GitHub), orgs, invites, roles (owner, admin, builder, operator, viewer). Console identity via self-hosted Keycloak.
- **P0** API keys per org with scopes; secret and publishable keys for the widget.
- **P0** Usage metering per tenant: voice minutes by component (speech-to-text, LLM, text-to-speech, telephony), messages, tasks, knowledge storage.
- **P0** Prepaid credits with auto top-up, spend caps, low-balance alerts. Payment provider subject to the M0 check (Stripe availability in Sri Lanka).
- **P0** Abuse controls: card or phone verification before outbound and before buying numbers, free-credit limits, toll-fraud destination blocklist.
- **P1** Plans: Pay as you go, Business (region choice, custom retention, no-recording mode, own LLM keys, audit export, SSO), Enterprise (dedicated cell or customer VPC, BAA-style agreements, data residency).

### 6.14 Security and compliance
- **P0** Tenant isolation in every query (tenant ID on every row, enforced in the repository layer; Postgres row-level security as a second guard).
- **P0** Secrets encrypted at rest with per-tenant data keys.
- **P0** PII masking in logs, traces and analytics (Presidio, with tests for local name and number formats).
- **P0** Audit log of every config change, deploy, data export and takeover.
- **P0** Webhooks: signed requests, timeouts, retries with idempotency keys, SSRF and egress protection.
- **P0** Data deletion per customer on request (Sri Lanka PDPA); retention per tenant.
- **P2** SOC 2 Type I readiness work starts after launch.

## 7. Non-functional requirements

| Area | Target |
|---|---|
| Voice latency | p50 ≤ 900 ms, p95 ≤ 1.5 s end of speech → first audio (Sri Lanka callers) |
| Chat latency | First token ≤ 1.5 s p95 |
| Availability | 99.9% monthly for the data plane (calls and messages); 99.5% for the console |
| Durability | No accepted message or task lost across a pod or node failure |
| Scale at launch | 200 concurrent calls, 2,000 concurrent chat sessions per cell, horizontally scalable |
| Deploys | Zero-downtime; active calls drain on the old version |
| Region | Primary cell in the region closest to Sri Lanka with low latency to speech and LLM vendors (decide in M0, e.g. Mumbai or Singapore) |

## 8. Pricing (initial hypothesis)

- Voice: per minute, all-in (speech-to-text, LLM, text-to-speech, telephony), with a visible breakdown. Target list price **$0.10–0.14/min** for the default stack; lower with the customer's own LLM keys.
- Messages: per message (chat, WhatsApp; Meta's own fees passed through), SMS at carrier cost plus margin.
- Plans: Business adds a monthly platform fee. Enterprise is per contract.
- Validate against costs measured in M2. Must meet the ≥ 40% gross margin goal.

## 9. Success metrics

| Metric | Launch target (first 90 days after M5) |
|---|---|
| Time to first live agent (sign-up → first real call) | median < 60 min |
| Activated orgs (≥ 1 live agent with real traffic) | 50 |
| Paying orgs | 15 |
| Voice latency p50 | ≤ 900 ms |
| Conversations ending without human handoff (containment) | ≥ 70% on template agents |
| Gross margin on usage | ≥ 40% |

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Sinhala/Tamil speech quality too low | Local-language pitch fails | M0 bake-off with real code-mixed audio; launch English-first if the gate fails |
| Stripe not available to a Sri Lankan entity | No self-serve billing | M0: confirm; options are a local gateway (e.g. PayHere) or a foreign entity |
| Local phone numbers hard to get via Twilio | Callers dial foreign numbers | M0: check Twilio/Telnyx SL numbers; local SIP carrier as fallback |
| Scope: five channels + self-serve in v1 | Slow to revenue | Milestones with exit criteria; internal and design-partner use from M1 |
| Funded competitors cut prices | Margin squeeze | Compete on built-in features (§1), not price |
| Toll fraud and abuse | Direct cost | Outbound gating, spend caps, destination blocklist from M4 |
| Voice latency from region | Calls feel slow | Region choice in M0; latency measured per stage from M2 |
| Single founder bandwidth | Everything | Keep the stack small; managed services where they don't block tiers |

## 11. Open questions

1. Company entity and country for billing and contracts (affects payment provider and compliance).
2. Final speech vendor list after the bake-off.
3. Whether outbound campaigns are P0 or P1 for launch customers.
4. Human takeover for phone: warm transfer only, or barge-in to listen?
5. Free tier size and fraud limits.
