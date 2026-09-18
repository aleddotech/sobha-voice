"""SQLite store for voice-agent calls, transcripts, and recordings."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("CALLS_DB_PATH", str(PROJECT_ROOT / "data" / "calls.db")))
AUDIO_DIR = Path(os.getenv("CALLS_AUDIO_DIR", str(PROJECT_ROOT / "data" / "recordings")))

_lock = threading.Lock()
_init = False


def _connect() -> sqlite3.Connection:
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    global _init
    with _lock:
        if _init:
            return
        conn = _connect()
        try:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS calls (
                  id TEXT PRIMARY KEY,
                  created_at TEXT NOT NULL,
                  ended_at TEXT,
                  duration_sec REAL DEFAULT 0,
                  caller_name TEXT,
                  caller_id TEXT,
                  phone TEXT,
                  dept TEXT,
                  issue TEXT,
                  outcome TEXT,
                  sentiment TEXT,
                  ticket_id TEXT,
                  ticket_status TEXT,
                  ticket_priority TEXT,
                  assigned_to TEXT,
                  summary TEXT,
                  audio_path TEXT,
                  live INTEGER DEFAULT 1
                );
                CREATE TABLE IF NOT EXISTS turns (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  call_id TEXT NOT NULL,
                  t_sec REAL DEFAULT 0,
                  who TEXT NOT NULL,
                  text TEXT NOT NULL,
                  FOREIGN KEY(call_id) REFERENCES calls(id)
                );
                """
            )
            conn.commit()
            n = conn.execute("SELECT COUNT(*) FROM calls").fetchone()[0]
            if n == 0:
                _seed(conn)
                conn.commit()
        finally:
            conn.close()
        _init = True


def _seed(conn: sqlite3.Connection) -> None:
    now = datetime.now(timezone.utc)
    seeds = [
        {
            "id": "sobha-demo-01",
            "ago_h": 2,
            "dur": 148,
            "name": "Priya Nair",
            "eid": "EMP-10412",
            "phone": "+971 50 412 8801",
            "dept": "Operations",
            "issue": "Missed staff bus",
            "outcome": "resolved",
            "sentiment": "Neutral",
            "ticket_id": "TR-0004",
            "ticket_status": "In Transit",
            "ticket_priority": "High",
            "assigned_to": "Transport Desk",
            "summary": "Priya Nair reported missing bus 12 at Al Khail stop. The agent contacted the driver; the bus was running late with a 9-minute ETA.",
            "turns": [
                ("AGENT", "Thank you for reaching Sobha agent. Which language do you prefer? We support Hindi, English, Malayalam, and Arabic."),
                ("CALLER", "English please."),
                ("AGENT", "Do you want to check an existing request, or file a new one?"),
                ("CALLER", "I missed my bus this morning. Name Priya Nair, bus 12, Al Khail stop."),
                ("AGENT", "I will contact the bus driver right away. Please hold on for a moment."),
                ("AGENT", "I checked, it seems the bus is late. It will pick you up in 9 minutes. Ticket TR-0004 is logged."),
                ("CALLER", "Thank you."),
            ],
        },
        {
            "id": "sobha-demo-02",
            "ago_h": 5,
            "dur": 176,
            "name": "Rahul Menon",
            "eid": "EMP-10521",
            "phone": "+971 55 219 4410",
            "dept": "Operations",
            "issue": "Driver missed pickup",
            "outcome": "escalated",
            "sentiment": "Negative",
            "ticket_id": "TR-0005",
            "ticket_status": "Alternative Arranged",
            "ticket_priority": "High",
            "assigned_to": "Transport Desk",
            "summary": "Rahul Menon missed pickup at Discovery Gardens. The driver had already left the stop. Alternative transport was arranged and complaint TR-0005 registered.",
            "turns": [
                ("AGENT", "Thank you for reaching Sobha agent. Which language do you prefer?"),
                ("CALLER", "Malayalam."),
                ("AGENT", "Language switched to Malayalam. How can I help?"),
                ("CALLER", "Ente bus miss aayi. Rahul Menon, bus 7, Discovery Gardens."),
                ("AGENT", "I will contact the bus driver right away. Please hold on."),
                ("AGENT", "Sorry, they missed your pickup. A complaint has been registered as TR-0005, and I have arranged someone to pick you up shortly."),
            ],
        },
        {
            "id": "sobha-demo-03",
            "ago_h": 26,
            "dur": 201,
            "name": "Omar Al Suwaidi",
            "eid": "EMP-10240",
            "phone": "+971 52 330 1194",
            "dept": "Facilities",
            "issue": "Driver unreachable",
            "outcome": "escalated",
            "sentiment": "Negative",
            "ticket_id": "TR-0006",
            "ticket_status": "Escalated",
            "ticket_priority": "High",
            "assigned_to": "Transport Desk",
            "summary": "Omar Al Suwaidi could not board bus 3 at Marina. The driver did not answer. Urgent ticket TR-0006 was raised for the transport desk.",
            "turns": [
                ("AGENT", "Thank you for reaching Sobha agent. Which language do you prefer?"),
                ("CALLER", "Arabic."),
                ("AGENT", "Language switched to Arabic. How can I help today?"),
                ("CALLER", "Missed the staff bus. Omar Al Suwaidi, bus 3, Marina."),
                ("AGENT", "I will contact the bus driver right away."),
                ("AGENT", "I tried calling the driver, but they are not available. I have raised complaint TR-0006. Someone from the transport desk will be in touch immediately."),
            ],
        },
        {
            "id": "sobha-demo-04",
            "ago_h": 30,
            "dur": 92,
            "name": "Hessa Al Ketbi",
            "eid": "EMP-10388",
            "phone": "+971 56 881 2203",
            "dept": "Facilities",
            "issue": "AC not working",
            "outcome": "escalated",
            "sentiment": "Neutral",
            "ticket_id": "TK-0012",
            "ticket_status": "Open",
            "ticket_priority": "Medium",
            "assigned_to": "Maintenance",
            "summary": "Hessa Al Ketbi reported AC failure in Villa 14. Ticket TK-0012 was registered in the facility system.",
            "turns": [
                ("AGENT", "Thank you for reaching Sobha agent. Which language do you prefer?"),
                ("CALLER", "English. AC is not cooling in villa 14. Hessa Al Ketbi."),
                ("AGENT", "Registering the ticket in the facility system. Please hold."),
                ("AGENT", "Ticket TK-0012 is open with medium priority for AC in villa 14."),
                ("CALLER", "Okay, thanks."),
            ],
        },
        {
            "id": "sobha-demo-05",
            "ago_h": 50,
            "dur": 84,
            "name": "Arjun Pillai",
            "eid": "EMP-10602",
            "phone": "+971 50 774 0199",
            "dept": "Operations",
            "issue": "Ticket status lookup",
            "outcome": "resolved",
            "sentiment": "Positive",
            "ticket_id": "TR-0004",
            "ticket_status": "In Transit",
            "ticket_priority": "High",
            "assigned_to": "Transport Desk",
            "summary": "Arjun Pillai asked for the status of TR-0004. The agent confirmed the bus was still in transit.",
            "turns": [
                ("AGENT", "Thank you for reaching Sobha agent. Which language do you prefer?"),
                ("CALLER", "English. Checking status of TR-0004."),
                ("AGENT", "TR-0004 is In Transit. The bus is delayed and the last ETA was 9 minutes."),
                ("CALLER", "Got it, thank you."),
            ],
        },
    ]
    for s in seeds:
        created = (now - timedelta(hours=s["ago_h"])).isoformat()
        ended = (now - timedelta(hours=s["ago_h"]) + timedelta(seconds=s["dur"])).isoformat()
        conn.execute(
            """INSERT INTO calls (id, created_at, ended_at, duration_sec, caller_name, caller_id,
               phone, dept, issue, outcome, sentiment, ticket_id, ticket_status, ticket_priority,
               assigned_to, summary, live)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,0)""",
            (
                s["id"], created, ended, s["dur"], s["name"], s["eid"], s["phone"], s["dept"],
                s["issue"], s["outcome"], s["sentiment"], s["ticket_id"], s["ticket_status"],
                s["ticket_priority"], s["assigned_to"], s["summary"],
            ),
        )
        step = s["dur"] / max(1, len(s["turns"]))
        for i, (who, text) in enumerate(s["turns"]):
            conn.execute(
                "INSERT INTO turns (call_id, t_sec, who, text) VALUES (?,?,?,?)",
                (s["id"], round(i * step, 1), who, text),
            )


def upsert_call(call_id: str, **fields: Any) -> None:
    init_db()
    with _lock:
        conn = _connect()
        try:
            row = conn.execute("SELECT id FROM calls WHERE id=?", (call_id,)).fetchone()
            if not row:
                conn.execute(
                    "INSERT INTO calls (id, created_at, live) VALUES (?,?,1)",
                    (call_id, datetime.now(timezone.utc).isoformat()),
                )
            if fields:
                cols = []
                vals = []
                for k, v in fields.items():
                    if k in {
                        "ended_at", "duration_sec", "caller_name", "caller_id", "phone", "dept",
                        "issue", "outcome", "sentiment", "ticket_id", "ticket_status",
                        "ticket_priority", "assigned_to", "summary", "audio_path", "live",
                    }:
                        cols.append(f"{k}=?")
                        vals.append(v)
                if cols:
                    vals.append(call_id)
                    conn.execute(f"UPDATE calls SET {', '.join(cols)} WHERE id=?", vals)
            conn.commit()
        finally:
            conn.close()


def add_turn(call_id: str, who: str, text: str, t_sec: float = 0) -> None:
    if not text or not str(text).strip():
        return
    upsert_call(call_id)
    with _lock:
        conn = _connect()
        try:
            conn.execute(
                "INSERT INTO turns (call_id, t_sec, who, text) VALUES (?,?,?,?)",
                (call_id, t_sec, who, str(text).strip()),
            )
            conn.commit()
        finally:
            conn.close()


def save_audio(call_id: str, data: bytes, suffix: str = ".webm") -> str:
    upsert_call(call_id)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    path = AUDIO_DIR / f"{call_id}{suffix}"
    path.write_bytes(data)
    upsert_call(call_id, audio_path=str(path))
    return str(path)


def get_call(call_id: str) -> Optional[dict]:
    init_db()
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM calls WHERE id=?", (call_id,)).fetchone()
        if not row:
            return None
        turns = conn.execute(
            "SELECT t_sec, who, text FROM turns WHERE call_id=? ORDER BY id", (call_id,)
        ).fetchall()
        d = dict(row)
        d["turns"] = [dict(t) for t in turns]
        return d
    finally:
        conn.close()


def list_calls(limit: int = 500) -> list[dict]:
    init_db()
    conn = _connect()
    try:
        rows = conn.execute(
            "SELECT * FROM calls ORDER BY created_at DESC LIMIT ?", (limit,)
        ).fetchall()
        out = []
        for row in rows:
            d = dict(row)
            turns = conn.execute(
                "SELECT t_sec, who, text FROM turns WHERE call_id=? ORDER BY id", (d["id"],)
            ).fetchall()
            d["turns"] = [dict(t) for t in turns]
            out.append(d)
        return out
    finally:
        conn.close()
