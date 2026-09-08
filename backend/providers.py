from __future__ import annotations

import asyncio
import base64
import io
import json
import logging
import math
import struct
import wave
from abc import ABC, abstractmethod

import httpx

from .config import Settings

logger = logging.getLogger(__name__)


class LLMProvider(ABC):
    @abstractmethod
    async def generate(self, prompt: str, system: str = "", response_format: dict | None = None) -> str: ...


class OpenAICompatibleProvider(LLMProvider):
    def __init__(self, api_key: str, model: str, url: str, name: str):
        self.api_key, self.model, self.url, self.name = api_key, model, url, name

    async def generate(self, prompt: str, system: str = "", response_format: dict | None = None) -> str:
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        if self.name == "openrouter":
            headers.update({"HTTP-Referer": "https://miletus.app", "X-Title": "Miletus"})
        payload = {"model": self.model, "temperature": 0.35, "messages": [{"role": "system", "content": system}, {"role": "user", "content": prompt}]}
        if response_format:
            payload["response_format"] = response_format
        async with httpx.AsyncClient(timeout=120) as client:
            response = await client.post(self.url, headers=headers, json=payload)
            if response.is_error:
                logger.warning(
                    "llm_http_error",
                    extra={
                        "provider": self.name,
                        "status_code": response.status_code,
                        "response_body": response.text[:500],
                    },
                )
            response.raise_for_status()
            data = response.json()
        return data["choices"][0]["message"]["content"]


class GroqProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings.groq_api_key, settings.groq_model, "https://api.groq.com/openai/v1/chat/completions", "groq")


class OpenRouterProvider(OpenAICompatibleProvider):
    def __init__(self, settings: Settings):
        super().__init__(settings.openrouter_api_key, settings.openrouter_model, "https://openrouter.ai/api/v1/chat/completions", "openrouter")


class LLMManager:
    def __init__(self, settings: Settings):
        self.providers = [p for p in (GroqProvider(settings), OpenRouterProvider(settings)) if p.api_key]

    async def generate(self, prompt: str, system: str = "", response_format: dict | None = None) -> str:
        if not self.providers:
            raise RuntimeError("No LLM provider is configured")
        last_error: Exception | None = None
        for provider in self.providers:
            try:
                return await provider.generate(prompt, system, response_format)
            except (httpx.HTTPError, asyncio.TimeoutError, KeyError, IndexError) as error:
                last_error = error
                logger.warning("llm_provider_failed", extra={"provider": provider.name, "error_type": type(error).__name__})
        raise RuntimeError("All LLM providers failed") from last_error


class TTSProvider(ABC):
    @abstractmethod
    async def generate_audio(self, text: str, voice_id: str) -> bytes: ...


class ElevenLabsTTSProvider(TTSProvider):
    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate_audio(self, text: str, voice_id: str) -> bytes:
        from elevenlabs.client import ElevenLabs
        def request() -> bytes:
            client = ElevenLabs(api_key=self.settings.elevenlabs_api_key)
            result = client.text_to_speech.convert(text=text, voice_id=voice_id, model_id="eleven_multilingual_v2", output_format="mp3_44100_128")
            return b"".join(result)
        return await asyncio.to_thread(request)


class SpeechifyTTSProvider(TTSProvider):
    """Speechify adapter. The SDK is imported lazily so it remains optional."""

    def __init__(self, settings: Settings):
        self.settings = settings

    async def generate_audio(self, text: str, voice_id: str) -> bytes:
        from speechify import Speechify
        selected_voice = self.settings.speechify_teacher_voice_id if voice_id == "teacher" else self.settings.speechify_student_voice_id

        def request() -> bytes:
            try:
                client = Speechify(token=self.settings.speechify_api_key)
            except TypeError:
                # Newer SDK releases renamed this constructor argument.
                client = Speechify(api_key=self.settings.speechify_api_key)
            result = client.audio.speech(input=text, voice_id=selected_voice, model=self.settings.speechify_model, audio_format="mp3")
            data = result.audio_data
            return base64.b64decode(data) if isinstance(data, str) else bytes(data)

        return await asyncio.to_thread(request)


class KokoroTTSProvider(TTSProvider):
    """Local, free Kokoro fallback. Model initialization happens only once."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._pipeline = None
        self._lock = asyncio.Lock()

    async def generate_audio(self, text: str, voice_id: str) -> bytes:
        async with self._lock:
            if self._pipeline is None:
                from kokoro import KPipeline
                self._pipeline = KPipeline(lang_code="a")
        return await asyncio.to_thread(self._render, text, voice_id)

    def _render(self, text: str, voice_id: str) -> bytes:
        import soundfile as sf
        voice = self.settings.kokoro_teacher_voice if voice_id == "teacher" else self.settings.kokoro_student_voice
        chunks = []
        for _, _, audio in self._pipeline(text, voice=voice, speed=1.0):
            output = io.BytesIO()
            sf.write(output, audio, 24000, format="WAV", subtype="PCM_16")
            chunks.append(output.getvalue())
        if not chunks:
            raise RuntimeError("Kokoro returned no audio")
        return _concat_wav_bytes(chunks)


class Pyttsx3TTSProvider(TTSProvider):
    """Last-resort offline system voice fallback."""

    async def generate_audio(self, text: str, voice_id: str) -> bytes:
        return await asyncio.to_thread(self._render, text)

    @staticmethod
    def _render(text: str) -> bytes:
        import os
        import tempfile
        import pyttsx3
        fd, path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        try:
            engine = pyttsx3.init()
            engine.save_to_file(text, path)
            engine.runAndWait()
            data = open(path, "rb").read()
            if not data:
                raise RuntimeError("pyttsx3 returned no audio")
            return data
        finally:
            try:
                os.unlink(path)
            except FileNotFoundError:
                pass


class FallbackTTSProvider(TTSProvider):
    """Try ElevenLabs, then Speechify, then local free voices."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.providers: list[tuple[str, TTSProvider]] = []
        if settings.elevenlabs_api_key:
            self.providers.append(("elevenlabs", ElevenLabsTTSProvider(settings)))
        if settings.speechify_api_key:
            self.providers.append(("speechify", SpeechifyTTSProvider(settings)))
        self.providers.extend((("kokoro", KokoroTTSProvider(settings)), ("pyttsx3", Pyttsx3TTSProvider())))
        self.disabled: set[str] = set()

    async def generate_audio(self, text: str, voice_id: str) -> bytes:
        errors = []
        for name, provider in self.providers:
            if name in self.disabled:
                continue
            # One Speechify voice cannot produce distinct teacher/student voices.
            # Let the student use Kokoro in that case instead of silently using the teacher voice.
            if (
                name == "speechify"
                and voice_id == "student"
                and self.settings.speechify_teacher_voice_id == self.settings.speechify_student_voice_id
            ):
                continue
            try:
                provider_voice = voice_id
                if name == "elevenlabs":
                    provider_voice = self.settings.teacher_voice_id if voice_id == "teacher" else self.settings.student_voice_id
                audio = await provider.generate_audio(text, provider_voice)
                if audio:
                    logger.info("tts_provider_used", extra={"provider": name})
                    return audio
            except Exception as error:
                # An exhausted/invalid paid account should not be retried for every segment.
                self.disabled.add(name)
                errors.append(f"{name}: {type(error).__name__}")
                logger.warning("tts_provider_failed", extra={"provider": name, "error_type": type(error).__name__})
        raise RuntimeError("All TTS providers failed (" + ", ".join(errors) + ")")


class MockTTSProvider(TTSProvider):
    async def generate_audio(self, text: str, voice_id: str) -> bytes:
        return _tone_wav(max(0.35, min(4.0, len(text) / 70)), 440 if voice_id == "teacher" else 554)


def _tone_wav(duration: float, frequency: int) -> bytes:
    sample_rate, frames = 8000, int(8000 * duration)
    output = io.BytesIO()
    with wave.open(output, "wb") as file:
        file.setnchannels(1); file.setsampwidth(2); file.setframerate(sample_rate)
        for i in range(frames):
            value = int(7000 * math.sin(2 * math.pi * frequency * i / sample_rate) * (1 - i / frames))
            file.writeframes(struct.pack("<h", value))
    return output.getvalue()


def _concat_wav_bytes(chunks: list[bytes]) -> bytes:
    """Concatenate same-format WAV chunks without requiring ffmpeg."""
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        with wave.open(io.BytesIO(chunks[0]), "rb") as first:
            writer.setparams(first.getparams())
            writer.writeframes(first.readframes(first.getnframes()))
        for chunk in chunks[1:]:
            with wave.open(io.BytesIO(chunk), "rb") as part:
                writer.writeframes(part.readframes(part.getnframes()))
    return output.getvalue()


def parse_json_object(value: str) -> dict:
    value = value.strip().removeprefix("```json").removesuffix("```").strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM did not return a JSON object")
    return json.loads(value[start:end + 1])
