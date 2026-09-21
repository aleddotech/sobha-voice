#!/usr/bin/env python3
"""
Sobha Voice Agent — Web Server & Token Dispatcher
==================================================
Serves the LiveKit Web Playground at http://localhost:8765
Automatically launches and manages the Sobha LiveKit Agent worker in the background.
"""

import os
import sys
import uuid
import datetime
import subprocess
import atexit
import signal
from pathlib import Path
from contextlib import asynccontextmanager

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env")

import uvicorn
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

sys.path.insert(0, str(Path(__file__).resolve().parent))
from calls_api import router as calls_router
import call_store

from livekit.api import (
    AccessToken,
    VideoGrants,
    LiveKitAPI,
    CreateAgentDispatchRequest,
)
from livekit.protocol.room import RoomConfiguration
from livekit.protocol.agent_dispatch import RoomAgentDispatch

import livekit_admin

AGENT_NAME = "sobha-agent"

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")

STATIC_DIR = Path(__file__).parent / "static"

# Background agent worker (local only). Never spawn inside the Render web
# dyno — LiveKit + uvicorn together OOM a Starter instance and 502 /api/token.
_agent_process = None
_on_render = os.getenv("RENDER") is not None
_SPAWN_AGENT = (
    False
    if _on_render
    else os.getenv("SPAWN_AGENT", "1").strip() not in ("0", "false", "False", "no")
)
_CORS_ORIGINS = [o.strip() for o in os.getenv("CORS_ORIGINS", "*").split(",") if o.strip()]

def start_agent_worker():
    global _agent_process
    if not _SPAWN_AGENT:
        return
    if _agent_process is None or _agent_process.poll() is not None:
        agent_script = Path(__file__).parent / "agent.py"
        mode = "dev" if os.getenv("RENDER") is None else "start"
        print(f"[Server] Spawning background LiveKit agent worker: python {agent_script.name} {mode}", flush=True)
        env = os.environ.copy()
        _agent_process = subprocess.Popen(
            [sys.executable, str(agent_script), mode],
            cwd=str(PROJECT_ROOT),
            env=env,
        )

def stop_agent_worker():
    global _agent_process
    if _agent_process and _agent_process.poll() is None:
        print("[Server] Terminating background agent worker...", flush=True)
        try:
            _agent_process.terminate()
            _agent_process.wait(timeout=3)
        except Exception:
            _agent_process.kill()
        _agent_process = None

atexit.register(stop_agent_worker)

@asynccontextmanager
async def lifespan(app: FastAPI):
    call_store.init_db()
    start_agent_worker()
    yield
    stop_agent_worker()

app = FastAPI(title="Sobha Voice Agent Playground", lifespan=lifespan)
app.include_router(calls_router)
_cors_star = _CORS_ORIGINS == ["*"]
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if _cors_star else _CORS_ORIGINS,
    allow_credentials=not _cors_star,
    allow_methods=["*"],
    allow_headers=["*"],
)

_last_dispatch = {"ok": None, "error": None, "room": None}

@app.get("/api/token")
async def get_token():
    """Generate a fresh LiveKit token with RoomAgentDispatch for sobha-agent."""
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        return JSONResponse(
            {"error": "Server missing LIVEKIT credentials in .env"},
            status_code=500,
        )

    # One demo call at a time — drop leftover LiveKit rooms/jobs first.
    start_agent_worker()
    closed = await livekit_admin.delete_sobha_rooms()
    if closed:
        print(f"[Server] Closed leftover rooms: {closed}", flush=True)

    room_name = f"sobha-{uuid.uuid4().hex[:8]}"
    identity = f"user-{uuid.uuid4().hex[:6]}"
    call_store.upsert_call(
        room_name,
        caller_name=identity,
        caller_id=identity,
        dept="Operations",
        issue="Live voice call",
        outcome="resolved",
        live=1,
    )

    http_url = LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")
    lkapi = LiveKitAPI(http_url, LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
    try:
        await lkapi.agent_dispatch.create_dispatch(
            CreateAgentDispatchRequest(agent_name=AGENT_NAME, room=room_name)
        )
        print(f"[Server] Dispatched {AGENT_NAME} to {room_name}", flush=True)
        _last_dispatch.update({"ok": True, "error": None, "room": room_name})
    except Exception as e:
        print(f"[Server] ERROR creating agent dispatch: {e}", flush=True)
        _last_dispatch.update({"ok": False, "error": str(e), "room": room_name})
    finally:
        await lkapi.aclose()

    token = (
        AccessToken(LIVEKIT_API_KEY, LIVEKIT_API_SECRET)
        .with_identity(identity)
        .with_grants(
            VideoGrants(
                room_join=True,
                room=room_name,
                can_publish=True,
                can_subscribe=True,
                can_publish_data=True,
            )
        )
        .with_room_config(
            RoomConfiguration(
                agents=[RoomAgentDispatch(agent_name=AGENT_NAME)],
            )
        )
        .with_ttl(datetime.timedelta(hours=1))
        .to_jwt()
    )

    return {
        "token": token,
        "url": LIVEKIT_URL,
        "room": room_name,
        "identity": identity,
        "cleared": closed,
    }

@app.post("/api/hangup")
async def hangup_all(request: Request):
    """End Call / tab close. JSON may name one `room` or `keep` a live room."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    only = payload.get("room")
    keep = payload.get("keep")
    if only:
        ok = await livekit_admin.delete_room(str(only))
        return {"ok": True, "closed": [only] if ok else []}
    closed = await livekit_admin.delete_sobha_rooms(keep=str(keep) if keep else None)
    return {"ok": True, "closed": closed}

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": AGENT_NAME,
        "worker_running": _agent_process is not None and _agent_process.poll() is None,
        "spawn_agent": _SPAWN_AGENT,
        "last_dispatch": _last_dispatch,
    }

@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = STATIC_DIR / "index.html"
    return HTMLResponse(html_path.read_text())

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard():
    html_path = STATIC_DIR / "dashboard.html"
    return HTMLResponse(html_path.read_text())

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8765"))
    print("\n" + "=" * 60)
    print("  Sobha Voice Agent (LiveKit Powered)")
    print(f"  Web Portal: http://localhost:{port}")
    print("=" * 60 + "\n", flush=True)

    def sig_handler(sig, frame):
        stop_agent_worker()
        sys.exit(0)

    signal.signal(signal.SIGINT, sig_handler)
    signal.signal(signal.SIGTERM, sig_handler)

    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
