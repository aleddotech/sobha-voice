import sys
import os
import asyncio
import logging
import json
import random
import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
    force=True,
)

from decouple import config
from livekit.agents import (
    JobContext,
    AgentServer,
    cli,
    llm,
    room_io,
)
from livekit.agents.voice import Agent, AgentSession
from livekit.plugins import openai, deepgram, elevenlabs, silero
from livekit.agents.stt import StreamAdapter

from sarvam_tts import SarvamTTS
from sarvam_stt import SarvamSTT
from routed_stt import RoutedSTT
from tickets import load_db, save_db, lookup_tickets, matching_open_tickets
import call_log
import time

try:
    from livekit.plugins import noise_cancellation
    _HAS_NOISE_CANCELLATION = True
except ImportError:
    _HAS_NOISE_CANCELLATION = False

from system_prompt import SYSTEM_PROMPT

logger = logging.getLogger("sobha-agent")

_REQUIRED_KEYS = [
    ("LIVEKIT_URL", "LiveKit Cloud URL"),
    ("LIVEKIT_API_KEY", "LiveKit API Key"),
    ("LIVEKIT_API_SECRET", "LiveKit API Secret"),
    ("SARVAM_API_KEY", "Sarvam AI STT/TTS"),
    ("DEEPGRAM_API_KEY", "Deepgram Nova-3 STT (English, Arabic)"),
    ("ELEVEN_API_KEY", "ElevenLabs Multilingual Fallback"),
    ("OPENAI_API_KEY", "OpenAI LLM"),
]

def _validate_env():
    missing = []
    for key, desc in _REQUIRED_KEYS:
        val = config(key, default="")
        if not val or not str(val).strip():
            missing.append(f"  {key} ({desc})")
    if missing:
        msg = "Missing required env vars in .env:\n" + "\n".join(missing)
        print(f"[SOBHA] >>> ERROR: {msg}", flush=True)
        raise SystemExit(1)

server = AgentServer(num_idle_processes=1, max_retry=6)

def prewarm(proc):
    """Prewarm Silero VAD model with aggressive threshold for instant speech/interruption detection."""
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.3,
        prefix_padding_duration=0.2,
        activation_threshold=0.45,
    )

server.setup_fnc = prewarm

def _load_db() -> list:
    return load_db()

def _save_db(db: list):
    try:
        save_db(db)
    except Exception as e:
        logger.error(f"Could not save DB: {e}")

@server.rtc_session(agent_name="sobha-agent")
async def sobha_voice_agent(ctx: JobContext):
    print(f"[SOBHA] >>> Entrypoint called for room {ctx.room.name}", flush=True)
    logger.info(f"Starting Sobha agent for room {ctx.room.name}")

    # Join immediately so the playground leaves "waiting for agent"
    # before STT/TTS clients are constructed.
    await ctx.connect()
    print(f"[SOBHA] >>> Connected to room {ctx.room.name}", flush=True)

    def _has_caller() -> bool:
        for p in ctx.room.remote_participants.values():
            ident = (getattr(p, "identity", None) or "")
            if ident.startswith("agent") or ident.startswith("sobha-agent"):
                continue
            return True
        return False

    def _on_caller_left(participant):
        if _has_caller():
            return
        print(
            f"[SOBHA] >>> Caller left ({getattr(participant, 'identity', '')}); shutting down",
            flush=True,
        )
        ctx.shutdown("caller left")

    ctx.room.on("participant_disconnected", _on_caller_left)

    async def _watch_empty_room():
        for _ in range(12):
            if _has_caller():
                break
            await asyncio.sleep(1)
        else:
            print("[SOBHA] >>> No caller joined; deleting empty room", flush=True)
            ctx.shutdown("no caller")
            try:
                await ctx.delete_room()
            except Exception:
                pass
            return
        while True:
            await asyncio.sleep(2)
            if not _has_caller():
                print("[SOBHA] >>> Room empty; shutting down", flush=True)
                ctx.shutdown("caller gone")
                try:
                    await ctx.delete_room()
                except Exception:
                    pass
                return

    asyncio.create_task(_watch_empty_room())

    try:
        room_id = ctx.room.name
        t0 = time.time()
        initial_ctx = llm.ChatContext()
        initial_ctx.add_message(
            content=SYSTEM_PROMPT,
            role="system",
        )

        _call_notes: dict = {}
        _turns: list[dict] = []
        _seen_turns: set[tuple[str, str]] = set()

        def _log_turn(who: str, text: str) -> None:
            t = (text or "").strip()
            if not t:
                return
            key = (who, t)
            if key in _seen_turns:
                return
            _seen_turns.add(key)
            _turns.append({"who": who, "text": t})
            asyncio.create_task(call_log.add_turn(room_id, who, t, time.time() - t0))

        class SobhaTools:
            @llm.function_tool(
                description=(
                    "Call this when the employee or resident reports they missed their bus or transportation. "
                    "Provide the employee name or ID, bus number, and stop name. "
                    "action_delay_seconds: duration in seconds to simulate contacting the driver (default 30s). "
                    "Returns the situation report: Situation 1 (bus is late with ETA in minutes), "
                    "Situation 2 (driver forgot/missed pickup, complaint registered and alternative arranged), "
                    "or Situation 3 (driver unavailable/not answering, complaint raised, transport team in touch)."
                )
            )
            async def handle_transport_dispatch(
                self,
                name_or_emp_id: str,
                bus_number: str,
                stop_name: str,
                action_delay_seconds: int = 30,
            ) -> str:
                logger.info(f"Transport dispatch: {name_or_emp_id}, bus={bus_number}, stop={stop_name}, simulating {action_delay_seconds}s call to driver...")
                print(f"[SOBHA] >>> Calling bus driver... Waiting {action_delay_seconds}s to imitate call.", flush=True)
                if action_delay_seconds > 0:
                    # Bound between 1 and 60 seconds
                    delay = max(1, min(action_delay_seconds, 60))
                    await asyncio.sleep(delay)
                print(f"[SOBHA] >>> Call to driver complete. Generating dispatch report.", flush=True)
                db = _load_db()
                new_id = f"TR-{str(len(db) + 1).zfill(4)}"
                now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

                scenarios = [
                    {
                        "situation": 1,
                        "status": "In Transit",
                        "eta_minutes": random.randint(5, 15),
                        "message": "The bus is running behind schedule due to traffic. ETA has been determined.",
                    },
                    {
                        "situation": 2,
                        "status": "Alternative Arranged",
                        "message": "Driver missed the stop. Standby alternative transport has been assigned.",
                    },
                    {
                        "situation": 3,
                        "status": "Escalated",
                        "message": "Driver could not be reached. Urgent escalation ticket created for transport desk.",
                    }
                ]
                chosen = random.choice(scenarios)
                outcome = "resolved" if chosen["situation"] == 1 else "escalated"

                record = {
                    "ticket_id": new_id,
                    "type": "Transportation - Missed Bus",
                    "name_or_emp_id": name_or_emp_id,
                    "bus_number": bus_number,
                    "stop_name": stop_name,
                    "situation": chosen["situation"],
                    "status": chosen["status"],
                    "notes": chosen["message"],
                    "eta_minutes": chosen.get("eta_minutes"),
                    "outcome": outcome,
                    "action_delay_seconds": action_delay_seconds,
                    "created_at": now,
                }
                db.append(record)
                _save_db(db)
                _call_notes.setdefault("ticket_id", new_id)
                _call_notes.setdefault("ticket_status", chosen["status"])
                _call_notes.setdefault("issue", "Transportation - Missed Bus")
                _call_notes.setdefault("outcome", outcome)
                _call_notes.setdefault("assigned_to", "Transport Desk")
                if name_or_emp_id:
                    _call_notes.setdefault("caller_name", name_or_emp_id)
                    _call_notes.setdefault("caller_id", name_or_emp_id)
                print(f"[SOBHA] >>> Logged transport ticket {new_id} -> Situation {chosen['situation']} ({outcome})", flush=True)
                return json.dumps(record, indent=2)

            @llm.function_tool(
                description=(
                    "Search the facility DB for an existing ticket. "
                    "Call this FIRST when the caller mentions an old/existing ticket, a ticket ID, "
                    "or asks for status. Pass ticket_id and/or name_or_id (resident, employee, unit, bus). "
                    "If found=true, read back ticket_id, status, assigned_to — do not raise a duplicate. "
                    "If found=false, say it is not on file, then raise_ticket only if they want a new one."
                )
            )
            async def lookup_requests(
                self,
                ticket_id: str | None = None,
                name_or_id: str | None = None,
            ) -> str:
                logger.info(f"Lookup requests: ticket_id={ticket_id}, name_or_id={name_or_id}")
                result = lookup_tickets(_load_db(), ticket_id=ticket_id, name_or_id=name_or_id)
                if result.get("found"):
                    first = (result.get("tickets") or [{}])[0]
                    _call_notes.setdefault("ticket_id", first.get("ticket_id"))
                    _call_notes.setdefault("ticket_status", first.get("status"))
                    _call_notes.setdefault("issue", first.get("issue_type") or first.get("type") or "Ticket status")
                    _call_notes.setdefault("outcome", "resolved")
                    if first.get("assigned_to"):
                        _call_notes.setdefault("assigned_to", first.get("assigned_to"))
                print(
                    f"[SOBHA] >>> Ticket lookup found={result.get('found')} handle={result.get('handle')}",
                    flush=True,
                )
                return json.dumps(result, indent=2)

            @llm.function_tool(
                description=(
                    "Raise a new general facility or maintenance ticket. "
                    "action_delay_seconds: duration in seconds to simulate ticket registration in the ERP/ticketing system (default 5s)."
                )
            )
            async def raise_ticket(
                self,
                resident_name: str,
                issue_type: str,
                description: str,
                priority: str = "Medium",
                action_delay_seconds: int = 5,
            ) -> str:
                logger.info(f"Raise ticket: {resident_name}, issue={issue_type}, simulating {action_delay_seconds}s ticket creation...")
                print(f"[SOBHA] >>> Registering ticket in facility system... Waiting {action_delay_seconds}s.", flush=True)
                db = _load_db()
                same = matching_open_tickets(db, resident_name, issue_type)
                if same:
                    print(f"[SOBHA] >>> Existing open ticket {same[0].get('ticket_id')}; not duplicating.", flush=True)
                    _call_notes.setdefault("ticket_id", same[0].get("ticket_id"))
                    _call_notes.setdefault("ticket_status", same[0].get("status"))
                    _call_notes.setdefault("issue", issue_type)
                    _call_notes.setdefault("outcome", "resolved")
                    return json.dumps({"found": True, "handle": "existing", "tickets": same[:1]}, indent=2)
                if action_delay_seconds > 0:
                    delay = max(1, min(action_delay_seconds, 30))
                    await asyncio.sleep(delay)
                db = _load_db()
                new_id = f"TK-{str(len(db) + 1).zfill(4)}"
                now = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
                ticket = {
                    "ticket_id": new_id,
                    "resident_name": resident_name,
                    "issue_type": issue_type,
                    "priority": priority,
                    "status": "Open",
                    "description": description,
                    "outcome": "escalated",
                    "action_delay_seconds": action_delay_seconds,
                    "created_at": now,
                }
                db.append(ticket)
                _save_db(db)
                _call_notes.setdefault("ticket_id", new_id)
                _call_notes.setdefault("ticket_status", "Open")
                _call_notes.setdefault("issue", issue_type)
                _call_notes.setdefault("outcome", "escalated")
                _call_notes.setdefault("caller_name", resident_name)
                print(f"[SOBHA] >>> Ticket {new_id} successfully created.", flush=True)
                return json.dumps(ticket, indent=2)

            @llm.function_tool(
                description=(
                    "Save caller name, employee ID, issue, outcome, and a 1-2 line summary. "
                    "Call this once before hanging up. "
                    "outcome must be 'resolved' or 'escalated'."
                )
            )
            async def save_call_notes(
                self,
                caller_name: str = "",
                caller_id: str = "",
                issue: str = "",
                outcome: str = "resolved",
                summary: str = "",
                ticket_id: str = "",
                ticket_status: str = "",
            ) -> str:
                oc = (outcome or "resolved").strip().lower()
                if oc not in ("resolved", "escalated"):
                    oc = "resolved"
                _call_notes.update(
                    {
                        "caller_name": (caller_name or "").strip(),
                        "caller_id": (caller_id or "").strip(),
                        "issue": (issue or "").strip() or "Live voice call",
                        "outcome": oc,
                        "summary": (summary or "").strip(),
                        "ticket_id": (ticket_id or "").strip() or None,
                        "ticket_status": (ticket_status or "").strip() or None,
                    }
                )
                print(f"[SOBHA] >>> Call notes saved: {oc} / {_call_notes.get('issue')}", flush=True)
                return "saved"

        sobha_tools = SobhaTools()
        vad = ctx.proc.userdata.get("vad") or silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=0.3,
            prefix_padding_duration=0.2,
            activation_threshold=0.45,
        )

        # ElevenLabs: Indian-accent female Riya Rao (Data-Fluent Support) for English + Arabic.
        # Sarvam female priya/pooja for Hindi and Malayalam.
        _INDIAN_EN_VOICE = "aScXqoGnNOyGvIIcxgOT"
        _el_voice = config("ELEVEN_VOICE_ID", default=_INDIAN_EN_VOICE)
        if _el_voice in ("", "i80JxxvpWr5Q7cTdT1Ik", "hLvRzHEBXR9scnhmrX9E"):
            _el_voice = _INDIAN_EN_VOICE
        elevenlabs_tts = elevenlabs.TTS(
            model="eleven_multilingual_v2",
            voice_id=_el_voice,
            api_key=config("ELEVEN_API_KEY"),
            streaming_latency=4,
        )

        sarvam_tts_engine = SarvamTTS(
            api_key=config("SARVAM_API_KEY"),
            speaker="ishita",
            model="bulbul:v3",
            language="en",
            pace=1.0,
            fallback_tts=elevenlabs_tts,
        )

        raw_sarvam_stt = SarvamSTT(
            api_key=config("SARVAM_API_KEY"),
            model="saaras:v3",
            language="hi",
        )
        deepgram_stt = deepgram.STT(
            model="nova-3",
            language="en-IN",
            api_key=config("DEEPGRAM_API_KEY"),
            interim_results=False,
            punctuate=True,
            smart_format=True,
            filler_words=False,
        )
        routed_stt = RoutedSTT(
            sarvam=raw_sarvam_stt,
            deepgram=deepgram_stt,
            language="en",
        )
        stt_adapter = StreamAdapter(
            stt=routed_stt,
            vad=vad,
        )

        print(
            "[SOBHA] >>> TTS: ElevenLabs Indian English/Arabic (Riya) | "
            "Sarvam priya/pooja (hi, ml). STT: Deepgram (en, ar) | Sarvam (hi, ml). "
            f"LLM={config('OPENAI_MODEL', default='gpt-5.6-terra')}",
            flush=True,
        )

        session = AgentSession(
            stt=stt_adapter,
            llm=openai.LLM(
                api_key=config("OPENAI_API_KEY"),
                model=config("OPENAI_MODEL", default="gpt-5.6-terra"),
            ),
            tts=sarvam_tts_engine,
            vad=vad,
            turn_detection="vad",
            allow_interruptions=True,
            min_interruption_duration=0.15,
            min_interruption_words=1,
            min_endpointing_delay=0.3,
            max_endpointing_delay=1.2,
            preemptive_generation=True,
            tools=[
                sobha_tools.handle_transport_dispatch,
                sobha_tools.lookup_requests,
                sobha_tools.raise_ticket,
                sobha_tools.save_call_notes,
            ],
        )

        await call_log.ensure_call(room_id, issue="Live voice call", dept="Operations")

        def _item_text(item) -> str:
            text = getattr(item, "text_content", None)
            if isinstance(text, list):
                text = " ".join(str(x) for x in text if x)
            if text:
                return str(text).strip()
            content = getattr(item, "content", None)
            if isinstance(content, str):
                return content.strip()
            if isinstance(content, list):
                return " ".join(str(x) for x in content if isinstance(x, str)).strip()
            return ""

        @session.on("user_input_transcribed")
        def _on_user_tx(ev):
            if getattr(ev, "is_final", True) and getattr(ev, "transcript", None):
                _log_turn("CALLER", ev.transcript)

        @session.on("conversation_item_added")
        def _on_item(ev):
            item = getattr(ev, "item", None)
            if item is None:
                return
            role = getattr(item, "role", "")
            text = _item_text(item)
            if role in ("assistant", "agent") and text:
                _log_turn("AGENT", text)
            elif role == "user" and text:
                _log_turn("CALLER", text)

        async def _llm_notes() -> dict:
            transcript = "\n".join(f"{t['who']}: {t['text']}" for t in _turns) or "(no transcript)"
            hint = json.dumps({k: v for k, v in _call_notes.items() if v}, ensure_ascii=False)
            prompt = (
                "Return JSON only with keys: caller_name, caller_id, issue, outcome, summary, "
                "ticket_id, ticket_status, assigned_to.\n"
                "caller_name and caller_id from what the caller said. Empty string if unknown. "
                "Do not invent. Do not use a villa/bus number as their name unless they said that is their name.\n"
                "outcome is resolved or escalated. resolved = no complaint, status lookup, or handled on the call "
                "(including bus late with an ETA). escalated = complaint still needs the desk.\n"
                "summary: one or two short sentences. What they called about, and if it was resolved or escalated. "
                "No play-by-play.\n"
                "issue: short label like 'Missed staff bus'.\n"
                f"Tool hints: {hint}\n\nTranscript:\n{transcript}"
            )
            url = "https://api.openai.com/v1/chat/completions"
            headers = {
                "Authorization": f"Bearer {config('OPENAI_API_KEY')}",
                "Content-Type": "application/json",
            }
            payload = {
                "model": config("OPENAI_WRAPUP_MODEL", default="gpt-5.6-luna"),
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": "Extract Sobha call wrap-up JSON."},
                    {"role": "user", "content": prompt},
                ],
            }
            try:
                import aiohttp
                timeout = aiohttp.ClientTimeout(total=12)
                async with aiohttp.ClientSession(timeout=timeout) as http:
                    async with http.post(url, json=payload, headers=headers) as resp:
                        data = await resp.json()
                raw = data["choices"][0]["message"]["content"]
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    return parsed
            except Exception as e:
                print(f"[SOBHA] >>> wrap-up LLM failed: {e}", flush=True)
            return {}

        async def _on_shutdown():
            notes = dict(_call_notes)
            extracted = await _llm_notes()
            for k, v in extracted.items():
                if v in (None, ""):
                    continue
                if not notes.get(k):
                    notes[k] = v
            oc = str(notes.get("outcome") or "resolved").strip().lower()
            if oc not in ("resolved", "escalated"):
                oc = "resolved"
            if not _turns:
                oc = "resolved"
                notes.setdefault("issue", "Live voice call")
            fields = {
                "duration_sec": round(time.time() - t0, 1),
                "ended_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                "caller_name": notes.get("caller_name") or None,
                "caller_id": notes.get("caller_id") or None,
                "issue": notes.get("issue") or "Live voice call",
                "outcome": oc,
                "summary": notes.get("summary") or "",
                "ticket_id": notes.get("ticket_id") or None,
                "ticket_status": notes.get("ticket_status") or None,
                "assigned_to": notes.get("assigned_to") or None,
            }
            await call_log.finish_call(room_id, **fields)

        ctx.add_shutdown_callback(_on_shutdown)

        class SobhaAgent(Agent):
            async def on_enter(self) -> None:
                print("[SOBHA] >>> Agent on_enter called, speaking welcome greeting", flush=True)
                greeting = (
                    "Hey, I'm Sobha's voice agent. How would you like me to help you? "
                    "I speak English, Hindi, Arabic, and Malayalam."
                )
                _log_turn("AGENT", greeting)
                self.session.say(greeting)

            @llm.function_tool(
                description=(
                    "Switch the conversation language. Call this whenever the user requests a different language. "
                    "lang must be one of: 'en' (English), 'hi' (Hindi), 'ar' (Arabic), 'ml' (Malayalam)."
                )
            )
            async def switch_language(self, lang: str) -> str:
                lang_names = {"en": "English", "hi": "Hindi", "ar": "Arabic", "ml": "Malayalam"}
                name = lang_names.get(lang, lang)
                sarvam_tts_engine.update_language(lang)
                routed_stt.update_language(lang)
                tts_engine = "ElevenLabs" if (lang.startswith("ar") or lang.startswith("en")) else "Sarvam"
                stt_engine = "Deepgram" if lang in ("en", "ar") or lang.startswith("ar") else "Sarvam"
                print(
                    f"[SOBHA] >>> Language switched to {name} ({lang}); "
                    f"TTS={tts_engine} STT={stt_engine}",
                    flush=True,
                )
                speak = {
                    "en": (
                        "Speak English only. Do not repeat the Sobha intro. First line: "
                        "Would you like to raise a new ticket, or is this an existing ticket?"
                    ),
                    "hi": (
                        "अब सिर्फ हिन्दी। सोभा का परिचय दोबारा मत दो। पहली पंक्ति: "
                        "नमस्ते। मैं आपकी कैसे मदद करूँ? क्या नया टिकट खोलना है, या पहले से कोई टिकट है?"
                    ),
                    "ml": (
                        "ഇനി മലയാളം മാത്രം. ഇംഗ്ലീഷ് അക്ഷരം വേണ്ട. ഷോഭ ഇൻട്രോ വീണ്ടും പറയേണ്ട. ആദ്യ വാചകം: "
                        "നമസ്കാരം. ഞാൻ നിങ്ങളെ എങ്ങനെയാണ് സഹായിക്കേണ്ടത്? "
                        "പുതിയ ടിക്കറ്റ് ആണോ വേണ്ടത്, അതോ നേരത്തെ ഉണ്ടാക്കിയ ടിക്കറ്റാണോ?"
                    ),
                    "ar": (
                        "تكلم عربي خليجي فقط. لا تعيد تعريف شوبا. أول جملة: "
                        "مرحبا. كيف أقدر أساعدك؟ تبي تذكرة جديدة، ولا في تذكرة موجودة من قبل؟"
                    ),
                }
                return speak.get(lang, f"Speak {name} only.")

        agent = SobhaAgent(
            instructions=SYSTEM_PROMPT,
            chat_ctx=initial_ctx,
        )


        room_opts = room_io.RoomOptions()
        if _HAS_NOISE_CANCELLATION and os.getenv("ENABLE_BVC", "0").strip() in ("1", "true", "True"):
            room_opts = room_io.RoomOptions(
                audio_input=room_io.AudioInputOptions(
                    noise_cancellation=noise_cancellation.BVC(),
                ),
            )

        print("[SOBHA] >>> Starting AgentSession in LiveKit room...", flush=True)
        await session.start(
            agent=agent,
            room=ctx.room,
            room_options=room_opts,
        )
        print(f"[SOBHA] >>> Agent session actively running for room {ctx.room.name}", flush=True)
        logger.info(f"Agent session actively running for room {ctx.room.name}")
    except Exception as e:
        print(f"[SOBHA] >>> ERROR in agent session: {e}", flush=True)
        logger.exception("Agent session failed")
        raise


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "download-files":
        print("Downloading Silero VAD model...", flush=True)
        silero.VAD.load()
        print("Done.", flush=True)
        sys.exit(0)
    _validate_env()
    print("[SOBHA] >>> Env validation OK (LiveKit, STT, TTS, LLM keys present)", flush=True)
    cli.run_app(server)
