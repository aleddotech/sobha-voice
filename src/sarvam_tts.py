"""
sarvam_tts.py — Sarvam AI Bulbul TTS Plugin for LiveKit Agents
--------------------------------------------------------------
High-quality, ultra-natural Indian language text-to-speech engine.
Supports en-IN, hi-IN, ml-IN (Malayalam), and other Indian languages natively.
Includes automatic script detection (Malayalam, Hindi, English) and fallback
to ElevenLabs for Arabic (ar) or unsupported scripts.
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
_DEFAULT_SPEAKER = "ritu"

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

        # Auto-detect script if present in text, else use configured language
        detected = detect_text_language(self._input_text)
        target_lang = detected if (detected and detected in _LANG_MAP.values()) else opts.target_language_code

        # If Arabic or non-Indian language detected and fallback TTS is available
        if (detected == "ar" or target_lang == "ar") and self._sarvam_tts._fallback_tts:
            fallback_stream = self._sarvam_tts._fallback_tts.synthesize(
                self._input_text, conn_options=self._conn_options
            )
            async for ev in fallback_stream:
                if hasattr(ev, "frame") and ev.frame:
                    output_emitter.push(ev.frame.data.tobytes())
            output_emitter.flush()
            return

        payload = {
            "inputs": [self._input_text],
            "target_language_code": target_lang,
            "speaker": opts.speaker,
            "model": opts.model,
            "pace": opts.pace,
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
                    fallback_stream = self._sarvam_tts._fallback_tts.synthesize(
                        self._input_text, conn_options=self._conn_options
                    )
                    async for ev in fallback_stream:
                        if hasattr(ev, "frame") and ev.frame:
                            output_emitter.push(ev.frame.data.tobytes())
                    output_emitter.flush()
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
    Specialized for natural Indian languages (Malayalam, Hindi, English).
    Supports seamless fallback to another TTS (e.g. ElevenLabs) for Arabic.
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        speaker: str = _DEFAULT_SPEAKER,
        language: str = "en-IN",
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

        target_lang = _LANG_MAP.get(language, language)
        self._opts = _TTSOpts(
            api_key=resolved_key,
            speaker=speaker,
            model=model,
            target_language_code=target_lang,
            pace=pace,
        )
        self._fallback_tts = fallback_tts
        self._session = http_session

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = utils.http_context.http_session()
        return self._session

    def update_language(self, language: str):
        target_lang = _LANG_MAP.get(language, language)
        self._opts.target_language_code = target_lang

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
