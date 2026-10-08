# A2U — Agent Config Spec (v0)

Status: draft · 2026-10-09. This is the surface self-serve customers build with. Code agents built with `a2u-sdk` compile to the same model.

The cap on expressiveness is deliberate (see [decisions.md](decisions.md), D-006). Requests for loops or general-purpose logic are answered with webhooks or code agents, not new constructs.

---

## 1. Top level

```yaml
agent: sunrise-dental            # slug, unique per tenant
description: Front desk for Sunrise Dental
languages: [en, si, ta]          # first is the default

front: {...}                     # §2, required
channels: {...}                  # §3
memory: {...}                    # §4
router: {...}                    # §6
workers: {...}                   # §5
triggers: [...]                  # §7
tools: {...}                     # §8
knowledge: {...}                 # §9
policies: {...}                  # §10
evals: [...]                     # §11
```

## 2. Front agent

```yaml
front:
  persona: prompts/front_desk.md      # or inline `instructions:`
  model: anthropic:claude-haiku-4-5
  profiles:                           # optional per-channel overrides
    whatsapp: { model: anthropic:claude-sonnet-5-5 }
    web_chat: { model: anthropic:claude-sonnet-5-5 }
  voice:
    stt: deepgram:nova-3
    tts: cartesia:sonic
    voice_id: maya
    mode: cascaded                    # cascaded | speech_to_speech
  knowledge: [clinic_faq, price_list] # read-only, answered directly
  filler: auto                        # auto | off | list of phrases
  handoff_number: "+94 11 234 5600"   # human fallback for calls
```

## 3. Channels

```yaml
channels:
  phone:    { numbers: ["+94 11 234 5678"], recording: false }
  whatsapp: { account: sunrise-dental }
  sms:      { sender: SUNRISE }
  web:      { chat: true, voice: true, mode: verified }   # anonymous | verified | both
```

## 4. Memory

```yaml
memory:
  identify_by: [phone, whatsapp, web_user]
  link_web_to_phone: verified_only
  summary_after_turns: 10
  facts: true
  retention: 365d
```

## 5. Workers

### 5.1 Kinds

```yaml
workers:
  products:                      # kind: llm
    kind: llm
    description: Product questions, availability, specs   # used by routing
    instructions: prompts/products.md
    model: anthropic:claude-sonnet-5-5
    tools: [search_catalog]
    knowledge: [catalog]

  insurance:                     # kind: tool
    kind: tool
    tool: insurer_check
    background: true
    timeout: 10m

  bookings:                      # kind: flow
    kind: flow
    description: Book, move or cancel appointments
    steps: [...]                 # §5.2
```

### 5.2 Flow steps (complete list)

| Step | Shape | Behaviour |
|---|---|---|
| `collect` | `{ name: type \| choose(source) }` | Emits `NeedsInput`. The front agent asks; the value is validated against the type |
| `choose` | `{ name: choose(tool_or_list), max_options: 3 }` | Offers options; the user picks one |
| `confirm` | `"text with ${vars}"` | Explicit yes/no. A "no" ends the flow with `Failed(reason="declined")` |
| `verify` | `otp \| knowledge: [dob, postcode]` | Identity check before continuing |
| `call_tool` | `{ name, args, as }` | Calls a tool; the result is bound to `as` |
| `delegate` | `{ worker, args, as, background }` | Runs another worker |
| `handoff` | `human \| worker_name` | Ends this flow and passes the conversation on |
| `if` | `{ if: condition, then: [steps], else: [steps] }` | Branch on collected values (§5.3). Nesting depth ≤ 2 |
| `result` | `{ data: {...}, must_say: "..." }` | Final `Result`. Defaults to the last tool result |

No loops, no variables apart from `collect`/`as` bindings, no arbitrary code.

### 5.3 Conditions

CEL-style expressions over bound values and customer traits: comparison, `&&`, `||`, `!`, `in`, `has()`, string `startsWith`. No function definitions, no I/O.

```yaml
- if: "order.status == 'damaged' && order.total <= 100"
  then: [ { call_tool: { name: issue_refund, args: { id: "${order.id}" } } } ]
  else: [ { handoff: human } ]
```

### 5.4 Full flow example

```yaml
bookings:
  kind: flow
  description: Move an existing appointment
  steps:
    - verify: otp
    - collect: { appointment: "choose(calendar.upcoming)" }
    - collect: { slot: "choose(calendar.free_slots, doctor=appointment.doctor)" }
    - confirm: "Move your appointment to ${slot}?"
    - call_tool: { name: calendar.move, args: { id: "${appointment.id}", to: "${slot}" }, as: moved }
    - result: { data: { slot: "${slot}" }, must_say: "${slot}" }
```

## 6. Router

```yaml
router:
  mode: rules_then_classifier     # handoff (default) | classifier | rules_then_classifier
  model: fast                     # classifier model alias
  fallback: front                 # front agent answers itself
  rules:
    - if: "customer.has_upcoming_appointment"
      prefer: bookings
  examples:
    - { text: "I want my money back", worker: refunds }
```

## 7. Triggers

```yaml
triggers:
  - inbound: [phone, whatsapp, sms, web]

  - api: start_call                 # POST /v1/agents/{agent}/conversations

  - webhook: hubspot.new_lead
    action: call
    to: "${event.lead.phone}"
    within: 60s
    context: { name: "${event.lead.name}" }
    goal: qualify_and_book

  - schedule: "0 17 * * *"
    timezone: Asia/Colombo
    for_each: "calendar.appointments(day='tomorrow')"
    action: whatsapp_then_call
    template: appointment_reminder
    call_if_no_reply: 2h
    goal: confirm_or_reschedule
    pacing: { max_concurrent: 5, calling_hours: "09:00-19:00" }
```

## 8. Tools

```yaml
tools:
  insurer_check:
    type: webhook
    url: https://api.sunrise.lk/a2u/insurance
    method: POST
    input: { policy_no: string }
    output: { covered: bool, percent: number }
    timeout: 8s
    retries: 2
    side_effects: false
  calendar:
    type: connector
    connector: google_calendar
    connection: sunrise-gcal
  crm:
    type: mcp
    server: https://mcp.example.com
    allow: [lookup_patient]          # explicit allowlist of MCP tools
```

`side_effects: true` tools are only callable from flow steps after a `confirm`, or behind an approval policy.

## 9. Knowledge

```yaml
knowledge:
  clinic_faq: { sources: [docs/faq.pdf, "https://sunrise.lk/faq"], refresh: weekly }
  price_list: { sources: [docs/prices.xlsx] }
```

## 10. Policies

```yaml
policies:
  pii: mask                         # mask | off
  approval: { issue_refund: "amount > 100" }
  task_delivery: [live, whatsapp, whatsapp_template, sms]
  max_call_minutes: 20
  recording_disclosure: true
```

## 11. Evals

```yaml
evals:
  - name: reschedule happy path
    channel: phone
    customer: { phone: "+94771234567", traits: { has_upcoming_appointment: true } }
    script:
      - user: "I need to move Thursday's appointment"
      - expect: { worker: bookings, step: verify }
    simulate: { goal: "move to Wednesday afternoon", persona: polite }
    assert:
      - tool_called: calendar.move
      - said_contains: "${result.slot}"
```
