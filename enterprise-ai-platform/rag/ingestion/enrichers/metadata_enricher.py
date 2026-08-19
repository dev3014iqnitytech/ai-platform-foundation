"""
Metadata Enricher — Extracts and enriches document metadata before indexing.
Adds document type, version, project tags, and AI-generated keywords.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from structlog import get_logger

logger = get_logger(__name__)

CATEGORY_KEYWORDS: dict[str, list[str]] = {
    "testing_standards": ["istqb", "iso 29119", "test standard", "testing principle", "test level"],
    "test_templates": ["test case template", "test plan template", "test design", "gherkin template"],
    "existing_test_cases": ["given", "when", "then", "test case", "test step", "precondition"],
    "business_rules": ["business rule", "must", "shall", "policy", "constraint", "regulation"],
    "regulatory_documents": ["gdpr", "hipaa", "sox", "pci-dss", "compliance", "regulatory"],
    "domain_documents": ["domain", "glossary", "functional spec", "system design"],
    "org_guidelines": ["guideline", "best practice", "coding standard", "qa process"],
}


def enrich_metadata(
    chunk_text: str,
    source_metadata: dict,
    document_id: str,
    category: str,
    uploaded_by: str = "system",
) -> dict:
    """
    Produce a rich metadata dict suitable for indexing in Azure AI Search.
    """
    filename = source_metadata.get("source", "unknown")
    page = source_metadata.get("page", 1)

    return {
        "id": f"{document_id}_{page}_{hash(chunk_text) & 0xFFFFFFFF}",
        "document_id": document_id,
        "filename": filename,
        "category": category,
        "page": page,
        "section": source_metadata.get("section"),
        "doc_type": source_metadata.get("type", "text"),
        "keywords": _extract_keywords(chunk_text),
        "has_gherkin": _has_gherkin(chunk_text),
        "char_count": len(chunk_text),
        "token_estimate": len(chunk_text) // 4,
        "uploaded_by": uploaded_by,
        "indexed_at": datetime.now(timezone.utc).isoformat(),
        "is_active": True,
    }


def infer_category(filename: str, content_sample: str) -> str:
    """
    Infer document category from filename and content keywords.
    Falls back to 'domain_documents'.
    """
    name_lower = filename.lower()
    content_lower = content_sample[:2000].lower()

    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in name_lower or kw in content_lower for kw in keywords):
            logger.debug("category_inferred", filename=filename, category=category)
            return category

    return "domain_documents"


def _extract_keywords(text: str, max_keywords: int = 10) -> list[str]:
    """Extract significant keywords using simple frequency analysis."""
    stop_words = {
        "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
        "of", "with", "by", "from", "is", "are", "was", "were", "be", "been",
        "that", "this", "it", "its", "not", "no", "if", "as", "so", "we",
        "should", "must", "can", "will", "when", "then", "given",
    }
    words = re.findall(r"\b[a-zA-Z]{4,}\b", text.lower())
    freq: dict[str, int] = {}
    for word in words:
        if word not in stop_words:
            freq[word] = freq.get(word, 0) + 1
    sorted_words = sorted(freq.items(), key=lambda x: x[1], reverse=True)
    return [w for w, _ in sorted_words[:max_keywords]]


def _has_gherkin(text: str) -> bool:
    """Detect if chunk contains Gherkin BDD syntax."""
    patterns = [r"\bGiven\b", r"\bWhen\b", r"\bThen\b", r"\bScenario\b", r"\bFeature\b"]
    return sum(1 for p in patterns if re.search(p, text)) >= 2
