"""WP-0.3 throwaway voice agent on LiveKit: STT (Deepgram | Gemini) -> LLM (Gemini | Claude | echo) -> TTS.

Defaults come from .env. The test page can override them per call (voice, story, greeting, ...):
they arrive as dispatch metadata, so no restart is needed between calls.

Logs per-turn latency to stdout and to runs/latency.jsonl, and sends each turn's
numbers to the browser on the `a2u.metrics` text topic.

    uv run agent.py start      # connect to LiveKit Cloud (restart after code changes)
    uv run agent.py console    # talk in the terminal, no browser needed
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from collections.abc import AsyncIterable
from dataclasses import asdict, dataclass, fields, replace
from pathlib import Path
from typing import Any, get_args

from dotenv import load_dotenv
from livekit.agents import (
    Agent,
    AgentServer,
    AgentSession,
    ConversationItemAddedEvent,
    ErrorEvent,
    FunctionToolsExecutedEvent,
    JobContext,
    ModelSettings,
    cli,
    inference,
    llm,
)
from livekit.plugins import anthropic, cartesia, deepgram, google
from livekit.plugins.deepgram.models import TTSModels as DeepgramVoices
from livekit.plugins.google import beta as google_beta
from livekit.plugins.google.beta.gemini_tts import GEMINI_VOICES

from demo_bank import TOOLS, CallState

load_dotenv()

AGENT_NAME = "a2u-spike"
RUNS_DIR = Path(__file__).parent / "runs"
log = logging.getLogger(AGENT_NAME)

STT_MODELS = {"deepgram": "nova-3", "gemini": "gemini-3.5-transcribe-live"}
# Gemini 3.5 Flash-Lite: ~0.95 s first token from Colombo vs ~1.3 s for 3.5 Flash, and it handled the
# bank scenario's tools correctly (measured 2026-10-09). 3.7/3.8 Flash were far too slow for voice.
LLM_MODELS = {"gemini": "gemini-3.5-flash-lite", "anthropic": "claude-haiku-4-5", "echo": ""}
# Models the test page offers per LLM provider (first = default).
LLM_MODEL_CHOICES = {
    "gemini": ["gemini-3.5-flash-lite", "gemini-3.1-flash-lite", "gemini-3.5-flash"],
    "anthropic": ["claude-haiku-4-5"],
    "echo": [""],
}
# Gemini TTS: 3.8 Flash-Lite TTS made 4.6 s of Sinhala in ~2.5 s vs ~3.5 s for 3.1 Flash TTS preview, and
# preview models have a 100 requests/day cap on paid tier 1 (one request per spoken sentence).
TTS_MODELS = {"deepgram": "aura-2", "cartesia": "sonic-3", "gemini": "gemini-3.8-flash-lite-tts"}
# Models the test page offers per TTS provider (first = default). Deepgram's model is the voice.
TTS_MODEL_CHOICES = {
    "gemini": ["gemini-3.8-flash-lite-tts", "gemini-3.8-flash-tts", "gemini-3.1-flash-tts-preview"],
    "deepgram": ["aura-2"],
    "cartesia": ["sonic-3"],
}
# Voices the test page offers per TTS provider. Deepgram's voice is part of its model name;
# Cartesia takes a voice ID, so the page shows a free-text box for it instead.
VOICES = {
    "deepgram": [v for v in get_args(DeepgramVoices) if v.startswith("aura-2-")],
    "gemini": list(get_args(GEMINI_VOICES)),
    "cartesia": [],
}
DEFAULT_VOICE = {"deepgram": "aura-2-andromeda-en", "gemini": "Kore", "cartesia": ""}
API_KEYS = {
    "deepgram": "DEEPGRAM_API_KEY",
    "gemini": "GOOGLE_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "cartesia": "CARTESIA_API_KEY",
}

DEFAULT_STORY = """You are Nadeesha, a customer support agent at ABC Bank in Sri Lanka, on a phone call.
Talk like a real, experienced call-centre agent: warm, patient, natural, and sure of yourself.
You have the bank's systems through your tools. Use them; never say you cannot access accounts.
Never invent account or card details. Only state what a tool returned.

How to handle a card problem (the common call today):
1. Let the caller explain. Acknowledge it in one short sentence ("I'm sorry about that, let me check").
2. Ask for their NIC number. Read it back one digit at a time and wait for a yes. If they say
   no, ask them to say it slowly, one digit at a time, and read it back again.
3. Ask for their registered mobile number. Read it back the same way and wait for a yes.
   Never call verify_customer until both numbers have been read back and confirmed.
4. Say something like "one moment while I check", then call verify_customer.
   If it fails, say what the tool says kindly and ask again. Once verified, greet them by name.
5. Call get_card_status, then explain what you found in plain words: is the card active, is
   there money in the account, and why the payments failed (use the decline reasons).
6. If the chip could not be read: tell them the card has no fault and is working. Suggest they
   gently wipe the gold chip with a soft dry cloth and try the payment again.
7. If it still does not work after wiping, ask them to visit their nearest ABC Bank branch with
   their NIC so the staff can check the card in person. Only order a replacement card if the
   caller asks for one: call order_replacement_card after a clear yes, then give the reference slowly.
8. Ask if there is anything else, then close warmly using their name.
If the card is lost, stolen or misused, offer to block it with block_card, after a clear yes.
If the caller asks about something else, answer briefly and come back to where you were.

Security rules, never broken:
- Never ask for or accept a PIN, password, OTP, CVV or full card number. If the caller starts
  to give one, stop them and say ABC Bank will never ask for it.
- Never share account details before verify_customer has succeeded.

Bank facts (fictional):
- Branches open 9 am to 3 pm on weekdays; selected branches 9 am to 1 pm on Saturdays.
- ABC Bank ATMs are free; other banks' ATMs charge 50 rupees.
- Hotline 011 2 000 000, open 24 hours."""

# Always appended to the story, so editing the story on the test page can't break speech output.
VOICE_RULES = """
You are speaking on a voice call, so reply in one or two short sentences of plain speech.
No lists, no markdown, no emoji.
Reply in the language the caller uses. For Sinhala or Tamil, write in the native script
(සිංහල / தமிழ்), never in romanised Latin letters, because your text is read aloud by a
speech engine. Mixing in common English words, as Sri Lankans do, is fine.

Callers often say long numbers (NIC, phone, account) in chunks, as Sri Lankans do, in English or
Sinhala. Turn each chunk into its full digits, keeping every zero, then join the chunks in order.
A Sri Lankan NIC is 12 digits (new), or 9 digits then V or X (old). For example
"දෙදහස් දෙකයි, එකසිය හැත්තෑ හතයි, බිංදුව, එකසිය විසි තුනයි, හතරයි" (two thousand two, one
hundred seventy seven, zero, one hundred twenty three, four) is 2002, 177, 0, 123, 4, so the NIC
is 200217701234: twelve digits. "Two thousand two" is always four digits, 2002, never 202. If you
have fewer than 12 digits (or 9 plus a letter), part is missing: ask for it, do not guess.
When you read a number back, say every digit on its own, including each zero:
"two, zero, zero, two, one, seven, seven, zero, one, two, three, four" or
"දෙක, බිංදුව, බිංදුව, දෙක, එක, හත, හත, බිංදුව, එක, දෙක, තුන, හතර".
Never read a number back in chunks, and pass the tool exactly the digits you read back."""


@dataclass(frozen=True)
class Settings:
    stt_provider: str = "deepgram"  # "deepgram" (English only here) or "gemini" (Sinhala, Tamil)
    stt_language: str = "en-US"  # Deepgram only
    stt_languages: str = "si-LK,en-US"  # Gemini only, comma separated; empty = auto-detect
    llm_provider: str = "gemini"  # "gemini", "anthropic", or "echo" (no LLM: speech-only latency floor)
    llm_model: str = ""  # empty = provider default
    tts_provider: str = "deepgram"  # "deepgram" (Aura-2), "cartesia", or "gemini"
    tts_model: str = ""  # empty = provider default (Gemini only; Deepgram's voice picks its model)
    tts_voice: str = ""  # empty = provider default
    tts_instructions: str = ""  # Gemini only: speaking style, e.g. "Speak calmly, at a measured pace."
    greeting: str = "Hi, thank you for calling ABC Bank. This is Nadeesha. How can I help you today?"
    story: str = DEFAULT_STORY
    # Silence before the turn ends. The turn detector picks min_delay when it thinks you're done
    # and max_delay when it thinks you're mid-sentence. LiveKit's default max is 3.0 s, which showed
    # up as ~2.5 s waits on ordinary Sri Lankan English turns.
    endpoint_min_delay: float = 0.5
    endpoint_max_delay: float = 1.2

    @classmethod
    def from_env(cls) -> Settings:
        """Each field can be set in .env by its upper-case name, e.g. TTS_VOICE=Charon."""
        return cls()._merge({f.name: os.environ[f.name.upper()] for f in fields(cls) if f.name.upper() in os.environ})

    def _merge(self, overrides: dict[str, Any]) -> Settings:
        """Apply known, non-empty fields from `overrides`; unknown keys and blanks are ignored."""
        types = {f.name: type(getattr(self, f.name)) for f in fields(self)}
        clean = {k: types[k](v) for k, v in overrides.items() if k in types and v not in (None, "")}
        return replace(self, **clean)

    def for_call(self, overrides: dict[str, Any]) -> Settings:
        s = self._merge(overrides)
        # A voice belongs to one provider; drop it if the provider changed under it.
        if s.tts_voice and VOICES[s.tts_provider] and s.tts_voice not in VOICES[s.tts_provider]:
            s = replace(s, tts_voice="")
        if s.tts_model and s.tts_model not in TTS_MODEL_CHOICES[s.tts_provider]:
            s = replace(s, tts_model="")
        return s

    @property
    def stt_model(self) -> str:
        return STT_MODELS[self.stt_provider]

    @property
    def resolved_llm_model(self) -> str:
        return self.llm_model or LLM_MODELS[self.llm_provider]

    @property
    def resolved_tts_model(self) -> str:
        return self.tts_model or TTS_MODELS[self.tts_provider]

    @property
    def resolved_voice(self) -> str:
        return self.tts_voice or DEFAULT_VOICE[self.tts_provider]

    def missing_keys(self) -> list[str]:
        providers = {self.stt_provider, self.llm_provider, self.tts_provider}
        return [API_KEYS[p] for p in sorted(providers) if p in API_KEYS and not os.getenv(API_KEYS[p])]


ENV_SETTINGS = Settings.from_env()


class SpikeAgent(Agent):
    def __init__(self, settings: Settings) -> None:
        self._echo = settings.llm_provider == "echo"
        super().__init__(
            instructions=settings.story.strip() + "\n" + VOICE_RULES,
            tools=[] if self._echo else TOOLS,
        )

    async def llm_node(
        self,
        chat_ctx: llm.ChatContext,
        tools: list[llm.Tool],
        model_settings: ModelSettings,
    ) -> AsyncIterable[llm.ChatChunk | str]:
        if not self._echo:
            async for chunk in Agent.default.llm_node(self, chat_ctx, tools, model_settings):
                yield chunk
            return

        last_user = next((m for m in reversed(chat_ctx.items) if getattr(m, "role", None) == "user"), None)
        text = last_user.text_content if last_user else None
        yield f"You said: {text}" if text else "I didn't catch that."


server = AgentServer()


@server.rtc_session(agent_name=AGENT_NAME)
async def entrypoint(ctx: JobContext) -> None:
    # Dispatch metadata from the token server: {"client": "colombo-wifi", "settings": {...}}
    dispatch = json.loads(ctx.job.metadata or "{}")
    settings = ENV_SETTINGS.for_call(dispatch.get("settings") or {})

    if missing := settings.missing_keys():
        await ctx.connect()
        await ctx.room.local_participant.send_text(f"missing in .env: {', '.join(missing)}", topic="a2u.error")
        return

    RUNS_DIR.mkdir(exist_ok=True)
    out = (RUNS_DIR / "latency.jsonl").open("a")
    run_meta = {
        "room": ctx.room.name,
        "llm_provider": settings.llm_provider,
        "llm_model": settings.resolved_llm_model or None,
        "stt_provider": settings.stt_provider,
        "stt_model": settings.stt_model,
        "endpoint_delay": [settings.endpoint_min_delay, settings.endpoint_max_delay],
        "tts_provider": settings.tts_provider,
        "tts_model": settings.resolved_tts_model,
        "tts_voice": settings.resolved_voice,
        "story_edited": settings.story != DEFAULT_STORY,
        "dispatch": {"client": dispatch.get("client", "unknown")},
    }
    log.info("call settings %s", {k: v for k, v in asdict(settings).items() if k != "story"})

    session = AgentSession[CallState](
        userdata=CallState(),
        stt=_make_stt(settings),
        llm=_make_llm(settings),
        tts=_make_tts(settings),
        turn_handling={
            "turn_detection": inference.TurnDetector(),
            "endpointing": {"min_delay": settings.endpoint_min_delay, "max_delay": settings.endpoint_max_delay},
            "preemptive_generation": {"enabled": True},
        },
    )

    last_user_metrics: dict[str, float] = {}
    last_user_text: str | None = None

    @session.on("error")
    def on_error(ev: ErrorEvent) -> None:
        # Surface vendor failures (quota, auth, timeouts) in the browser instead of silence.
        source = type(ev.source).__module__.split(".")[-2] + "." + type(ev.source).__name__
        err = getattr(ev.error, "error", ev.error)
        if isinstance(err, asyncio.CancelledError) or "CancelledError" in repr(err):
            return  # a reply cancelled because the caller kept talking: normal, not an error
        recoverable = getattr(ev.error, "recoverable", False)
        msg = f"{'retrying after ' if recoverable else ''}{source}: {str(err)[:300]}"
        log.error("session error %s", msg)
        topic = "a2u.warning" if recoverable else "a2u.error"
        asyncio.create_task(ctx.room.local_participant.send_text(msg, topic=topic))  # noqa: RUF006

    @session.on("function_tools_executed")
    def on_tools(ev: FunctionToolsExecutedEvent) -> None:
        # Show each tool call and its result in the browser's transcript.
        for call, result in ev.zipped():
            msg = f"{call.name}({call.arguments}) -> {'ERROR ' if result.is_error else ''}{result.output}"
            log.info("tool %s", msg)
            asyncio.create_task(ctx.room.local_participant.send_text(msg[:600], topic="a2u.tool"))  # noqa: RUF006

    @session.on("conversation_item_added")
    def on_item(ev: ConversationItemAddedEvent) -> None:
        nonlocal last_user_metrics, last_user_text
        item = ev.item
        if not isinstance(item, llm.ChatMessage):
            return
        if item.role == "user":
            last_user_metrics = dict(item.metrics)
            last_user_text = item.text_content
            return
        if item.role != "assistant":
            return

        m = item.metrics
        turn = {
            **run_meta,
            "ts": time.time(),
            "interrupted": item.interrupted,
            # end of user speech -> agent starts speaking (the number we gate on)
            "e2e": m.get("e2e_latency"),
            "transcription_delay": last_user_metrics.get("transcription_delay"),
            "end_of_turn_delay": last_user_metrics.get("end_of_turn_delay"),
            "llm_ttft": m.get("llm_node_ttft"),
            "tts_ttfb": m.get("tts_node_ttfb"),
            # What STT heard for the turn this reply answers, to tell STT errors from LLM errors.
            "user_text": last_user_text,
            "text": item.text_content,
        }
        log.info(
            "turn e2e=%s stt=%s eot=%s llm_ttft=%s tts_ttfb=%s",
            *(_ms(turn[k]) for k in ("e2e", "transcription_delay", "end_of_turn_delay", "llm_ttft", "tts_ttfb")),
        )
        out.write(json.dumps(turn) + "\n")
        out.flush()
        asyncio.create_task(  # noqa: RUF006 - fire and forget is fine for a spike
            ctx.room.local_participant.send_text(json.dumps(turn), topic="a2u.metrics")
        )

    async def close_log() -> None:
        out.close()

    ctx.add_shutdown_callback(close_log)

    await session.start(agent=SpikeAgent(settings), room=ctx.room)
    await session.say(settings.greeting)


def _make_stt(s: Settings) -> deepgram.STT | google_beta.GeminiSTT:
    if s.stt_provider == "gemini":
        langs = [c.strip() for c in s.stt_languages.split(",") if c.strip()]
        return google_beta.GeminiSTT(model=s.stt_model, language=None, language_codes=langs)
    return deepgram.STT(model=s.stt_model, language=s.stt_language)


def _make_llm(s: Settings) -> llm.LLM:
    model = s.resolved_llm_model
    if s.llm_provider == "anthropic":
        return anthropic.LLM(model=model, max_tokens=200)
    # Echo still needs an LLM object for the session; it is never called.
    # Thinking off: it costs first-token latency and a support reply doesn't need it.
    # Gemini 3+ takes a level, 2.5 takes a token budget.
    model = model or LLM_MODELS["gemini"]
    thinking = {"thinking_level": "minimal"} if "gemini-2" not in model else {"thinking_budget": 0}
    return google.LLM(model=model, max_output_tokens=200, thinking_config=thinking)


def _make_tts(s: Settings) -> deepgram.TTS | cartesia.TTS | google_beta.GeminiTTS:
    voice = s.resolved_voice
    if s.tts_provider == "gemini":
        # Not streaming: each sentence is synthesised whole before it plays, so expect a higher tts_ttfb.
        return google_beta.GeminiTTS(
            model=s.resolved_tts_model,
            voice_name=voice,
            **({"instructions": s.tts_instructions} if s.tts_instructions else {}),
        )
    if s.tts_provider == "cartesia":
        return cartesia.TTS(model=TTS_MODELS["cartesia"], **({"voice": voice} if voice else {}))
    return deepgram.TTS(model=voice)


def _ms(v: float | None) -> str:
    return "-" if v is None else f"{v * 1000:.0f}ms"


def _check_env() -> None:
    required = ["LIVEKIT_URL", "LIVEKIT_API_KEY", "LIVEKIT_API_SECRET"]
    if missing := [k for k in required if not os.getenv(k)] + ENV_SETTINGS.missing_keys():
        raise SystemExit(f"missing in .env: {', '.join(missing)}")


if __name__ == "__main__":
    _check_env()
    cli.run_app(server)
