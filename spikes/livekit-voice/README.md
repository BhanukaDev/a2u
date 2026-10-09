# WP-0.3 · LiveKit voice spike

Throwaway. This project measures a latency floor before any A2U code exists ([build-plan WP-0.3](../../docs/build-plan.md)). Nothing here is imported by `packages/` or `services/`. The real voice worker is WP-2.1 in `services/voice`.

Pipeline: browser → LiveKit Cloud room → Deepgram nova-3 → Gemini 3.5 Flash, Claude Haiku 4.5 or echo (`LLM_PROVIDER`) → Deepgram Aura-2 or Cartesia sonic-3 (`TTS_PROVIDER`), with LiveKit's turn detector and preemptive generation turned on.

## Setup

```sh
cd spikes/livekit-voice
cp .env.example .env    # fill in LiveKit, Deepgram, Google (Gemini) keys; Anthropic and Cartesia optional
uv sync
uv run agent.py download-files   # turn detector / VAD weights, once
```

## Run

Run these in two terminals:

```sh
uv run agent.py start        # agent worker, registers as "a2u-spike"
uv run token_server.py       # http://localhost:8080
```

The page has a settings panel: speech-to-text, LLM and text-to-speech providers, the voice, the Gemini voice style, the greeting and the agent's story (system prompt). Settings apply from the next call, with no restart, and the browser remembers them. `.env` sets the defaults.

Open the page, type a label (for example `colombo-wifi` or `colombo-4g`) and start a call. Each turn appears in the table, and each turn is appended to `runs/latency.jsonl`.

To test without a browser, run `uv run agent.py console`.

To measure the speech-only floor, run `LLM_PROVIDER=echo uv run agent.py start`. The agent then repeats what you said back to you.

### Sinhala (all Gemini, one key)

```sh
STT_PROVIDER=gemini TTS_PROVIDER=gemini \
GREETING="ආයුබෝවන්, ABC Bank එකට කතා කළාට ස්තුතියි. මම නදීෂා. මට ඔබට උදව් කරන්න පුළුවන් කොහොමද?" \
uv run agent.py start
```

Gemini STT expects `si-LK` and `en-US` (set `STT_LANGUAGES`, or leave it empty for auto-detect). Gemini TTS isn't streaming, so `tts_ttfb` covers a whole sentence. This is a first look only. The real Sinhala/Tamil verdict is the WP-0.1 bake-off.

```sh
uv run report.py             # p50/p95 per stage, per client label and model
```

## Demo scenario: ABC Bank card support

`demo_bank.py` holds a fake customer (NIC **200217701234**, Kasun Perera) whose working debit card was declined at Keells and at an ATM because the chip could not be read (no fault on the card), and four tools: `verify_customer`, `get_card_status`, `order_replacement_card` and `block_card`. The LLM decides when to call them, and the code decides what is true and what is allowed. Card tools refuse until the caller is verified, verification locks after 3 failures, and actions require `caller_confirmed`. Tool calls show in grey in the page's transcript. Set `DEMO_MOBILE` to require a specific mobile number.

## What the numbers mean

| Field | Meaning |
|---|---|
| `e2e` | End of user speech → agent's first audio frame, measured **at the worker**. This is the number that is gated on. |
| `transcription_delay` | End of speech → final transcript |
| `end_of_turn_delay` | End of speech → turn committed (endpointing + turn detector) |
| `llm_ttft` | LLM time to first token |
| `tts_ttfb` | First text to TTS → first audio chunk |

`e2e` doesn't include the network leg between the browser and LiveKit in either direction. A user in Sri Lanka also hears that leg. Add it by measuring RTT to the LiveKit edge; the WebRTC stats in the browser show it. Better still, record a few calls and measure the gap between end of speech and start of reply in an audio editor. The M2 gate (p50 ≤ 900 ms, p95 ≤ 1.5 s) is end to end as the user hears it.

Where the worker runs matters. On a laptop in Colombo, every vendor call crosses the Colombo ↔ US/Singapore link. For the real number, run the worker on an EC2 instance in the candidate region (WP-0.2) and keep the browser in Sri Lanka.

## Still to do in WP-0.3

- [ ] Web voice runs from Sri Lanka: worker on laptop vs. worker in ap-south-1 / ap-southeast-1
- [ ] Echo vs. Gemini 3.5 Flash vs. Haiku 4.5, to separate LLM time from the speech floor (feeds WP-0.2)
- [ ] Deepgram Aura-2 vs. Cartesia sonic-3 `tts_ttfb` (English only; Sinhala/Tamil voice is WP-0.1)
- [ ] Barge-in: interrupt mid-sentence about 20 times and note misses and false triggers
- [ ] Twilio SIP trunk → LiveKit SIP → same agent (dispatch rule to `a2u-spike`), measured the same way
- [ ] Write `docs/spikes/livekit.md`: both latencies, barge-in behaviour, and whether D-012 and D-024 hold
