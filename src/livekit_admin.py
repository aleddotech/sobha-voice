"""Delete leftover LiveKit rooms so this demo only has one live call."""

from __future__ import annotations

import os
from typing import Optional

from livekit.api import LiveKitAPI
from livekit.protocol.room import DeleteRoomRequest, ListRoomsRequest

LIVEKIT_URL = os.getenv("LIVEKIT_URL", "")
LIVEKIT_API_KEY = os.getenv("LIVEKIT_API_KEY", "")
LIVEKIT_API_SECRET = os.getenv("LIVEKIT_API_SECRET", "")


def _http_url() -> str:
    return LIVEKIT_URL.replace("wss://", "https://").replace("ws://", "http://")


def _client() -> LiveKitAPI:
    return LiveKitAPI(_http_url(), LIVEKIT_API_KEY, LIVEKIT_API_SECRET)


async def delete_room(room_name: str) -> bool:
    if not room_name or not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        return False
    api = _client()
    try:
        await api.room.delete_room(DeleteRoomRequest(room=room_name))
        print(f"[LiveKit] deleted room {room_name}", flush=True)
        return True
    except Exception as e:
        print(f"[LiveKit] delete {room_name} failed: {e}", flush=True)
        return False
    finally:
        await api.aclose()


async def delete_sobha_rooms(keep: Optional[str] = None) -> list[str]:
    """End every sobha-* room except `keep`. Returns names that were deleted."""
    if not all([LIVEKIT_URL, LIVEKIT_API_KEY, LIVEKIT_API_SECRET]):
        return []
    api = _client()
    deleted: list[str] = []
    try:
        resp = await api.room.list_rooms(ListRoomsRequest())
        for room in resp.rooms:
            name = room.name or ""
            if not name.startswith("sobha-"):
                continue
            if keep and name == keep:
                continue
            try:
                await api.room.delete_room(DeleteRoomRequest(room=name))
                deleted.append(name)
                print(f"[LiveKit] deleted leftover room {name}", flush=True)
            except Exception as e:
                print(f"[LiveKit] delete leftover {name} failed: {e}", flush=True)
    except Exception as e:
        print(f"[LiveKit] list rooms failed: {e}", flush=True)
    finally:
        await api.aclose()
    return deleted
