"""
google_tts.py — Google Cloud Text-to-Speech plugin for LiveKit Agents
----------------------------------------------------------------------
Implements livekit.agents.tts.TTS + ChunkedStream using the
Google Cloud TTS REST API (API-key based, no service account needed).

Language → voice mapping (female, high quality):
    en → en-US-Chirp3-HD-Aoede    (Neural)
    hi → hi-IN-Chirp3-HD-Aoede    (Neural)
    ar → ar-XA-Chirp3-HD-Aoede    (Neural)
    ml → ml-IN-Chirp3-HD-Aoede    (WaveNet — best ML quality)
"""

from __future__ import annotations

import asyncio
import base64
import os
import time
from dataclasses import dataclass, replace
from typing import AsyncIterator, Optional

import aiohttp
from livekit.agents import tts, utils
from livekit.agents.tts import SynthesizeStream, SynthesizedAudio
from livekit.agents.utils import AudioBuffer
from livekit import rtc

# ──────────────────────────────────────────────────────────────────────────────
# Voice config per language tag
# ──────────────────────────────────────────────────────────────────────────────

_VOICE_MAP = {
    "en": {"languageCode": "en-US", "name": "en-US-Chirp3-HD-Aoede", "ssmlGender": "FEMALE"},
    "hi": {"languageCode": "hi-IN", "name": "hi-IN-Chirp3-HD-Aoede", "ssmlGender": "FEMALE"},
    "ar": {"languageCode": "ar-XA", "name": "ar-XA-Chirp3-HD-Aoede", "ssmlGender": "FEMALE"},
    "ml": {"languageCode": "ml-IN", "name": "ml-IN-Chirp3-HD-Aoede", "ssmlGender": "FEMALE"},
}

_DEFAULT_LANG = "en"
_SAMPLE_RATE = 24000
_TTS_URL = "https://texttospeech.googleapis.com/v1/text:synthesize"

# ──────────────────────────────────────────────────────────────────────────────
# Options
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class _TTSOpts:
    api_key: str
    language: str = _DEFAULT_LANG
    speaking_rate: float = 1.0

# ──────────────────────────────────────────────────────────────────────────────
# ChunkedStream (non-streaming REST call, returns full MP3 in one shot)
# ──────────────────────────────────────────────────────────────────────────────

class _ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: "GoogleTTS", input_text: str, conn_options, language: str):
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self._language = language
        self._google_tts = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        api_key = self._google_tts._opts.api_key
        lang = self._language or self._google_tts._opts.language
        voice_cfg = _VOICE_MAP.get(lang, _VOICE_MAP[_DEFAULT_LANG])

        payload = {
            "input": {"text": self._input_text},
            "voice": voice_cfg,
            "audioConfig": {
                "audioEncoding": "LINEAR16",
                "sampleRateHertz": _SAMPLE_RATE,
                "speakingRate": self._google_tts._opts.speaking_rate,
            },
        }

        url = f"{_TTS_URL}?key={api_key}"

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    raise Exception(f"Google TTS error {resp.status}: {body}")
                data = await resp.json()

        audio_bytes = base64.b64decode(data["audioContent"])

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/l16",
        )

        # Push in 4KB chunks
        chunk_size = 4096
        for i in range(0, len(audio_bytes), chunk_size):
            output_emitter.push(audio_bytes[i:i + chunk_size])

        output_emitter.flush()

# ──────────────────────────────────────────────────────────────────────────────
# TTS plugin
# ──────────────────────────────────────────────────────────────────────────────

class GoogleTTS(tts.TTS):
    """
    Google Cloud TTS LiveKit plugin.
    Set the active language at any time via `.set_language(lang)` where
    lang ∈ {"en", "hi", "ar", "ml"}.
    """

    def __init__(
        self,
        *,
        api_key: str,
        language: str = _DEFAULT_LANG,
        speaking_rate: float = 1.0,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_SAMPLE_RATE,
            num_channels=1,
        )
        self._opts = _TTSOpts(
            api_key=api_key,
            language=language,
            speaking_rate=speaking_rate,
        )

    def set_language(self, lang: str) -> None:
        """Dynamically switch language (en / hi / ar / ml)."""
        if lang in _VOICE_MAP:
            self._opts = replace(self._opts, language=lang)
        else:
            print(f"[GoogleTTS] Unknown language '{lang}', keeping '{self._opts.language}'")

    def synthesize(
        self,
        text: str,
        *,
        conn_options=None,
        language: Optional[str] = None,
    ) -> _ChunkedStream:
        from livekit.agents.tts.tts import DEFAULT_API_CONNECT_OPTIONS
        return _ChunkedStream(
            tts_instance=self,
            input_text=text,
            conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS,
            language=language or self._opts.language,
        )
