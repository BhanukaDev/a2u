# A2U — Milestones to v1

Status: draft v2 · 2026-10-09. Order follows D-016: web chat → web voice → phone → WhatsApp. Each milestone ends in something usable, with exit criteria that are measured, not felt. The detailed work packages are in [build-plan.md](build-plan.md).

Sizes assume one engineer working with Claude Code, so typing is cheap and integration against real vendors, devices and carriers is what costs time. They are re-estimated at the end of M0 and again at the end of M1.

| # | Milestone | Rough size | Ends with |
|---|---|---|---|
| M0 | De-risk | 2–3 weeks | Go/no-go answers on speech vendors, region, LiveKit, DBOS, numbers, WhatsApp, payments |
| M1 | Brain + web chat | 6–8 weeks | A config agent with workers, flows and background tasks, live on web chat for a design partner |
| M2 | Web voice | 4–5 weeks | Browser voice meeting the latency target from Sri Lanka |
| M3 | Phone | 3–4 weeks | Inbound calls through LiveKit SIP, shared test number, API-triggered outbound |
| M4 | WhatsApp + cross-channel + delivery | 5–6 weeks | A conversation spanning call and WhatsApp; results delivered after hang-up |
| M5 | Operate + launch | 4–5 weeks | Three paying design partners, invoices, operations, docs |
| M5b | Self-serve (on pull) | 4–5 weeks | Public sign-up, prepaid credits, abuse controls |

Total to M5: roughly 24–31 weeks. Design partners (2–3 businesses using it free or at a discount, then invoiced) join at M1 and stay through launch. Their traffic is the test.

---

## M0 — De-risk

Spikes, each ending in a short written result in `docs/spikes/`. Several run in parallel because they are mostly waiting on vendors.

- **Speech bake-off.** 50 real utterances each in English (Sri Lankan accent), Sinhala, Tamil and code-mixed speech. Score speech-to-text word error rate and text-to-speech naturalness for Deepgram, Google, ElevenLabs, Cartesia and Gemini Live. **Gate:** which languages launch on voice (D-019).
- **Region, LLM endpoint and LiveKit.** Round-trip times from Colombo to Mumbai and Singapore, from each to LiveKit Cloud's nearest region, to the speech vendors, and LLM time to first token for Haiku 5.5 and a fast Gemini model from each region. Pick the EC2 region and the default voice LLM.
- **LiveKit Agents end to end.** A throwaway agent: browser → LiveKit room → Deepgram → trivial LLM → Cartesia, measured from Sri Lanka. Then the same agent answering a Twilio SIP trunk call via LiveKit SIP. Confirms D-012 and D-024 and gives a latency floor before any of our code exists.
- **Durability.** DBOS with 200 concurrent workflows; `LISTEN/NOTIFY` on direct connections versus DBOS workflow events for fan-out; how Pydantic AI's durable execution integration fits.
- **k3s on EC2.** User-data script that brings up k3s, Traefik, cert-manager, External Secrets; `helm install` of a hello-world chart; RDS reachable from the node. Records instance size and monthly cost.
- **Telephony numbers.** Can Twilio or Telnyx sell Sri Lankan numbers? If not, which local carrier offers a SIP trunk LiveKit SIP can register with, and at what price.
- **WhatsApp.** Apply for Meta Tech Provider status and start A2U's own business verification now. Confirm what a tenant needs to bring their own WhatsApp Business account in the meantime.
- **Payments.** Confirm whether Stripe is available to our entity. If not, decide between a local gateway and a foreign entity. Only needed by M5b, but entity decisions take time.

**Exit:** vendor list, region, LLM endpoint, number supply, WhatsApp path and payment path written in [decisions.md](decisions.md). Milestone sizes re-estimated.

## M1 — Brain + web chat

- Repo scaffold per [architecture.md §9](architecture.md#9-repository-layout): uv workspace, bun, CI, lint, type check, tests, `docker-compose.dev.yml`.
- `a2u-core`: front agent, worker contract, `llm`/`tool`/`flow` workers, flow engine with all steps from the spec including `say` rendering, `collect` retries and `confirm.on_no`, router (all three modes), result check for LLM worker results.
- Config loader and validator for spec v0.2; documents (persona, instructions); versions and deploys in the control API; plain-language diff.
- Postgres schema (architecture §6), orgs and workspaces, tenant isolation with row-level security, migrations.
- Memory: customers, identities, summaries, facts with sensitivity.
- Knowledge: upload, parse, embed, retrieve, cite.
- Tools: webhooks (signed, retries, idempotency, SSRF guard) and MCP.
- DBOS task workers; `background: true`; task events to a live chat session; cancel; idempotency keys.
- Web chat widget (anonymous + verified sessions), headless client, white-label theme.
- Console v0 with Clerk: workspace switcher, agent editor (YAML with validation), document editor, playground (chat, test customer, fake time), transcripts and traces.
- Eval harness: scripted and simulated-user runs, run on each version, blocking deploy.
- Usage metering for messages, LLM tokens and tasks.
- Helm chart v0 deployed to the k3s node; CI builds and deploys.

**Exit:**
- A design partner's agent with ≥ 2 workers, 1 flow and 1 background task serves real web chat traffic for a week.
- Evals block a bad deploy.
- Zero cross-tenant reads in the isolation test suite.
- A background task started in chat delivers its result into the same chat at the next turn boundary.

## M2 — Web voice

- Voice worker on LiveKit Agents: `a2u-core` as the LLM node, speech-to-text and text-to-speech plugins per language, voice activity and turn detection, barge-in, filler, streaming.
- Gateway: LiveKit token minting and agent dispatch; conversation shared between chat and voice in the same widget session.
- Speculative knowledge retrieval on interim transcripts.
- Deterministic `say` on voice with spoken filters; result check on streamed text for LLM worker results.
- Per-stage latency tracing and dashboard; voice usage metering by component.
- Widget: voice button, mic permissions, bot check for anonymous users.
- Playground: browser voice; voice evals with latency assertions.
- Optional recording with disclosure.

**Exit:**
- p50 ≤ 900 ms and p95 ≤ 1.5 s from end of speech to first audio over 300 browser sessions from Sri Lanka.
- Barge-in works in ≥ 95% of scripted interruptions.
- A design partner uses web voice on their site.
- Measured cost per minute supports the pricing hypothesis.

## M3 — Phone

- LiveKit SIP with the Twilio trunk: inbound dispatch by called number, caller-ID lookup while answering, DTMF, transfer to a human number, call-length cap.
- Shared test number with per-agent PINs; reception job that loads the chosen agent in-session.
- Number provisioning in the console (Twilio inventory; local carrier if M0 found one).
- API-triggered outbound calls with opt-out list, per-workspace rate limit and number verification.
- Phone-specific latency measurements and dashboard.
- Pricing check including telephony and LiveKit minutes.

**Exit:**
- A stranger with an existing agent hears it on the phone within 5 minutes via the test number, in 5 of 5 moderated sessions.
- Phone meets the same latency target over 300 calls from Sri Lankan mobiles.
- Three design partners have real traffic (chat, voice or phone).

## M4 — WhatsApp + cross-channel + delivery

- WhatsApp Cloud API: tenant connection (bring-your-own account; embedded signup once Tech Provider status arrives), inbound, interactive lists and buttons for `choose`/`confirm`, templates, window enforcement, voice notes.
- Consent records; `ask_consent` behaviour; opt-outs.
- Delivery policy after a conversation ends: live → WhatsApp → template, gated by consent.
- `NeedsInput` after a call ends, resumed over WhatsApp.
- Conversations with several active channels; `NeedsInput.prefer_channel`; "call me" from chat.
- Identity linking rules (verified only); the `verify: otp` step over WhatsApp.
- Human takeover for chat and WhatsApp in the console.
- Playground: simulated WhatsApp with window and fake time.

**Exit:**
- The insurance-check scenario from the blueprint works on real devices: a task starts during a call and its result arrives on WhatsApp after the hang-up, only when consent was given.
- A scripted conversation starts on a call, collects an email over WhatsApp mid-call, and ends with a WhatsApp confirmation.

## M5 — Operate + launch

- Approvals in the console and by webhook; webhook triggers.
- Analytics: volume, containment, outcomes per goal, latency percentiles, cost per conversation.
- Generated agent diagram, clickable into conversations; routing confusion matrix; eval-from-transcript.
- Invoices from metering (USD and LKR), per org with per-workspace lines.
- Business-tier data settings: retention, no-recording, own LLM keys, audit export, SSO via Clerk.
- Operations: node rebuild drill under 30 minutes, RDS restore drill, load test at 100 concurrent calls, runbooks, alerts, status page.
- Public docs, API reference, quickstarts, templates (receptionist, lead qualifier, support triage).

**Exit:**
- Three design partners are invoiced and paying.
- A builder goes from an empty workspace to a live web chat + voice agent in under 60 minutes in 5 of 5 moderated sessions.
- Rebuild and restore drills pass; the load test passes.

## M5b — Self-serve (only when there is pull)

- Public sign-up through Clerk with onboarding and templates.
- Prepaid credits, auto top-up, spend caps, low-balance alerts, invoices for credit purchases. Payment provider per the M0 decision.
- Abuse controls: card or phone verification before outbound or number purchase, free-credit limits, toll-fraud destination blocklist, rate limits.
- Plan gating.

**Exit:** a stranger signs up, pays and is live without talking to us.

## After v1
- Schedule triggers and campaigns (D-022); SMS as a delivery fallback and for one-time codes (D-013).
- Second node for voice; EKS and dedicated cells for Enterprise; VPC deploys.
- Sinhala voice when the speech gate passes; speech-to-speech mode GA; client OIDC; more connectors.
- SOC 2 Type I readiness.
