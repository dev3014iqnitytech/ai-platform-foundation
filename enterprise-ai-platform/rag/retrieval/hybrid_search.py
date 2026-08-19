"""
Enterprise RAG Retrieval — Hybrid search with multi-query, re-ranking,
contextual compression, and Redis semantic cache.

Pipeline:
1. Semantic cache check (Redis) → return cached if hit
2. Multi-query expansion (GPT-4o-mini) → better recall
3. Hybrid dense+sparse search (Azure AI Search) — or FAISS in LOCAL_MODE
4. Cohere Re-ranking → precision boost (skipped when COHERE_API_KEY unset)
5. Contextual compression → reduce context window
6. Cache result in Redis
"""
from __future__ import annotations

import hashlib
import json

from langchain_classic.retrievers import ContextualCompressionRetriever
from langchain_classic.retrievers.multi_query import MultiQueryRetriever
from langchain_core.documents import Document
from structlog import get_logger

logger = get_logger(__name__)


class EnterpriseRAGRetriever:
    """
    Production RAG retriever with:
    - Hybrid search (dense vector + BM25 keyword) via Azure AI Search
    - FAISS in-memory fallback when Azure Search is not configured (LOCAL_MODE)
    - Multi-query expansion for better recall
    - Cohere re-ranking for precision (optional)
    - Redis semantic cache (configurable TTL)
    """

    def __init__(self):
        from app.core.config import settings
        from app.core.llm_factory import get_embeddings, get_mini_llm

        self._settings = settings
        self.embeddings = get_embeddings()
        self.router_llm = get_mini_llm(temperature=0.0, max_tokens=200)

        # Vector store: Azure AI Search (cloud) or FAISS (local / fallback)
        if settings.LOCAL_MODE or not settings.AZURE_SEARCH_ENDPOINT:
            self.vector_store = self._init_faiss()
        else:
            self.vector_store = self._init_azure_search(settings)

        # Cohere reranker (optional — gracefully skipped if key not set)
        self.reranker = None
        if settings.COHERE_API_KEY:
            try:
                from langchain.retrievers.document_compressors import CohereRerank
                self.reranker = CohereRerank(
                    cohere_api_key=settings.COHERE_API_KEY.get_secret_value(),
                    top_n=5,
                    model="rerank-english-v3.0",
                )
            except Exception as e:
                logger.warning("cohere_reranker_unavailable", error=str(e))

        self._redis = None

    def _init_faiss(self):
        """Initialize an in-memory FAISS vector store for local development."""
        try:
            from langchain_community.vectorstores import FAISS
            # Start with an empty store — documents are added via the ingestion pipeline
            logger.info("rag_vector_store", backend="faiss_local")
            return FAISS.from_texts(
                texts=["FAISS local vector store initialized"],
                embedding=self.embeddings,
            )
        except Exception as e:
            logger.error("faiss_init_failed", error=str(e))
            raise RuntimeError(
                "FAISS initialization failed. Ensure faiss-cpu is installed."
            ) from e

    def _init_azure_search(self, settings):
        """Initialize Azure AI Search vector store."""
        from langchain_community.vectorstores.azuresearch import AzureSearch

        logger.info("rag_vector_store", backend="azure_search")
        return AzureSearch(
            azure_search_endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
            azure_search_key=settings.AZURE_SEARCH_API_KEY.get_secret_value(),
            index_name=settings.AZURE_SEARCH_INDEX_NAME,
            embedding_function=self.embeddings.embed_query,
        )

    async def _get_redis(self):
        if self._redis is None:
            import redis.asyncio as aioredis
            self._redis = aioredis.from_url(
                self._settings.redis_url_str, decode_responses=True
            )
        return self._redis

    def _cache_key(self, query: str, filters: dict | None, top_k: int) -> str:
        payload = json.dumps(
            {"q": query, "f": filters, "k": top_k}, sort_keys=True
        )
        return f"rag_cache:{hashlib.sha256(payload.encode()).hexdigest()}"

    async def _check_cache(self, key: str) -> list[Document] | None:
        if not self._settings.ENABLE_SEMANTIC_CACHE:
            return None
        try:
            redis = await self._get_redis()
            cached = await redis.get(key)
            if cached:
                data = json.loads(cached)
                logger.info("rag_cache_hit", key=key[:20])
                return [
                    Document(
                        page_content=d["content"],
                        metadata=d["metadata"],
                    )
                    for d in data
                ]
        except Exception as e:
            logger.warning("rag_cache_check_failed", error=str(e))
        return None

    async def _set_cache(self, key: str, docs: list[Document]) -> None:
        if not self._settings.ENABLE_SEMANTIC_CACHE:
            return
        try:
            redis = await self._get_redis()
            data = [
                {"content": d.page_content, "metadata": d.metadata}
                for d in docs
            ]
            await redis.setex(key, 3600, json.dumps(data))  # 1h TTL
        except Exception as e:
            logger.warning("rag_cache_set_failed", error=str(e))

    async def retrieve(
        self,
        query: str,
        filters: dict | None = None,
        top_k: int = 10,
    ) -> list[Document]:
        """
        Main retrieval method. Checks cache first, then runs full pipeline.
        """
        cache_key = self._cache_key(query, filters, top_k)
        cached = await self._check_cache(cache_key)
        if cached:
            return cached

        # Build base retriever (hybrid search)
        search_kwargs: dict = {"k": top_k}
        if filters:
            filter_str = " and ".join(
                [f"{k} eq '{v}'" for k, v in filters.items()]
            )
            search_kwargs["filters"] = filter_str

        base_retriever = self.vector_store.as_retriever(
            search_type="hybrid",
            search_kwargs=search_kwargs,
        )

        # Multi-query expansion for better recall
        if self._settings.ENABLE_MULTI_QUERY_RETRIEVAL:
            retriever = MultiQueryRetriever.from_llm(
                retriever=base_retriever,
                llm=self.router_llm,
                include_original=True,
            )
        else:
            retriever = base_retriever

        # Add re-ranking if Cohere is configured
        if self.reranker:
            retriever = ContextualCompressionRetriever(
                base_compressor=self.reranker,
                base_retriever=retriever,
            )

        # Execute retrieval
        docs = await retriever.ainvoke(query)

        # Deduplicate
        seen: set[str] = set()
        unique_docs: list[Document] = []
        for doc in docs:
            key = doc.page_content[:100]
            if key not in seen:
                seen.add(key)
                unique_docs.append(doc)

        result = unique_docs[:top_k]
        logger.info(
            "rag_retrieval_complete",
            query_preview=query[:80],
            retrieved=len(result),
            cached=False,
        )

        await self._set_cache(cache_key, result)
        return result
