"""
Chunkers — Semantic and recursive text chunking strategies.
Produces optimally-sized chunks for vector embedding.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from structlog import get_logger

logger = get_logger(__name__)


@dataclass
class Chunk:
    text: str
    chunk_index: int
    token_estimate: int
    metadata: dict = field(default_factory=dict)

    def to_dict(self, base_metadata: dict | None = None) -> dict:
        m = {**(base_metadata or {}), **self.metadata, "chunk_index": self.chunk_index}
        return {"chunk_text": self.text, "token_estimate": self.token_estimate, "metadata": m}


def _estimate_tokens(text: str) -> int:
    """Rough token estimate: ~4 chars per token for English."""
    return len(text) // 4


class RecursiveCharacterChunker:
    """
    Recursively splits text using a hierarchy of separators.
    Strategy: paragraph → sentence → word — stops when chunk is within budget.
    """

    SEPARATORS = ["\n\n", "\n", ". ", "! ", "? ", "; ", ", ", " ", ""]

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100):
        self.chunk_size = chunk_size        # In tokens
        self.chunk_overlap = chunk_overlap  # In tokens
        self._chunk_chars = chunk_size * 4
        self._overlap_chars = chunk_overlap * 4

    def split(self, text: str, metadata: dict | None = None) -> list[Chunk]:
        """Split text into overlapping chunks."""
        if not text or not text.strip():
            return []

        raw_chunks = self._split_recursive(text, self.SEPARATORS)
        merged = self._merge_with_overlap(raw_chunks)

        chunks = []
        for i, chunk_text in enumerate(merged):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue
            chunks.append(Chunk(
                text=chunk_text,
                chunk_index=i,
                token_estimate=_estimate_tokens(chunk_text),
                metadata=metadata or {},
            ))

        logger.debug("text_chunked", chunk_count=len(chunks), strategy="recursive")
        return chunks

    def _split_recursive(self, text: str, separators: list[str]) -> list[str]:
        if len(text) <= self._chunk_chars:
            return [text]

        sep = separators[0] if separators else ""
        splits = text.split(sep) if sep else list(text)

        parts: list[str] = []
        for s in splits:
            if len(s) > self._chunk_chars and len(separators) > 1:
                parts.extend(self._split_recursive(s, separators[1:]))
            else:
                parts.append(s)

        # Rejoin with separator
        result: list[str] = []
        current = ""
        for part in parts:
            candidate = (current + sep + part).strip() if current else part
            if len(candidate) <= self._chunk_chars:
                current = candidate
            else:
                if current:
                    result.append(current)
                current = part
        if current:
            result.append(current)

        return result

    def _merge_with_overlap(self, chunks: list[str]) -> list[str]:
        if len(chunks) <= 1:
            return chunks

        merged: list[str] = []
        for i, chunk in enumerate(chunks):
            if i > 0 and self._overlap_chars > 0:
                # Prepend tail of previous chunk as overlap
                prev = chunks[i - 1]
                overlap = prev[-self._overlap_chars:].strip()
                chunk = overlap + "\n" + chunk if overlap else chunk
            merged.append(chunk)
        return merged


class SemanticChunker:
    """
    Splits text at semantic boundaries (headings, section breaks).
    Best for structured documents like test templates and standards.
    """

    HEADING_PATTERN = re.compile(r"^#{1,6}\s+.+|^[A-Z][^a-z\n]{10,}$", re.MULTILINE)

    def __init__(self, max_chunk_tokens: int = 512, overlap_tokens: int = 64):
        self.max_chars = max_chunk_tokens * 4
        self.overlap_chars = overlap_tokens * 4

    def split(self, text: str, metadata: dict | None = None) -> list[Chunk]:
        if not text.strip():
            return []

        # Find semantic boundaries
        boundaries = [0] + [m.start() for m in self.HEADING_PATTERN.finditer(text)] + [len(text)]
        sections = [text[boundaries[i]:boundaries[i + 1]].strip() for i in range(len(boundaries) - 1)]
        sections = [s for s in sections if s]

        # Further split oversized sections with recursive chunker
        recursive = RecursiveCharacterChunker(self.max_chars // 4, self.overlap_chars // 4)
        all_chunks: list[Chunk] = []
        for section in sections:
            if len(section) > self.max_chars:
                sub = recursive.split(section, metadata)
                all_chunks.extend(sub)
            else:
                all_chunks.append(Chunk(
                    text=section,
                    chunk_index=len(all_chunks),
                    token_estimate=_estimate_tokens(section),
                    metadata=metadata or {},
                ))

        # Re-index
        for i, c in enumerate(all_chunks):
            c.chunk_index = i

        logger.debug("text_chunked", chunk_count=len(all_chunks), strategy="semantic")
        return all_chunks


def get_chunker(document_type: str) -> RecursiveCharacterChunker | SemanticChunker:
    """Select optimal chunker based on document category."""
    semantic_types = {"test_templates", "testing_standards", "regulatory_documents"}
    if document_type in semantic_types:
        return SemanticChunker()
    return RecursiveCharacterChunker()
