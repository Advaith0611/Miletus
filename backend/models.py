from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class JobStatus(StrEnum):
    queued = "queued"
    extracting = "extracting"
    analyzing = "analyzing"
    writing = "writing"
    validating = "validating"
    generating_audio = "generating_audio"
    assembling = "assembling"
    complete = "complete"
    failed = "failed"


class DocumentSection(BaseModel):
    source_file: str
    page: int | None = None
    heading: str | None = None
    text: str


class DocumentChunk(BaseModel):
    chunk_id: str
    source_file: str
    section: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    text: str


class PodcastSegment(BaseModel):
    id: str
    speaker: Literal["teacher", "student"]
    display_text: str
    spoken_text: str | None = None

    @field_validator("display_text")
    @classmethod
    def text_not_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("segment text cannot be empty")
        return value.strip()


class PodcastChapter(BaseModel):
    title: str
    segment_id: str


class PodcastScript(BaseModel):
    title: str = Field(min_length=1, max_length=180)
    segments: list[PodcastSegment] = Field(min_length=2)
    chapters: list[PodcastChapter] = Field(default_factory=list)

    @field_validator("segments")
    @classmethod
    def validate_dialogue(cls, segments: list[PodcastSegment]) -> list[PodcastSegment]:
        if len(segments) > 500:
            raise ValueError("podcast has too many segments")
        if {item.speaker for item in segments} != {"teacher", "student"}:
            raise ValueError("podcast must contain both speakers")
        return segments


class JobResponse(BaseModel):
    job_id: str
    status: JobStatus
    progress: int
    message: str
    title: str | None = None
    audio_url: str | None = None
    transcript_url: str | None = None
    error: str | None = None
