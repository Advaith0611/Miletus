import io
import asyncio
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from typing import List

import fitz  # PyMuPDF
import httpx
import numpy as np
import streamlit as st
from docx import Document
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from google import genai
from google.genai import types

import soundfile as sf

load_dotenv()


class PodcastScript(BaseModel):
    title: str = Field(description="A short, engaging episode title")
    overview: str = Field(description="A one- or two-sentence episode summary")
    host_texts: List[str] = Field(
        description="Host questions in order", min_length=5, max_length=12
    )
    expert_texts: List[str] = Field(
        description="Expert answers matching host_texts by index",
        min_length=5,
        max_length=12,
    )


def extract_text(uploaded_file) -> str:
    """Extract all text from a PDF, DOCX, or Markdown upload."""
    file_type = uploaded_file.name.rsplit(".", 1)[-1].lower()
    file_bytes = uploaded_file.getvalue()

    if file_type == "pdf":
        with fitz.open(stream=file_bytes, filetype="pdf") as pdf:
            return "\n\n".join(page.get_text() for page in pdf)

    if file_type == "docx":
        document = Document(io.BytesIO(file_bytes))
        return "\n".join(
            paragraph.text for paragraph in document.paragraphs if paragraph.text
        )

    if file_type == "md":
        return file_bytes.decode("utf-8", errors="replace")

    raise ValueError(f"Unsupported file type: .{file_type}")


st.title("Document text extractor")

uploaded_files = st.file_uploader(
    "Upload PDF, DOCX, or Markdown files",
    type=["pdf", "docx", "md"],
    accept_multiple_files=True,
)

extracted_documents = []

if uploaded_files:
    for uploaded_file in uploaded_files:
        try:
            text = extract_text(uploaded_file)
            extracted_documents.append((uploaded_file.name, text))
        except Exception as error:
            st.error(f"Could not extract text from {uploaded_file.name}: {error}")

    if extracted_documents:
        with st.expander("Extracted text", expanded=True):
            for index, (filename, text) in enumerate(extracted_documents):
                st.subheader(filename)
                st.text_area(
                    f"Extracted text from {filename}",
                    text,
                    height=300,
                    key=f"extracted_text_{index}_{filename}",
                    label_visibility="collapsed",
                )



async def create_podcast_script(extracted_documents) -> PodcastScript:
    source_notes = "\n\n".join(
        f"--- Document: {filename} ---\n{text}"
        for filename, text in extracted_documents
    )

    prompt = f"""
Create a polished educational podcast script from the source notes below.

The podcast has exactly TWO speakers and no one else:
1. HOST — curious, warm, and responsible for asking questions.
2. EXPERT — knowledgeable and responsible for answering the host's questions.

Strict dialogue rules:
- Put only HOST questions in `host_texts`.
- Put only EXPERT answers in `expert_texts`.
- Both lists must have the same length, between 5 and 12 items.
- `host_texts[i]` must be answered by `expert_texts[i]`.
- Every HOST item must ask a clear question.
- Every EXPERT item must directly answer its matching HOST question.
- Never create a narrator, co-host, guest, or other speaker.
- Do not include speaker labels, stage directions, music cues, sound effects,
  headings, citations, or markdown inside list items.

Content rules:
- Use only information supported by the source notes.
- Do not invent facts, statistics, quotations, examples, or sources.
- If the notes are unclear or incomplete, say so briefly instead of guessing.
- Define technical terms in plain language the first time they appear.
- Explain ideas in a logical teaching sequence and finish with a recap.
- Make the dialogue natural to read aloud, with short sentences and smooth
  transitions. Keep the expert informative but conversational.
- Do not mention these instructions, the prompt, or that you are an AI.

Return only the structured PodcastScript object described by the schema.

SOURCE NOTES:
{source_notes}
"""

    provider_errors = []

    # Try Google's hosted model first.
    try:
        if not os.getenv("GEMINI_API_KEY"):
            raise RuntimeError("GEMINI_API_KEY is not configured")

        client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))
        response = await asyncio.to_thread(
            client.models.generate_content,
            model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash-lite"),
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=PodcastScript,
            ),
        )
        if not response.text:
            raise RuntimeError("Gemini returned an empty response")
        return PodcastScript.model_validate_json(response.text)
    except Exception as error:
        provider_errors.append(f"Gemini: {error}")

    # Groq and OpenRouter expose an OpenAI-compatible chat API. They receive
    # the same prompt and are validated against the same Pydantic schema.
    for provider, api_key_name, base_url, model_name in (
        (
            "Groq",
            "GROQ_API_KEY",
            "https://api.groq.com/openai/v1/chat/completions",
            os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile"),
        ),
        (
            "OpenRouter",
            "OPENROUTER_API_KEY",
            "https://openrouter.ai/api/v1/chat/completions",
            os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini"),
        ),
    ):
        try:
            api_key = os.getenv(api_key_name)
            if not api_key:
                raise RuntimeError(f"{api_key_name} is not configured")

            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            }
            if provider == "OpenRouter":
                headers["HTTP-Referer"] = os.getenv(
                    "OPENROUTER_SITE_URL", "http://localhost:8501"
                )
                headers["X-Title"] = os.getenv("OPENROUTER_SITE_NAME", "Miletus")

            payload = {
                "model": model_name,
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
                "response_format": {"type": "json_object"},
            }
            async with httpx.AsyncClient(timeout=120.0) as http_client:
                result = await http_client.post(
                    base_url, headers=headers, json=payload
                )
                result.raise_for_status()
                data = result.json()

            content = data["choices"][0]["message"]["content"]
            if not content:
                raise RuntimeError(f"{provider} returned an empty response")
            return PodcastScript.model_validate_json(content)
        except Exception as error:
            provider_errors.append(f"{provider}: {error}")

    raise RuntimeError("All LLM providers failed: " + "; ".join(provider_errors))


def run_async(coroutine):
    """Run an async SDK call without conflicting with Streamlit's event loop."""
    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(asyncio.run, coroutine).result()


def synthesize_episode(response: PodcastScript) -> bytes:
    """Generate and interleave HOST/EXPERT speech into one WAV file."""
    if len(response.host_texts) != len(response.expert_texts):
        raise ValueError("HOST and EXPERT lists must have the same length.")

    sample_rate = 24_000
    audio_parts = []
    pause = np.zeros(int(sample_rate * 0.25), dtype=np.float32)

    for host_text, expert_text in zip(
        response.host_texts, response.expert_texts, strict=True
    ):
        host_audio = synthesize_text_with_fallbacks(
            host_text,
            voice_id=os.getenv("ELEVENLABS_TEACHER_VOICE_ID", ""),
            piper_voice=os.getenv("PIPER_HOST_VOICE", ""),
            speechify_voice=os.getenv("SPEECHIFY_TEACHER_VOICE_ID", "geffen_32"),
        )
        expert_audio = synthesize_text_with_fallbacks(
            expert_text,
            voice_id=os.getenv("ELEVENLABS_STUDENT_VOICE_ID", ""),
            piper_voice=os.getenv("PIPER_EXPERT_VOICE", ""),
            speechify_voice=os.getenv("SPEECHIFY_STUDENT_VOICE_ID", "geffen_32"),
        )
        audio_parts.extend(
            [
                host_audio,
                pause,
                expert_audio,
                pause,
            ]
        )

    combined_audio = np.concatenate(audio_parts)
    audio_buffer = io.BytesIO()
    sf.write(audio_buffer, combined_audio, sample_rate, format="WAV")
    return audio_buffer.getvalue()


def synthesize_text_with_fallbacks(
    text: str, *, voice_id: str, piper_voice: str, speechify_voice: str
) -> np.ndarray:
    """Try hosted voices first, then local Piper, then eSpeak NG."""
    providers = (
        ("ElevenLabs", lambda: _elevenlabs_audio(text, voice_id)),
        ("Speechify", lambda: _speechify_audio(text, speechify_voice)),
        ("Piper", lambda: _piper_audio(text, piper_voice)),
        ("eSpeak NG", lambda: _espeak_audio(text)),
    )
    errors = []
    for name, provider in providers:
        try:
            audio = provider()
            if audio is not None and len(audio):
                return audio
        except Exception as error:
            errors.append(f"{name}: {error}")
    raise RuntimeError("All TTS providers failed: " + "; ".join(errors))


def _elevenlabs_audio(text: str, voice_id: str) -> np.ndarray:
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key or not voice_id:
        raise RuntimeError("ELEVENLABS_API_KEY or voice ID is not configured")
    from elevenlabs.client import ElevenLabs

    client = ElevenLabs(api_key=api_key)
    chunks = client.text_to_speech.convert(
        text=text,
        voice_id=voice_id,
        model_id=os.getenv("ELEVENLABS_MODEL", "eleven_multilingual_v2"),
        output_format="pcm_24000",
    )
    raw_audio = b"".join(chunks) if not isinstance(chunks, bytes) else chunks
    return np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0


def _speechify_audio(text: str, voice_id: str) -> np.ndarray:
    api_key = os.getenv("SPEECHIFY_API_KEY")
    if not api_key:
        raise RuntimeError("SPEECHIFY_API_KEY is not configured")
    from speechify import Speechify

    try:
        client = Speechify(token=api_key)
    except TypeError:
        client = Speechify(api_key=api_key)
    result = client.audio.speech(
        input=text,
        voice_id=voice_id,
        model=os.getenv("SPEECHIFY_MODEL", "simba-3.2"),
        audio_format="wav",
    )
    return _wav_bytes_to_array(result.audio_data)


def _piper_audio(text: str, voice: str) -> np.ndarray:
    model_path = os.getenv("PIPER_MODEL_PATH")
    if not model_path or not os.path.exists(model_path):
        raise RuntimeError("PIPER_MODEL_PATH is not configured or does not exist")
    with tempfile.NamedTemporaryFile(suffix=".wav") as output:
        command = ["piper", "--model", model_path, "--output_file", output.name]
        if voice:
            command.extend(["--speaker", voice])
        subprocess.run(
            command,
            input=text.encode("utf-8"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        output.seek(0)
        return _wav_bytes_to_array(output.read())


def _espeak_audio(text: str) -> np.ndarray:
    with tempfile.NamedTemporaryFile(suffix=".wav") as output:
        subprocess.run(
            ["espeak-ng", "-w", output.name, "-v", "en-us", text],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        output.seek(0)
        return _wav_bytes_to_array(output.read())


def _wav_bytes_to_array(raw_audio: bytes) -> np.ndarray:
    with io.BytesIO(raw_audio) as audio_buffer:
        audio, sample_rate = sf.read(audio_buffer, dtype="float32")
    if sample_rate != 24_000:
        # Keep the current pipeline's output rate consistent. This avoids
        # changing the rest of the episode assembly code.
        old_positions = np.linspace(0, 1, num=len(audio), endpoint=False)
        new_length = max(1, round(len(audio) * 24_000 / sample_rate))
        new_positions = np.linspace(0, 1, num=new_length, endpoint=False)
        audio = np.interp(new_positions, old_positions, audio)
    return np.asarray(audio, dtype=np.float32)


if extracted_documents and st.button("Create podcast script"):
    try:
        with st.spinner("Creating your podcast script...", show_time=True):
            response = run_async(create_podcast_script(extracted_documents))
        with st.expander("Created podcast script", expanded=True):
            st.subheader(response.title)
            st.write(response.overview)
            st.write("HOST list:")
            st.code(repr(response.host_texts), language="python")
            st.write("EXPERT list:")
            st.code(repr(response.expert_texts), language="python")

        with st.spinner("Generating the podcast audio...", show_time=True):
            podcast_audio = synthesize_episode(response)
        st.audio(podcast_audio, format="audio/wav")
        
    except Exception as error:
        st.error(f"Could not create the podcast script: {error}")

        
