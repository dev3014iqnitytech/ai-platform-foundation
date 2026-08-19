"""
Contextual Compression — Reduces retrieved chunks to only the portions
most relevant to the query, cutting context window usage by ~50%.
Uses LLMChainFilter + LLMChainExtractor strategy.
"""
from __future__ import annotations

from structlog import get_logger

logger = get_logger(__name__)


class ContextualCompressor:
    """
    Post-retrieval compressor that:
    1. Filters out documents not relevant to the query (LLMChainFilter)
    2. Extracts only the relevant passages from retained documents
    
    Falls back to returning documents unmodified if LLM is unavailable.
    """

    def __init__(self, model: str = "gpt-4o-mini", max_tokens_per_chunk: int = 300):
        self.model = model
        self.max_tokens_per_chunk = max_tokens_per_chunk

    async def compress(self, query: str, documents: list[dict]) -> list[dict]:
        """
        Compress retrieved documents to query-relevant passages.
        Returns documents with a new 'compressed_text' field.
        """
        if not documents:
            return documents

        try:
            return await self._compress_with_llm(query, documents)
        except Exception as e:
            logger.warning("compression_failed", error=str(e), fallback="uncompressed")
            return documents

    async def _compress_with_llm(self, query: str, documents: list[dict]) -> list[dict]:
        from langchain_openai import OpenAI
        from langchain.retrievers.document_compressors import LLMChainFilter, LLMChainExtractor
        from langchain_core.documents import Document
        from app.core.config import settings

        llm = OpenAI(
            azure_deployment=settings.AZURE_OPENAI_MINI_DEPLOYMENT,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_API_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
            temperature=0.0,
            max_tokens=self.max_tokens_per_chunk,
        )

        # Convert to LangChain Documents
        lc_docs = [
            Document(
                page_content=doc.get("chunk_text", doc.get("content", "")),
                metadata=doc.get("metadata", {}),
            )
            for doc in documents
        ]

        # Stage 1: Filter irrelevant docs
        filter_compressor = LLMChainFilter.from_llm(llm)
        relevant_docs = await filter_compressor.acompress_documents(lc_docs, query)

        if not relevant_docs:
            logger.debug("compression_filtered_all", query=query[:60])
            return documents[:3]  # Safety fallback — return top 3

        # Stage 2: Extract relevant passages
        extractor = LLMChainExtractor.from_llm(llm)
        extracted_docs = await extractor.acompress_documents(relevant_docs, query)

        # Merge back to original format
        compressed = []
        original_map = {doc.get("chunk_text", ""): doc for doc in documents}

        for i, ed in enumerate(extracted_docs):
            original_content = relevant_docs[i].page_content if i < len(relevant_docs) else ""
            base_doc = original_map.get(original_content, documents[i] if i < len(documents) else {})
            compressed.append({
                **base_doc,
                "compressed_text": ed.page_content,
                "compression_applied": True,
            })

        logger.debug(
            "compression_complete",
            original_count=len(documents),
            compressed_count=len(compressed),
        )
        return compressed

    def simple_truncate(self, documents: list[dict], max_chars: int = 1000) -> list[dict]:
        """Fast truncation without LLM — for latency-sensitive paths."""
        result = []
        for doc in documents:
            text = doc.get("chunk_text", doc.get("content", ""))
            if len(text) > max_chars:
                text = text[:max_chars] + "..."
            result.append({**doc, "compressed_text": text, "compression_applied": False})
        return result
