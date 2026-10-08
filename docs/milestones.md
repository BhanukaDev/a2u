# A2U — Milestones to v1

Status: draft · 2026-10-09. All of v1 ships, in this order. Each milestone ends in something usable, with exit criteria that are measured, not felt. Sizes are rough for one full-time engineer and get re-estimated at the end of M0.

| # | Milestone | Rough size | Ends with |
|---|---|---|---|
| M0 | De-risk | 2–3 weeks | Go/no-go answers on vendors, region, payments, numbers |
| M1 | Brain + web chat | 5–6 weeks | A config agent with workers and flows, live on web chat |
| M2 | Voice: web + inbound phone | 5–6 weeks | Calls meeting the latency target |
| M3 | WhatsApp + SMS + cross-channel | 4–5 weeks | One conversation spanning call and WhatsApp |
| M4 | Background tasks, triggers, outbound | 4–5 weeks | Reminder campaigns and post-call delivery |
| M5 | Self-serve launch | 5–6 weeks | Public sign-up, billing, abuse controls, docs |

Design partners (2–3 businesses using it for free or at a discount) join at M1 and stay through launch. Their traffic is the test.

---

## M0 — De-risk

Spikes, each ending in a short written result in `docs/spikes/`.

- **Speech bake-off.** 50 real utterances each in English (Sri Lankan accent), Sinhala, Tamil and code-mixed speech. Score speech-to-text word error rate and text-to-speech naturalness for Deepgram, Google, ElevenLabs, Cartesia and Gemini Live. **Gate:** decide which languages launch.
- **Region and latency.** Measure round-trip times from Colombo to Mumbai and Singapore, and from each to the speech and LLM vendor endpoints. Pick the primary cell region.
- **Telephony.** Can we buy Sri Lankan local numbers via Twilio or Telnyx? If not, find a local SIP carrier and test a call through Pipecat.
- **Voice stack.** Pipecat with Twilio media streams versus LiveKit SIP: latency, barge-in quality, effort.
- **Durability.** DBOS with 200 concurrent workflows and `LISTEN/NOTIFY` fan-out. Check how Pydantic AI's durable execution support fits.
- **Payments.** Confirm whether Stripe is available to our entity. If not, decide between a local gateway and a foreign entity.
- **WhatsApp.** Start Meta business verification now (it takes time) and confirm embedded signup for tenants.

**Exit:** vendor list, region, payment path and number supply decided and written in [decisions.md](decisions.md).

## M1 — Brain + web chat

- Repo scaffold per [architecture.md §9](architecture.md#9-repository-layout-proposed): uv workspace, CI, lint, type check, tests.
- `a2u-core`: front agent, worker contract, `llm`/`tool`/`flow` workers, flow engine with all steps from the spec, router (all three modes), result check.
- Config loader and validator for spec v0; versions and deploys in the control API.
- Postgres schema (§6), tenant isolation with row-level security, migrations.
- Memory: customers, identities, summaries.
- Knowledge: upload, parse, embed, retrieve.
- Tools: webhooks (signed, retries, idempotency, SSRF guard) and MCP.
- Web chat widget (anonymous + verified sessions) and headless client.
- Console v0: agent editor (YAML with validation), playground (chat), transcripts and traces.
- Eval harness: scripted and simulated-user runs, run on each version.
- Keycloak in the control plane; orgs and roles.

**Exit:**
- A design partner's agent with ≥ 2 workers and 1 flow serves real web chat traffic for a week.
- Evals block a bad deploy.
- Zero cross-tenant reads in the isolation test suite.

## M2 — Voice: web + inbound phone

- Voice worker on Pipecat: transport, voice activity and end-of-turn detection, barge-in, filler, streaming text-to-speech.
- Provider abstraction for speech-to-text and text-to-speech; voices per agent.
- Inbound phone: numbers, SIP or media streams, caller-ID lookup during ringing, DTMF, transfer to a human, call-length cap.
- Web voice in the widget, with a bot check for anonymous users.
- Per-stage latency tracing and dashboard; usage metering per component.
- Optional recording with disclosure.
- Playground: browser voice.

**Exit:**
- p50 ≤ 900 ms and p95 ≤ 1.5 s from end of speech to first audio over 500 test calls from Sri Lanka.
- Barge-in works in ≥ 95% of scripted interruptions.
- Measured cost per minute supports the pricing hypothesis.

## M3 — WhatsApp + SMS + cross-channel

- WhatsApp Cloud API: embedded signup, inbound, interactive replies (buttons/lists), templates, 24-hour window enforcement, voice notes.
- SMS inbound/outbound with sender IDs.
- Conversations with several active channels; `NeedsInput.prefer_channel`; "call me" from chat.
- Identity linking rules (verified only) and the `verify` step via one-time code.
- Human takeover for chat and WhatsApp in the console.

**Exit:** a scripted conversation that starts on a call, collects an email over WhatsApp mid-call, and ends with a WhatsApp confirmation passes end to end on real devices.

## M4 — Background tasks, triggers, outbound

- `background: true` for workers and tools; task events by `LISTEN/NOTIFY`; delivery policy (live → WhatsApp → template → SMS).
- `NeedsInput` after a call ends, resumed over WhatsApp/SMS.
- Cancel on change of mind; idempotent side effects.
- Triggers: API, webhook, schedule; campaigns with pacing, calling hours, retries, opt-out list.
- Outbound calls with voicemail detection.
- Approvals in the console and by webhook.

**Exit:**
- The insurance-check scenario from the blueprint works on real calls: a task starts during the call and its result arrives on WhatsApp after the hang-up.
- A 500-contact reminder campaign completes within its calling hours without double-sending.

## M5 — Self-serve launch

- Sign-up (email, Google, GitHub), onboarding with templates, number purchase, WhatsApp connection wizard.
- Billing: prepaid credits, auto top-up, spend caps, invoices; plans and tier gating.
- Abuse controls: verification before outbound or number purchase, toll-fraud blocklist, free-credit limits, rate limits.
- Business tier data settings: region, retention, no-recording, own LLM keys, audit export, SSO.
- Console polish: generated agent diagram, analytics, version diffs.
- Public docs, API reference, quickstarts, status page.
- Load test at 200 concurrent calls per cell; incident runbooks; backups and restore drill.

**Exit:**
- A stranger goes from sign-up to a live phone agent in under 60 minutes in 5 of 5 moderated sessions.
- The load test passes.
- The restore drill succeeds.

## After v1
- Dedicated cells and VPC deploys for Enterprise.
- Speech-to-speech mode GA, client OIDC for web identity, more connectors.
- SOC 2 Type I readiness.
