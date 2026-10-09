# WP-0.3 · LiveKit voice spike: interim findings

Status: closed · 2026-10-10. The spike passed: a multilingual web voice agent with tools works end to end on LiveKit Cloud, which confirms D-012. The labelled latency runs from Colombo and the barge-in counts were not done here. They move to WP-0.2 (region and LLM) and WP-2.8 (M2 exit measurement), and the Twilio SIP path to WP-3.1, which keeps D-024 open until then (D-030).

## Setup tested

Browser → LiveKit Cloud → Deepgram nova-3 or Gemini transcribe → Gemini 3.5 Flash / Flash Lite → Deepgram Aura-2 or Gemini TTS. Demo agent: "ABC Bank" card support with four typed tools (`verify_customer`, `get_card_status`, `order_replacement_card`, `block_card`), in English and Sinhala.

## Findings

1. **The plumbing is cheap.** A working multilingual voice agent with tools took a few hours, so it is not a differentiator. The value is in correctness (D-025).
2. **Numbers said in chunks break LLM extraction.** The NIC `2002177`, said in Sinhala as "දෙදහස් දෙකයි, එකසිය හැත්තෑ හතයි", was read back as `202177` and as "2002, 1, 7" in different calls (Gemini 3.5 Flash Lite). Prompt rules helped; the fix that holds is code. The tool rejects a wrong-length NIC without using up an attempt and says which digits it received (D-026).
3. **Models claim confirmations that did not happen.** The agent said "I have confirmed your NIC and mobile" and called `verify_customer` without waiting for a yes. A required `caller_confirmed_both` argument makes the claim explicit, but the model still sets it, so the real fix is a read-back rendered and confirmed by code (D-026).
4. **Prompt rules drift with story edits.** Rules that must always hold (number handling, language and script) were moved into a fixed block appended to every prompt. In the product these are harness behaviour, not prompt text.
5. **An open call costs money while silent.** LiveKit minutes and streaming speech-to-text are billed for silence; the LLM and TTS are not. A forgotten tab keeps billing. The product needs idle check-in and hang-up plus a call-length cap (spec §10 `idle`, PRD §6.6).
6. **Transcript logging is needed to tell speech-to-text errors from LLM errors.** The spike now logs `user_text` next to each agent reply in `runs/latency.jsonl`.

## Not measured here (moved, see D-030)

End-of-speech → first-audio p50/p95 from Sri Lanka (laptop worker and regional worker), echo vs. LLM, Aura-2 vs. Cartesia, barge-in over about 20 interruptions, and the Twilio SIP → LiveKit SIP path. Then confirm or revise D-012 and D-024 here.
