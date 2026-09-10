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

server = AgentServer()

def prewarm(proc):
    """Prewarm Silero VAD model with aggressive threshold for instant speech/interruption detection."""
    proc.userdata["vad"] = silero.VAD.load(
        min_speech_duration=0.05,
        min_silence_duration=0.3,
        prefix_padding_duration=0.2,
        activation_threshold=0.45,
    )

server.setup_fnc = prewarm

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "db.json")

def _load_db() -> list:
    try:
        with open(DB_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Could not load DB: {e}")
        return []

def _save_db(db: list):
    try:
        with open(DB_PATH, "w") as f:
            json.dump(db, f, indent=2)
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

    try:
        initial_ctx = llm.ChatContext()
        initial_ctx.add_message(
            content=SYSTEM_PROMPT,
            role="system",
        )

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
                    "action_delay_seconds": action_delay_seconds,
                    "created_at": now,
                }
                db.append(record)
                _save_db(db)
                print(f"[SOBHA] >>> Logged transport ticket {new_id} -> Situation {chosen['situation']}", flush=True)
                return json.dumps(record, indent=2)

            @llm.function_tool(
                description="Look up existing maintenance requests or transportation tickets by ticket ID or resident name."
            )
            async def lookup_requests(
                self,
                ticket_id: str | None = None,
                name_or_id: str | None = None,
            ) -> str:
                logger.info(f"Lookup requests: ticket_id={ticket_id}, name_or_id={name_or_id}")
                db = _load_db()
                matches = []
                for r in db:
                    if ticket_id and r.get("ticket_id", "").lower() == ticket_id.strip().lower():
                        matches.append(r)
                        continue
                    if name_or_id:
                        val = name_or_id.strip().lower()
                        if val in r.get("resident_name", "").lower() or val in r.get("name_or_emp_id", "").lower() or val in r.get("unit_number", "").lower():
                            matches.append(r)
                            continue
                if not matches:
                    return "No tickets found matching the provided details."
                return json.dumps(matches[:3], indent=2)

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
                    "action_delay_seconds": action_delay_seconds,
                    "created_at": now,
                }
                db.append(ticket)
                _save_db(db)
                print(f"[SOBHA] >>> Ticket {new_id} successfully created.", flush=True)
                return json.dumps(ticket, indent=2)

        sobha_tools = SobhaTools()
        vad = ctx.proc.userdata.get("vad") or silero.VAD.load(
            min_speech_duration=0.05,
            min_silence_duration=0.3,
            prefix_padding_duration=0.2,
            activation_threshold=0.45,
        )

        # ElevenLabs for English + Arabic; Sarvam for Hindi + Malayalam
        elevenlabs_tts = elevenlabs.TTS(
            model="eleven_multilingual_v2",
            voice_id=config("ELEVEN_VOICE_ID", default="i80JxxvpWr5Q7cTdT1Ik"),
            api_key=config("ELEVEN_API_KEY"),
            streaming_latency=4,
        )

        sarvam_tts_engine = SarvamTTS(
            api_key=config("SARVAM_API_KEY"),
            speaker="ritu",
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
            "[SOBHA] >>> TTS: Sarvam (hi, ml) | ElevenLabs (en, ar). "
            "STT: Sarvam (hi, ml) | Deepgram Nova-3 (en, ar)",
            flush=True,
        )

        session = AgentSession(
            stt=stt_adapter,
            llm=openai.LLM(
                api_key=config("OPENAI_API_KEY"),
                model="gpt-4o-mini",
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
            ],
        )

        class SobhaAgent(Agent):
            async def on_enter(self) -> None:
                print("[SOBHA] >>> Agent on_enter called, speaking welcome greeting", flush=True)
                self.session.say(
                    "Thank you for reaching to Sobha agent. Which language do you prefer? We support Hindi, English, Malayalam, and Arabic."
                )

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
                tts_engine = "ElevenLabs" if lang in ("en", "ar") else "Sarvam"
                stt_engine = "Deepgram" if lang in ("en", "ar") else "Sarvam"
                print(
                    f"[SOBHA] >>> Language switched to {name} ({lang}); "
                    f"TTS={tts_engine} STT={stt_engine}",
                    flush=True,
                )
                return f"Language switched to {name}. Please respond in {name} from now on."

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
