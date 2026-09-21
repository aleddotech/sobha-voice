"""
sarvam_tts.py — Sarvam AI Bulbul TTS Plugin for LiveKit Agents
--------------------------------------------------------------
Sarvam: Hindi, Malayalam.
ElevenLabs fallback: English, Arabic (including mid-conversation switches).
"""

from __future__ import annotations

import base64
import os
import re
from dataclasses import dataclass
from typing import Optional

import aiohttp
from livekit.agents import tts, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

_BASE_URL = "https://api.sarvam.ai/text-to-speech"
_DEFAULT_MODEL = "bulbul:v3"
_DEFAULT_SPEAKER = "ishita"

# Sarvam: Hindi + Malayalam. ElevenLabs: English (Indian accent) + Arabic.
_SARVAM_LANGS = {"hi", "hi-IN", "ml", "ml-IN"}
_ELEVENLABS_LANGS = {"en", "en-IN", "ar", "ar-SA", "ar-AE"}
_SPEAKER_BY_LANG = {
    "en": "ishita",
    "en-IN": "ishita",
    "hi": "priya",
    "hi-IN": "priya",
    "ml": "pooja",
    "ml-IN": "pooja",
}

# Map language codes to Sarvam target_language_code
_LANG_MAP = {
    "en": "en-IN",
    "en-IN": "en-IN",
    "hi": "hi-IN",
    "hi-IN": "hi-IN",
    "ml": "ml-IN",
    "ml-IN": "ml-IN",
    "ta": "ta-IN",
    "ta-IN": "ta-IN",
    "te": "te-IN",
    "te-IN": "te-IN",
    "bn": "bn-IN",
    "bn-IN": "bn-IN",
    "kn": "kn-IN",
    "kn-IN": "kn-IN",
    "gu": "gu-IN",
    "gu-IN": "gu-IN",
    "mr": "mr-IN",
    "mr-IN": "mr-IN",
    "pa": "pa-IN",
    "pa-IN": "pa-IN",
    "ur": "ur-IN",
    "ur-IN": "ur-IN",
}


def detect_text_language(text: str) -> Optional[str]:
    """Detects target language based on Unicode script ranges."""
    if re.search(r"[\u0D00-\u0D7F]", text):
        return "ml-IN"
    if re.search(r"[\u0900-\u097F]", text):
        return "hi-IN"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    return None


def _uses_elevenlabs(lang: str) -> bool:
    if not lang:
        return True
    if lang in _ELEVENLABS_LANGS or lang.startswith("ar") or lang.startswith("en"):
        return True
    if lang in _SARVAM_LANGS or lang in ("hi-IN", "ml-IN"):
        return False
    return lang.startswith("en")


def _pace_for(lang: str, default: float) -> float:
    # Malayalam a touch slower so the synthesis is easier to follow.
    if (lang or "").startswith("ml"):
        return 0.92
    return default


@dataclass
class _TTSOpts:
    api_key: str
    speaker: str = _DEFAULT_SPEAKER
    model: str = _DEFAULT_MODEL
    target_language_code: str = "en-IN"
    pace: float = 1.0


class _ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: "SarvamTTS", input_text: str, conn_options: APIConnectOptions):
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self._sarvam_tts = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        opts = self._sarvam_tts._opts

        detected = detect_text_language(self._input_text)
        if detected in ("hi-IN", "ml-IN"):
            target_lang = detected
        elif detected == "ar":
            target_lang = "ar"
        else:
            target_lang = opts.target_language_code

        if self._sarvam_tts._fallback_tts and _uses_elevenlabs(target_lang):
            print(f"[TTS] ElevenLabs Indian accent ({target_lang})", flush=True)
            try:
                fallback_stream = self._sarvam_tts._fallback_tts.synthesize(
                    self._input_text, conn_options=self._conn_options
                )
                await fallback_stream._run(output_emitter)
                return
            except Exception as e:
                print(f"[TTS] ElevenLabs failed ({e}); Sarvam Indian English fallback", flush=True)

        speaker = _SPEAKER_BY_LANG.get(target_lang, opts.speaker)
        pace = _pace_for(target_lang, opts.pace)
        print(f"[TTS] Sarvam {speaker} ({target_lang}) pace={pace}", flush=True)

        payload = {
            "inputs": [self._input_text],
            "target_language_code": target_lang if target_lang in _LANG_MAP.values() else _LANG_MAP.get(target_lang, "en-IN"),
            "speaker": speaker,
            "model": opts.model,
            "pace": pace,
        }

        headers = {
            "api-subscription-key": opts.api_key,
            "Content-Type": "application/json",
        }

        session = self._sarvam_tts._ensure_session()
        async with session.post(_BASE_URL, json=payload, headers=headers) as resp:
            if resp.status != 200:
                body = await resp.text()
                # If Sarvam fails and we have fallback, try fallback
                if self._sarvam_tts._fallback_tts:
                    print("[TTS] Sarvam failed, falling back to ElevenLabs", flush=True)
                    fallback_stream = self._sarvam_tts._fallback_tts.synthesize(
                        self._input_text, conn_options=self._conn_options
                    )
                    await fallback_stream._run(output_emitter)
                    return
                raise Exception(f"Sarvam TTS API returned status {resp.status}: {body}")

            data = await resp.json()
            audios = data.get("audios", [])
            if not audios:
                raise Exception("Sarvam TTS returned empty audios array")

            wav_bytes = base64.b64decode(audios[0])

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=22050,
            num_channels=1,
            mime_type="audio/wav",
        )

        chunk_size = 4096
        for i in range(0, len(wav_bytes), chunk_size):
            output_emitter.push(wav_bytes[i : i + chunk_size])

        output_emitter.flush()


class SarvamTTS(tts.TTS):
    """
    LiveKit TTS Plugin powered by Sarvam AI Bulbul TTS.
    Sarvam female voices for Indian English, Hindi, Malayalam.
    ElevenLabs female fallback for Arabic.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        speaker: str = _DEFAULT_SPEAKER,
        language: str = "en",
        model: str = _DEFAULT_MODEL,
        pace: float = 1.0,
        fallback_tts: Optional[tts.TTS] = None,
        http_session: Optional[aiohttp.ClientSession] = None,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=22050,
            num_channels=1,
        )
        resolved_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        if not resolved_key:
            raise ValueError("Sarvam API key must be provided or set in SARVAM_API_KEY")

        self._opts = _TTSOpts(
            api_key=resolved_key,
            speaker=speaker,
            model=model,
            target_language_code=_LANG_MAP.get(language, language),
            pace=pace,
        )
        self._fallback_tts = fallback_tts
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = utils.http_context.http_session()
        return self._session

    def update_language(self, language: str):
        lang = (language or "en").strip()
        self._opts.target_language_code = _LANG_MAP.get(lang, lang)
        speaker = _SPEAKER_BY_LANG.get(lang) or _SPEAKER_BY_LANG.get(self._opts.target_language_code)
        if speaker:
            self._opts.speaker = speaker
        if (lang or "").startswith("ml"):
            self._opts.pace = 0.92
        else:
            self._opts.pace = 1.0
        if self._fallback_tts and hasattr(self._fallback_tts, "update_options"):
            el_lang = "ar" if lang.startswith("ar") else "en"
            try:
                self._fallback_tts.update_options(language=el_lang)
            except Exception:
                pass

    def synthesize(
        self,
        text: str,
        *,
        conn_options: Optional[APIConnectOptions] = None,
    ) -> "tts.ChunkedStream":
        return _ChunkedStream(
            tts_instance=self,
            input_text=text,
            conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS,
        )
