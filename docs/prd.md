# A2U — Product Requirements (v1)

Status: draft v2 · 2026-10-09 · Owner: Bhanuka Dassanayake

Related: [architecture.md](architecture.md) · [agent-config-spec.md](agent-config-spec.md) · [milestones.md](milestones.md) · [build-plan.md](build-plan.md) · [decisions.md](decisions.md) · [Blueprint diagrams](https://claude.ai/artifact/WA2RpmQpHjSRvR5DdTKyxN)

Changes from v1 of this document are recorded as decisions D-012 to D-024.

---

## 1. Summary

A2U is a platform for building AI agents that talk to customers over **web chat, web voice, phone calls and WhatsApp**. Each agent has one brain and one customer memory, whatever the channel. Developers describe an agent in config, connect their systems via webhooks, MCP or built-in connectors, and pay for usage.

It competes with Retell AI and Vapi. The difference is what is built in, in order of how much it matters to a buyer:

1. **Work that outlives the call.** Slow tools run as durable tasks owned by the conversation, not the call. The agent keeps talking, and the result is delivered on WhatsApp after the call ends if it has to be. Retell and Vapi are call-centric; this is the feature a buyer can picture and neither does cleanly.
2. **One persona, one memory, across channels.** A single front agent talks; specialist workers do the work behind it. A customer who called yesterday and messages on WhatsApp today is the same customer with the same context. Channels work together inside one conversation: collect an email over WhatsApp during a call, or call back from chat with full context.
3. **Deterministic where it matters.** Typed flows (`collect` → `confirm` → `call_tool` → `result`) for actions with consequences. The LLM understands and speaks; code decides, and for typed facts code also speaks.
4. **Data controls on every tier**, without an enterprise contract: retention, no-recording mode, PII masking.
5. **Built for South Asia first.** WhatsApp-heavy behaviour, local languages where quality allows, and agencies as first-class customers.

## 2. Goals and non-goals

### Goals (v1)
- A developer can build, test and deploy a web chat + web voice agent **in under 1 hour**, and hear it on the phone within 5 minutes more using the shared test number.
- Voice feels natural: **p50 ≤ 900 ms, p95 ≤ 1.5 s** from the end of user speech to the first agent audio, measured from Sri Lanka, on both web voice and phone.
- Actions are safe: no tool with side effects runs without its flow's `confirm` or approval step having passed.
- Every agent change is versioned, diffable, tested by evals and reversible.
- Three design partners run real traffic before public launch, and pay by invoice.
- Gross margin on usage of **≥ 40%** at list price.

### Non-goals (v1)
- SMS as a conversation channel (D-013). It may return as a delivery fallback and for one-time codes.
- Schedule triggers and outbound campaigns (D-022).
- Customers uploading or running their own code (D-006). Custom logic goes to webhooks, MCP or code agents built by the A2U team.
- A drag-and-drop flow builder. The console *visualises* agents and runs tests; it does not edit flows.
- Speech-to-speech as the default voice mode. It is optional per agent (§6.6).
- Multi-node clusters, dedicated cells, self-hosted control plane. v1 is a single-node k3s cluster on one EC2 instance plus RDS (D-015).
- Public self-serve sign-up and card billing before there is pull for it (D-018).
- Video, email channel, mobile SDKs.

## 3. Users

| Persona | Who | What they need |
|---|---|---|
| **Builder** (primary) | A developer at a company, agency or BPO building a customer-facing agent | Fast setup, clear config, good debugging (traces, transcripts), webhooks that work, predictable pricing |
| **Agency** | An agency or integrator running agents for several clients | One org, one workspace per client, per-client usage, white-label widget, roles per workspace |
| **Operator** | Support or ops lead at the customer | Read transcripts, edit persona and knowledge as documents without touching YAML, see outcomes, take over a live conversation |
| **Admin** | Account owner | Team, roles, invoices, API keys, data settings, SSO (Business+) |
| **End user** | The customer's customer: caller, chatter, WhatsApp user | Fast answers, no repeating themselves, a human when needed |
| **A2U team** | Us | Build code agents for managed accounts; operate the platform |

## 4. Core concepts

| Concept | Definition |
|---|---|
| **Org** | Billing entity. A Clerk organisation. Owns workspaces |
| **Workspace** | The tenant. One per end client. Every data row carries its ID (`tenant_id`). Usage is metered here |
| **Agent** | A versioned definition: front agent, workers, router, triggers, channels, knowledge, policies |
| **Front agent** | The only component that talks to the end user. Fast model, persona, channel-aware rendering |
| **Worker** | Does work behind the front agent: an LLM specialist, a deterministic **flow**, or a single tool. Never talks to the user, except that a flow's final `result.say` is rendered verbatim |
| **Worker contract** | Workers return exactly one of `Result`, `NeedsInput`, `Progress`, `Failed` |
| **Router** | Picks which worker the front agent delegates to: `handoff` (front agent decides), `classifier`, or `rules_then_classifier` |
| **Task** | A durable execution of a worker, owned by the **conversation**, not by the call or the agent turn |
| **Trigger** | What starts a conversation: inbound channel, API, webhook event. Carries context and an optional goal |
| **Customer** | An end user within one workspace, with one or more **identities** (phone, WhatsApp, web user) |
| **Conversation** | A thread with one customer, which may have several active channels at once |
| **Consent** | A record that a customer agreed to be contacted on a channel for a purpose (D-023) |
| **Config agent / Code agent** | Built by customers in config / built by the A2U team in Python with the SDK. Same runtime |

## 5. v1 scope

Delivered in milestone order (see [milestones.md](milestones.md)). Design partners use it from M1.

| Area | v1 |
|---|---|
| Channels | Web chat, web voice, phone (inbound + API-triggered outbound), WhatsApp (inbound + templates) |
| Builder surface | Console config editor, YAML import/export, REST API, CLI (`a2u deploy`) |
| Agent features | Front agent, workers, capped flows, routing modes, knowledge (RAG), memory, background tasks, approvals, human handoff |
| Triggers | Inbound per channel, API, webhook events |
| Tools | Webhooks, MCP servers, built-in connectors (v1 set: Google Calendar, Microsoft Calendar, HubSpot, Google Sheets, generic REST) |
| Testing | Playground (chat, browser voice, simulated WhatsApp, fake time), evals with simulated users, routing accuracy, eval-from-transcript |
| Operate | Transcripts, traces with per-stage latency, recordings (opt-in), live monitor and takeover, analytics |
| Account | Orgs and workspaces, roles, API keys, usage metering, invoices; self-serve sign-up and prepaid credits in M5b |
| Languages | English on all channels. Tamil on voice and text, Sinhala on text, each subject to its gate (§10, M0) |

## 6. Functional requirements

Priority: **P0** blocks the design-partner launch, **P1** should ship in v1, **P2** may slip past launch.

### 6.1 Agent definition
- **P0** Agents are defined in YAML/JSON per [agent-config-spec.md](agent-config-spec.md), editable in the console, via API, or with the CLI.
- **P0** Every save creates an immutable version. Deploy promotes a version to an environment (`draft`, `live`). Rollback is one action.
- **P0** The console shows a plain-language diff between versions before deploy.
- **P0** Schema validation with clear errors, before save.
- **P0** Persona prompts and knowledge sources are first-class documents in the console with their own history, so an operator can edit them without opening YAML. The YAML references them by name.
- **P1** Templates: receptionist, lead qualifier, support triage.

### 6.2 Front agent
- **P0** Exactly one front agent per agent. It is the only component allowed to produce free-form user-facing output.
- **P0** Per-channel profiles: model, reply length, formatting (voice: short, spoken; WhatsApp: buttons/lists; web: cards).
- **P0** Answers knowledge questions directly, using read-only retrieval, without delegating. Retrieval starts on interim transcripts on voice so it is ready at end of speech.
- **P0** A pending `NeedsInput` does not block the front agent from answering a side question from knowledge, then returning to the pending question.
- **P0** Speaks or shows filler while a delegated task is pending. Filler is declared per worker (for example "I'm checking with your insurer now") and varied; it never repeats the same phrase twice in a row.
- **P0** **Flow results are spoken by code.** A flow's `result.say` template is rendered and sent verbatim; the front agent is told what was said (D-017).
- **P0** **Result check** for LLM worker results: `Result.must_say` and typed values (amounts, dates, times) must appear in the front agent's reply. On mismatch, regenerate once, then fall back to a template sentence.
- **P1** Language detection and per-conversation language switching among the languages enabled for that channel.

### 6.3 Workers and flows
- **P0** Worker kinds: `llm` (prompt, model, scoped tools), `flow` (capped steps), `tool` (single tool call).
- **P0** Flow steps: `collect`, `choose`, `confirm`, `verify`, `call_tool`, `delegate`, `handoff`, `if`, `result`. No loops, no free-form expressions beyond the condition language (spec §5).
- **P0** `collect` has a retry cap and a timeout. `confirm` on "no" can return to a named `collect` step instead of ending the flow (spec §5.2).
- **P0** Tool permissions are scoped per worker. The front agent has no side-effecting tools.
- **P0** Workers return only the four contract types. Anything else is a `Failed`.
- **P1** Approvals: a policy like `refund: amount > 100` pauses the task until a human approves in the console or via webhook.

### 6.4 Routing
- **P0** Modes: `handoff` (default), `classifier`, `rules_then_classifier`.
- **P0** Sticky routing: a conversation stays with a worker until it returns `Result`/`Failed` or the user changes topic.
- **P1** Routing evals: confusion matrix per scope in the console, and "why this worker" with classifier scores in traces.

### 6.5 Background tasks
- **P0** Any worker or tool can be marked `background: true`. Delegation returns immediately; the conversation continues.
- **P0** Tasks are durable (survive restarts and deploys), cancellable, with timeouts and idempotency keys for side effects. Live from M1 on web chat.
- **P0** Delivery: speak or show at the next turn boundary if the conversation is live; otherwise WhatsApp, inside the user-initiated window or with an approved template. Requires a consent record (D-023). Configurable per agent.
- **P0** The front agent sees task state in context and must not claim completion of a pending task (enforced by the result check).
- **P1** A task can return `NeedsInput` after the call ended; the question goes out on WhatsApp and the answer resumes the task.

### 6.6 Voice
- **P0** Pipeline on LiveKit Agents: speech-to-text → front agent → text-to-speech, streaming end to end. Interruptions (barge-in), end-of-turn detection, filler.
- **P0** Choice of speech-to-text and text-to-speech providers and voices per agent and per language (v1 candidates: Deepgram, ElevenLabs, Cartesia, Google; final list after the M0 bake-off).
- **P0** Web voice first (M2), phone second (M3). Same worker code.
- **P0** Phone: DTMF input, transfer to a human number, call-length cap.
- **P1** Speech-to-speech mode (OpenAI Realtime or Gemini Live) per agent, with the documented trade-off: no result check, fixed provider voices.
- **P1** Call recording, opt-in per agent, with a spoken disclosure.

### 6.7 Channels
- **P0** **Web widget**: chat and voice in one embeddable component; anonymous and verified modes (§6.9); a headless JS client. Voice uses the LiveKit client SDK under the hood. White-label per workspace.
- **P0** **Shared test number**: every workspace can reach any of its agents by phone through one A2U number plus a PIN, before it owns a number. This is how a new builder hears their agent in minutes.
- **P0** **Phone**: numbers through A2U (Twilio inventory; local Sri Lankan numbers subject to the M0 carrier check), or bring a SIP trunk. All calls enter through LiveKit SIP (D-024).
- **P0** **WhatsApp**: Meta Cloud API. Tenants connect through embedded signup once A2U is an approved Tech Provider; until then they bring an existing WhatsApp Business account. Template management, window enforcement, interactive buttons and lists.
- **P1** Cross-channel inside one conversation: `NeedsInput` can prefer a channel (ask for an email over WhatsApp during a call); "call me" from chat starts an outbound call with context.
- **P1** WhatsApp voice notes, transcribed and answered as text or voice.

### 6.8 Triggers and outbound
- **P0** Inbound triggers per channel, with context injected before the first turn (caller ID, page, user traits).
- **P0** API trigger: start a call or WhatsApp conversation with context and a goal.
- **P1** Webhook triggers (for example CRM "new lead" → call within 60 s).
- **P0** Outbound guardrails: opt-out list per workspace, per-workspace rate limits, number verification before outbound is enabled, consent for WhatsApp.
- **Post-v1** Schedule triggers, campaigns, calling hours, voicemail detection (D-022).

### 6.9 Customers, identity and memory
- **P0** Customer record per workspace, with identities: `phone`, `whatsapp`, `web_user`, `email`, `web_visitor`. Never shared across workspaces.
- **P0** Phone/WhatsApp: identify by number. Sensitive flows require `verify` (one-time code over WhatsApp, or knowledge check). Shared household phones are common, so identification by number is treated as weak until verified.
- **P0** Web, anonymous: publishable key + allowed origins + bot check before voice; visitor ID; upgrade by verifying phone or email.
- **P0** Web, verified: the client's backend calls `POST /v1/sessions` with a secret key and user info; the widget uses the short-lived session token. Optional `act_as_token`, forwarded to the client's webhooks only.
- **P0** Identity linking: a web user is linked to a phone identity only if the client marks the phone verified or the user verifies it in-chat.
- **P0** Memory: per-conversation transcripts, rolling summaries, and customer facts with source, expiry and **sensitivity**. New conversations start with a summary, not full transcripts. High-sensitivity facts are never volunteered before a `verify` step.
- **P1** Client OIDC: accept the client's own ID tokens instead of a backend call.

### 6.10 Knowledge
- **P0** Upload PDFs, DOCX, URLs and plain text; parse, chunk, embed into pgvector; cite sources in traces.
- **P1** Scheduled re-crawl of URLs.

### 6.11 Testing and evals
- **P0** Playground: chat, browser voice, simulated WhatsApp, choose a test customer identity. **Fake time**: fast-forward timers so post-conversation delivery and window expiry can be tested in seconds.
- **P0** Evals: scripted and simulated-user conversations with assertions on tool calls, flow steps, `say`/`must_say` values and routing. They run on every version; deploy is blocked on failure unless overridden.
- **P1** One-click eval from a real conversation (PII masked), replayed against a new version.

### 6.12 Operate
- **P0** Conversation list with transcripts, channel, outcome, cost and a per-turn latency waterfall (speech-to-text, routing, LLM, tools, result check, text-to-speech).
- **P0** Live monitor; human takeover for chat and WhatsApp; warm transfer for calls.
- **P1** Generated agent diagram, clickable into the conversations that passed through each worker.
- **P1** Analytics: volume, containment, outcomes per goal, latency percentiles, cost per conversation.
- **P1** Post-conversation analysis: summary, sentiment and custom extracted fields, pushed by webhook.

### 6.13 Accounts, billing and plans
- **P0** Console identity via Clerk (D-014): sign-in with email, Google, GitHub; orgs, invites, roles (owner, admin, builder, operator, viewer) at org and workspace level.
- **P0** Orgs own workspaces (D-021). Agencies get one workspace per client.
- **P0** API keys per workspace with scopes; secret and publishable keys for the widget.
- **P0** Usage metering per workspace from M1: voice minutes by component (speech-to-text, LLM, text-to-speech, telephony, LiveKit), messages, tasks, knowledge storage.
- **P0** Invoices generated from metering for design partners, in USD or LKR.
- **M5b** Public sign-up, prepaid credits with auto top-up, spend caps, low-balance alerts, card or phone verification before outbound and before buying numbers, free-credit limits, toll-fraud destination blocklist. Payment provider subject to the M0 check.
- **P1** Plans: Pay as you go, Business (custom retention, no-recording mode, own LLM keys, audit export, SSO), Enterprise (dedicated cell, data residency) after v1.

### 6.14 Security and compliance
- **P0** Tenant isolation in every query (`tenant_id` on every row, enforced in the repository layer; Postgres row-level security as a second guard).
- **P0** Secrets encrypted at rest with per-workspace data keys.
- **P0** PII masking in logs, traces and analytics (Presidio, with tests for local name and number formats), applied before anything leaves the cell.
- **P0** Audit log of every config change, deploy, data export and takeover.
- **P0** Webhooks: signed requests, timeouts, retries with idempotency keys, SSRF and egress protection.
- **P0** Consent records for business-initiated WhatsApp (D-023).
- **P0** Data deletion per customer on request (Sri Lanka PDPA); retention per workspace.
- **P2** SOC 2 Type I readiness work starts after launch.

## 7. Non-functional requirements

| Area | Target |
|---|---|
| Voice latency | p50 ≤ 900 ms, p95 ≤ 1.5 s end of speech → first audio, measured from Sri Lanka, web voice and phone |
| Chat latency | First token ≤ 1.5 s p95 |
| Availability | 99.9% monthly for the data plane (calls and messages); 99.5% for the console |
| Durability | No accepted message or task lost across a pod or node failure |
| Scale at launch | 100 concurrent calls, 1,000 concurrent chat sessions on the single k3s node; scale by resizing the instance, then adding a voice node |
| Deploys | Zero-downtime; voice pods drain before termination |
| Recovery | Node rebuild from image and chart in under 30 minutes; RDS point-in-time restore |
| Region | One region close to Sri Lanka with low latency to LiveKit Cloud, speech vendors and an LLM endpoint (decide in M0; Mumbai or Singapore) |

## 8. Pricing (initial hypothesis)

- Voice: per minute, all-in (speech-to-text, LLM, text-to-speech, telephony, LiveKit), with a visible breakdown. Target list price **$0.10–0.14/min** for the default stack; lower with the customer's own LLM keys.
- Messages: per message (chat, WhatsApp; Meta's own fees passed through).
- Agencies: platform fee per workspace above the first.
- Design partners: invoiced monthly from metering, USD or LKR.
- Validate against costs measured in M2 and M3. Must meet the ≥ 40% gross margin goal.

## 9. Success metrics

| Metric | Target |
|---|---|
| Design partners with real traffic | 3 by end of M3 |
| Time to first live agent (web chat + voice, moderated session) | median < 60 min at M5 |
| Time to first phone call via shared test number | < 5 min after the agent exists |
| Voice latency p50 from Sri Lanka | ≤ 900 ms at M2 (web) and M3 (phone) |
| Conversations ending without human handoff (containment) | ≥ 70% on template agents |
| Gross margin on usage | ≥ 40% |
| Paying orgs, 90 days after launch | 15 |

## 10. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Sinhala/Tamil speech quality too low | Local-language voice pitch fails | M0 bake-off with real code-mixed audio; gate per channel (D-019) so Sinhala still ships on text |
| Meta Tech Provider approval slow | Tenants cannot self-connect WhatsApp | Apply in M0; tenants bring an existing WhatsApp Business account meanwhile |
| Payment provider not available to a Sri Lankan entity | No card billing | Invoices for design partners (D-018); M0 decides entity and provider for M5b |
| Local phone numbers hard to get | Callers dial foreign numbers | Shared test number covers trials; M0 checks Twilio inventory and a local SIP carrier into LiveKit SIP |
| Voice latency from region | Calls feel slow | Web voice first isolates our pipeline from carrier latency; region and LLM endpoint chosen on M0 measurements |
| LiveKit Cloud dependency | Pricing or outage risk | Self-hostable; measured in M0 |
| Funded competitors cut prices | Margin squeeze | Compete on built-in features (§1), not price |
| Scope for one engineer | Slow to revenue | D-013, D-015, D-018, D-022 cut channels, infra, billing and campaigns; sizes re-estimated after M0 and M1 |
| Single node | An instance failure takes everything down | RDS for data; node rebuild scripted and drilled in M5; second node when revenue justifies it |
| Toll fraud and abuse | Direct cost | Outbound gating and rate limits from M3; strangers only get access in M5b with its controls |

## 11. Open questions

1. Company entity and country for billing and contracts (affects payment provider and compliance). Needed by M5b, not before.
2. Final speech vendor list and LLM endpoint per language after the bake-off.
3. Human takeover for phone: warm transfer only, or barge-in to listen?
4. Which local Sri Lankan carrier offers a SIP trunk we can point LiveKit SIP at, and at what cost per number.
5. Whether to meter LiveKit minutes separately or fold them into the voice minute price.
