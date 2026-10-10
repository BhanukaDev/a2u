# A2U — Architecture

Status: draft v3 · 2026-10-10 · Companion to [prd.md](prd.md). Decisions and their reasons are in [decisions.md](decisions.md). The work packages that build this are in [build-plan.md](build-plan.md).

---

## 1. Principles

1. **Python and Postgres for almost everything.** Each new piece of infrastructure has to justify its operating cost. Buy transport (LiveKit), auth (Clerk) and observability (Langfuse Cloud, Grafana Cloud) rather than run them.
2. **The LLM understands and speaks; code decides.** Side effects only happen in typed tools, behind flow steps or approvals. For typed facts, code also speaks (D-017).
3. **One brain, thin channels.** Channel adapters normalise input and render output. They hold no agent logic.
4. **Only the front agent talks.** Workers return typed results. The one exception is a flow's final `result.say`, rendered verbatim by the engine.
5. **Conversations own tasks.** Work outlives calls, turns and pods.
6. **What the agent says is an action** (D-027). Commitments come only from code (`result.say`, `must_say`). Factual claims must be grounded in this turn's evidence. Every sentence is checked before it is sent.
7. **Guard inline, watch asynchronously** (D-028). Anything that must stop a mistake is deterministic and on the hot path within its latency budget. Anything that needs an LLM to judge runs beside the conversation and acts from the next turn.
8. **One chart.** The same Helm chart runs on the single k3s node in v1, on a multi-node cluster later, and in a dedicated cell for Enterprise. Only values change.

## 2. Planes

```
 ┌──────────────────────────── k3s, one EC2 instance (v1) ──────────────────────────────┐
 │                                                                                        │
 │  namespace: control                        namespace: cell                             │
 │  ┌──────────────────────────────┐          ┌───────────────────────────────────────┐   │
 │  │ Console (React, static)      │          │ Channel gateway (FastAPI)             │   │
 │  │ Control API (FastAPI)        │ versions │  web chat WS · widget sessions ·      │   │
 │  │  orgs · workspaces · agents  │ ───────▶ │  WhatsApp webhooks · outbound sender  │   │
 │  │  versions · deploys · evals  │          │  identity · LiveKit tokens/dispatch   │   │
 │  │  numbers · usage · invoices  │ ◀─────── │                                       │   │
 │  └──────────────────────────────┘  usage   │ Voice workers (LiveKit Agents)        │   │
 │                                    traces  │  1 call per job · STT → brain → TTS   │   │
 │  Traefik ingress · cert-manager            │                                       │   │
 │  External Secrets (AWS Secrets Manager)    │ Task workers (DBOS)                   │   │
 │                                            │  durable workers · tools · triggers   │   │
 │                                            └───────────────────────────────────────┘   │
 └──────────────────────────────┬─────────────────────────────────────────────────────────┘
                                │
   RDS Postgres (+ pgvector) · S3 (recordings, uploads) · LiveKit Cloud (rooms, SIP) ·
   Twilio SIP trunk · Meta Cloud API · Clerk · Langfuse Cloud · Grafana Cloud · speech/LLM vendors
```

- **Control plane** (`control` namespace): accounts, configs, deploys, evals, metering, invoices. Clerk authenticates its users.
- **Data plane cell** (`cell` namespace): everything that touches live conversations and customer data. In v1 both namespaces share one node; the boundary exists so a dedicated cell is a second release of the same chart with `control.enabled=false`.
- A cell keeps serving live traffic if the control API is down. It only loses deploys and config changes.
- All services import `a2u-core`. Agent versions are pushed into the cell's Postgres by the control API; the cell never calls the control API on the hot path.

## 3. Components

| Component | Tech | Responsibility |
|---|---|---|
| `a2u-core` | Python library, Pydantic AI | Front agent, router, worker runtime, flow engine, worker contract, result rendering, memory access, channel rendering, and the harness: entity capture, guards and the claim check, the watcher runtime. Imported by voice workers, the gateway and task workers |
| `a2u-sdk` | Python, on top of `a2u-core` | The code-agent API used by the A2U team (`Agent`, `@flow`, `@worker`, `Result`, …). Config agents compile to the same in-memory model |
| Voice worker | LiveKit Agents (Python) | Per-call job: room or SIP participant, voice activity and turn detection, speech-to-text plugin, `a2u-core` as the LLM node, text-to-speech plugin, barge-in, filler, DTMF, transfer |
| Channel gateway | FastAPI | Web chat WebSocket, widget sessions, `POST /v1/sessions`, LiveKit token minting and agent dispatch, WhatsApp webhooks, identity resolution, outbound sending, window and consent rules |
| Task workers | DBOS | Durable execution of delegated workers, background tools, triggers, approvals |
| Control API | FastAPI | Orgs, workspaces, agents, versions, deploys, numbers, keys, usage, invoices, evals |
| Console | React + TypeScript, Clerk components | Builder, documents (persona, knowledge), playground, agent diagram, transcripts and traces, analytics, settings |
| Widget | TypeScript web component + headless client | Chat over WebSocket, voice over the LiveKit client SDK, session refresh, white-label theme |
| Observability | OpenTelemetry → Langfuse Cloud (LLM traces) + Grafana Cloud (metrics, logs) | Per-stage latency, cost, errors. PII masked before export |

## 4. The brain

### 4.1 Turn loop (front agent)

```
event (user said/typed X, task event, or "flow said Y")
  → load conversation state (customer, active worker, pending tasks, summary, recent turns)
  → on voice: knowledge retrieval already started on the interim transcript; await it
  → router (if no sticky worker, or topic change detected)
  → front agent LLM call with tools:
        knowledge.search (read-only)
        delegate(worker, args)            -> returns task handle immediately
        answer_task_input(task, value)    -> resumes a NeedsInput task; entity values go through capture (§4.6)
        cancel_task(task)
     context includes: this turn's evidence, steer notes from watchers (§4.7), restricted-topic rules
  → claim check per sentence as the reply streams (§4.5): class → grounded? → send, regenerate or replace
  → render for channel → send/speak
  → emit the turn to watchers (async, never awaited)
```

- The front agent never holds side-effecting tools. `delegate` is the only way to cause effects.
- A pending `NeedsInput` is in context as "waiting for: slot (choose from …)". The front agent may answer a side question first, then return to it. It never invents the answer to a pending input.
- Filler: while a delegated task is `running`, the front agent emits the worker's declared filler, rotating phrases, at most one per pending task per turn.

### 4.2 Worker contract

```python
class Result(BaseModel):
    data: dict[str, Any]
    say: str | None = None        # set by the flow engine: already rendered, send verbatim (D-017)
    must_say: str | None = None   # set by LLM workers: values the front agent's paraphrase must contain

class NeedsInput(BaseModel):
    field: str
    hint: str
    schema: dict[str, Any]
    options: list[Option] | None = None          # for choose steps; renders as WhatsApp list / spoken options
    prefer_channel: Literal["same", "whatsapp"] = "same"

class Progress(BaseModel):
    note: str

class Failed(BaseModel):
    reason: str               # internal, goes to traces
    user_safe_message: str    # defaults to a platform apology
```

- A worker ends with `Result`, `NeedsInput` or `Failed`. `Progress` is reported while it runs, not returned. Anything else, including an exception, a timeout or a denied tool call, becomes `Failed` (D-032).
- LLM workers end through three output tools, `finish`, `ask_user` and `give_up`; they have no text output and cannot set `say`.
- Every tool call is checked when it is made against the worker's scope: listed, existing, MCP-allowlisted, and for side effects, after `confirm` or with an approval condition that is false for the call's arguments (D-033). A tool error fails the worker.
- Code: `a2u_core.workers` (`contract`, `tools`, `runtime`). Flow workers run in the flow engine (§4.4) and use the same scoped tool calls.

### 4.3 Task lifecycle

`queued → running → (needs_input ⇄ running) → done | failed | cancelled`

- Tasks live in DBOS workflows keyed by `(tenant_id, conversation_id, task_id)`.
- Task events are published on Postgres `LISTEN/NOTIFY`, channel `conv:{id}`, from a **direct** connection per process (not through a connection pooler, which breaks `LISTEN`). The M0 spike compares this with DBOS workflow events and the loser is deleted.
- A live voice worker or gateway session subscribed to the conversation receives events at once and surfaces them at the next turn boundary.
- If no live session is subscribed, the **delivery policy** runs: WhatsApp inside the user-initiated window, else an approved WhatsApp template. Both require a consent record (D-023). Without consent the result is stored and shown on the customer's next contact.
- Side-effecting tool calls carry an idempotency key `task_id:step`.

### 4.4 Flow engine

Config flows compile to a step list interpreted by `a2u-core`. Each step is a durable DBOS step, so a flow paused on `collect` can wait hours for a WhatsApp reply. Code flows (`@app.flow`) run the same primitives as Python `await`s.

- `collect` emits `NeedsInput`; the engine validates the answer against the type, re-asks up to `retries`, then fails the flow with `Failed(reason="invalid_input")`. A `timeout` fails it with `Failed(reason="timeout")`.
- `confirm` on "no" jumps to the step named in `on_no`, or ends with `Failed(reason="declined")` when `on_no` is absent.
- `result` renders `say` from bound values and returns `Result(data, say=...)`.
- Code: `a2u_core.flows` (`engine`, `render`, `inputs`) and `a2u_core.conditions`. The engine holds no run state: `start`, `resume` and `expire` take and return a JSON `FlowState`, which the task stores between steps (WP-1.11). `handoff` is reported on `FlowRun.handoff`. `verify` and `delegate` go through `Verifier` and `Delegator` interfaces. Rules the spec left open are in D-033.

### 4.5 Result rendering and the claim check

**Flow results are spoken by code.** The engine renders `result.say` and the channel adapter sends or speaks it directly. The front agent receives a `flow_said` event so its context is correct. This path has no LLM call and no check (D-017).

**Everything the front agent writes is checked, one sentence at a time** (D-027). The reply streams into a sentence buffer. Each sentence goes through these steps before it is sent or spoken:

1. **Class.** Fast rules (money, dates, percentages, IDs, words like "refund", "guarantee", "waive", "I have …") plus a small classifier label the sentence `conversation`, `information` or `commitment`.
2. **Grounding.** `information` must be supported by this turn's evidence: tool results, flow results, `must_say`, retrieved knowledge chunks, and customer facts the conversation may reveal. Typed values must match after normalisation ("15:30" = "3:30 pm"). `commitment` must match a `result.say` or `must_say` from this turn. Anything else is unsupported.
3. **Policy.** Never-say rules, restricted topics (no answer without an approved source), and no claim that a pending task is complete.
4. **Verdict.** If it passes, the sentence is sent. If it fails on a text channel, the reply is regenerated once with the reason, then falls back to a template. If it fails on voice, the sentence is replaced with the policy's fallback ("Let me confirm that for you") and the agent offers a check or a handoff.

Every checked sentence is written to `turn_claims` with its class, evidence references and verdict. On voice, the check adds the time to finish one sentence plus the guard budget (≤ 50 ms p95, D-028) before text-to-speech. WP-0.9 measures it.

### 4.6 Entity capture

`collect` steps with entity types, and identity checks such as `verify`, bind values through capture rather than taking the LLM's reading of the transcript (D-026):

```
transcript ("dedas dekai, ekasiya hathe hatha, ...")
  → LLM proposes a candidate, with the raw spans it used
  → parser for the type and language (chunked numbers, number words) → normalised value
  → validator (length, format, check digit) → if invalid: re-ask; the attempt does not count
  → read-back rendered by code (digits_spoken, spell, summary) → caller confirms
  → value bound; downstream tools receive only bound values
```

Disagreement between the LLM's candidate and the parser's output counts as a failed parse: the agent asks again rather than picking one.

### 4.7 Watchers

Watchers are asynchronous tasks subscribed to a conversation's turn stream (transcript, tool calls, claim verdicts). Each one is a classifier, a rule set or an LLM judge declared in `policies.watch`. Watchers never block a turn (D-028). Their actions are:

- `steer`: a short note added to the front agent's context on the next turn ("the caller asked for a refund twice; offer a handoff").
- `escalate`: offer a handoff, or perform one for severe findings.
- `flag`: write a `watch_flags` row, show it in the console's review queue, and offer it as an eval candidate (Prove).
- `remediate`: draft a correction message after the conversation. It is sent only after operator approval and with consent (D-023).

Watchers run in the task workers, so they survive restarts and continue after the call. Their LLM usage is metered per workspace.

## 5. Request flows

### 5.1 Web chat
Widget opens a WebSocket to the gateway with a session token → identity resolved (`web_visitor` or `web_user`) → conversation reopened or created → each message runs the turn loop in the gateway process → tokens streamed back → delegations become DBOS tasks whose events arrive over the same socket.

### 5.2 Web voice
1. Widget calls `POST /v1/voice/token` with its session token. The gateway creates or reopens the conversation, mints a LiveKit room token for room `conv:{id}`, and requests an agent dispatch with metadata `{tenant_id, agent_version_id, conversation_id}`.
2. LiveKit Cloud starts a job on one of our voice workers. The job loads the agent version from Postgres, subscribes to `conv:{id}`, and starts an `AgentSession` with the configured speech-to-text, text-to-speech, voice activity and turn detection plugins. `a2u-core` is the session's LLM node.
3. Turn loop per utterance. Interim transcripts start knowledge retrieval early. Barge-in cancels text-to-speech and the in-flight LLM call.
4. On disconnect: transcript saved, summary job queued, pending tasks keep running, delivery policy applies.

### 5.3 Inbound phone call
1. The carrier (Twilio trunk in v1, local carrier later) delivers the call to LiveKit SIP. A dispatch rule maps the called number to our voice worker with metadata `{number}`.
2. The job resolves `number → tenant, agent version` from Postgres, looks the customer up by caller ID, and loads summary, open tasks and context while the call is being answered.
3. Same turn loop as web voice. DTMF arrives as events; `transfer` uses LiveKit SIP transfer to the handoff number.

**Shared test number.** One A2U number maps to a `reception` job. It asks for a PIN (DTMF or spoken), resolves `PIN → tenant, agent version`, then loads that agent into the same session and continues as 5.3 step 3. PINs are per agent, shown in the console, and rotate.

### 5.4 WhatsApp message
Webhook → gateway verifies the signature → identity by `wa_id` → conversation reopened if within the inactivity window → turn loop in a gateway worker → reply via Cloud API, free-form inside the user-initiated window, template outside it. `choose` options render as an interactive list; `confirm` renders as buttons.

### 5.5 Web, verified user
Client backend → `POST /v1/sessions` (secret key, user, context, optional `act_as_token`) → session token (1 h) → widget connects → identity `web_user:{external_id}`.

### 5.6 API-triggered outbound
`POST /v1/agents/{agent}/conversations` with `to`, `channel`, `context`, `goal` → DBOS workflow checks opt-out list, rate limit and (for WhatsApp) consent → places a SIP call through LiveKit or sends a template → the conversation starts with `goal` and `context` in state.

## 6. Data model (core tables)

```
orgs(id, clerk_org_id, name, plan, settings jsonb)
workspaces(id, org_id, name, region, settings jsonb)                 -- the tenant; tenant_id below = workspaces.id
agents(id, tenant_id, name)
agent_versions(id, agent_id, version, spec jsonb, created_by, created_at, eval_status)
documents(id, tenant_id, kind, name, content, version, created_by, created_at)   -- persona prompts, knowledge source text
deployments(agent_id, env, version_id, deployed_at)
numbers(id, tenant_id, e164, provider, agent_id, test_pin)
customers(id, tenant_id, display_name, traits jsonb, created_at)
identities(id, tenant_id, customer_id, kind, value, verified_at)      -- unique(tenant_id, kind, value)
consents(id, tenant_id, customer_id, channel, purpose, source, granted_at, revoked_at)
conversations(id, tenant_id, customer_id, agent_id, version_id, goal, state jsonb, opened_at, closed_at, summary)
conversation_channels(conversation_id, channel, address, active, last_inbound_at)  -- WhatsApp window lives here
messages(id, conversation_id, role, channel, content jsonb, created_at)
tasks(id, tenant_id, conversation_id, worker, status, result jsonb, created_at, updated_at)
memory_facts(id, tenant_id, customer_id, fact, sensitivity, source_conversation_id, expires_at)
knowledge_chunks(id, tenant_id, source_id, content, embedding vector, metadata jsonb)
usage_events(id, tenant_id, conversation_id, kind, quantity, unit_cost, at)
invoices(id, org_id, period, lines jsonb, total, currency, status)
audit_log(id, tenant_id, actor, action, target, diff jsonb, at)
api_keys(id, tenant_id, kind, hash, scopes, created_at, revoked_at)
sessions(id, tenant_id, identity_id, expires_at, act_as_token_enc)
secrets(id, tenant_id, name, ciphertext, key_id)
opt_outs(tenant_id, address, at)
turn_claims(id, tenant_id, conversation_id, message_id, sentence, class, evidence jsonb, verdict, action, at)   -- claim check audit trail (D-027)
watch_flags(id, tenant_id, conversation_id, watcher, severity, finding, action, status, eval_candidate, at)  -- watcher findings (D-028)
```

Every tenant-scoped table carries `tenant_id`. Repositories require it, and Postgres row-level security enforces it as a second guard.

## 7. Identity and auth

| Who | Mechanism |
|---|---|
| Console users | Clerk. JWTs verified by the console and control API. Clerk organisation = A2U org; workspace roles stored by A2U. SSO on Business+ |
| Customer servers | Workspace API keys (hashed, scoped). Widget: publishable key (browser) + secret key (server only) |
| End users, phone/WhatsApp | Number = identity, treated as weak. `verify` step for sensitive flows (one-time code via WhatsApp, or knowledge check) |
| End users, web | Anonymous visitor (bot check before voice) or client-verified session via `POST /v1/sessions`; later client OIDC |
| Voice clients | LiveKit room tokens minted by the gateway, scoped to one room, short-lived |
| Outbound to customer systems | Signed webhooks (HMAC, timestamp), per-workspace secrets, `act_as_token` forwarding, OAuth tokens for connectors in an encrypted vault |

## 8. Deployment

- **One EC2 instance** running k3s in the M0 region. Starting size: 4 vCPU, 16 GB. Resize before adding nodes.
- **Postgres on RDS** with pgvector, automated backups and point-in-time restore. Never in-cluster in production.
- **One Helm chart** `deploy/helm/a2u` with sub-charts per service and values files `single-node.yaml`, `multi-node.yaml`, `cell-only.yaml`.
- **Voice workers**: a Deployment with guaranteed QoS (requests = limits), `terminationGracePeriodSeconds` equal to the call-length cap, and a preStop hook that stops accepting LiveKit jobs so active calls drain. When a second node exists, a `role=voice` node selector and taint keep everything else off it.
- **Ingress**: k3s Traefik, cert-manager with Let's Encrypt. Webhook endpoints (Meta, Twilio, LiveKit) are on the gateway host.
- **Secrets**: AWS Secrets Manager → External Secrets Operator → Kubernetes secrets. Per-workspace data keys wrapped by a KMS key.
- **Images**: built in CI, pushed to ECR, deployed by `helm upgrade` from CI with the chart's image tags.
- **Recovery**: the node is rebuilt from a user-data script (install k3s, External Secrets, cert-manager, `helm install`). Target under 30 minutes, drilled in M5.
- **Growth path**: bigger instance → second node for voice → EKS with the same chart for a dedicated cell.

## 9. Repository layout

```
a2u/
  CLAUDE.md          how to work in this repo (read first in every session)
  packages/
    a2u-core/        brain: front agent, router, flows, contract, memory, rendering; harness: capture, guards, claim check, watchers
    a2u-sdk/         code-agent API on top of core
    a2u-connectors/  built-in connectors (calendar, CRM, sheets, REST)
  services/
    gateway/         channels, sessions, webhooks, LiveKit tokens and dispatch, outbound
    voice/           LiveKit Agents worker (web voice, phone, reception for the test number)
    tasks/           DBOS workers, triggers, approvals
    control/         control API, metering, invoices, evals, deploys
  web/
    console/         React console
    widget/          embeddable widget + headless client
  deploy/
    helm/a2u/        one chart, values per layout
    k3s/             EC2 user-data, bootstrap scripts, RDS and secrets setup notes
  evals/             shared eval harness and fixtures
  docs/              these documents; docs/spikes/ holds M0 results
```

Python workspace with `uv`; TypeScript with `bun`. One `docker-compose.dev.yml` at the root runs Postgres and the services locally; production is the Helm chart only.

## 10. Reuse from the previous runtime

Port the ideas, and rewrite where the graph model leaks in:
- Structured LLM call plumbing on Pydantic AI (streaming, deferred tools, usage).
- Classifier config (categories, examples, fallback) → `classifier` router mode.
- Ask-question / deferred tool pattern → `NeedsInput`.
- Widget sessions, publishable/secret keys, refresh and 401 re-acquire, `context` prop.
- Egress and SSRF protection, redaction, audit, origin allowlists, limits.

Not carried over: the JSON graph compiler, ports and edges, LangGraph checkpointer.

## 11. Observability

- One trace per turn: speech-to-text, retrieval, routing, LLM (tokens, cost), tool calls, entity capture, claim check (class and verdict per sentence), text-to-speech, each with a duration. Watcher runs are separate spans linked to the turn they scored. Routing spans carry classifier scores so the console can show "why this worker".
- Voice dashboards: end-of-speech → first audio p50/p95 by channel, region, vendor and model.
- Cost per conversation, by component, joined to `usage_events` for invoices.
- PII masking is applied before traces leave the cell.
