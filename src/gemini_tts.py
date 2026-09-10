"""
gemini_tts.py — Gemini TTS Voice Plugin for LiveKit Agents
-----------------------------------------------------------
Uses the Google Gemini REST API (gemini-3.1-flash-tts-preview / gemini-2.5-flash-preview-tts)
with responseModalities: ["AUDIO"] to generate native high-fidelity audio.
Works with the user's Gemini AI Studio API key.
"""

from __future__ import annotations

import asyncio
import base64
import io
import os
import wave
from dataclasses import dataclass, replace
from typing import Optional

import aiohttp
from livekit.agents import tts, utils

_SAMPLE_RATE = 24000
_MODEL = "gemini-3.1-flash-tts-preview"
_FALLBACK_MODEL = "gemini-2.5-flash-preview-tts"
_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"

# Default female voice: Aoede
_DEFAULT_VOICE = "Aoede"

# Language prompt hints to ensure proper accent & pronunciation
_LANG_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "ar": "Arabic",
    "ml": "Malayalam",
}


def _pcm_to_wav(pcm_bytes: bytes, sample_rate: int = _SAMPLE_RATE, num_channels: int = 1) -> bytes:
    """Wraps raw 16-bit PCM bytes into a standard WAV container for PyAV/LiveKit decoder."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(num_channels)
        wf.setsampwidth(2)  # 16-bit
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_bytes)
    return buf.getvalue()


@dataclass
class _TTSOpts:
    api_key: str
    voice: str = _DEFAULT_VOICE
    language: str = "en"
    model: str = _MODEL


class _ChunkedStream(tts.ChunkedStream):
    def __init__(self, *, tts_instance: "GeminiTTS", input_text: str, conn_options, language: str):
        super().__init__(tts=tts_instance, input_text=input_text, conn_options=conn_options)
        self._language = language
        self._gemini_tts = tts_instance

    async def _run(self, output_emitter: tts.AudioEmitter) -> None:
        api_key = self._gemini_tts._opts.api_key
        voice = self._gemini_tts._opts.voice
        model = self._gemini_tts._opts.model

        payload = {
            "contents": [
                {
                    "parts": [{"text": self._input_text}]
                }
            ],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "speechConfig": {
                    "voiceConfig": {
                        "prebuiltVoiceConfig": {
                            "voiceName": voice
                        }
                    }
                }
            }
        }

        url = f"{_BASE_URL}/{model}:generateContent?key={api_key}"

        audio_bytes = None
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    try:
                        candidates = data.get("candidates", [])
                        if candidates:
                            parts = candidates[0].get("content", {}).get("parts", [])
                            for p in parts:
                                if "inlineData" in p and p["inlineData"].get("data"):
                                    audio_bytes = base64.b64decode(p["inlineData"]["data"])
                                    break
                    except Exception as parse_err:
                        print(f"[GeminiTTS] Parse error: {parse_err}", flush=True)
                else:
                    body = await resp.text()
                    print(f"[GeminiTTS] Primary model {model} returned {resp.status}: {body}, trying fallback {_FALLBACK_MODEL}", flush=True)
                    fallback_url = f"{_BASE_URL}/{_FALLBACK_MODEL}:generateContent?key={api_key}"
                    async with session.post(fallback_url, json=payload) as fb_resp:
                        if fb_resp.status == 200:
                            fb_data = await fb_resp.json()
                            candidates = fb_data.get("candidates", [])
                            if candidates:
                                parts = candidates[0].get("content", {}).get("parts", [])
                                for p in parts:
                                    if "inlineData" in p and p["inlineData"].get("data"):
                                        audio_bytes = base64.b64decode(p["inlineData"]["data"])
                                        break
                        else:
                            fb_body = await fb_resp.text()
                            raise Exception(f"Gemini TTS failed on both models ({resp.status}, {fb_resp.status}): {fb_body}")

        if not audio_bytes:
            raise Exception("Gemini TTS response did not contain audio data")

        # Convert raw PCM into standard WAV so LiveKit's AudioStreamDecoder parses it cleanly
        wav_bytes = _pcm_to_wav(audio_bytes, sample_rate=_SAMPLE_RATE, num_channels=1)

        output_emitter.initialize(
            request_id=utils.shortuuid(),
            sample_rate=_SAMPLE_RATE,
            num_channels=1,
            mime_type="audio/wav",
        )

        chunk_size = 4096
        for i in range(0, len(wav_bytes), chunk_size):
            output_emitter.push(wav_bytes[i:i + chunk_size])

        output_emitter.flush()


class GeminiTTS(tts.TTS):
    """
    LiveKit TTS Plugin powered by Google Gemini TTS (gemini-3.1-flash-tts-preview).
    """

    def __init__(
        self,
        *,
        api_key: str,
        voice: str = _DEFAULT_VOICE,
        language: str = "en",
        model: str = _MODEL,
    ):
        super().__init__(
            capabilities=tts.TTSCapabilities(streaming=False),
            sample_rate=_SAMPLE_RATE,
            num_channels=1,
        )
        self._opts = _TTSOpts(
            api_key=api_key,
            voice=voice,
            language=language,
            model=model,
        )

    def set_language(self, lang: str) -> None:
        """Set active language."""
        if lang in _LANG_NAMES:
            self._opts = replace(self._opts, language=lang)
            print(f"[GeminiTTS] Language set to {_LANG_NAMES[lang]} ({lang})", flush=True)

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
