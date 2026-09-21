"""HTTP API for dashboard calls, transcripts, and recordings."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path

from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse

import call_store
import livekit_admin

router = APIRouter()


def _public_base(request: Request) -> str:
    env = os.getenv("PUBLIC_BASE_URL", "").rstrip("/")
    if env:
        return env
    return str(request.base_url).rstrip("/")


def _serialize(request: Request, row: dict) -> dict:
    audio_url = None
    if row.get("audio_path") and Path(row["audio_path"]).exists():
        audio_url = f"{_public_base(request)}/api/calls/{row['id']}/audio"
    return {
        "id": row["id"],
        "created_at": row["created_at"],
        "ended_at": row.get("ended_at"),
        "duration_sec": row.get("duration_sec") or 0,
        "caller_name": row.get("caller_name"),
        "caller_id": row.get("caller_id"),
        "phone": row.get("phone"),
        "dept": row.get("dept") or "Operations",
        "issue": row.get("issue"),
        "outcome": row.get("outcome") or "resolved",
        "sentiment": row.get("sentiment") or "Neutral",
        "ticket_id": row.get("ticket_id"),
        "ticket_status": row.get("ticket_status"),
        "ticket_priority": row.get("ticket_priority"),
        "assigned_to": row.get("assigned_to"),
        "summary": row.get("summary"),
        "live": bool(row.get("live", 1)),
        "audio_url": audio_url,
        "turns": row.get("turns") or [],
    }


@router.get("/api/calls")
async def list_calls(request: Request):
    call_store.init_db()
    rows = call_store.list_calls()
    return {"calls": [_serialize(request, r) for r in rows]}


@router.get("/api/calls/{call_id}")
async def get_call(call_id: str, request: Request):
    row = call_store.get_call(call_id)
    if not row:
        return JSONResponse({"error": "not found"}, status_code=404)
    return _serialize(request, row)


@router.post("/api/calls")
async def create_call(payload: dict):
    call_id = payload.get("id") or payload.get("room")
    if not call_id:
        return JSONResponse({"error": "id required"}, status_code=400)
    fields = {k: payload[k] for k in payload if k != "id"}
    call_store.upsert_call(call_id, **fields)
    return {"ok": True, "id": call_id}


@router.post("/api/calls/{call_id}/turns")
async def add_turn(call_id: str, payload: dict):
    call_store.add_turn(
        call_id,
        who=payload.get("who") or "CALLER",
        text=payload.get("text") or "",
        t_sec=float(payload.get("t_sec") or 0),
    )
    return {"ok": True}


@router.post("/api/calls/{call_id}/end")
async def end_call(call_id: str, request: Request):
    """Beacon-friendly hangup when the browser tab closes."""
    payload = {}
    try:
        payload = await request.json()
    except Exception:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    payload.setdefault("ended_at", datetime.now(timezone.utc).isoformat())
    call_store.upsert_call(call_id, **payload)
    await livekit_admin.delete_room(call_id)
    return {"ok": True}


@router.patch("/api/calls/{call_id}")
async def patch_call(call_id: str, payload: dict):
    if "ended_at" not in payload:
        payload = {**payload, "ended_at": datetime.now(timezone.utc).isoformat()}
    call_store.upsert_call(call_id, **payload)
    return {"ok": True}


@router.post("/api/calls/{call_id}/audio")
async def upload_audio(call_id: str, file: UploadFile = File(...)):
    data = await file.read()
    name = file.filename or "audio.webm"
    suffix = Path(name).suffix or ".webm"
    call_store.save_audio(call_id, data, suffix=suffix)
    return {"ok": True}


@router.get("/api/calls/{call_id}/audio")
async def get_audio(call_id: str):
    row = call_store.get_call(call_id)
    if not row or not row.get("audio_path"):
        return JSONResponse({"error": "no audio"}, status_code=404)
    path = Path(row["audio_path"])
    if not path.exists():
        return JSONResponse({"error": "missing file"}, status_code=404)
    media = "audio/webm" if path.suffix == ".webm" else "audio/mpeg"
    return FileResponse(
        path,
        media_type=media,
        filename=path.name,
        content_disposition_type="inline",
    )
