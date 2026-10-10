# A2U — Decision Log

Short architecture decision records. Add new ones at the bottom; never edit the body of an accepted decision. Supersede it with a new one and update only its status line.

---

### D-001 · Horizontal platform, self-serve last
**Status:** accepted · 2026-10-09 · amended by D-018
**Decision:** A2U is a horizontal platform like Retell or Vapi. Developers in any industry build agents themselves from config.
**Considered:** vertical (one industry, done-for-you, like Zeya Health); service first, then self-serve.
**Consequences:** the pitch has to rest on built-in features (PRD §1), because features are what horizontal buyers compare. See D-018 for the order in which self-serve pieces ship.

### D-002 · First market: Sri Lanka / South Asia
**Status:** accepted · 2026-10-09
**Consequences:** cell region near Sri Lanka; WhatsApp is first-class; Sinhala/Tamil depend on the M0 speech gate (per channel, see D-019); PDPA data rights from v1; payments and telephony checked in M0.

### D-003 · Front agent + workers
**Status:** accepted · 2026-10-09
**Decision:** one front agent talks to the user. Workers (LLM specialists, flows, tools) never do; they return typed results.
**Considered:** handoffs between specialists that each talk to the user (Vapi Squads style).
**Consequences:** one persona and one place for safety checks; asynchronous work comes naturally. Costs: an extra hop for delegated questions (reduced by letting the front agent answer from knowledge directly and use filler), and a paraphrasing risk (handled by D-017 and the result check).

### D-004 · Pydantic AI over Google ADK
**Status:** accepted · 2026-10-09
**Decision:** use Pydantic AI for LLM calls and typed outputs. A2U owns sessions, orchestration and memory.
**Why:** native multi-provider support; typed, validated outputs; a thin library rather than a framework competing with our own runtime; existing code from the previous runtime already uses it.
**Revisit if:** we go all-in on Gemini Live or Vertex Agent Engine.

### D-005 · DBOS for durable tasks
**Status:** proposed, pending M0 spike
**Decision:** durable workflows as a Postgres-backed library, not a separate cluster.
**Considered:** Temporal (heavier to operate), Restate, Hatchet.
**Revisit if:** the M0 load test fails, or we need workflow features DBOS lacks.

### D-006 · No customer code; capped config flows
**Status:** accepted · 2026-10-09
**Decision:** customers never upload code. Config flows support only the steps in the spec, with no loops. Custom logic lives in webhooks, MCP or code agents built by the A2U team.
**Why:** removes sandboxing from the platform; stops config growing back into a node-graph engine.
**Consequences:** connectors and webhooks must be excellent. The likely future request for inline transforms gets an expression language (CEL/JSONata), not Python.

### D-007 · Pipecat for voice, cascaded by default
**Status:** superseded by D-012 · 2026-10-09
**Decision:** Pipecat workers, one call per process, separate speech-to-text → LLM → text-to-speech by default. Speech-to-speech is optional per agent.
**Why:** any vendor or voice; text exists before audio, so the result check can run.

### D-008 · Managed Kubernetes; one Helm chart per plane
**Status:** superseded by D-015 · 2026-10-09
**Decision:** managed Kubernetes (GKE/EKS/AKS). The data plane is a "cell" chart deployable to the shared cluster, a dedicated cluster or a customer VPC. The control plane always stays hosted by A2U.
**Rejected:** kagent / Agent Substrate. It is built for idle, long-lived agents; voice needs guaranteed CPU per call, and its API is still changing.

### D-009 · Self-hosted Keycloak for console identity only
**Status:** superseded by D-014 · 2026-10-09
**Decision:** self-hosted Keycloak in the control plane, for console users only.
**Rejected:** Keycloak + Firebase (two user stores, nothing Firebase adds).
**Consequences:** custom login theme (e.g. Keycloakify); we operate Keycloak's upgrades and database. End-user identity (callers, chat users) is A2U's own: phone numbers, client-asserted web sessions, one-time codes.

### D-010 · Web identity by assertion from the client
**Status:** accepted · 2026-10-09
**Decision:** for logged-in web users, the client's backend creates A2U sessions with a secret key and its own user ID. Anonymous mode uses a publishable key, allowed origins and a bot check. Web users link to phone identities only when the phone is verified.

### D-011 · Postgres-first
**Status:** accepted · 2026-10-09
**Decision:** Postgres for state, memory, queues (DBOS), vectors (pgvector), audit and task events. Add Redis only when measurements show we need it.

---

## Review round, 2026-10-09

The decisions below come from the first review of the v1 docs. The theme: keep the brain, cut everything that does not differentiate, and buy transport, auth and observability instead of running them.

### D-012 · LiveKit for voice transport and the audio pipeline
**Status:** accepted · 2026-10-09 · supersedes D-007
**Decision:** LiveKit Agents (Python) for the per-call audio pipeline, LiveKit Cloud for WebRTC rooms and SIP ingress/egress. Cascaded speech-to-text → `a2u-core` → text-to-speech by default; `a2u-core` plugs in as the LLM node of the LiveKit agent session. Speech-to-speech stays optional per agent.
**Why:** LiveKit bundles the parts that are pure cost for us: WebRTC for the web widget (client SDKs included), SIP trunking for phone, voice activity and turn detection, noise cancellation, global edge. Pipecat would have us assemble each of those. Vendor and voice choice is unaffected.
**Consequences:** web voice and phone share one worker codebase. Our voice workers run in the same region as the LiveKit Cloud region we pick in M0. We depend on LiveKit Cloud pricing and uptime; self-hosting LiveKit stays possible if that becomes a problem.

### D-013 · No SMS in v1
**Status:** accepted · 2026-10-09
**Decision:** v1 channels are web chat, web voice, phone and WhatsApp. SMS is not a conversation channel in v1.
**Why:** in Sri Lanka SMS is used for one-time codes, not conversations, and WhatsApp covers everything else. Supporting SMS as a channel means sender-ID rules per country and a second messaging adapter for little gain. Twilio one-way SMS can serve as a delivery fallback and for one-time codes later, without being a channel.
**Consequences:** the `verify: otp` step sends codes over WhatsApp in v1. The task delivery policy is `live → whatsapp` only; SMS fallback is after v1.

### D-014 · Managed auth for console identity
**Status:** accepted · 2026-10-09 · supersedes D-009
**Decision:** use Clerk for console users: sign-up, social login, organisations, invitations, roles and SSO on the Business tier. Clerk organisations map one-to-one to A2U orgs.
**Considered:** WorkOS AuthKit (also fine; pick it if Clerk pricing becomes a problem), Supabase Auth, self-hosted Keycloak (D-009).
**Why:** operating Keycloak is weeks of work with no differentiation for a solo founder. Clerk ships React components, org management and SSO with a day of integration.
**Consequences:** console and control API verify Clerk JWTs. End-user identity (callers, chat users) remains A2U's own, exactly as in D-009.

### D-015 · k3s on one EC2 instance first; grow the same chart later
**Status:** accepted · 2026-10-09 · supersedes D-008
**Decision:** v1 runs on a single-node k3s cluster on one EC2 instance in the region chosen in M0, with Postgres on RDS. Control plane and data plane are one Helm chart (`deploy/helm/a2u`) with values for single-node and multi-node layouts. Ingress is k3s's bundled Traefik with cert-manager for TLS. Scaling steps are: bigger instance → add a dedicated voice node → EKS for a dedicated or VPC cell, all with the same chart.
**Considered:** Docker Compose on VMs (cheaper to learn, but a rewrite when the first dedicated cell is sold); managed Kubernetes from day one (D-008, too much cost and ops for v1).
**Why:** one artifact from day one at roughly the cost of a Compose VM. k3s adds under a gigabyte of overhead and nothing to operate beyond the instance itself.
**Consequences:** single node means no high availability in v1; the availability target is met by fast recovery (RDS, an AMI or user-data script that rebuilds the node, and images in a registry). Voice workers run as a Deployment with guaranteed QoS (requests equal to limits) so chat and control work cannot starve a live call. Zero-downtime deploys use pod drain with a termination grace period sized to the call-length cap. Postgres is never in-cluster in production; a local Postgres is fine for dev.

### D-016 · Delivery order: web chat → web voice → phone → WhatsApp
**Status:** accepted · 2026-10-09
**Decision:** milestones ship channels in this order. Web chat proves the brain, web voice proves latency with no carrier in the way, phone adds SIP and numbers, WhatsApp adds the cross-channel story.
**Why:** phone numbers and WhatsApp business verification are slow external dependencies. Web chat and browser voice have none, so design partners can use the product from M1 and hear it from M2.

### D-017 · Flows speak typed results deterministically
**Status:** accepted · 2026-10-09 · amended by D-027
**Decision:** a flow's final `result` carries a `say` template rendered by code and sent or spoken verbatim. The front agent is told what was said and continues from there. LLM workers return `Result.must_say` instead, and the result check applies to the front agent's paraphrase of those.
**Why:** on voice the result check must buffer the whole sentence before text-to-speech, and a regenerate costs a full round trip of silence. For flows the values are already typed, so code can speak them. "Code decides" becomes "code decides and code speaks the facts."
**Consequences:** the result check runs only after LLM worker results, which is the rarer path. Flow authors write one sentence per result.

### D-018 · Self-serve pieces ship last, and only on pull
**Status:** accepted · 2026-10-09 · amends D-001
**Decision:** design partners pay by invoice. Usage metering exists from M1 so invoices are accurate. Public sign-up, prepaid credits and card payments are the final milestone and start only when there is demand from people we have not met.
**Why:** for one engineer, billing and abuse controls are the lowest-return work in the plan. Paying partners are a stronger signal than a sign-up page.
**Consequences:** M5 is "operate and launch", with self-serve as an optional second half. Abuse controls that only matter for strangers (free-credit limits, card verification) move with it.

### D-019 · Language support is gated per channel
**Status:** accepted · 2026-10-09
**Decision:** each language is enabled separately for voice and for text. The M0 speech bake-off gates voice languages. Text languages are gated by LLM quality on WhatsApp and web chat.
**Why:** Sinhala speech-to-text and text-to-speech are weak everywhere, but LLMs handle Sinhala and Tamil text reasonably. A global gate would fail Sinhala entirely; a per-channel gate ships it where it works.

### D-020 · Hosted observability
**Status:** accepted · 2026-10-09
**Decision:** OpenTelemetry from every service. LLM traces go to Langfuse Cloud; metrics and logs go to Grafana Cloud. PII masking runs before anything leaves the cell. Self-hosting is reconsidered when a dedicated cell demands it.
**Why:** same reasoning as D-014 and D-015: no differentiation in running these ourselves.

### D-021 · Agencies are a first-class customer
**Status:** accepted · 2026-10-09
**Decision:** an **org** (the billing entity, a Clerk organisation) owns one or more **workspaces**. A workspace is the tenant: every data row carries its ID, and usage is metered per workspace. A company has one workspace; an agency has one per client.
**Why:** in South Asia the people who will build agents from config are agencies, integrators and BPOs, not the clinic itself. Vapi runs an agency programme for the same reason.
**Consequences:** `tenant_id` in the data model means workspace ID. Roles exist at both org and workspace level. The widget can be white-labelled per workspace.

### D-022 · Campaigns and schedule triggers are after v1
**Status:** accepted · 2026-10-09
**Decision:** v1 triggers are inbound, API and webhook. Schedule triggers, campaigns with pacing, calling hours, retries and voicemail detection ship after launch.
**Why:** campaigns are a product of their own. The v1 story is inbound conversations plus work that outlives them.
**Consequences:** the outbound guardrails that remain in v1 are the opt-out list, per-workspace rate limits and number verification, because API-triggered outbound calls still need them.

### D-023 · Business-initiated WhatsApp requires recorded consent
**Status:** accepted · 2026-10-09
**Decision:** the task delivery policy and any WhatsApp message outside a user-initiated window require a consent record for that customer and purpose. Consent is collected in conversation (the agent asks) or asserted by the client with a timestamp and source.
**Why:** Meta policy and Sri Lanka's PDPA both require opt-in for business-initiated messages. Building it in avoids every tenant getting it wrong.

### D-024 · Telephony through LiveKit SIP with Twilio as the first trunk
**Status:** accepted · 2026-10-09 · local-number path pending M0
**Decision:** phone calls enter and leave through LiveKit SIP. The first SIP trunk is Twilio Elastic SIP Trunking, which gives outbound calling to Sri Lanka and inbound numbers in the countries Twilio serves. A local Sri Lankan carrier trunk is added when the M0 check finds one that works, so tenants can have local numbers.
**Why:** one SIP integration point regardless of carrier. Twilio is the fastest way to a working phone channel; it is not the long-term source of Sri Lankan numbers.
**Consequences:** the shared test number (PRD §6.7) is a Twilio number. Per-tenant local numbers depend on the carrier spike.

---

## Harness round, 2026-10-10

The decisions below come from the first voice spike (WP-0.3). The spike showed that wiring speech-to-text, an LLM and text-to-speech together takes hours, so it is not where A2U's value is. The value is a reusable harness that makes agents for any company and any use case (support, sales, emergency triage, WhatsApp assistants) say and do what was expected. In the spike, a Sinhala NIC said in chunks ("දෙදහස් දෙකයි, එකසිය හැත්තෑ හතයි") was read back with a digit missing, and the agent claimed it had confirmed numbers the caller never confirmed. Both were fixed by code, not by prompting.

### D-025 · A2U is a reliability harness: Capture, Guard, Watch, Prove
**Status:** accepted · 2026-10-10
**Decision:** every agent runs inside four built-in layers. Builders configure them; they do not build them.
- **Capture** (inline): typed entities (IDs, phone numbers, names, addresses, amounts, dates) with locale-aware parsers, validators and read-back policies (D-026).
- **Guard** (inline, deterministic, fast): checks before a tool call and before speech. It can block, replace or regenerate (D-027, D-028).
- **Watch** (asynchronous): classifiers and LLM judges that score the conversation as it happens, never on the hot path. They steer the next turn, escalate or flag (D-028).
- **Prove** (offline): evals on every version. Flagged conversations become eval candidates, so the suite grows from real failures.
**Why:** buyers compare platforms on correctness, not on whether the voice pipeline exists. A horizontal platform (D-001) can only promise "an agent for any company in days" if correctness is built in rather than rebuilt per agent.
**Consequences:** PRD §6.15, architecture §4.5–4.7, spec §5.5, §10 and §11. Every layer emits spans and records, so a tenant can show what the agent said and why.

### D-026 · Entities are captured by code, not by the LLM's reading of a transcript
**Status:** accepted · 2026-10-10
**Decision:** `collect` takes entity types (`nic_lk`, `phone`, `person_name`, `address`, `money`, `date`, `email`, and others) as well as plain types. Each type has a parser for spoken forms per language (chunked numbers such as "two thousand two, one seventy seven"; Sinhala and Tamil number words), a validator (length, format, check digit where one exists), a normaliser, and a read-back policy (`digits`, `spell`, `summary`, `none`). A value is bound only after the read-back has been confirmed. A value that fails format validation sends the agent back to the caller without using up a verification attempt.
**Considered:** leaving extraction to the LLM with prompt instructions. The spike showed this drops digits on chunked numbers, and models also claim a confirmation that never happened.
**Why:** a wrong ID, number or address is the most common and most visible failure of voice agents, and it is the one where local-language handling is an edge over US-built platforms.
**Consequences:** the LLM still proposes a value from the transcript; code validates it, renders the read-back (`digits_spoken` and the other filters) and binds it. Entity parsers need test corpora per language, recorded alongside the WP-0.1 speech bake-off.

### D-027 · What the agent says is an action: claims are classed and grounded
**Status:** accepted · 2026-10-10 · amends D-017
**Decision:** every sentence the front agent produces falls into one of three classes:
- **Conversation** (greetings, empathy, questions, filler) is free-form.
- **Information** (policy, prices, hours, eligibility, account facts) must be supported by evidence given in that turn: a tool result, a flow result, `must_say`, or a retrieved knowledge chunk.
- **Commitment** (refunds, waivers, compensation, deadlines, amounts owed, "I have done X") may only be spoken from a flow's `result.say` or a worker's `must_say`. The front agent never phrases a commitment on its own.
The **claim check** replaces the result check and runs on every front-agent sentence, not only after LLM worker results. An unsupported sentence is regenerated once on text channels, or replaced by a fallback sentence ("Let me confirm that for you") on voice, and the event is recorded. Tenants can also mark **restricted topics** (answers only from approved sources, otherwise hand off) and write **never-say** rules.
**Why:** in *Moffatt v. Air Canada* (BC Civil Resolution Tribunal, 2024), a chatbot invented a bereavement refund policy. The airline was held liable for what the bot said, and the correct policy being on its website was no defence. The tenant owns every sentence, so the harness has to make unsupported sentences impossible or visible.
**Consequences:** D-017's note that "the result check runs only after LLM worker results" no longer holds. The claim check is on the hot path for every sentence, so it must meet the inline guard budget in D-028. A spike (WP-0.9) measures its latency and accuracy before M1 builds it. Each checked sentence is stored with its class, evidence and verdict (`turn_claims`), which gives tenants an audit trail.

### D-028 · Guards run inline and are deterministic; watchers run asynchronously and act on the next turn
**Status:** accepted · 2026-10-10
**Decision:** inline guards (format validation, confirmation gates, tool permissions, the claim check, PII before speech) are code, rules or small classifiers with a budget of **≤ 50 ms p95 per sentence**. An LLM judge is never on the hot path. Watchers (LLM judges, sentiment, policy compliance, trajectory scoring, custom classifiers) run asynchronously on the transcript and tool trace and can act only from the next turn onward:
- `steer`: add a note to the front agent's context for the next turn.
- `escalate`: offer or perform a handoff to a human.
- `flag`: mark the conversation for review and make it an eval candidate.
- `remediate`: draft a correction message after the conversation. It is sent only with operator approval and a consent record (D-023).
**Considered:** an LLM judge on every reply before it is spoken. That adds hundreds of milliseconds per sentence on voice and breaks the latency goal.
**Why:** a background monitor cannot stop a mistake that has already been spoken. Only inline code can. Watchers find what rules miss and feed the eval suite.
**Consequences:** a watcher's finding never changes a turn that has already been sent. Builders choose for each risk whether it is a guard (blocks) or a watcher (detects). Watchers use the same model providers as workers and are metered as usage.

### D-029 · Safety-critical uses are a separate tier, not v1
**Status:** accepted · 2026-10-10
**Decision:** A2U is horizontal, but emergency response and other safety-critical uses (medical triage, crisis lines, dispatch) are not sold in v1. When offered, they need a safety-critical profile: a human dispatcher can always join or take over, no free-form advice (only restricted topics answered from approved protocols), provider failover for speech and LLM, and stricter latency and availability targets.
**Why:** a stalled call, a misheard address or a wrong triage can cost a life, not a refund. The single-node v1 deployment (D-015) cannot meet the availability such uses need.
**Consequences:** PRD non-goals list it. The Capture and Guard layers are designed so the safety-critical profile is stricter configuration, not a different product.

## Build-order round, 2026-10-10

### D-030 · M1 starts now; the remaining M0 spikes run alongside and gate only the work that needs them
**Status:** accepted · 2026-10-10 · amends the M0-before-M1 order in milestones.md
**Decision:** WP-0.3 is closed and M1 (brain + web chat) starts. Each remaining spike must finish before the first WP that depends on it, not before M1:
- WP-0.9 claim check → before WP-1.21 (and before WP-1.7 locks its sentence-buffer hook).
- WP-0.4 durability → before WP-1.11.
- WP-0.5 k3s → before WP-1.18.
- WP-0.1 speech bake-off and WP-0.2 region and LLM → before M2.
- WP-0.6 telephony numbers → before M3; WhatsApp application → before M4.
- WP-0.7 payments → before M5b.
**Why:** one engineer. The web-chat brain does not depend on speech vendors, region, numbers, WhatsApp or payments, and the LiveKit spike already showed the voice plumbing works. Waiting for every spike delays the first design partner for no gain. The claim check stays first among the M1 harness pieces because D-027 and D-028 depend on its result.
**Consequences:** the WP-0.3 latency and barge-in numbers are collected in WP-0.2 and WP-2.8, and the SIP path in WP-3.1. WP-0.8's re-estimate happens at the end of M1 instead of the end of M0. The WhatsApp application is filed late, which risks the M4 schedule because Meta verification takes weeks.

## Config round, 2026-10-10

### D-031 · How agent config is parsed and what validation fills in where the spec was silent
**Status:** accepted · 2026-10-10
**Decision:** the config loader (WP-1.3) follows these rules, now written into spec §12:
- **YAML 1.2 scalars.** Plain scalars are resolved with the YAML 1.2 core schema, not PyYAML's YAML 1.1 rules, so `claim_check: on`, `filler: off` and `confirm` answers such as `no` stay strings. Only `true`/`false` are booleans. Explicit tags, merge keys (`<<`) and duplicate keys are errors. Input is capped at 1 MB and 50,000 values after alias expansion.
- **Unknown keys are errors.** Every object rejects fields the spec does not define, so a typo cannot silently disable a setting.
- **`instructions` is a document name when it looks like one** (`[a-z][a-z0-9_-]*`, such as `products`); anything else is inline text. `persona` is always a document name.
- **Delegated flows read their arguments as `${args.x}`.** Flows can read `args`, `customer` and their own bindings; nothing else. Webhook triggers read `event`; evals read `result` and `customer`.
- **Webhook tools have side effects unless they say `side_effects: false`.** A side-effecting tool needs an earlier `confirm` on every path through a flow, or an approval policy; LLM and tool workers need an approval policy. Connector and MCP operations are not checked until the connector catalog (WP-1.10) declares their effects.
- **`speech_to_speech` cannot be combined with `restricted_topics` or `never_say`**, because no sentence exists to check (PRD §6.9).
- Smaller fills: a webhook trigger's `action` is `call` or `whatsapp`; `knowledge.refresh` is `daily`, `weekly` or `monthly`; `max_call_minutes` defaults to 20; `recording_disclosure` defaults to `true`; voice vendors left out mean the platform default.
**Why:** YAML 1.1 reads `on` as `true`, which breaks the spec's own examples. The remaining rules close gaps that would otherwise be decided by accident in code; the side-effect default is the safe one under "code decides".
**Consequences:** the console editor and the CLI give the same line-referenced errors. Configs written with YAML 1.1 habits (`enabled: yes`) get a type error rather than a silent boolean. Condition expressions (`if:`, `approval`) are parsed by the flow engine in WP-1.5, not by the loader.
