from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


@dataclass(frozen=True)
class Settings:
    groq_api_key: str = os.getenv("GROQ_API_KEY", "")
    groq_model: str = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    openrouter_api_key: str = os.getenv("OPENROUTER_API_KEY", "")
    openrouter_model: str = os.getenv("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    elevenlabs_api_key: str = os.getenv("ELEVENLABS_API_KEY", "")
    teacher_voice_id: str = os.getenv("ELEVENLABS_TEACHER_VOICE_ID", "")
    student_voice_id: str = os.getenv("ELEVENLABS_STUDENT_VOICE_ID", "")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "50"))
    max_files_per_job: int = int(os.getenv("MAX_FILES_PER_JOB", "20"))
    temp_file_retention_minutes: int = int(os.getenv("TEMP_FILE_RETENTION_MINUTES", "60"))
    frontend_url: str = os.getenv("FRONTEND_URL", "http://localhost:5173")
    mock_providers: bool = os.getenv("MOCK_PROVIDERS", "false").lower() in {"1", "true", "yes"}
    debug_errors: bool = os.getenv("DEBUG_ERRORS", "false").lower() in {"1", "true", "yes"}
    temp_root: Path = Path(os.getenv("MILETUS_TEMP_ROOT", "/tmp/miletus"))
    max_chunk_chars: int = int(os.getenv("MAX_CHUNK_CHARS", "12000"))

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
