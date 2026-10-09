"""Serves web/index.html, the agent's settings options, and LiveKit room tokens that dispatch the spike agent.

uv run token_server.py     # http://localhost:8080
"""

from __future__ import annotations

import json
import os
import secrets
from dataclasses import asdict
from pathlib import Path

from aiohttp import web
from dotenv import load_dotenv
from livekit import api
from livekit.protocol.agent_dispatch import RoomAgentDispatch
from livekit.protocol.room import RoomConfiguration

from agent import (
    AGENT_NAME,
    API_KEYS,
    DEFAULT_STORY,
    DEFAULT_VOICE,
    ENV_SETTINGS,
    LLM_MODEL_CHOICES,
    LLM_MODELS,
    STT_MODELS,
    TTS_MODEL_CHOICES,
    TTS_MODELS,
    VOICES,
)

load_dotenv()

WEB_DIR = Path(__file__).parent / "web"


async def config(_: web.Request) -> web.Response:
    """Defaults (from .env) and the choices the page can offer."""
    return web.json_response(
        {
            "defaults": asdict(ENV_SETTINGS),
            "default_story": DEFAULT_STORY,
            "providers": {"stt": list(STT_MODELS), "llm": list(LLM_MODELS), "tts": list(TTS_MODELS)},
            "voices": VOICES,
            "llm_models": LLM_MODEL_CHOICES,
            "tts_models": TTS_MODEL_CHOICES,
            "default_voice": DEFAULT_VOICE,
            "keys_present": {p: bool(os.getenv(k)) for p, k in API_KEYS.items()},
        }
    )


async def token(request: web.Request) -> web.Response:
    # Body: {"client": "colombo-4g", "settings": {...}}. `client` labels the run in runs/latency.jsonl.
    body = await request.json() if request.can_read_body else {}
    metadata = {"client": body.get("client") or "unknown", "settings": body.get("settings") or {}}
    room = f"spike-{secrets.token_hex(4)}"
    identity = f"user-{secrets.token_hex(4)}"
    jwt = (
        api.AccessToken()  # reads LIVEKIT_API_KEY / LIVEKIT_API_SECRET
        .with_identity(identity)
        .with_grants(api.VideoGrants(room_join=True, room=room))
        .with_room_config(
            RoomConfiguration(agents=[RoomAgentDispatch(agent_name=AGENT_NAME, metadata=json.dumps(metadata))])
        )
        .to_jwt()
    )
    return web.json_response({"url": os.environ["LIVEKIT_URL"], "token": jwt, "room": room})


async def index(_: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB_DIR / "index.html")


def main() -> None:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/config", config)
    app.router.add_post("/token", token)
    web.run_app(app, port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    main()
