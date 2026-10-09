# WP-0.3 · LiveKit voice spike: interim findings

Status: in progress · 2026-10-10. The latency measurements and barge-in counts that close WP-0.3 are still to do (see `spikes/livekit-voice/README.md`). This file records what the spike has taught so far, because it changed the plan (D-025 to D-029).

## Setup tested

Browser → LiveKit Cloud → Deepgram nova-3 or Gemini transcribe → Gemini 3.5 Flash / Flash Lite → Deepgram Aura-2 or Gemini TTS. Demo agent: "ABC Bank" card support with four typed tools (`verify_customer`, `get_card_status`, `order_replacement_card`, `block_card`), in English and Sinhala.

## Findings

1. **The plumbing is cheap.** A working multilingual voice agent with tools took a few hours, so it is not a differentiator. The value is in correctness (D-025).
2. **Numbers said in chunks break LLM extraction.** The NIC `2002177`, said in Sinhala as "දෙදහස් දෙකයි, එකසිය හැත්තෑ හතයි", was read back as `202177` and as "2002, 1, 7" in different calls (Gemini 3.5 Flash Lite). Prompt rules helped; the fix that holds is code. The tool rejects a wrong-length NIC without using up an attempt and says which digits it received (D-026).
3. **Models claim confirmations that did not happen.** The agent said "I have confirmed your NIC and mobile" and called `verify_customer` without waiting for a yes. A required `caller_confirmed_both` argument makes the claim explicit, but the model still sets it, so the real fix is a read-back rendered and confirmed by code (D-026).
4. **Prompt rules drift with story edits.** Rules that must always hold (number handling, language and script) were moved into a fixed block appended to every prompt. In the product these are harness behaviour, not prompt text.
5. **An open call costs money while silent.** LiveKit minutes and streaming speech-to-text are billed for silence; the LLM and TTS are not. A forgotten tab keeps billing. The product needs idle check-in and hang-up plus a call-length cap (spec §10 `idle`, PRD §6.6).
6. **Transcript logging is needed to tell speech-to-text errors from LLM errors.** The spike now logs `user_text` next to each agent reply in `runs/latency.jsonl`.

## Still to measure

End-of-speech → first-audio p50/p95 from Sri Lanka (laptop worker and regional worker), echo vs. LLM, Aura-2 vs. Cartesia, barge-in over about 20 interruptions, and the Twilio SIP → LiveKit SIP path. Then confirm or revise D-012 and D-024 here.
