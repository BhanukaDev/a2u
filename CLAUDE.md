# A2U

A platform for AI agents that talk to customers over web chat, web voice, phone and WhatsApp, with one brain and one customer memory across channels, and durable work that outlives the call. Built by one engineer with Claude Code.

## Read first, in this order

1. `docs/build-plan.md` — find the work package (WP) for this session. One WP per session.
2. The spec sections that WP references: `docs/agent-config-spec.md`, `docs/architecture.md`, `docs/prd.md`.
3. `docs/decisions.md` — the decision log. Check it before proposing a technology or structural change.

`docs/milestones.md` has the milestone goals and exit criteria. `docs/spikes/` holds M0 measurements.

## Principles that constrain code

- **Only the front agent talks.** Workers return `Result`, `NeedsInput`, `Progress` or `Failed`, nothing else. The one exception is a flow's `result.say`, rendered by the engine and sent verbatim (D-017).
- **The LLM understands and speaks; code decides.** Side effects happen only in typed tools called from flow steps after `confirm`, or behind an approval policy. The front agent has no side-effecting tools.
- **What the agent says is an action** (D-027). Commitments (refunds, amounts, deadlines, "I have done X") come only from `result.say` or `must_say`. Factual claims must be grounded in this turn's evidence. Every front-agent sentence goes through the claim check before it is sent.
- **Guard inline, watch asynchronously** (D-028). Inline guards are deterministic and within ≤ 50 ms p95 per sentence; no LLM judge on the hot path. Watchers run beside the conversation and act from the next turn.
- **Entities are bound by code** (D-026). IDs, phone numbers, amounts and addresses are parsed, validated and read back by code before any tool sees them.
- **Conversations own tasks.** Tasks are DBOS workflows keyed by `(tenant_id, conversation_id, task_id)`. They survive restarts and hang-ups.
- **Every tenant-scoped query carries `tenant_id`** (the workspace ID). Repository methods require it; Postgres row-level security is the second guard. A query without it is a bug, not a shortcut.
- **One brain, thin channels.** Channel adapters normalise input and render output. No agent logic in adapters.
- **Config stays capped.** No loops, no code, no new flow steps without a decision record. Requests for more logic get webhooks, MCP or code agents.
- **Buy, do not run:** LiveKit Cloud (voice transport), Clerk (console auth), Langfuse Cloud and Grafana Cloud (observability), RDS (Postgres). Do not add Redis, a queue, or another datastore without a measurement that demands it (D-011).

## Stack

- Python 3.12+, `uv` workspace. Pydantic AI for LLM calls. DBOS for durable tasks. FastAPI for services. LiveKit Agents for voice. Alembic for migrations. Postgres with pgvector.
- TypeScript with `bun`. React for the console, a web component plus headless client for the widget. LiveKit JS SDK for web voice.
- k3s on one EC2 instance, one Helm chart at `deploy/helm/a2u`, images in ECR, secrets through External Secrets Operator. Local dev uses `docker-compose.dev.yml`.
- Default models: `anthropic:claude-haiku-5-5` for the front agent on voice, `anthropic:claude-sonnet-5-5` for specialists and text channels. The voice default may change after the M0 region spike.

## Conventions

- Tests before implementation for anything in `packages/a2u-core`. Flows get eval YAML before code.
- Python: ruff, pyright strict, pytest. TypeScript: biome, strict TS, vitest.
- Commit per WP; put the WP ID in the message (`WP-1.5: flow engine`).
- Tool calls to customer systems are signed, time out, retry with idempotency keys, and pass the SSRF guard. No exceptions for "internal" URLs.
- PII is masked before anything is exported to Langfuse or Grafana Cloud.
- Status fields and enums are strings in the database with a CHECK constraint, not Postgres enums.

## When the plan and reality disagree

Add a decision to `docs/decisions.md` (new number, status, decision, why, consequences), update the affected spec section, then build. Never leave the docs describing something the code does not do. Mark the WP done in `docs/build-plan.md` with a note if the size estimate was badly wrong.
