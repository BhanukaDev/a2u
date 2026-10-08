# A2U — Architecture

Status: draft · 2026-10-09 · Companion to [prd.md](prd.md). Decisions and their reasons are in [decisions.md](decisions.md).

---

## 1. Principles

1. **Python and Postgres for almost everything.** Each new piece of infrastructure has to justify its operating cost.
2. **The LLM understands and speaks; code decides.** Side effects only happen in typed tools, behind flow steps or approvals.
3. **One brain, thin channels.** Channel adapters normalise input and render output. They hold no agent logic.
4. **Only the front agent talks.** Workers return typed results.
5. **Conversations own tasks.** Work outlives calls, turns and pods.
6. **One data-plane artifact.** The same Helm chart runs in the shared cluster, a dedicated cell, or a customer VPC.

## 2. Planes

```
                         ┌───────────────────────── CONTROL PLANE (A2U-hosted, always) ─────────────────────────┐
                         │ Console (React) · Control API · Keycloak (self-hosted) · Billing/metering · Registry  │
                         │ of agent versions · Number/WhatsApp provisioning · Eval runner · Deploy orchestrator │
                         └──────────────┬───────────────────────────────────────────────────────────────────────┘
                                        │ pushes agent versions, tenant settings; pulls usage, traces
          ┌─────────────────────────────▼──────────────────────────── DATA PLANE (one "cell") ───────────────────────┐
 Phone ──▶│ Telephony edge ──▶ Voice workers (Pipecat, 1 call/process) ──┐                                            │
 Web voice│                                                              ▼                                            │
 Web chat▶│ Channel gateway (FastAPI) ─────────────────────────────▶  Brain (a2u-core library)  ◀── Task workers (DBOS)│
 WhatsApp▶│   webhooks · widget sessions · identity · outbound sender     front agent · router · worker contract       │
 SMS ────▶│                                                                                                            │
          │ Postgres (tenants' conversations, customers, memory, tasks/queues, pgvector, audit)  ·  Object storage    │
          └────────────────────────────────────────────────────────────────────────────────────────────────────────────┘
```

- **Control plane**: one global deployment that we run. Handles accounts, configs, billing, evals and deploys. Keycloak lives here and serves only console users.
- **Data plane cell**: everything that touches live conversations and customer data. The shared cell is multi-tenant. Dedicated and VPC cells run the same chart for one tenant.
- A cell keeps serving live traffic if the control plane is down. It only loses deploys and config changes.

## 3. Components

| Component | Tech | Responsibility |
|---|---|---|
| `a2u-core` | Python library, Pydantic AI | Front agent, router, worker runtime, flow engine, worker contract, result check, memory access, channel rendering. Imported by voice workers, the gateway and task workers |
| `a2u-sdk` | Python, on top of `a2u-core` | The code-agent API used by the A2U team (`Agent`, `@flow`, `@worker`, `Result`, …). Config agents compile to the same in-memory model |
| Voice worker | Pipecat | Per-call audio pipeline: transport (Twilio media streams / SIP / WebRTC), voice activity detection, end-of-turn detection, speech-to-text, `a2u-core` front agent, text-to-speech, barge-in, filler |
| Channel gateway | FastAPI | Webhooks (WhatsApp, SMS, telephony status), widget sessions, `POST /v1/sessions`, identity resolution, outbound sending, 24-hour window rules |
| Task workers | DBOS | Durable execution of delegated workers, background tools, triggers, campaigns, approvals |
| Control API | FastAPI | Orgs, agents, versions, deploys, numbers, keys, usage, evals |
| Console | React + TypeScript | Builder, playground, generated agent diagram, transcripts and traces, analytics, settings |
| Widget | TypeScript web component + headless client | Chat + web voice, session refresh |
| Observability | OpenTelemetry → Langfuse (self-hosted) + Prometheus/Grafana | LLM traces, per-stage latency, cost |

## 4. The brain

### 4.1 Turn loop (front agent)

```
event (user said/typed X, or task event)
  → load conversation state (customer, active worker, pending tasks, summary)
  → router (if no sticky worker, or topic change detected)
  → front agent LLM call with tools:
        knowledge.search (read-only)
        delegate(worker, args)            -> returns task handle immediately
        answer_task_input(task, value)    -> resumes a NeedsInput task
        cancel_task(task)
  → result check (must_say / typed values vs reply)
  → render for channel → send/speak
```

The front agent never holds side-effecting tools. `delegate` is the only way to cause effects.

### 4.2 Worker contract

```python
class Result(BaseModel):
    data: dict[str, Any]
    must_say: str | None = None

class NeedsInput(BaseModel):
    field: str
    hint: str
    schema: dict[str, Any]
    prefer_channel: Literal["same", "whatsapp", "sms"] = "same"

class Progress(BaseModel):
    note: str

class Failed(BaseModel):
    reason: str               # internal, goes to traces
    user_safe_message: str
```

### 4.3 Task lifecycle

`queued → running → (needs_input ⇄ running) → done | failed | cancelled`

- Tasks live in DBOS workflows keyed by `(tenant_id, conversation_id, task_id)`.
- Task events go out via Postgres `LISTEN/NOTIFY` on channel `conv:{id}`. A live voice worker or gateway session subscribed to that conversation receives them at once.
- If no live session is subscribed, the **delivery policy** runs: WhatsApp (window open) → WhatsApp template → SMS.
- Side-effecting tool calls carry an idempotency key `task_id:step`.

### 4.4 Flow engine

Config flows compile to a step list interpreted by `a2u-core`. Each step is a durable DBOS step, so a flow paused on `collect` can wait hours for a WhatsApp reply. Code flows (`@app.flow`) run the same primitives as Python `await`s.

### 4.5 Result check

After the front agent drafts a reply that follows a `Result`:
1. Pull typed values from `Result.data` that are marked as user-facing (money, dates, times, IDs), plus `must_say`.
2. Check that they appear in the draft, normalised (e.g. "15:30" = "3:30 pm").
3. On mismatch: regenerate once with the error; then use a template sentence.
4. Also block phrases claiming completion while a related task is pending.

On voice this adds latency only when a `Result` is being spoken, and it runs on streamed text before text-to-speech.

## 5. Request flows

### 5.1 Inbound phone call
1. The carrier sends a call to the telephony edge; the gateway resolves `number → tenant, agent version`.
2. While it rings: the customer is looked up by caller ID; summary, open tasks and upcoming context load into state.
3. A voice worker is assigned (pre-warmed pool), opens the media stream and subscribes to `conv:{id}`.
4. Turn loop per utterance. Delegations become DBOS tasks.
5. On hang-up: transcript saved, summary job queued, pending tasks keep running, and the delivery policy applies to their results.

### 5.2 WhatsApp message
Webhook → gateway verifies the signature → identity by `wa_id` → conversation (reopened if within the inactivity window) → turn loop in a gateway worker → reply via Cloud API (free-form inside 24 h, template outside).

### 5.3 Web, verified user
Client backend → `POST /v1/sessions` (secret key, user, context, optional `act_as_token`) → session token (1 h) → widget connects (WebSocket for chat, WebRTC for voice) → identity `web_user:{external_id}`.

### 5.4 Outbound trigger
Webhook or schedule fires → DBOS workflow checks opt-out list, calling hours and tenant rate limit → places call or sends template → the conversation starts with `goal` and `context` in state.

## 6. Data model (core tables)

```
tenants(id, plan, region, settings jsonb)
agents(id, tenant_id, name)
agent_versions(id, agent_id, version, spec jsonb, created_by, created_at, eval_status)
deployments(agent_id, env, version_id, deployed_at)
customers(id, tenant_id, display_name, traits jsonb, created_at)
identities(id, tenant_id, customer_id, kind, value, verified_at)      -- unique(tenant_id, kind, value)
conversations(id, tenant_id, customer_id, agent_id, version_id, goal, state jsonb, opened_at, closed_at, summary)
conversation_channels(conversation_id, channel, address, active, last_inbound_at)  -- 24h window lives here
messages(id, conversation_id, role, channel, content jsonb, created_at)
tasks(id, tenant_id, conversation_id, worker, status, result jsonb, created_at, updated_at)
memory_facts(id, tenant_id, customer_id, fact, source_conversation_id, expires_at)
knowledge_chunks(id, tenant_id, source_id, content, embedding vector, metadata jsonb)
usage_events(id, tenant_id, conversation_id, kind, quantity, unit_cost, at)
audit_log(id, tenant_id, actor, action, target, diff jsonb, at)
api_keys(id, tenant_id, kind, hash, scopes, created_at, revoked_at)
sessions(id, tenant_id, identity_id, expires_at, act_as_token_enc)
secrets(id, tenant_id, name, ciphertext, key_id)
```

Every table carries `tenant_id`. Repositories require it, and Postgres row-level security enforces it as a second guard.

## 7. Identity and auth

| Who | Mechanism |
|---|---|
| Console users | Keycloak (self-hosted, control plane). OIDC to the console and control API. Organisations, MFA, social login; SAML/OIDC SSO on Business+ |
| Customer servers | Org API keys (hashed, scoped). Widget: publishable key (browser) + secret key (server only) |
| End users, phone/SMS/WhatsApp | Number = identity. `verify` step for sensitive flows (one-time code via SMS/WhatsApp) |
| End users, web | Anonymous visitor (bot check before voice) or client-verified session via `POST /v1/sessions`; later client OIDC |
| Outbound to customer systems | Signed webhooks (HMAC, timestamp), per-tenant secrets, `act_as_token` forwarding, OAuth tokens for connectors in an encrypted vault |

## 8. Deployment

- Managed Kubernetes. One Helm chart for the data plane (`deploy/helm/a2u-cell`) and one for the control plane.
- Node pools: `voice` (CPU-guaranteed, no overcommit, scaled on active calls plus a warm buffer), `general` (gateway, task workers, control), `data` (Postgres if not managed).
- Postgres: managed (Cloud SQL / RDS) in the shared cell; the chart also supports an in-cluster operator (CloudNativePG) for VPC cells.
- Rollouts: voice workers drain (no new calls; existing calls finish) before termination; `terminationGracePeriodSeconds` sized to the call-length cap.
- Secrets: external secret store (cloud KMS) → Kubernetes secrets; per-tenant data keys wrapped by the cell key.

## 9. Repository layout (proposed)

```
a2u/
  packages/
    a2u-core/        brain: front agent, router, flows, contract, memory, rendering
    a2u-sdk/         code-agent API on top of core
    a2u-connectors/  built-in connectors (calendar, CRM, sheets, REST)
  services/
    gateway/         channels, sessions, webhooks, outbound
    voice/           Pipecat workers
    tasks/           DBOS workers, triggers, campaigns
    control/         control API, billing, evals, deploys
  web/
    console/         React console
    widget/          embeddable widget + headless client
  deploy/
    helm/a2u-cell/
    helm/a2u-control/
  evals/             shared eval harness and fixtures
  docs/
```

Python workspace with `uv`; TypeScript with `bun`.

## 10. Reuse from the previous runtime

Port the ideas, and rewrite where the graph model leaks in:
- Structured LLM call plumbing on Pydantic AI (streaming, deferred tools, usage).
- Classifier config (categories, examples, fallback) → `classifier` router mode.
- Ask-question / deferred tool pattern → `NeedsInput`.
- Widget sessions, publishable/secret keys, refresh and 401 re-acquire, `context` prop.
- Egress and SSRF protection, redaction, audit, origin allowlists, limits.

Not carried over: the JSON graph compiler, ports and edges, LangGraph checkpointer.

## 11. Observability

- One trace per turn: speech-to-text, routing, LLM (tokens, cost), tool calls, result check, text-to-speech, each with a duration.
- Voice dashboards: end-of-speech → first audio p50/p95 by region, vendor and model.
- Cost per conversation, by component, joined to `usage_events` for billing.
- PII masking is applied before traces leave the cell.
