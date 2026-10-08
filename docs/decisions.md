# A2U — Decision Log

Short architecture decision records. Add new ones at the bottom; never edit an accepted decision. Supersede it with a new one instead.

---

### D-001 · Horizontal, self-serve from day one
**Status:** accepted · 2026-10-09
**Decision:** A2U is a horizontal platform like Retell or Vapi. Developers in any industry sign up and build agents themselves. Self-serve is part of v1.
**Considered:** vertical (one industry, done-for-you, like Zeya Health); service first, then self-serve.
**Consequences:** v1 includes sign-up, billing, a config editor and abuse controls. The pitch has to rest on built-in features (PRD §1), because features are what horizontal buyers compare.

### D-002 · First market: Sri Lanka / South Asia
**Status:** accepted · 2026-10-09
**Consequences:** cell region near Sri Lanka; local numbers; WhatsApp is first-class; Sinhala/Tamil depend on the M0 speech gate; PDPA data rights from v1; payments and telephony checked in M0.

### D-003 · Front agent + workers
**Status:** accepted · 2026-10-09
**Decision:** one front agent talks to the user. Workers (LLM specialists, flows, tools) never do; they return typed results.
**Considered:** handoffs between specialists that each talk to the user (Vapi Squads style).
**Consequences:** one persona and one place for safety checks; asynchronous work comes naturally. Costs: an extra hop for delegated questions (reduced by letting the front agent answer from knowledge directly and use filler), and a paraphrasing risk (handled by the result check).

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
**Status:** proposed, pending M0 spike against LiveKit
**Decision:** Pipecat workers, one call per process, separate speech-to-text → LLM → text-to-speech by default. Speech-to-speech is optional per agent.
**Why:** any vendor or voice; text exists before audio, so the result check can run.

### D-008 · Managed Kubernetes; one Helm chart per plane
**Status:** accepted · 2026-10-09
**Decision:** managed Kubernetes (GKE/EKS/AKS). The data plane is a "cell" chart deployable to the shared cluster, a dedicated cluster or a customer VPC. The control plane always stays hosted by A2U.
**Rejected:** kagent / Agent Substrate. It is built for idle, long-lived agents; voice needs guaranteed CPU per call, and its API is still changing.

### D-009 · Self-hosted Keycloak for console identity only
**Status:** accepted · 2026-10-09
**Decision:** self-hosted Keycloak in the control plane, for console users only.
**Rejected:** Keycloak + Firebase (two user stores, nothing Firebase adds).
**Consequences:** custom login theme (e.g. Keycloakify); we operate Keycloak's upgrades and database. End-user identity (callers, chat users) is A2U's own: phone numbers, client-asserted web sessions, one-time codes.

### D-010 · Web identity by assertion from the client
**Status:** accepted · 2026-10-09
**Decision:** for logged-in web users, the client's backend creates A2U sessions with a secret key and its own user ID. Anonymous mode uses a publishable key, allowed origins and a bot check. Web users link to phone identities only when the phone is verified.

### D-011 · Postgres-first
**Status:** accepted · 2026-10-09
**Decision:** Postgres for state, memory, queues (DBOS), vectors (pgvector), audit and `LISTEN/NOTIFY` events. Add Redis only when measurements show we need it.
