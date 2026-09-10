"""
sarvam_stt.py — Sarvam AI Saaras STT Plugin for LiveKit Agents
--------------------------------------------------------------
Accurate Indian language speech-to-text supporting Malayalam (ml-IN), Hindi (hi-IN),
and English (en-IN) with auto-detection ('unknown') or specific language codes.
Uses livekit.agents.stt.StreamAdapter wrapping SarvamSTT with Silero VAD to deliver
turn-based streaming audio frames cleanly to Sarvam's REST endpoint.
"""

from __future__ import annotations

import asyncio
import io
import os
import uuid
from dataclasses import dataclass
from typing import Optional

import aiohttp
from livekit import rtc
from livekit.agents import stt, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions

_BASE_URL = "https://api.sarvam.ai/speech-to-text"
_DEFAULT_MODEL = "saaras:v3"

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
    "auto": "unknown",
    "unknown": "unknown",
}


@dataclass
class _STTOpts:
    api_key: str
    model: str = _DEFAULT_MODEL
    language_code: str = "unknown"
    with_diarization: bool = False


class SarvamSTT(stt.STT):
    """
    LiveKit STT Plugin powered by Sarvam AI Saaras STT.
    Native high-accuracy Indian multilingual recognition (Hindi, Malayalam, English).
    """

    def __init__(
        self,
        *,
        api_key: Optional[str] = None,
        model: str = _DEFAULT_MODEL,
        language: str = "unknown",
        with_diarization: bool = False,
        http_session: Optional[aiohttp.ClientSession] = None,
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        resolved_key = api_key or os.environ.get("SARVAM_API_KEY", "")
        if not resolved_key:
            raise ValueError("Sarvam API key must be provided or set in SARVAM_API_KEY")

        lang_code = _LANG_MAP.get(language, language)
        self._opts = _STTOpts(
            api_key=resolved_key,
            model=model,
            language_code=lang_code,
            with_diarization=with_diarization,
        )
        self._session = http_session

    @property
    def model(self) -> str:
        return self._opts.model

    @property
    def provider(self) -> str:
        return "SarvamAI"

    def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = utils.http_context.http_session()
        return self._session

    def update_language(self, language: str):
        self._opts.language_code = _LANG_MAP.get(language, language)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: Optional[str] = None,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        lang_code = _LANG_MAP.get(language, self._opts.language_code) if language else self._opts.language_code
        
        # Combine frames into a single AudioFrame and export WAV bytes
        combined_frame = rtc.combine_audio_frames(buffer)
        wav_bytes = combined_frame.to_wav_bytes()

        form_data = aiohttp.FormData()
        form_data.add_field(
            "file",
            wav_bytes,
            filename="audio.wav",
            content_type="audio/wav",
        )
        form_data.add_field("model", self._opts.model)
        form_data.add_field("language_code", lang_code)
        if self._opts.with_diarization:
            form_data.add_field("with_diarization", "true")

        headers = {
            "api-subscription-key": self._opts.api_key,
        }

        session = self._ensure_session()
        try:
            async with session.post(
                _BASE_URL,
                data=form_data,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=conn_options.timeout or 15.0),
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    print(f"[SarvamSTT] Error {resp.status}: {body}", flush=True)
                    return stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                        alternatives=[
                            stt.SpeechData(
                                language=lang_code,
                                text="",
                                confidence=0.0,
                            )
                        ],
                    )

                data = await resp.json()
                transcript = data.get("transcript", "").strip()
                detected_lang = data.get("language_code", lang_code)
                prob = float(data.get("language_probability", 1.0))

                return stt.SpeechEvent(
                    type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                    request_id=data.get("request_id", str(uuid.uuid4())),
                    alternatives=[
                        stt.SpeechData(
                            language=detected_lang,
                            text=transcript,
                            confidence=prob,
                        )
                    ],
                )
        except Exception as e:
            print(f"[SarvamSTT] Request exception: {e}", flush=True)
            return stt.SpeechEvent(
                type=stt.SpeechEventType.FINAL_TRANSCRIPT,
                alternatives=[
                    stt.SpeechData(
                        language=lang_code,
                        text="",
                        confidence=0.0,
                    )
                ],
            )
