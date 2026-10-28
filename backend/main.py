from __future__ import annotations

import asyncio
import os
import shutil
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from .podcast import PodcastScript, create_script, extract_text, synthesize_episode

load_dotenv()

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".md"}
MAX_FILES = int(os.getenv("MAX_FILES_PER_JOB", "20"))
MAX_FILE_BYTES = int(os.getenv("MAX_UPLOAD_MB", "50")) * 1024 * 1024
JOB_LIFETIME_SECONDS = 60 * int(os.getenv("TEMP_FILE_RETENTION_MINUTES", "60"))
TEMP_ROOT = Path(os.getenv("MILETUS_TEMP_ROOT", "/tmp/miletus"))


@dataclass
class Job:
    id: str
    files: list[tuple[str, bytes]]
    status: str = "queued"
    message: str = "Preparing your documents"
    progress: int = 5
    title: str | None = None
    overview: str | None = None
    script: PodcastScript | None = None
    error: str | None = None
    audio_path: Path | None = None
    created_at: float = field(default_factory=time.time)


jobs: dict[str, Job] = {}


def public_job(job: Job) -> dict[str, object]:
    result: dict[str, object] = {
        "job_id": job.id, "status": job.status, "message": job.message,
        "progress": job.progress, "title": job.title, "overview": job.overview,
        "error": job.error,
    }
    if job.status == "complete":
        result.update({"audio_url": f"/api/jobs/{job.id}/audio", "script": job.script.model_dump() if job.script else None})
    return result


async def run_job(job: Job) -> None:
    try:
        job.status, job.message, job.progress = "extracting", "Reading your documents", 15
        documents = await asyncio.to_thread(
            lambda: [(name, extract_text(name, content)) for name, content in job.files]
        )
        if not any(text.strip() for _, text in documents):
            raise ValueError("We could not find readable text in those documents.")
        job.status, job.message, job.progress = "writing", "Writing your two-person podcast", 40
        script = await create_script(documents)
        job.title, job.overview, job.script = script.title, script.overview, script
        job.status, job.message, job.progress = "generating_audio", "Generating the voices", 65
        audio = await asyncio.to_thread(synthesize_episode, script)
        job.status, job.message, job.progress = "assembling", "Finishing your audio", 92
        job_dir = TEMP_ROOT / job.id
        job_dir.mkdir(parents=True, exist_ok=True)
        job.audio_path = job_dir / "miletus-podcast.wav"
        job.audio_path.write_bytes(audio)
        job.status, job.message, job.progress = "complete", "Your podcast is ready", 100
    except Exception as error:
        job.status, job.message, job.error = "failed", "We could not create this podcast", str(error)


def clean_expired_jobs() -> None:
    threshold = time.time() - JOB_LIFETIME_SECONDS
    for job_id, job in list(jobs.items()):
        if job.created_at < threshold:
            if job.audio_path:
                shutil.rmtree(job.audio_path.parent, ignore_errors=True)
            jobs.pop(job_id, None)


app = FastAPI(title="Miletus API", version="1.0.0")
origins = [origin.strip() for origin in os.getenv("FRONTEND_URL", "http://localhost:5173").split(",") if origin.strip()]
app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"], allow_headers=["*"], allow_credentials=False)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/api/generate", status_code=202)
async def generate(files: list[UploadFile] = File(...)) -> dict[str, object]:
    clean_expired_jobs()
    if not 1 <= len(files) <= MAX_FILES:
        raise HTTPException(400, detail=f"Please upload between 1 and {MAX_FILES} files.")
    uploads: list[tuple[str, bytes]] = []
    for upload in files:
        name = Path(upload.filename or "document").name
        if Path(name).suffix.lower() not in SUPPORTED_EXTENSIONS:
            raise HTTPException(415, detail="Supported formats are PDF, DOCX, and Markdown.")
        content = await upload.read()
        if not content:
            raise HTTPException(400, detail=f"{name} is empty.")
        if len(content) > MAX_FILE_BYTES:
            raise HTTPException(413, detail=f"Each file must be under {MAX_FILE_BYTES // 1024 // 1024} MB.")
        uploads.append((name, content))
    job = Job(id=uuid.uuid4().hex, files=uploads)
    jobs[job.id] = job
    asyncio.create_task(run_job(job))
    return public_job(job)


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str) -> dict[str, object]:
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, detail="That job no longer exists. Please create it again.")
    return public_job(job)


@app.get("/api/jobs/{job_id}/audio")
async def audio(job_id: str) -> FileResponse:
    job = jobs.get(job_id)
    if not job or job.status != "complete" or not job.audio_path or not job.audio_path.exists():
        raise HTTPException(404, detail="The podcast audio is not ready.")
    filename = "miletus-podcast.wav"
    return FileResponse(job.audio_path, media_type="audio/wav", filename=filename)
