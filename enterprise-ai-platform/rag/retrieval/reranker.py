"""
Re-ranker — Cohere Rerank v3 integration for post-retrieval relevance scoring.
Significantly improves retrieval precision by re-ordering results before
they are passed to the LLM context window.
"""
from __future__ import annotations

from structlog import get_logger

logger = get_logger(__name__)


class CohereReranker:
    """
    Re-ranks retrieved documents using Cohere Rerank v3.
    Falls back to relevance-score ordering if Cohere is unavailable.
    """

    MODEL = "rerank-english-v3.0"
    DEFAULT_TOP_N = 5

    def __init__(self, api_key: str | None = None, top_n: int = DEFAULT_TOP_N):
        self.api_key = api_key
        self.top_n = top_n
        self._client = None
        self._available = False
        if api_key:
            self._try_init(api_key)

    def _try_init(self, api_key: str) -> None:
        try:
            import cohere
            self._client = cohere.AsyncClient(api_key=api_key)
            self._available = True
            logger.info("cohere_reranker_initialized", model=self.MODEL)
        except ImportError:
            logger.warning("cohere_unavailable", reason="cohere package not installed")
        except Exception as e:
            logger.warning("cohere_init_failed", error=str(e))

    async def rerank(
        self,
        query: str,
        documents: list[dict],
        top_n: int | None = None,
    ) -> list[dict]:
        """
        Re-rank documents by relevance to query.
        Returns documents sorted by rerank_score (descending) with score injected.
        """
        if not documents:
            return documents

        effective_top_n = top_n or self.top_n

        if self._available and self._client:
            return await self._rerank_cohere(query, documents, effective_top_n)

        # Fallback: sort by existing relevance_score
        return self._fallback_sort(documents, effective_top_n)

    async def _rerank_cohere(
        self, query: str, documents: list[dict], top_n: int
    ) -> list[dict]:
        try:
            texts = [
                doc.get("compressed_text") or doc.get("chunk_text") or doc.get("content", "")
                for doc in documents
            ]
            response = await self._client.rerank(
                model=self.MODEL,
                query=query,
                documents=texts,
                top_n=min(top_n, len(documents)),
                return_documents=False,
            )

            reranked = []
            for result in response.results:
                doc = documents[result.index].copy()
                doc["rerank_score"] = round(result.relevance_score, 4)
                reranked.append(doc)

            logger.debug(
                "cohere_rerank_complete",
                query=query[:60],
                input_count=len(documents),
                output_count=len(reranked),
                top_score=reranked[0]["rerank_score"] if reranked else 0,
            )
            return reranked

        except Exception as e:
            logger.warning("cohere_rerank_failed", error=str(e), fallback="score_sort")
            return self._fallback_sort(documents, top_n)

    def _fallback_sort(self, documents: list[dict], top_n: int) -> list[dict]:
        """Sort by existing relevance_score when Cohere is unavailable."""
        sorted_docs = sorted(
            documents,
            key=lambda d: d.get("relevance_score", d.get("rerank_score", 0)),
            reverse=True,
        )
        return sorted_docs[:top_n]


class AzureReranker:
    """
    Azure AI Search semantic ranker (L2 ranker).
    Use this when Cohere is not available or for Azure-native deployments.
    """

    async def rerank(self, query: str, documents: list[dict], top_n: int = 5) -> list[dict]:
        """
        Azure AI Search already performs semantic ranking during hybrid search.
        This method simply trims to top_n and injects a placeholder rerank_score.
        """
        if not documents:
            return documents
        trimmed = documents[:top_n]
        for i, doc in enumerate(trimmed):
            if "rerank_score" not in doc:
                # Simulate descending scores
                doc["rerank_score"] = round(1.0 - (i * 0.1), 2)
        return trimmed


def get_reranker() -> CohereReranker:
    """Factory — returns Cohere reranker, falls back to no-op if key missing."""
    try:
        from app.core.config import settings
        key = getattr(settings, "COHERE_API_KEY", None)
        return CohereReranker(api_key=key)
    except Exception:
        return CohereReranker()
