from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import ValidationError

from .chunking import chunk_sections
from .config import Settings
from .extractors import extract_document
from .models import JobResponse, JobStatus, PodcastScript
from .prompts import ANALYSIS_PROMPT, GENERATION_PROMPT, render_prompt
from .providers import ElevenLabsTTSProvider, LLMManager, MockTTSProvider, parse_json_object

logger = logging.getLogger(__name__)
JSON_RESPONSE_FORMAT = {"type": "json_object"}


@dataclass
class Job:
    job_id: str
    directory: Path
    files: list[tuple[Path, str]]
    status: JobStatus = JobStatus.queued
    progress: int = 0
    message: str = "Queued"
    title: str | None = None
    error: str | None = None
    script: PodcastScript | None = None
    task: asyncio.Task | None = field(default=None, repr=False)


class JobManager:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.jobs: dict[str, Job] = {}
        self.llm = LLMManager(settings)
        self.tts = MockTTSProvider() if settings.mock_providers else ElevenLabsTTSProvider(settings)

    def create(self, files: list[tuple[Path, str]]) -> Job:
        job_id = uuid.uuid4().hex
        directory = self.settings.temp_root / job_id
        for folder in ("source", "chunks", "script", "audio", "final"):
            (directory / folder).mkdir(parents=True, exist_ok=True)
        job = Job(job_id, directory, files)
        self.jobs[job_id] = job
        job.task = asyncio.create_task(self.run(job))
        return job

    def response(self, job: Job) -> JobResponse:
        return JobResponse(job_id=job.job_id, status=job.status, progress=job.progress, message=job.message, title=job.title,
            audio_url=f"/api/jobs/{job.job_id}/audio" if job.status == JobStatus.complete else None,
            transcript_url=f"/api/jobs/{job.job_id}/transcript" if job.status == JobStatus.complete else None,
            error=job.error if self.settings.debug_errors else None)

    async def run(self, job: Job) -> None:
        started = time.monotonic()
        try:
            self._update(job, JobStatus.extracting, 8, "Extracting your documents")
            sections = []
            for path, name in job.files:
                sections.extend(extract_document(path, name))
            if not any(section.text.strip() for section in sections):
                raise ValueError("The supplied documents contain no readable text")
            chunks = chunk_sections(sections, self.settings.max_chunk_chars)
            (job.directory / "chunks" / "chunks.json").write_text(json.dumps([c.model_dump() for c in chunks], indent=2), encoding="utf-8")
            self._update(job, JobStatus.analyzing, 25, "Understanding the material")
            source = "\n\n".join(f"[{c.source_file}{f', page {c.page_start}' if c.page_start else ''}]\n{c.text}" for c in chunks)
            source_for_prompt = self._bounded_source(chunks, 50000)
            if self.settings.mock_providers:
                analysis = {"subject": "general study material", "title": "Your Notes", "major_topics": ["central ideas", "important details"], "key_concepts": ["The material is explained through connected concepts."], "terminology": [], "relationships": [], "examples": [], "misconceptions": [], "uncertainties": []}
            else:
                raw_analysis = await self.llm.generate(render_prompt(ANALYSIS_PROMPT, source=source_for_prompt), "You are Miletus's source-grounded analysis engine. Return JSON only.", JSON_RESPONSE_FORMAT)
                try:
                    analysis = parse_json_object(raw_analysis)
                except (ValueError, json.JSONDecodeError):
                    repair_prompt = "Convert the following source analysis into a valid JSON object with keys subject, title, major_topics, key_concepts, terminology, relationships, examples, misconceptions, and uncertainties. Preserve only supported claims. Return JSON only.\n\nANALYSIS:\n" + raw_analysis
                    analysis = parse_json_object(await self.llm.generate(repair_prompt, "You repair source analysis into valid JSON. Return JSON only.", JSON_RESPONSE_FORMAT))
            self._update(job, JobStatus.writing, 43, "Writing the podcast")
            if self.settings.mock_providers:
                script_data = self._mock_script(analysis, chunks)
            else:
                generation_prompt = render_prompt(GENERATION_PROMPT, analysis=json.dumps(analysis), source=source_for_prompt)
                raw = await self.llm.generate(generation_prompt, "Return only valid JSON and stay strictly within the supplied source.", JSON_RESPONSE_FORMAT)
                try:
                    script_data = parse_json_object(raw)
                except (ValueError, json.JSONDecodeError):
                    script_data = parse_json_object(await self.llm.generate("Repair this into valid podcast JSON. Return JSON only:\n" + raw, "You repair structured podcast JSON.", JSON_RESPONSE_FORMAT))
            self._update(job, JobStatus.validating, 50, "Checking the podcast script")
            try:
                job.script = PodcastScript.model_validate(script_data)
            except ValidationError as validation_error:
                repair_prompt = (
                    "Repair this podcast JSON so it validates against the required schema. "
                    "It must contain at least four useful dialogue segments, include both teacher and student speakers, "
                    "alternate naturally, and preserve the source-grounded content. Return JSON only.\n"
                    f"Validation errors: {validation_error}\n\nJSON:\n{json.dumps(script_data)}"
                )
                repaired_data = parse_json_object(await self.llm.generate(repair_prompt, "You repair invalid podcast scripts. Return JSON only.", JSON_RESPONSE_FORMAT))
                job.script = PodcastScript.model_validate(repaired_data)
            if not self._is_source_anchored(job.script, source):
                if self.settings.mock_providers:
                    raise ValueError("Generated mock script was not source anchored")
                grounded_repair = (
                    "Rewrite this podcast from scratch so it is strictly about the supplied source. "
                    "The previous script was off-topic. Remove every unsupported topic and use the source's actual subject, "
                    "terms, examples, and relationships. Provide both display_text and natural spoken_text for every turn. "
                    "Return valid JSON only.\n\nSOURCE:\n" + source_for_prompt + "\n\nPREVIOUS SCRIPT:\n" + json.dumps(job.script.model_dump())
                )
                repaired_data = parse_json_object(await self.llm.generate(grounded_repair, "You are a strict source-fidelity editor. Return JSON only.", JSON_RESPONSE_FORMAT))
                job.script = PodcastScript.model_validate(repaired_data)
                if not self._is_source_anchored(job.script, source):
                    raise ValueError("The generated podcast could not be grounded in the uploaded material")
            job.title = job.script.title
            (job.directory / "script" / "script.json").write_text(job.script.model_dump_json(indent=2), encoding="utf-8")
            self._update(job, JobStatus.generating_audio, 55, f"Generating voices (0 of {len(job.script.segments)})")
            audio_paths: list[Path] = []
            for index, segment in enumerate(job.script.segments, start=1):
                voice = "teacher" if segment.speaker == "teacher" else "student"
                voice_id = voice if self.settings.mock_providers else (self.settings.teacher_voice_id if voice == "teacher" else self.settings.student_voice_id)
                target = job.directory / "audio" / f"{index:04d}-{voice}.wav"
                for attempt in range(2):
                    try:
                        target.write_bytes(await self.tts.generate_audio(segment.spoken_text or segment.display_text, voice_id))
                        break
                    except Exception:
                        if attempt == 1:
                            raise
                        await asyncio.sleep(0.5)
                audio_paths.append(target)
                self._update(job, JobStatus.generating_audio, 55 + int(index / len(job.script.segments) * 35), f"Generating voices ({index} of {len(job.script.segments)})")
            self._update(job, JobStatus.assembling, 93, "Finalizing your podcast")
            self._assemble(audio_paths, job.directory / "final" / "podcast.mp3")
            (job.directory / "script" / "transcript.txt").write_text(self._transcript(job), encoding="utf-8")
            self._update(job, JobStatus.complete, 100, "Your podcast is ready")
            logger.info("job_complete", extra={"job_id": job.job_id, "stage": "complete", "duration": round(time.monotonic() - started, 2), "status": "complete"})
        except Exception as error:
            job.error = str(error)
            self._update(job, JobStatus.failed, job.progress, "Something went wrong while generating your podcast")
            logger.exception("job_failed", extra={"job_id": job.job_id, "stage": job.status.value, "duration": round(time.monotonic() - started, 2), "status": "failed", "error_type": type(error).__name__})

    @staticmethod
    def _bounded_source(chunks: list, limit: int) -> str:
        """Represent every chunk in a bounded prompt instead of dropping later sections."""
        full = "\n\n".join(f"[{c.source_file}{f', page {c.page_start}' if c.page_start else ''}]\n{c.text}" for c in chunks)
        if len(full) <= limit:
            return full
        per_chunk = max(300, limit // max(1, len(chunks)))
        excerpts = []
        for chunk in chunks:
            text = chunk.text
            excerpt = text if len(text) <= per_chunk else text[:per_chunk // 2] + "\n[...middle of this source chunk omitted...]\n" + text[-per_chunk // 2:]
            excerpts.append(f"[{chunk.source_file}{f', page {chunk.page_start}' if chunk.page_start else ''}]\n{excerpt}")
        return "\n\n".join(excerpts)[:limit]

    @staticmethod
    def _is_source_anchored(script: PodcastScript, source: str) -> bool:
        """Reject obviously unrelated titles, such as a Newton lesson for biology notes."""
        source_words = set(re.findall(r"[a-z]{4,}", source.lower()))
        title_words = [word for word in re.findall(r"[a-z]{4,}", script.title.lower()) if word not in {"your", "podcast", "understanding", "conversation", "material", "guide", "discussion", "notes", "study"}]
        if title_words and sum(word in source_words for word in title_words) / len(title_words) < 0.5:
            return False
        return True

    @staticmethod
    def _update(job: Job, status: JobStatus, progress: int, message: str) -> None:
        job.status, job.progress, job.message = status, progress, message

    @staticmethod
    def _mock_script(analysis: dict, chunks: list) -> dict:
        title = analysis.get("title") or "A conversation about your material"
        first = re.sub(r"\s+", " ", chunks[0].text[:500])
        return {"title": title, "segments": [
            {"id": "001", "speaker": "teacher", "display_text": f"Let's begin with the big picture of {title.lower()}. The first idea to hold on to is that this material is connected rather than a list of isolated facts."},
            {"id": "002", "speaker": "student", "display_text": "So the useful question is not just what the notes say, but how the ideas fit together?"},
            {"id": "003", "speaker": "teacher", "display_text": f"Exactly. The source begins with this important point: {first}"},
            {"id": "004", "speaker": "student", "display_text": "What should I remember when I come back to this later?"},
            {"id": "005", "speaker": "teacher", "display_text": "Remember the central relationship, the terminology that names it, and the evidence or example that makes it concrete. Then explain it in your own words."},
        ], "chapters": [{"title": "Big picture", "segment_id": "001"}, {"title": "Key idea", "segment_id": "003"}]}

    @staticmethod
    def _assemble(paths: list[Path], output: Path) -> None:
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            output.write_bytes(paths[0].read_bytes())
            return
        concat = output.parent / "concat.txt"
        concat.write_text("\n".join(f"file '{path}'" for path in paths), encoding="utf-8")
        subprocess.run([ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", str(concat), "-af", "loudnorm=I=-16:TP=-1.5:LRA=11", "-codec:a", "libmp3lame", "-b:a", "128k", str(output)], check=True, capture_output=True)

    @staticmethod
    def _transcript(job: Job) -> str:
        return "\n\n".join(f"{segment.speaker.title()}: {segment.spoken_text or segment.display_text}" for segment in job.script.segments)

    def cleanup(self) -> None:
        cutoff = time.time() - self.settings.temp_file_retention_minutes * 60
        if self.settings.temp_root.exists():
            for directory in self.settings.temp_root.iterdir():
                if directory.is_dir() and directory.stat().st_mtime < cutoff:
                    shutil.rmtree(directory, ignore_errors=True)
