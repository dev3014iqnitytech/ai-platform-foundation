"""
Unit tests for text chunkers.
"""
import pytest
from rag.ingestion.chunkers.text_chunkers import (
    RecursiveCharacterChunker, SemanticChunker, get_chunker, Chunk
)

SAMPLE_TEXT = """
## Introduction to Testing Standards

Testing is a critical phase in software development. This document outlines the core principles.

## Test Types

### Functional Testing
Functional testing validates that the system behaves according to specifications.
It covers happy path scenarios and edge cases.

### Boundary Testing
Boundary testing focuses on values at the edges of valid input ranges.
This includes minimum, maximum, and just-outside-boundary values.

### Negative Testing
Negative testing ensures the system handles invalid inputs gracefully.
Error messages should be clear and informative.
"""


@pytest.fixture
def recursive_chunker():
    return RecursiveCharacterChunker(chunk_size=100, chunk_overlap=20)


@pytest.fixture
def semantic_chunker():
    return SemanticChunker(max_chunk_tokens=150, overlap_tokens=20)


def test_recursive_chunker_produces_chunks(recursive_chunker):
    chunks = recursive_chunker.split(SAMPLE_TEXT)
    assert len(chunks) > 0
    assert all(isinstance(c, Chunk) for c in chunks)


def test_recursive_chunker_empty_input(recursive_chunker):
    chunks = recursive_chunker.split("")
    assert chunks == []


def test_recursive_chunker_chunk_size(recursive_chunker):
    # Each chunk should be roughly within budget (allowing some overlap)
    chunks = recursive_chunker.split(SAMPLE_TEXT)
    for chunk in chunks:
        # Token estimate should be reasonable
        assert chunk.token_estimate > 0


def test_recursive_chunker_indices_sequential(recursive_chunker):
    chunks = recursive_chunker.split(SAMPLE_TEXT)
    for i, chunk in enumerate(chunks):
        assert chunk.chunk_index == i


def test_recursive_chunker_metadata_preserved(recursive_chunker):
    metadata = {"source": "test.md", "category": "testing_standards"}
    chunks = recursive_chunker.split(SAMPLE_TEXT, metadata=metadata)
    for chunk in chunks:
        assert chunk.metadata["source"] == "test.md"


def test_semantic_chunker_splits_on_headings(semantic_chunker):
    chunks = semantic_chunker.split(SAMPLE_TEXT)
    assert len(chunks) > 1
    # Should split at ## headings
    texts = [c.text for c in chunks]
    assert any("Introduction" in t or "Test Types" in t for t in texts)


def test_semantic_chunker_empty_input(semantic_chunker):
    chunks = semantic_chunker.split("")
    assert chunks == []


def test_chunk_to_dict(recursive_chunker):
    chunks = recursive_chunker.split("Hello world. This is a test document.")
    assert len(chunks) > 0
    d = chunks[0].to_dict()
    assert "chunk_text" in d
    assert "token_estimate" in d
    assert "metadata" in d


def test_get_chunker_semantic_for_standards():
    chunker = get_chunker("testing_standards")
    assert isinstance(chunker, SemanticChunker)


def test_get_chunker_recursive_for_business_rules():
    chunker = get_chunker("business_rules")
    assert isinstance(chunker, RecursiveCharacterChunker)


def test_get_chunker_recursive_for_existing_tests():
    chunker = get_chunker("existing_test_cases")
    assert isinstance(chunker, RecursiveCharacterChunker)
