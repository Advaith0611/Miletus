from __future__ import annotations

import logging
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, PlainTextResponse

from .config import settings
from .extractors import SUPPORTED_EXTENSIONS, SUPPORTED_MIME_TYPES
from .job_manager import JobManager
from .models import JobResponse

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


def safe_name(name: str) -> str:
    cleaned = Path(name or "document").name
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", cleaned).strip(".-")
    return cleaned or "document"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings.temp_root.mkdir(parents=True, exist_ok=True)
    yield
    app.state.jobs.cleanup()


def create_app() -> FastAPI:
    app = FastAPI(title="Miletus", version="1.0.0", lifespan=lifespan)
    app.state.jobs = JobManager(settings)
    origins = [item.strip() for item in settings.frontend_url.split(",") if item.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=False, allow_methods=["GET", "POST"], allow_headers=["*"])

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/generate", response_model=JobResponse, status_code=202)
    async def generate(files: list[UploadFile] = File(...)) -> JobResponse:
        if not files or len(files) > settings.max_files_per_job:
            raise HTTPException(400, f"Upload between 1 and {settings.max_files_per_job} files")
        settings.temp_root.mkdir(parents=True, exist_ok=True)
        pending = Path(tempfile.mkdtemp(prefix="pending-", dir=settings.temp_root))
        staged: list[tuple[Path, str]] = []
        try:
            for upload_index, upload in enumerate(files, start=1):
                original = safe_name(upload.filename or "document")
                suffix = Path(original).suffix.lower()
                if suffix not in SUPPORTED_EXTENSIONS:
                    raise HTTPException(415, "Supported files are PDF, DOCX, TXT, and Markdown")
                if upload.content_type and upload.content_type not in SUPPORTED_MIME_TYPES[suffix]:
                    raise HTTPException(415, "The file type does not match its extension")
                target = pending / f"{upload_index:02d}-{original}"
                size = 0
                with target.open("wb") as output:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > settings.max_upload_bytes:
                            raise HTTPException(413, f"Each file must be smaller than {settings.max_upload_mb} MB")
                        output.write(chunk)
                if size == 0:
                    raise HTTPException(400, "Empty files cannot be processed")
                staged.append((target, original))
            job = app.state.jobs.create(staged)
            source_files: list[tuple[Path, str]] = []
            for target, name in staged:
                destination_name = f"{len(source_files) + 1:02d}-{name}"
                destination = job.directory / "source" / destination_name
                shutil.move(str(target), str(destination))
                source_files.append((destination, name))
            shutil.rmtree(pending, ignore_errors=True)
            job.files = source_files
            return app.state.jobs.response(job)
        except Exception:
            shutil.rmtree(pending, ignore_errors=True)
            raise

    @app.get("/api/jobs/{job_id}", response_model=JobResponse)
    async def job_status(job_id: str) -> JobResponse:
        job = app.state.jobs.jobs.get(job_id)
        if not job:
            raise HTTPException(404, "Job not found")
        return app.state.jobs.response(job)

    @app.get("/api/jobs/{job_id}/audio")
    async def audio(job_id: str) -> FileResponse:
        job = app.state.jobs.jobs.get(job_id)
        path = job.directory / "final" / "podcast.mp3" if job else None
        if not job or job.status.value != "complete" or not path.exists():
            raise HTTPException(404, "Podcast not ready")
        return FileResponse(path, media_type="audio/mpeg", filename=f"miletus-{safe_name(job.title or 'podcast').lower()}.mp3")

    @app.get("/api/jobs/{job_id}/transcript")
    async def transcript(job_id: str) -> PlainTextResponse:
        job = app.state.jobs.jobs.get(job_id)
        path = job.directory / "script" / "transcript.txt" if job else None
        if not job or job.status.value != "complete" or not path.exists():
            raise HTTPException(404, "Transcript not ready")
        return PlainTextResponse(path.read_text(encoding="utf-8"))

    return app
