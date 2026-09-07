from __future__ import annotations

import asyncio
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


def parse_json_object(value: str) -> dict:
    value = value.strip().removeprefix("```json").removesuffix("```").strip()
    start, end = value.find("{"), value.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("LLM did not return a JSON object")
    return json.loads(value[start:end + 1])
