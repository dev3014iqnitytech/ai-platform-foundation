"""
Knowledge Base MCP Server — Implements the MCP HTTP transport for RAG retrieval.

Exposes POST /tools/{tool_name} endpoints consumed by MCPClient.
Owns all embedding + search credentials — agents stay credential-free.

Tools exposed:
  search          — hybrid dense+BM25 search with reranking and semantic cache
  get_document    — fetch a single document by ID
  list_categories — list available KB categories for metadata filtering

Run:  uvicorn mcp.servers.knowledge_server:app --port 8002
"""
from __future__ import annotations

from typing import Any

from fastapi import FastAPI
from fastapi.responses import ORJSONResponse
from pydantic import BaseModel
from structlog import get_logger

logger = get_logger(__name__)

app = FastAPI(
    title="Knowledge Base MCP Server",
    version="1.0.0",
    default_response_class=ORJSONResponse,
)


# ─────────────────────────────────────────────────────────────────────────────
# MCP envelope models
# ─────────────────────────────────────────────────────────────────────────────

class ToolRequest(BaseModel):
    arguments: dict[str, Any]


class ToolResponse(BaseModel):
    result: dict[str, Any] | None = None
    error: str | None = None


# ─────────────────────────────────────────────────────────────────────────────
# Health
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    """MCPClient pings this before marking the server healthy."""
    try:
        from rag.retrieval.hybrid_search import EnterpriseRAGRetriever
        retriever = EnterpriseRAGRetriever()
        # Lightweight: just verify the vector store client initialises
        _ = retriever.vector_store
        return {"status": "ok", "retriever": "ready"}
    except Exception as e:
        return {"status": "degraded", "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# Tool dispatcher
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/tools/{tool_name}", response_model=ToolResponse)
async def call_tool(tool_name: str, body: ToolRequest):
    handlers = {
        "search":           _tool_search,
        "get_document":     _tool_get_document,
        "list_categories":  _tool_list_categories,
    }
    handler = handlers.get(tool_name)
    if not handler:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail=f"Unknown tool: {tool_name}")
    try:
        result = await handler(body.arguments)
        return ToolResponse(result=result)
    except Exception as e:
        logger.error("kb_tool_error", tool=tool_name, error=str(e), exc_info=True)
        return ToolResponse(error=str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Tool implementations
# ─────────────────────────────────────────────────────────────────────────────

async def _tool_search(args: dict) -> dict:
    """
    args: {query, filters, top_k}
    returns: {chunks: [{content, source, category, score}, ...]}
    """
    from rag.retrieval.hybrid_search import EnterpriseRAGRetriever

    query = args["query"]
    filters = args.get("filters")
    top_k = int(args.get("top_k", 10))

    retriever = EnterpriseRAGRetriever()
    docs = await retriever.retrieve(query=query, filters=filters, top_k=top_k)

    chunks = [
        {
            "content": doc.page_content,
            "source": doc.metadata.get("source", "unknown"),
            "category": doc.metadata.get("category", "general"),
            "score": float(doc.metadata.get("relevance_score", 0.0)),
        }
        for doc in docs
    ]

    logger.info(
        "kb_search_completed",
        query_preview=query[:80],
        chunks_returned=len(chunks),
    )
    return {"chunks": chunks, "total": len(chunks)}


async def _tool_get_document(args: dict) -> dict:
    """
    args: {document_id}
    returns: {content, metadata}
    """
    from rag.retrieval.hybrid_search import EnterpriseRAGRetriever

    doc_id = args["document_id"]
    retriever = EnterpriseRAGRetriever()

    # Azure AI Search fetch by document key
    results = await retriever.retrieve(
        query=f"id:{doc_id}",
        filters={"document_id": doc_id},
        top_k=1,
    )
    if not results:
        return {"content": None, "metadata": {}, "found": False}

    doc = results[0]
    return {
        "content": doc.page_content,
        "metadata": doc.metadata,
        "found": True,
    }


async def _tool_list_categories(args: dict) -> dict:  # noqa: ARG001
    """
    args: {} (no args)
    returns: {categories: [...]}
    Static list kept in sync with ingestion pipeline enrichers.
    """
    return {
        "categories": [
            "auth",
            "api",
            "ui",
            "performance",
            "security",
            "integration",
            "data-validation",
            "general",
        ]
    }
