"""
RAG Ingestion Pipeline — Document loading, chunking, embedding, and indexing.
Supports PDF, DOCX, Markdown, HTML via Azure Document Intelligence.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import (
    PyPDFLoader,
    UnstructuredMarkdownLoader,
    UnstructuredWordDocumentLoader,
)
from langchain_core.documents import Document
from langchain_openai import AzureOpenAIEmbeddings
from structlog import get_logger

logger = get_logger(__name__)

CHUNK_CONFIG = {
    "testing_standards":     {"size": 800,  "overlap": 100},
    "org_guidelines":        {"size": 800,  "overlap": 100},
    "existing_test_cases":   {"size": 400,  "overlap": 50},
    "domain_documents":      {"size": 1000, "overlap": 150},
    "business_rules":        {"size": 300,  "overlap": 40},
    "test_templates":        {"size": 512,  "overlap": 64},
    "regulatory_documents":  {"size": 1500, "overlap": 150},
    "qa_checklists":         {"size": 300,  "overlap": 40},
    "naming_standards":      {"size": 256,  "overlap": 32},
    "default":               {"size": 600,  "overlap": 80},
}


def _load_document(file_path: str, content: bytes | None = None) -> list[Document]:
    ext = Path(file_path).suffix.lower()
    if content:
        tmp = Path(f"/tmp/{Path(file_path).name}")
        tmp.write_bytes(content)
        file_path = str(tmp)

    if ext == ".pdf":
        return PyPDFLoader(file_path).load()
    elif ext in (".docx", ".doc"):
        return UnstructuredWordDocumentLoader(file_path).load()
    elif ext in (".md", ".markdown"):
        return UnstructuredMarkdownLoader(file_path).load()
    else:
        from langchain_community.document_loaders import TextLoader
        return TextLoader(file_path, encoding="utf-8").load()


def _chunk_documents(
    docs: list[Document], category: str
) -> list[Document]:
    cfg = CHUNK_CONFIG.get(category, CHUNK_CONFIG["default"])
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=cfg["size"],
        chunk_overlap=cfg["overlap"],
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(docs)


async def trigger_ingestion(
    document_id: str,
    blob_path: str,
    category: str,
    content: bytes,
) -> int:
    """
    Full ingestion pipeline:
    1. Load → 2. Chunk → 3. Enrich metadata → 4. Embed → 5. Index
    Returns the number of chunks indexed.
    """
    from app.core.config import settings

    logger.info("ingestion_started", document_id=document_id, category=category)

    # Load
    docs = _load_document(blob_path, content)
    if not docs:
        raise ValueError(f"No content extracted from {blob_path}")

    # Chunk
    chunks = _chunk_documents(docs, category)

    # Enrich metadata
    content_hash = hashlib.sha256(content).hexdigest()
    for i, chunk in enumerate(chunks):
        chunk.metadata.update({
            "document_id": document_id,
            "category": category,
            "blob_path": blob_path,
            "chunk_index": i,
            "total_chunks": len(chunks),
            "content_hash": content_hash,
            "source": blob_path.split("/")[-1],
        })

    # Embed and index into Azure AI Search
    embeddings = AzureOpenAIEmbeddings(
        model=settings.AZURE_OPENAI_EMBEDDING_DEPLOYMENT,
        azure_endpoint=str(settings.AZURE_OPENAI_ENDPOINT),
        api_key=settings.AZURE_OPENAI_API_KEY.get_secret_value(),
        api_version=settings.AZURE_OPENAI_API_VERSION,
    )

    from langchain_community.vectorstores.azuresearch import AzureSearch
    vector_store = AzureSearch(
        azure_search_endpoint=str(settings.AZURE_SEARCH_ENDPOINT),
        azure_search_key=settings.AZURE_SEARCH_API_KEY.get_secret_value(),
        index_name=settings.AZURE_SEARCH_INDEX_NAME,
        embedding_function=embeddings.embed_query,
    )

    await vector_store.aadd_documents(chunks)

    logger.info(
        "ingestion_completed",
        document_id=document_id,
        chunks_indexed=len(chunks),
    )
    return len(chunks)
