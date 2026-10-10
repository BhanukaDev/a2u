# A2U — Build Plan

Status: draft · 2026-10-10. This is the plan Claude Code sessions work from. Each **work package (WP)** is sized for one session to a few sessions, has a clear "done when", and names the spec sections it implements. Milestone-level goals and exit criteria are in [milestones.md](milestones.md).

## How to use this plan

1. **One WP per session.** Start the session by naming the WP. Read `CLAUDE.md`, this WP, and the sections it references. Do not start the next WP in the same session unless the current one is done and committed.
2. **Tests first for the brain.** Anything in `a2u-core` gets unit tests before implementation; flows get eval YAML before code.
3. **Deviations become decisions.** If a WP cannot be built as specified, add a decision to [decisions.md](decisions.md) and update the spec, then build. Never leave the docs describing something the code does not do.
4. **Spikes write results.** M0 WPs end in a short file in `docs/spikes/` with measurements and a recommendation.
5. **Mark progress here.** Flip `[ ]` to `[x]` in the WP heading when done. Add a one-line note if the size estimate was badly wrong; it feeds the re-estimate at the end of M0 and M1.
6. **Commit per WP** with the WP ID in the message.

Dependencies are listed where they are not obvious. Within a milestone, WPs are roughly in order.

---

## M0 — De-risk

### [ ] WP-0.1 Speech bake-off harness
**Goal:** word error rate and naturalness scores per vendor and language.
**Build:** `evals/speech/` with 50 utterances per language (en-LK, si, ta, code-mixed) recorded on real phones; a script that runs each speech-to-text vendor and computes WER; a listening sheet for text-to-speech samples. Plus an **entity corpus**: 30 NICs, 30 phone numbers and 20 amounts per language, said the way people actually say them (in chunks, with number words), each with the expected value (D-026).
**Done when:** `docs/spikes/speech.md` has a table per language and vendor, a go/no-go per language for voice (D-019), and the raw speech-to-text accuracy on the entity corpus.

### [ ] WP-0.2 Region, LLM and LiveKit latency
**Goal:** pick the EC2 region and default voice LLM.
**Build:** a script run from Colombo and from candidate regions measuring round trips to LiveKit Cloud regions, speech vendors, and LLM time to first token for Haiku 5.5 and a fast Gemini model.
**Done when:** `docs/spikes/region.md` names the region and LLM, with numbers.

### [x] WP-0.3 LiveKit Agents proof
**Goal:** a latency floor before any A2U code exists.
**Build:** a throwaway LiveKit Agents worker: Deepgram → echo-style LLM → Cartesia; a plain HTML page using the LiveKit JS SDK; then a Twilio SIP trunk → LiveKit SIP inbound call to the same worker. Measure end of speech → first audio from Sri Lanka on both.
**Done when:** `docs/spikes/livekit.md` records both latencies, barge-in behaviour, and confirms or revises D-012 and D-024.
**Note (2026-10-10):** closed on a qualitative pass: web voice with tools works in English and Sinhala, which confirms D-012. The labelled Colombo p50/p95 and the barge-in count move to WP-0.2 and WP-2.8, and the SIP path to WP-3.1 (D-030).

### [ ] WP-0.4 Durability spike
**Goal:** confirm DBOS and the event fan-out mechanism.
**Build:** 200 concurrent DBOS workflows that sleep, call a fake webhook and publish an event; a listener using `LISTEN/NOTIFY` on a direct connection and one using DBOS workflow events; kill the process mid-run and check recovery. Check Pydantic AI's durable execution integration.
**Done when:** `docs/spikes/durability.md` picks the event mechanism and confirms D-005.

### [ ] WP-0.5 k3s node bootstrap
**Goal:** a reproducible single-node cluster.
**Build:** `deploy/k3s/user-data.sh` installing k3s, cert-manager, External Secrets Operator; `deploy/k3s/README.md` for the RDS instance, security groups and Secrets Manager layout; a hello-world chart installed by `helm`.
**Done when:** a fresh instance comes up and serves HTTPS from the chart with no manual steps; cost per month recorded in `docs/spikes/k3s.md`.

### [ ] WP-0.6 Numbers and WhatsApp applications
**Goal:** know where Sri Lankan numbers and WhatsApp access come from.
**Build:** check Twilio and Telnyx inventory; contact one or two local carriers about SIP trunks; submit the Meta Tech Provider application and A2U business verification; document the bring-your-own-account steps for tenants.
**Done when:** `docs/spikes/telephony.md` and `docs/spikes/whatsapp.md` record findings and application dates.

### [ ] WP-0.7 Payments and entity
**Goal:** a path to card billing for M5b.
**Build:** confirm provider availability for the intended entity; list options.
**Done when:** `docs/spikes/payments.md` and an entry in open questions or a decision.

### [ ] WP-0.9 Claim check spike
**Reads:** D-027, D-028; architecture §4.5.
**Goal:** know whether every sentence can be checked inline within budget.
**Build:** 300 labelled agent sentences (conversation, grounded information, unsupported information, grounded commitment, invented commitment) with their turn evidence, including Sinhala and code-mixed ones; a rules-plus-small-classifier checker; latency per sentence and precision and recall per class. Compare with an LLM judge as a baseline (offline only).
**Done when:** `docs/spikes/claim-check.md` reports p95 latency against the 50 ms budget and recall on invented commitments, and confirms or revises D-027 and D-028.

### [ ] WP-0.8 Re-estimate
**Done when:** milestone sizes in `milestones.md` are updated with M0 learnings, and decisions.md records the vendor list, region and LLM.

---

## M1 — Brain + web chat

### [ ] WP-1.1 Repo scaffold
**Build:** uv workspace with `packages/a2u-core`, `a2u-sdk`, `a2u-connectors`; `services/gateway`, `tasks`, `control`; bun workspace with `web/console`, `web/widget`; ruff, pyright, pytest, biome; GitHub Actions for lint, type check, tests; `docker-compose.dev.yml` with Postgres + pgvector.
**Done when:** CI is green on an empty project and `uv run pytest` runs in every package.

### [ ] WP-1.2 Schema and tenancy
**Reads:** architecture §6, PRD §6.14.
**Build:** Alembic migrations for all core tables; `tenant_id` required by every repository method; row-level security policies; an isolation test suite that attempts cross-tenant reads for every table.
**Done when:** the isolation suite passes with zero leaks and fails if a policy is removed.

### [x] WP-1.3 Config loader and validator
**Reads:** agent-config-spec.md (all).
**Build:** Pydantic models for spec v0.3; YAML/JSON load; validation with line-referenced errors; language-per-channel checks; document references resolved by name.
**Done when:** every example in the spec loads; a corpus of invalid configs produces the expected errors.
**Note (2026-10-10):** built against spec v0.3 (the current one). `packages/a2u-core/src/a2u_core/config`; spec gaps filled by D-031 and written up as spec §12. A test checks that every YAML example in the spec appears verbatim in a fixture that loads. Condition parsing is left to WP-1.5.

### [ ] WP-1.4 Worker contract and runtime
**Reads:** architecture §4.2.
**Build:** `Result`, `NeedsInput`, `Progress`, `Failed`; `llm` and `tool` worker kinds on Pydantic AI; scoped tool permission checks; anything else returned becomes `Failed`.
**Done when:** unit tests cover each contract type and permission denial.

### [ ] WP-1.5 Flow engine
**Reads:** spec §5.2–5.4, architecture §4.4.
**Build:** step interpreter for all nine steps; `collect` retries and timeout; `confirm.on_no`; condition evaluator (CEL subset); `say` rendering with filters per channel; nesting depth checks.
**Done when:** the spec's booking flow runs end to end against fake tools, including the decline-and-pick-again path; every step has tests.

### [ ] WP-1.6 Router
**Reads:** spec §6, PRD §6.4.
**Build:** `handoff`, `classifier`, `rules_then_classifier`; sticky routing and topic-change detection; routing spans with scores.
**Done when:** a routing eval set of 50 utterances hits ≥ 90% on the example agent.

### [ ] WP-1.7 Front agent turn loop
**Reads:** architecture §4.1, §4.5; PRD §6.2.
**Build:** turn loop with the four front-agent tools; per-channel profiles; pending `NeedsInput` in context with side-question handling; filler rotation per worker; `flow_said` events; a sentence-buffer hook where the claim check (WP-1.21) plugs in; turn events emitted for watchers (WP-1.22).
**Done when:** scripted tests show a side question answered mid-flow and the flow resumed.

### [ ] WP-1.20 Entity capture
**Reads:** D-026; spec §5.2, §5.5; architecture §4.6.
**Depends on:** WP-1.5; the WP-0.1 entity corpus.
**Build:** entity types with validators and normalisers; spoken-form parsers for en, si and ta (chunked numbers, number words); read-back rendering (`digits`, `spell`, `summary`); binding only after a confirmed read-back; format failures that don't use up a retry; disagreement between candidate and parser leads to a re-ask.
**Done when:** the entity corpus passes at ≥ 99% after read-back on text, and `2002 177 0 123 4` said in Sinhala binds `200217701234`.

### [ ] WP-1.21 Claim check and speech policies
**Reads:** D-027, D-028; architecture §4.5; spec §10 `speech`.
**Depends on:** WP-1.7, WP-0.9.
**Build:** sentence classifier (rules plus the model chosen in WP-0.9); grounding against turn evidence with typed-value normalisation; restricted topics; never-say rules; pending-task and unconfirmed-confirmation checks; regenerate-then-template on text and fallback on voice; `turn_claims` rows; eval assertions `no_unsupported_claims`, `not_said` and `captured`.
**Done when:** an eval where the user pushes for an out-of-policy refund passes with no unsupported commitment, a wrong amount from an LLM worker is caught, and the check stays within 50 ms p95 per sentence.

### [ ] WP-1.22 Watcher runtime and review queue
**Reads:** D-028; architecture §4.7; spec §10 `watch`.
**Depends on:** WP-1.11, WP-1.7.
**Build:** watchers as DBOS tasks subscribed to turn events; `rules`, `classifier` and `llm_judge` kinds; `steer` notes injected into the next turn; `escalate`; `flag` to `watch_flags`; built-in watchers (unsupported commitment, repeated misunderstanding, frustration); review queue in the console; watcher usage metered.
**Done when:** a watcher flags a planted bad turn without adding latency to the next turn, and a `steer` note changes the next reply in a scripted test.

### [ ] WP-1.8 Memory
**Reads:** PRD §6.9, spec §4.
**Build:** customers, identities, conversation summaries after N turns, facts with sensitivity and expiry; summary-first context loading; high-sensitivity facts hidden until verified.
**Done when:** a second conversation starts with the first's summary and does not volunteer a high-sensitivity fact.

### [ ] WP-1.9 Knowledge
**Reads:** PRD §6.10.
**Build:** upload PDF, DOCX, URL, text; parse, chunk, embed into pgvector; retrieval with citations in traces; knowledge scoped per front agent and worker.
**Done when:** a 50-question set over a sample FAQ answers ≥ 90% with correct citations.

### [ ] WP-1.10 Webhook and MCP tools
**Reads:** spec §8, PRD §6.14.
**Build:** signed webhook calls with timeouts, retries, idempotency keys, SSRF and egress guard (port from the previous runtime); MCP client with per-tool allowlist; `side_effects` enforcement.
**Done when:** a side-effecting tool cannot be called outside a post-`confirm` step, proven by test.

### [ ] WP-1.11 DBOS task workers and background tasks
**Reads:** architecture §4.3, PRD §6.5.
**Build:** task workflows keyed by tenant/conversation/task; `background: true`; event publish on the mechanism chosen in WP-0.4; subscribe from a live chat session; cancel; timeouts; idempotency keys.
**Done when:** a background task survives a worker restart and its result appears in the chat at the next turn boundary.

### [ ] WP-1.12 Gateway: web chat and sessions
**Reads:** architecture §5.1, §5.5; PRD §6.9.
**Build:** WebSocket chat; anonymous sessions with publishable key, origin allowlist and visitor ID; `POST /v1/sessions` for verified users with `act_as_token`; session refresh; streaming tokens and task events.
**Done when:** both session modes work from a test page; token expiry and re-acquire are tested.

### [ ] WP-1.13 Widget and headless client
**Build:** web component with chat UI, white-label theme tokens, `context` prop; headless TypeScript client used by the component; npm package layout.
**Done when:** the widget embeds on a plain HTML page and a React page with two lines of code.

### [ ] WP-1.14 Control API: orgs, workspaces, agents, versions, deploys
**Reads:** PRD §6.1, §6.13; architecture §7.
**Build:** Clerk JWT verification; org and workspace models and roles; agent CRUD; immutable versions on save; deploy to `draft`/`live`; rollback; plain-language diff; API keys with scopes; audit log.
**Done when:** the CLI flow `a2u deploy` works against the API and every mutation appears in the audit log.

### [ ] WP-1.15 Console v0
**Build:** React app with Clerk sign-in and org/workspace switcher; YAML editor with validation errors; document editor for personas and knowledge; playground with chat, test customer picker and fake time; transcript list; per-turn trace view.
**Done when:** a design partner can build and test their agent without touching the API directly.

### [ ] WP-1.16 Eval harness
**Reads:** spec §11, PRD §6.11.
**Build:** scripted runs; simulated-user runs with an LLM persona; assertions on tool calls, steps, `said`, routing; run on every version; deploy blocked on failure with override.
**Done when:** a deliberately broken version is blocked from deploy.

### [ ] WP-1.17 Metering v0
**Build:** `usage_events` for messages, LLM tokens by model, tasks, knowledge storage; per-workspace rollup query.
**Done when:** a day of playground use produces a correct usage report.

### [ ] WP-1.18 Helm chart v0 and CI deploy
**Reads:** architecture §8.
**Build:** `deploy/helm/a2u` with gateway, tasks, control, console sub-charts; `single-node.yaml`; External Secrets mappings; CI builds images to ECR and runs `helm upgrade`.
**Done when:** a merge to main reaches the k3s node with no manual steps.

### [ ] WP-1.19 Design partner onboarding
**Done when:** one partner's agent (≥ 2 workers, 1 flow, 1 background task) has served a week of real web chat traffic and the M1 exit criteria are recorded.

---

## M2 — Web voice

### [ ] WP-2.1 Voice worker skeleton
**Reads:** architecture §3, §5.2; D-012.
**Build:** `services/voice` on LiveKit Agents; job entry reads dispatch metadata, loads the agent version, subscribes to task events; `AgentSession` with speech-to-text and text-to-speech plugins chosen per language from config; `a2u-core` as the LLM node streaming tokens.
**Done when:** a browser session talks to a config agent end to end on a dev LiveKit project.

### [ ] WP-2.2 Turn detection, barge-in and filler on voice
**Build:** voice activity and turn detection plugins; cancel text-to-speech and in-flight LLM on barge-in; filler phrases spoken while tasks run; task events spoken at turn boundaries; idle check-in and hang-up (`policies.idle`).
**Done when:** barge-in works in ≥ 95% of 100 scripted interruptions.

### [ ] WP-2.3 Gateway voice endpoints
**Build:** `POST /v1/voice/token` minting room tokens; explicit agent dispatch with metadata; chat and voice sharing one conversation in one widget session.
**Done when:** switching between chat and voice in the widget keeps the same conversation and memory.

### [ ] WP-2.4 Speculative retrieval and spoken rendering
**Reads:** architecture §4.1, §4.5; spec §5.4 filters.
**Build:** start knowledge retrieval on interim transcripts; `date_spoken`, `time_spoken`, `money`, `digits_spoken` filters; the claim check on streamed text, sentence-buffered, before text-to-speech; voice fallback sentence; entity read-back on voice.
**Done when:** the booking flow's result is spoken verbatim with a natural date, an invented commitment and a wrong amount are caught before audio, and the latency target still holds with the check on.

### [ ] WP-2.5 Latency tracing and dashboard
**Reads:** architecture §11.
**Build:** OpenTelemetry spans for every stage; export to Langfuse Cloud and Grafana Cloud with PII masking; a dashboard of end of speech → first audio p50/p95 by vendor and model.
**Done when:** every turn in the playground shows a waterfall in the console.

### [ ] WP-2.6 Widget voice and bot check
**Build:** voice button, mic permission flow, connection state; bot check before voice for anonymous sessions.
**Done when:** a design partner's site has working web voice.

### [ ] WP-2.7 Voice metering and pricing check
**Build:** usage events for speech-to-text, LLM, text-to-speech and LiveKit minutes; cost per minute report.
**Done when:** measured cost per minute is compared with the pricing hypothesis in the PRD and the result is recorded.

### [ ] WP-2.8 Voice evals, recording, exit measurement
**Build:** playground browser voice; voice evals with latency assertions; opt-in recording with disclosure to S3.
**Done when:** 300 sessions from Sri Lanka meet p50 ≤ 900 ms and p95 ≤ 1.5 s, recorded in `docs/spikes/m2-latency.md`.

---

## M3 — Phone

### [ ] WP-3.1 LiveKit SIP inbound
**Reads:** architecture §5.3; D-024.
**Build:** Twilio trunk → LiveKit SIP; dispatch rule by called number; `number → tenant, agent version` lookup; caller-ID customer lookup while answering; call-length cap.
**Done when:** a call to a Twilio number reaches a config agent with the caller's summary loaded.

### [ ] WP-3.2 DTMF and transfer
**Build:** DTMF as input events; transfer to `handoff_number` via SIP transfer; `handoff: human` on voice.
**Done when:** a scripted call presses digits and is transferred to a real phone.

### [ ] WP-3.3 Shared test number
**Reads:** PRD §6.7; architecture §5.3.
**Build:** `reception` job; per-agent PINs shown in the console; PIN by DTMF or speech; in-session agent load.
**Done when:** a new builder hears their agent within 5 minutes in 5 of 5 moderated sessions.

### [ ] WP-3.4 Number provisioning
**Build:** console flow to buy a number from Twilio inventory and attach it to an agent; local carrier path if M0 found one.
**Done when:** a workspace buys and attaches a number without our involvement.

### [ ] WP-3.5 API-triggered outbound
**Reads:** PRD §6.8; architecture §5.6.
**Build:** `POST /v1/agents/{agent}/conversations` for calls; opt-out list; per-workspace rate limit; number verification before outbound is enabled.
**Done when:** an outbound call starts with goal and context, and a blocked number is refused with a clear error.

### [ ] WP-3.6 Phone latency and pricing
**Done when:** 300 calls from Sri Lankan mobiles meet the latency target, and cost per minute including telephony and LiveKit is recorded.

### [ ] WP-3.7 Third design partner live
**Done when:** three partners have real traffic and the M3 exit criteria are recorded.

---

## M4 — WhatsApp + cross-channel + delivery

### [ ] WP-4.1 WhatsApp inbound and outbound
**Reads:** architecture §5.4; PRD §6.7.
**Build:** Cloud API webhooks with signature verification; identity by `wa_id`; inactivity window; free-form replies inside the user-initiated window; template sending outside it; template management in the console.
**Done when:** a conversation runs on a real WhatsApp number through a tenant's own account.

### [ ] WP-4.2 Interactive rendering
**Build:** `choose` as list messages, `confirm` as buttons, cards on web; WhatsApp profile for reply length.
**Done when:** the booking flow completes on WhatsApp using only taps.

### [ ] WP-4.3 Consent and opt-out
**Reads:** D-023; spec §10.
**Build:** `consents` records; `ask_consent` behaviour when a background task may outlive the conversation; client-asserted consent via API; opt-out handling for "STOP"-style messages.
**Done when:** delivery is refused without consent and proceeds with it, by test and on a real device.

### [ ] WP-4.4 Delivery policy and post-conversation NeedsInput
**Reads:** architecture §4.3; PRD §6.5.
**Build:** live → WhatsApp → template chain after a conversation ends; `NeedsInput` after hang-up sent on WhatsApp and resumed on reply; stored-until-next-contact fallback.
**Done when:** the insurance scenario passes on real devices: task starts in a call, result arrives on WhatsApp after hang-up.

### [ ] WP-4.5 Cross-channel conversations
**Build:** several active channels per conversation; `prefer_channel` on `NeedsInput`; "call me" from chat starting an outbound call with context.
**Done when:** the call → WhatsApp email → WhatsApp confirmation script passes end to end.

### [ ] WP-4.6 Identity linking and OTP verify
**Build:** verified-only linking; `verify: otp` over WhatsApp; knowledge-check verify.
**Done when:** an unverified web user cannot see phone-identity facts, and a verified one can.

### [ ] WP-4.7 Human takeover
**Build:** live monitor; takeover for chat and WhatsApp; agent pause and resume; warm transfer for calls via WP-3.2.
**Done when:** an operator takes over a live WhatsApp conversation from the console and hands it back.

### [ ] WP-4.8 Playground WhatsApp simulation and voice notes
**Build:** simulated WhatsApp with window and fake time; voice notes transcribed and answered.
**Done when:** the delivery chain can be tested in the playground in under a minute.

### [ ] WP-4.9 WhatsApp embedded signup
**Depends on:** Tech Provider approval from WP-0.6.
**Done when:** a tenant connects a new WhatsApp Business account from the console.

---

## M5 — Operate + launch

### [ ] WP-5.1 Approvals and webhook triggers
**Build:** approval policies pausing tasks; approve in console or by signed webhook; webhook triggers with `within`.
**Done when:** a refund over the threshold waits for approval and a CRM event starts a call within 60 s.

### [ ] WP-5.2 Analytics and agent diagram
**Build:** volume, containment, outcomes per goal, latency percentiles, cost per conversation; generated diagram clickable into conversations; routing confusion matrix.
**Done when:** an operator can answer "which worker fails most, and show me examples" from the console.

### [ ] WP-5.3 Eval from transcript and replay
**Build:** "turn into eval" on a conversation with PII masked, also from a watcher flag in the review queue; replay against a draft version; watcher `remediate` drafts with operator approval.
**Done when:** a real conversation becomes a passing eval in one click, and a flagged conversation becomes a failing eval until the agent is fixed.

### [ ] WP-5.4 Invoices
**Build:** monthly invoice generation from `usage_events` per org with per-workspace lines, USD and LKR, PDF export, status tracking.
**Done when:** three partners receive correct invoices for a real month.

### [ ] WP-5.5 Business-tier data settings
**Build:** retention per workspace, no-recording mode, own LLM keys, audit export, SSO via Clerk; PDPA deletion per customer.
**Done when:** a deletion request removes a customer across all tables and the audit log records it.

### [ ] WP-5.6 Operations
**Build:** node rebuild drill from user-data and chart; RDS restore drill; load test at 100 concurrent calls and 1,000 chat sessions; alerts in Grafana Cloud; runbooks; status page.
**Done when:** rebuild under 30 minutes and restore drills pass, with results in `docs/spikes/ops.md`.

### [ ] WP-5.7 Docs, templates and quickstarts
**Build:** public docs site, API reference generated from the control API, quickstarts for chat, voice and phone, three templates.
**Done when:** a builder reaches a live chat + voice agent in under 60 minutes in 5 of 5 moderated sessions.

### [ ] WP-5.8 Launch
**Done when:** three partners are paying and the M5 exit criteria are recorded.

---

## M5b — Self-serve (start only on pull, D-018)

### [ ] WP-5b.1 Public sign-up and onboarding
### [ ] WP-5b.2 Payments: prepaid credits, auto top-up, spend caps, alerts
### [ ] WP-5b.3 Abuse controls: verification gates, free-credit limits, destination blocklist, rate limits
### [ ] WP-5b.4 Plan gating
### [ ] WP-5b.5 Stranger test: sign up, pay, go live without talking to us
