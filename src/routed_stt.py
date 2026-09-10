"""
Route STT by language.

Deepgram Nova-3: English, Arabic
Sarvam Saaras: Hindi, Malayalam

Non-streaming; wrap with StreamAdapter + VAD for turn-based audio.
"""

from __future__ import annotations

from livekit.agents import stt, utils
from livekit.agents.types import DEFAULT_API_CONNECT_OPTIONS, APIConnectOptions, NOT_GIVEN, NotGivenOr

_SARVAM_LANGS = {"hi", "hi-in", "ml", "ml-in"}


class RoutedSTT(stt.STT):
    def __init__(
        self,
        *,
        sarvam: stt.STT,
        deepgram: stt.STT,
        language: str = "en",
    ):
        super().__init__(
            capabilities=stt.STTCapabilities(
                streaming=False,
                interim_results=False,
            )
        )
        self._sarvam = sarvam
        self._deepgram = deepgram
        self._backend = "deepgram"
        self._lang = "en"
        self.update_language(language)

    @property
    def model(self) -> str:
        return f"routed/{self._backend}"

    @property
    def provider(self) -> str:
        return "Deepgram" if self._backend == "deepgram" else "SarvamAI"

    def update_language(self, language: str) -> None:
        lang = (language or "en").strip().lower()
        if lang in _SARVAM_LANGS:
            self._backend = "sarvam"
            self._lang = "ml" if lang.startswith("ml") else "hi"
            if hasattr(self._sarvam, "update_language"):
                self._sarvam.update_language(self._lang)
        elif lang.startswith("ar"):
            self._backend = "deepgram"
            self._lang = "ar"
            if hasattr(self._deepgram, "update_options"):
                self._deepgram.update_options(language="ar")
        else:
            self._backend = "deepgram"
            self._lang = "en"
            if hasattr(self._deepgram, "update_options"):
                self._deepgram.update_options(language="en-IN")
        print(f"[STT] backend={self._backend} lang={self._lang}", flush=True)

    async def _recognize_impl(
        self,
        buffer: utils.AudioBuffer,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.SpeechEvent:
        use_sarvam = self._backend == "sarvam"
        engine = self._sarvam if use_sarvam else self._deepgram
        print(f"[STT] recognize via {self._backend} ({self._lang})", flush=True)
        try:
            return await engine.recognize(buffer=buffer, conn_options=conn_options)
        except Exception as e:
            if not use_sarvam:
                print(f"[STT] Deepgram failed ({e}); falling back to Sarvam", flush=True)
                return await self._sarvam.recognize(buffer=buffer, conn_options=conn_options)
            raise
