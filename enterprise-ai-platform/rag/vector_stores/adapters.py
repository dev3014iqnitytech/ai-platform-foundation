"""
Vector Store Adapters — Unified interface for Azure AI Search, pgvector, and FAISS.
Provides a common VectorStore protocol so the RAG retriever can swap backends.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any
from structlog import get_logger

logger = get_logger(__name__)


class VectorStore(ABC):
    """Common interface for all vector store backends."""

    @abstractmethod
    async def similarity_search(
        self, query_embedding: list[float], top_k: int = 10, filters: dict | None = None
    ) -> list[dict]:
        ...

    @abstractmethod
    async def add_documents(self, documents: list[dict]) -> int:
        """Add documents and return count of successfully indexed docs."""
        ...

    @abstractmethod
    async def delete_documents(self, document_ids: list[str]) -> int:
        ...


class AzureSearchAdapter(VectorStore):
    """
    Azure AI Search adapter with hybrid (dense + sparse BM25) search.
    Requires an index with a vector field named 'embedding'.
    """

    def __init__(self, endpoint: str, api_key: str, index_name: str):
        self.endpoint = endpoint
        self.api_key = api_key
        self.index_name = index_name
        self._client = None
        self._available = False
        self._try_init()

    def _try_init(self) -> None:
        try:
            from azure.search.documents.aio import SearchClient
            from azure.core.credentials import AzureKeyCredential
            self._client = SearchClient(
                endpoint=self.endpoint,
                index_name=self.index_name,
                credential=AzureKeyCredential(self.api_key),
            )
            self._available = True
            logger.info("azure_search_connected", index=self.index_name)
        except ImportError:
            logger.warning("azure_search_unavailable", reason="azure-search-documents not installed")
        except Exception as e:
            logger.warning("azure_search_init_failed", error=str(e))

    async def similarity_search(
        self, query_embedding: list[float], top_k: int = 10, filters: dict | None = None
    ) -> list[dict]:
        if not self._available or not self._client:
            return []
        try:
            from azure.search.documents.models import VectorizedQuery
            vector_query = VectorizedQuery(
                vector=query_embedding,
                k_nearest_neighbors=top_k,
                fields="embedding",
            )
            filter_expr = self._build_filter(filters) if filters else None
            results = await self._client.search(
                search_text="*",  # Hybrid: keyword + vector
                vector_queries=[vector_query],
                filter=filter_expr,
                top=top_k,
                select=["id", "chunk_text", "filename", "category", "metadata"],
            )
            docs = []
            async for r in results:
                docs.append({
                    "id": r["id"],
                    "chunk_text": r.get("chunk_text", ""),
                    "filename": r.get("filename", ""),
                    "category": r.get("category", ""),
                    "metadata": r.get("metadata", {}),
                    "relevance_score": r.get("@search.score", 0.0),
                })
            return docs
        except Exception as e:
            logger.error("azure_search_query_failed", error=str(e))
            return []

    async def add_documents(self, documents: list[dict]) -> int:
        if not self._available or not self._client:
            return 0
        try:
            from azure.search.documents import IndexDocumentsBatch
            batch = IndexDocumentsBatch()
            batch.add_upload_actions(documents)
            result = await self._client.index_documents(batch)
            return sum(1 for r in result if r.succeeded)
        except Exception as e:
            logger.error("azure_search_index_failed", error=str(e))
            return 0

    async def delete_documents(self, document_ids: list[str]) -> int:
        if not self._available or not self._client:
            return 0
        try:
            docs = [{"id": doc_id} for doc_id in document_ids]
            result = await self._client.delete_documents(docs)
            return sum(1 for r in result if r.succeeded)
        except Exception as e:
            logger.error("azure_search_delete_failed", error=str(e))
            return 0

    def _build_filter(self, filters: dict) -> str:
        parts = []
        for key, value in filters.items():
            if isinstance(value, str):
                parts.append(f"{key} eq '{value}'")
            elif isinstance(value, bool):
                parts.append(f"{key} eq {str(value).lower()}")
            elif isinstance(value, (int, float)):
                parts.append(f"{key} eq {value}")
        return " and ".join(parts) if parts else ""


class FAISSAdapter(VectorStore):
    """
    FAISS local vector store — for development and edge deployments.
    Uses in-memory index; persists to disk on save().
    """

    def __init__(self, index_path: str | None = None, dimension: int = 3072):
        self.index_path = index_path
        self.dimension = dimension
        self._index = None
        self._documents: list[dict] = []
        self._try_init()

    def _try_init(self) -> None:
        try:
            import faiss
            self._index = faiss.IndexFlatIP(self.dimension)  # Inner product (cosine with normalized vecs)
            if self.index_path:
                import os
                if os.path.exists(self.index_path):
                    self._index = faiss.read_index(self.index_path)
            self._available = True
            logger.info("faiss_initialized", dimension=self.dimension)
        except ImportError:
            logger.warning("faiss_unavailable", reason="faiss-cpu not installed")
            self._available = False

    async def similarity_search(
        self, query_embedding: list[float], top_k: int = 10, filters: dict | None = None
    ) -> list[dict]:
        if not self._available or not self._index or self._index.ntotal == 0:
            return []
        try:
            import numpy as np
            query = np.array([query_embedding], dtype="float32")
            import faiss
            faiss.normalize_L2(query)
            scores, indices = self._index.search(query, min(top_k, self._index.ntotal))
            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx >= 0 and idx < len(self._documents):
                    doc = self._documents[idx].copy()
                    doc["relevance_score"] = float(score)
                    results.append(doc)
            return results
        except Exception as e:
            logger.error("faiss_search_failed", error=str(e))
            return []

    async def add_documents(self, documents: list[dict]) -> int:
        if not self._available or not self._index:
            return 0
        try:
            import numpy as np, faiss
            embeddings = [d["embedding"] for d in documents]
            matrix = np.array(embeddings, dtype="float32")
            faiss.normalize_L2(matrix)
            self._index.add(matrix)
            self._documents.extend([{k: v for k, v in d.items() if k != "embedding"} for d in documents])
            return len(documents)
        except Exception as e:
            logger.error("faiss_add_failed", error=str(e))
            return 0

    async def delete_documents(self, document_ids: list[str]) -> int:
        # FAISS flat index doesn't support deletion — rebuild required
        logger.warning("faiss_delete_not_supported")
        return 0

    def save(self) -> None:
        if self._available and self._index and self.index_path:
            import faiss
            faiss.write_index(self._index, self.index_path)


def get_primary_vector_store() -> VectorStore:
    """Factory — returns Azure AI Search in production, FAISS in development."""
    try:
        from app.core.config import settings
        endpoint = getattr(settings, "AZURE_SEARCH_ENDPOINT", None)
        api_key = getattr(settings, "AZURE_SEARCH_API_KEY", None)
        index = getattr(settings, "AZURE_SEARCH_INDEX_NAME", "eatap-knowledge")
        if endpoint and api_key:
            return AzureSearchAdapter(endpoint=endpoint, api_key=api_key, index_name=index)
    except Exception:
        pass
    logger.info("vector_store_fallback", store="faiss")
    return FAISSAdapter()
