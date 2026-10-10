# A2U — Agent Config Spec (v0.3)

Status: draft · 2026-10-10. This is the surface customers build with. Code agents built with `a2u-sdk` compile to the same model.

The cap on expressiveness is deliberate (see [decisions.md](decisions.md), D-006). Requests for loops or general-purpose logic are answered with webhooks or code agents, not new constructs.

Changes in v0.3: entity types for `collect` with read-back (§5.5, D-026); `policies.speech` for claim classes, restricted topics and never-say rules (D-027); `policies.watch` (D-028); `policies.idle`; eval assertions on claims; validation rules (§12, D-031).

Changes from v0: no `sms` channel (D-013); languages per channel (D-019); `result.say` (D-017); `confirm.on_no`, `collect.retries`/`timeout`; filler per worker; consent in delivery; `schedule` triggers moved to "after v1" (D-022); current model IDs.

---

## 1. Top level

```yaml
agent: sunrise-dental            # slug, unique per workspace
description: Front desk for Sunrise Dental

languages:                       # §1.1
  default: en
  voice: [en, ta]
  text: [en, si, ta]

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

### 1.1 Languages
A language must be in `voice` to be spoken and in `text` to be written. Validation rejects a voice language that has no voice configured in `front.voice.voices` or that the platform has not enabled for speech (the M0 gate). The front agent detects the user's language among the enabled set for the current channel and stays in it.

## 2. Front agent

```yaml
front:
  persona: front_desk                 # name of a persona document (editable in the console), or inline `instructions:`
  model: anthropic:claude-haiku-5-5   # fast model for voice
  profiles:                           # optional per-channel overrides
    whatsapp: { model: anthropic:claude-sonnet-5-5, max_reply_chars: 600 }
    web_chat: { model: anthropic:claude-sonnet-5-5 }
  voice:
    mode: cascaded                    # cascaded | speech_to_speech
    stt: deepgram:nova-3              # default; per-language override below
    tts: cartesia:sonic
    voices:                           # one voice per voice language
      en: { voice_id: maya }
      ta: { tts: google, voice_id: ta-IN-Wavenet-A, stt: google }
  knowledge: [clinic_faq, price_list] # read-only, answered directly
  filler: auto                        # default filler when a worker declares none: auto | off | [phrases]
  handoff_number: "+94 11 234 5600"   # human fallback for calls
```

## 3. Channels

```yaml
channels:
  web:      { chat: true, voice: true, mode: verified }   # anonymous | verified | both
  phone:    { numbers: ["+94 11 234 5678"], recording: false }   # test number + PIN is always available
  whatsapp: { account: sunrise-dental }
```

## 4. Memory

```yaml
memory:
  identify_by: [phone, whatsapp, web_user]
  link_web_to_phone: verified_only
  summary_after_turns: 10
  facts: true
  fact_sensitivity_default: low       # low facts may be volunteered; high facts only after verify
  retention: 365d
```

## 5. Workers

### 5.1 Kinds

```yaml
workers:
  products:                      # kind: llm
    kind: llm
    description: Product questions, availability, specs   # used by routing
    instructions: products       # persona/instruction document name, or inline text
    model: anthropic:claude-sonnet-5-5
    tools: [search_catalog]
    knowledge: [catalog]
    filler: ["Let me look that up in the catalogue.", "One moment, checking stock."]

  insurance:                     # kind: tool
    kind: tool
    tool: insurer_check
    background: true
    timeout: 10m
    filler: ["I'm checking with your insurer now, this can take a minute."]

  bookings:                      # kind: flow
    kind: flow
    description: Book, move or cancel appointments
    filler: ["Just a second while I check the calendar."]
    steps: [...]                 # §5.2
```

`filler` is a list of phrases the front agent rotates through while the worker is running. It never repeats the same phrase twice in a row. `filler: off` disables filler for that worker.

### 5.2 Flow steps (complete list)

| Step | Shape | Behaviour |
|---|---|---|
| `collect` | `{ name: type \| entity \| choose(source), retries: 2, timeout: 2h, readback: digits }` | Emits `NeedsInput`. The front agent asks; the value is validated against the type. Entity types (§5.5) are parsed, validated and read back by code before binding, and a format failure does not use up a retry. `retries: n` allows n re-asks; the next invalid answer fails the flow with `invalid_input`. An unanswered ask fails it after `timeout` with `timeout`. Defaults come from `policies.collect` |
| `choose` | `{ name: choose(tool_or_list), max_options: 3 }` | Offers options; the user picks one and the whole item is bound. The source is a bound list or a tool returning a list or `{ items: [...] }`; an object's `id` is its value and `label` or `name` its label. Renders as a WhatsApp list, web buttons, or spoken options |
| `confirm` | `{ text: "with ${vars}", on_no: step_name }` or a bare string | Explicit yes/no. "No" jumps back to the named `collect` step and forgets everything bound from there on, or ends the flow with `Failed(reason="declined")` if `on_no` is absent. Renders as WhatsApp buttons |
| `verify` | `otp \| knowledge: [dob, postcode]` | Identity check before continuing. `otp` goes over WhatsApp in v1 |
| `call_tool` | `{ name, args, as }` | Calls a tool; the result is bound to `as` |
| `delegate` | `{ worker, args, as, background }` | Runs another worker |
| `handoff` | `human \| worker_name` | Ends this flow and passes the conversation on; the flow's `Result` carries the last tool data |
| `if` | `{ if: condition, then: [steps], else: [steps] }` | Branch on bound values (§5.3). Nesting depth ≤ 2 |
| `result` | `{ data: {...}, say: "..." }` | Final `Result`. `say` is rendered by code from bound values and sent or spoken verbatim (D-017). `data` defaults to the last tool result |

No loops, no variables apart from `collect`/`as` bindings, no arbitrary code. A `collect`'s retries are the only repetition in the language.

### 5.3 Conditions

CEL-style expressions over bound values and customer traits: comparison, `&&`, `||`, `!`, `in`, `has()`, string `startsWith`. No function definitions, no I/O.

Values of different types are never equal; `<` and friends need two numbers or two strings; `&&` and `||` need bools and short-circuit; a name that is not bound is an error except inside `has()`. A condition that cannot be evaluated fails the flow with `condition_error` (D-033). Approval conditions in `policies.approval` read the tool call's arguments (`amount > 100`); when one cannot be evaluated, approval is required.

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
  filler: ["Let me check the calendar."]
  steps:
    - verify: otp
    - collect: { appointment: "choose(calendar.upcoming)" }
    - collect: { slot: "choose(calendar.free_slots, doctor=appointment.doctor)", retries: 3 }
    - confirm: { text: "Move your appointment to ${slot}?", on_no: slot }
    - call_tool: { name: calendar.move, args: { id: "${appointment.id}", to: "${slot}" }, as: moved }
    - result:
        data: { slot: "${slot}", appointment_id: "${appointment.id}" }
        say: "Done. Your appointment is now on ${slot | date_spoken}."
```

Templates in `say` support filters for rendering values: `date_spoken`, `time_spoken`, `money`, `digits_spoken` (reads an ID digit by digit on voice). Rendering is per channel: `date_spoken` produces "Thursday the 16th at 3:30 pm" on voice and "Thu 16 Oct, 3:30 pm" on text. A value that cannot be rendered fails the flow rather than being guessed. In `args` and `data`, a string that is exactly `${ref}` keeps the value's type.

### 5.5 Entity types

Entity types are used in `collect` and in `verify: knowledge`. Each type has spoken-form parsers per language, a validator and a default read-back. The LLM proposes a value; code decides whether it is valid and what is read back (D-026).

| Type | Validates | Default read-back |
|---|---|---|
| `nic_lk` | 12 digits (new) or 9 digits + `V`/`X` (old); birth year and day plausible | `digits` |
| `phone` | E.164 after normalisation for `region` (default from the workspace), mobile or landline | `digits` |
| `person_name` | Non-empty; script recorded (Latin, Sinhala, Tamil) | `spell` on voice when the caller asks or confidence is low |
| `address` | Required parts per country; optional geocoder check | `summary` |
| `money` | Amount and currency; range limits from `max` | `summary` |
| `date`, `time` | Calendar-valid, within `min`/`max` | `summary` |
| `email` | Syntax; optional MX check | `spell` |
| `account_no`, `id` | Length and pattern set by the builder (`pattern: "\\d{10}"`); optional check-digit scheme | `digits` |

Read-back modes: `digits` (each digit spoken on its own, zeros included), `spell` (letter by letter), `summary` (a natural rendering, such as "Thursday the 16th at 3:30 pm"), `none`. Parsers understand numbers spoken in chunks ("two thousand two, one seventy seven" → `2002177`) and Sinhala and Tamil number words. When the LLM's candidate and the parser disagree, the agent asks again.

```yaml
- collect: { nic: nic_lk }
- collect: { mobile: { type: phone, region: LK } }
- collect: { amount: { type: money, currency: LKR, max: 500000 } }
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
  - inbound: [phone, whatsapp, web]

  - api: start_conversation          # POST /v1/agents/{agent}/conversations  { to, channel, context, goal }

  - webhook: hubspot.new_lead
    action: call
    to: "${event.lead.phone}"
    within: 60s
    context: { name: "${event.lead.name}" }
    goal: qualify_and_book
```

**After v1** (D-022): `schedule` triggers with `for_each`, `pacing`, `calling_hours` and `whatsapp_then_call` sequences.

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

`side_effects: true` tools are only callable from flow steps after a `confirm`, or behind an approval policy whose condition is false for the call's arguments. When the condition is true the call needs an approval, which pauses the task (WP-5.1).

## 9. Knowledge

```yaml
knowledge:
  clinic_faq: { sources: [docs/faq.pdf, "https://sunrise.lk/faq"], refresh: weekly }
  price_list: { sources: [docs/prices.xlsx] }
```

Sources are documents in the console; operators can replace or edit them without touching this file.

## 10. Policies

```yaml
policies:
  pii: mask                         # mask | off
  approval: { issue_refund: "amount > 100" }
  collect: { retries: 2, timeout: 24h }          # defaults for collect steps
  task_delivery:
    order: [live, whatsapp, whatsapp_template]  # no sms in v1
    consent: required                           # required (default) | assert_by_client
    ask_consent: "Can I send you the result on WhatsApp when it's ready?"
  max_call_minutes: 20
  idle: { prompt_after: 30s, hangup_after: 45s }  # "are you still there?", then a polite hang-up
  recording_disclosure: true

  speech:                                         # D-027
    claim_check: on                               # on (default) | off (only for internal test agents)
    restricted_topics:                            # answer only from these sources, otherwise hand off
      refunds: { sources: [refund_policy], otherwise: handoff }
      medical_advice: { sources: [], otherwise: "I can't advise on that, but I can connect you to a nurse." }
    never_say:
      - "promise or guarantee a refund"
      - "quote a price not in price_list"
    fallback: "Let me confirm that for you."      # spoken in place of an unsupported sentence on voice

  watch:                                          # D-028, asynchronous, never blocks a turn
    - name: unsupported_commitment
      kind: llm_judge                             # llm_judge | classifier | rules
      model: fast
      prompt: "Did the agent promise anything the evidence does not support?"
      action: flag                                # steer | escalate | flag | remediate
    - name: frustration
      kind: classifier
      labels: [frustrated, neutral]
      when: "frustrated for 2 turns"
      action: steer
      note: "Acknowledge the frustration and offer a human."
```

Every front-agent sentence is checked before it is sent: `conversation` sentences are free, `information` must be grounded in this turn's evidence, and `commitment` only comes from `result.say` or `must_say` (architecture §4.5). Watchers act from the next turn onward and never change a turn that has already been sent.

When a background task is running and the conversation might end before it finishes, the front agent asks `ask_consent` once (if no consent record exists) and stores the answer as a consent record for `whatsapp / task_results`.

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
      - said: "${result.slot | date_spoken}"      # exact for flow results
      - latency_p95_ms: 1500                       # voice evals only
      - no_unsupported_claims: true                # every information/commitment sentence grounded
      - captured: { nic: "200217701234" }          # entity bound exactly, after read-back

  - name: declines the move, picks another slot
    channel: whatsapp
    simulate: { goal: "say no to the first slot, then accept the second", persona: terse }
    assert:
      - step_reached: { worker: bookings, step: slot, times: 2 }
      - tool_called: calendar.move
```

Evals can also be created from a real conversation in the console ("turn into eval"); the generated YAML lands here with PII masked. Conversations flagged by a watcher are offered as eval candidates (Prove, D-025).

Other claim assertions: `not_said: "refund"`, `claim_class: { contains: commitment, from: result.say }`, `watch_flagged: { watcher: unsupported_commitment, expect: false }`.

## 12. Validation

`a2u_core.config` loads YAML or JSON and reports every error with a path and a line, for example `agent.yaml:14:9: workers.bookings.steps[3].confirm.on_no: on_no must name an earlier collect step, not 'slot'`. The console, the control API and the CLI all use it. Parsing rules and the gaps filled here are recorded in D-031.

**Parsing.** YAML 1.2 scalars: `on`, `off`, `yes` and `no` are strings, and only `true`/`false` are booleans. Duplicate keys, explicit tags and merge keys (`<<`) are errors. Unknown keys are errors everywhere. Durations are a whole number with `ms`, `s`, `m`, `h` or `d`.

**Shape.** Types, enums and required fields as in §1–§11, plus: `front` has exactly one of `persona` and `instructions`; `collect` binds exactly one name, and `readback` applies only to entity types; `account_no` and `id` need a `pattern` that compiles; `if` nests at most two deep; router `rules` need `mode: rules_then_classifier`; an `llm_judge` watcher needs a `prompt`, a `classifier` needs `labels`, and `action: steer` needs a `note`; an eval needs a `script` or `simulate`; `sms` and `schedule` triggers are rejected with the decision that removed them.

**Names and scope.** In `instructions`, a value that looks like a name (`products`) is a persona document; anything else is inline text. A flow can read its own bindings (`collect`, `choose`, `as`), `customer`, and `args` (the arguments it was delegated with). A webhook trigger reads `event`; evals read `result` and `customer`. A name is bound once per flow; a name bound inside one branch of an `if` is visible after it only if both branches bind it or the other branch ends the flow. Template filters are `date_spoken`, `time_spoken`, `money` and `digits_spoken`. Steps after a `result` or `handoff` are unreachable and rejected.

**References.** Every worker, tool, `tool.operation`, knowledge base, watcher and eval step name must exist. A webhook tool has no operations; an MCP operation must be in `allow`. `on_no` names an earlier `collect`. `delegate` must not form a cycle.

**Side effects.** Webhook tools have `side_effects: true` unless they say otherwise. A side-effecting `call_tool` needs an earlier `confirm` on every path to it, or an entry in `policies.approval`. An LLM or tool worker that uses a side-effecting tool needs an approval entry. Connector and MCP operations are checked once WP-1.10 adds their effects to the connector catalog.

**Channels and languages.** Voice (web voice or phone) needs `languages.voice` and `front.voice`; text (web chat or WhatsApp) needs `languages.text`. In `cascaded` mode every voice language needs a voice in `front.voice.voices`, and every voice there must be a voice language. `languages.default` must be enabled for voice or text. The platform's enabled languages (the M0 gate, D-019) are checked when the caller passes them. `front.profiles`, eval channels, inbound triggers, webhook trigger actions and `verify: otp` (which needs WhatsApp) must refer to configured channels. `speech_to_speech` cannot be used with `restricted_topics` or `never_say`. Latency assertions are for voice evals only.

**Documents.** When the workspace's documents are passed in, `persona`, document-named `instructions` and knowledge sources that are not URLs must name a document of the right kind (`persona` or `knowledge`). The loader returns the documents it resolved with their versions.

**Conditions.** Every condition (`if:`, router rules, approval policies) is parsed when the config loads, and a flow `if` may read only names bound before it, `customer` and `args` (D-033). Approval conditions read the tool's call arguments, so only their syntax is checked.
