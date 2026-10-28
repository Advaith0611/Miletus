"""The small, self-contained document-to-podcast pipeline used by the API.

This intentionally does not import Streamlit or ``app.py``.  The Streamlit
app remains available as the original prototype, while this module is safe to
run inside a web server.
"""

from __future__ import annotations

import asyncio
import io
import os
import subprocess
import tempfile
from collections.abc import Iterable
from typing import Any

import fitz
import httpx
import numpy as np
import soundfile as sf
from docx import Document
from google import genai
from google.genai import types
from pydantic import BaseModel, Field


class PodcastScript(BaseModel):
    title: str = Field(description="A short, engaging episode title")
    overview: str = Field(description="A one- or two-sentence episode summary")
    host_texts: list[str] = Field(min_length=5, max_length=12)
    expert_texts: list[str] = Field(min_length=5, max_length=12)


def extract_text(filename: str, content: bytes) -> str:
    """Extract text from the three formats accepted by the original app."""
    extension = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if extension == "pdf":
        with fitz.open(stream=content, filetype="pdf") as pdf:
            return "\n\n".join(page.get_text() for page in pdf)
    if extension == "docx":
        document = Document(io.BytesIO(content))
        return "\n".join(p.text for p in document.paragraphs if p.text.strip())
    if extension == "md":
        return content.decode("utf-8", errors="replace")
    raise ValueError(f"Unsupported file type: .{extension or 'unknown'}")


def _prompt(documents: Iterable[tuple[str, str]]) -> str:
    source_notes = "\n\n".join(
        f"--- Document: {name} ---\n{text}" for name, text in documents
    )
    return f"""
Create a polished educational podcast script from the source notes below.

The podcast has exactly TWO speakers: HOST and EXPERT. HOST is curious and
warm, and asks the questions. EXPERT is knowledgeable and answers them.

Return a JSON object with exactly these fields:
- title: a short, engaging episode title
- overview: a one- or two-sentence episode summary
- host_texts: 5 to 12 questions from HOST
- expert_texts: 5 to 12 matching answers from EXPERT

Both lists must have the same length. host_texts[i] must be answered by
expert_texts[i]. Do not include speaker labels, stage directions, headings,
citations, markdown, or any other speaker. Use only facts supported by the
source notes; if they are incomplete, say so briefly rather than guessing.
Define technical terms plainly, use a logical teaching sequence, and conclude
with a recap. Keep each line natural and comfortable to read aloud.

SOURCE NOTES:
{source_notes}
"""


async def create_script(documents: list[tuple[str, str]]) -> PodcastScript:
    """Use the first configured LLM provider, with fallbacks."""
    prompt = _prompt(documents)
    failures: list[str] = []

    try:
        key = os.getenv("GEMINI_API_KEY")
        if not key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        client = genai.Client(api_key=key)
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json", response_schema=PodcastScript
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini returned an empty response")
        return PodcastScript.model_validate_json(response.text)
    except Exception as error:  # Try the compatible providers below.
        failures.append(f"Gemini: {error}")

    providers = (
        ("Groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1/chat/completions", os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")),
        ("OpenRouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1/chat/completions", os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")),
    )
    for provider, key_name, endpoint, model in providers:
        try:
            key = os.getenv(key_name)
            if not key:
                raise RuntimeError(f"{key_name} is not configured")
            headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
            if provider == "OpenRouter":
                headers["HTTP-Referer"] = os.getenv("FRONTEND_URL", "http://localhost:5173")
                headers["X-Title"] = "Miletus"
            payload: dict[str, Any] = {
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            }
            async with httpx.AsyncClient(timeout=120) as client:
                response = await client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            return PodcastScript.model_validate_json(content)
        except Exception as error:
            failures.append(f"{provider}: {error}")
    raise RuntimeError("No AI provider could create the script. " + "; ".join(failures))


def synthesize_episode(script: PodcastScript) -> bytes:
    """Generate an interleaved two-voice WAV, matching the Streamlit app."""
    if len(script.host_texts) != len(script.expert_texts):
        raise ValueError("The generated dialogue has mismatched turns.")
    sample_rate = 24_000
    pause = np.zeros(int(sample_rate * 0.25), dtype=np.float32)
    parts: list[np.ndarray] = []
    for host, expert in zip(script.host_texts, script.expert_texts, strict=True):
        parts.extend((
            synthesize_text(host, os.getenv("ELEVENLABS_TEACHER_VOICE_ID", ""), os.getenv("PIPER_HOST_VOICE", ""), os.getenv("SPEECHIFY_TEACHER_VOICE_ID", "geffen_32")),
            pause,
            synthesize_text(expert, os.getenv("ELEVENLABS_STUDENT_VOICE_ID", ""), os.getenv("PIPER_EXPERT_VOICE", ""), os.getenv("SPEECHIFY_STUDENT_VOICE_ID", "geffen_32")),
            pause,
        ))
    buffer = io.BytesIO()
    sf.write(buffer, np.concatenate(parts), sample_rate, format="WAV")
    return buffer.getvalue()


def synthesize_text(text: str, eleven_voice: str, piper_voice: str, speechify_voice: str) -> np.ndarray:
    errors: list[str] = []
    for name, voice in (("ElevenLabs", eleven_voice), ("Speechify", speechify_voice), ("Piper", piper_voice), ("eSpeak NG", "")):
        try:
            if name == "ElevenLabs":
                return _elevenlabs_audio(text, voice)
            if name == "Speechify":
                return _speechify_audio(text, voice)
            if name == "Piper":
                return _piper_audio(text, voice)
            return _espeak_audio(text)
        except Exception as error:
            errors.append(f"{name}: {error}")
    raise RuntimeError("No speech provider succeeded. " + "; ".join(errors))


def _elevenlabs_audio(text: str, voice_id: str) -> np.ndarray:
    key = os.getenv("ELEVENLABS_API_KEY")
    if not key or not voice_id:
        raise RuntimeError("API key or voice ID is not configured")
    from elevenlabs.client import ElevenLabs

    audio = ElevenLabs(api_key=key).text_to_speech.convert(
        text=text, voice_id=voice_id, model_id=os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2"), output_format="pcm_24000"
    )
    raw = b"".join(audio) if not isinstance(audio, bytes) else audio
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768


def _speechify_audio(text: str, voice_id: str) -> np.ndarray:
    key = os.getenv("SPEECHIFY_API_KEY")
    if not key:
        raise RuntimeError("SPEECHIFY_API_KEY is not configured")
    from speechify import Speechify

    try:
        client = Speechify(token=key)
    except TypeError:
        client = Speechify(api_key=key)
    result = client.audio.speech(input=text, voice_id=voice_id, model=os.getenv("SPEECHIFY_MODEL", "simba-3.2"), audio_format="wav")
    return _wav_to_array(result.audio_data)


def _piper_audio(text: str, voice: str) -> np.ndarray:
    model = os.getenv("PIPER_MODEL_PATH")
    if not model or not os.path.exists(model):
        raise RuntimeError("PIPER_MODEL_PATH is not configured")
    with tempfile.NamedTemporaryFile(suffix=".wav") as output:
        command = ["piper", "--model", model, "--output_file", output.name]
        if voice:
            command.extend(("--speaker", voice))
        subprocess.run(command, input=text.encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        output.seek(0)
        return _wav_to_array(output.read())


def _espeak_audio(text: str) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav") as output:
        subprocess.run(["espeak-ng", "-w", output.name, "-v", "en-us", text], stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        output.seek(0)
        return _wav_to_array(output.read())


def _wav_to_array(raw: bytes) -> np.ndarray:
    audio, sample_rate = sf.read(io.BytesIO(raw), dtype="float32")
    audio = np.asarray(audio, dtype=np.float32)
    if audio.ndim > 1:
        audio = audio.mean(axis=1)
    if sample_rate == 24_000:
        return audio
    old = np.linspace(0, 1, len(audio), endpoint=False)
    length = max(1, round(len(audio) * 24_000 / sample_rate))
    return np.interp(np.linspace(0, 1, length, endpoint=False), old, audio).astype(np.float32)
