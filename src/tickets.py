"""Facility/transport ticket DB lookups for the voice agent."""

from __future__ import annotations

import json
import os
import re
from typing import Any, Optional

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db.json")

_ID_CHARS = re.compile(r"[^a-z0-9]")


def load_db(path: str = DB_PATH) -> list[dict]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def save_db(db: list, path: str = DB_PATH) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(db, f, indent=2, ensure_ascii=False)


def normalize_ticket_id(value: Optional[str]) -> str:
    return _ID_CHARS.sub("", (value or "").lower())


def _blob(record: dict) -> str:
    parts = [
        record.get("ticket_id"),
        record.get("resident_name"),
        record.get("name_or_emp_id"),
        record.get("unit_number"),
        record.get("bus_number"),
        record.get("stop_name"),
        record.get("issue_type"),
        record.get("description"),
        record.get("contact_number"),
    ]
    return " ".join(str(p) for p in parts if p).lower()


def lookup_tickets(
    db: list[dict],
    ticket_id: Optional[str] = None,
    name_or_id: Optional[str] = None,
) -> dict[str, Any]:
    """Search existing tickets. Returns found=true with matches, or found=false."""
    tid = (ticket_id or "").strip()
    name = (name_or_id or "").strip()
    if not tid and not name:
        return {
            "found": False,
            "tickets": [],
            "handle": "ask_id",
            "say": "Need a ticket ID or name.",
        }

    matches: list[dict] = []
    seen: set[str] = set()
    nid = normalize_ticket_id(tid) if tid else ""

    for rec in db:
        rid = str(rec.get("ticket_id") or "")
        if rid in seen:
            continue
        ok = False
        if nid:
            rec_nid = normalize_ticket_id(rid)
            if rec_nid == nid or (len(nid) >= 3 and nid in rec_nid) or (len(rec_nid) >= 3 and rec_nid in nid):
                ok = True
        if name:
            q = name.lower()
            if q in _blob(rec):
                ok = True
        if ok:
            seen.add(rid)
            matches.append(rec)

    if matches:
        first = matches[0]
        status = str(first.get("status") or "Open")
        return {
            "found": True,
            "tickets": matches[:3],
            "handle": "existing",
            "say": (
                f"Found {first.get('ticket_id')}. Status {status}."
                + (f" Assigned to {first['assigned_to']}." if first.get("assigned_to") else "")
            ),
        }

    return {
        "found": False,
        "tickets": [],
        "handle": "new",
        "query": {"ticket_id": tid or None, "name_or_id": name or None},
        "say": "No ticket on file. I can raise a new one.",
    }


def matching_open_tickets(db: list[dict], name: str, issue_type: str) -> list[dict]:
    qn = (name or "").strip().lower()
    qi = (issue_type or "").strip().lower()
    out = []
    for rec in db:
        status = str(rec.get("status") or "").lower()
        if status in ("resolved", "closed", "done"):
            continue
        blob = _blob(rec)
        if qn and qn not in blob:
            continue
        if qi and qi not in blob and qi not in str(rec.get("issue_type") or "").lower() and qi not in str(rec.get("type") or "").lower():
            continue
        out.append(rec)
    return out
