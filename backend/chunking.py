from __future__ import annotations

import re

from .models import DocumentChunk, DocumentSection


def chunk_sections(sections: list[DocumentSection], max_chars: int = 12000) -> list[DocumentChunk]:
    if max_chars < 500:
        raise ValueError("max_chars must be at least 500")
    chunks: list[DocumentChunk] = []
    for section in sections:
        paragraphs = [p.strip() for p in re.split(r"\n\s*\n", section.text) if p.strip()] or [section.text.strip()]
        current: list[str] = []
        size = 0
        for paragraph in paragraphs:
            for piece in [paragraph[i:i + max_chars] for i in range(0, len(paragraph), max_chars)] or [""]:
                if current and size + len(piece) + 2 > max_chars:
                    chunks.append(_make_chunk(section, current, len(chunks)))
                    current, size = [], 0
                current.append(piece); size += len(piece) + 2
        if current:
            chunks.append(_make_chunk(section, current, len(chunks)))
    return chunks


def _make_chunk(section: DocumentSection, parts: list[str], index: int) -> DocumentChunk:
    return DocumentChunk(chunk_id=f"chunk-{index + 1:04d}", source_file=section.source_file,
                         section=section.heading, page_start=section.page, page_end=section.page,
                         text="\n\n".join(parts).strip())

