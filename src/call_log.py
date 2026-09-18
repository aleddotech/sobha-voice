"""Worker-side client to post transcripts to the FastAPI call store."""

from __future__ import annotations

import os
from typing import Any, Optional

import aiohttp

_API = os.getenv("CALLS_API_URL", "").rstrip("/")


async def _post(path: str, json: Optional[dict] = None) -> None:
    if not _API:
        return
    url = f"{_API}{path}"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(url, json=json or {}) as resp:
                await resp.read()
    except Exception as e:
        print(f"[call_log] POST {path} failed: {e}", flush=True)


async def _patch(path: str, json: Optional[dict] = None) -> None:
    if not _API:
        return
    url = f"{_API}{path}"
    try:
        timeout = aiohttp.ClientTimeout(total=8)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.patch(url, json=json or {}) as resp:
                await resp.read()
    except Exception as e:
        print(f"[call_log] PATCH {path} failed: {e}", flush=True)


async def ensure_call(call_id: str, **fields: Any) -> None:
    await _post("/api/calls", {"id": call_id, **fields})


async def add_turn(call_id: str, who: str, text: str, t_sec: float = 0) -> None:
    await _post(f"/api/calls/{call_id}/turns", {"who": who, "text": text, "t_sec": t_sec})


async def finish_call(call_id: str, **fields: Any) -> None:
    await _patch(f"/api/calls/{call_id}", fields)
