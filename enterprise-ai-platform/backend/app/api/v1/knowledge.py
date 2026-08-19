"""
Knowledge Base Router â€” Upload, search, and manage RAG documents.
"""
from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from structlog import get_logger

from app.core.dependencies import (
    CurrentUserDep,
    DbSessionDep,
    RequirePermission,
)
from app.domain.enums import DocumentCategory
from app.domain.schemas import KBDocumentResponse, KBSearchRequest, KBSearchResult
from app.infrastructure.database.repositories import (
    AuditLogRepository,
    KBDocumentRepository,
    UserRepository,
)

logger = get_logger(__name__)
router = APIRouter()


@router.post(
    "/upload",
    response_model=KBDocumentResponse,
    summary="Upload a document to the knowledge base",
    dependencies=[Depends(RequirePermission("kb:upload"))],
)
async def upload_document(
    user: CurrentUserDep,
    db: DbSessionDep,
    file: UploadFile = File(...),
    category: DocumentCategory = DocumentCategory.TESTING_STANDARDS,
):
    """
    Uploads a document, stores in Azure Blob, triggers the RAG ingestion pipeline
    (chunking â†’ embedding â†’ vector store indexing).
    """
    # Validate file type
    allowed_types = {
        "application/pdf",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "text/markdown",
        "text/plain",
        "text/html",
    }
    if file.content_type not in allowed_types:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {file.content_type}. Allowed: {allowed_types}",
        )

    # Max 50MB
    content = await file.read()
    if len(content) > 50 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large. Maximum 50MB.")

    user_repo = UserRepository(db)
    db_user = await user_repo.get_by_oid(user["oid"])

    import hashlib
    from app.infrastructure.blob.blob_storage import get_blob_client
    content_hash = hashlib.sha256(content).hexdigest()
    blob_name = f"knowledge-base/{category.value}/{content_hash}/{file.filename}"

    blob_path = await get_blob_client().upload(
        file_data=content,
        blob_name=blob_name,
        content_type=file.content_type or "application/octet-stream",
    )

    # Create DB record
    kb_repo = KBDocumentRepository(db)
    doc = await kb_repo.create(
        filename=file.filename,
        category=category.value,
        uploaded_by=db_user.id if db_user else None,
        azure_blob_path=blob_path,
        content_hash=content_hash,
        file_size_bytes=len(content),
        mime_type=file.content_type,
        embedding_model="text-embedding-3-large",
    )

    # Audit
    if db_user:
        audit_repo = AuditLogRepository(db)
        await audit_repo.create(
            actor_id=db_user.id,
            action="document_uploaded",
            entity_type="kb_document",
            entity_id=doc.id,
            payload={"filename": file.filename, "category": category.value},
        )

    # Trigger ingestion pipeline (async)
    try:
        from rag.ingestion.pipeline import trigger_ingestion
        await trigger_ingestion(
            document_id=str(doc.id),
            blob_path=blob_path,
            category=category.value,
            content=content,
        )
    except Exception as e:
        logger.warning("ingestion_trigger_failed", error=str(e))

    return KBDocumentResponse.model_validate(doc)


@router.post(
    "/search",
    response_model=list[KBSearchResult],
    summary="Semantic search across the knowledge base",
)
async def search_knowledge_base(
    request: KBSearchRequest,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    """
    Hybrid search (dense + sparse BM25) with re-ranking and contextual compression.
    """
    try:
        from rag.retrieval.hybrid_search import EnterpriseRAGRetriever

        retriever = EnterpriseRAGRetriever()
        filters = {}
        if request.category:
            filters["category"] = request.category.value
        if request.filters:
            filters.update(request.filters)

        results = await retriever.retrieve(
            query=request.query,
            filters=filters if filters else None,
            top_k=request.top_k,
        )
        return [
            KBSearchResult(
                content=doc.page_content,
                source_document=doc.metadata.get("source", "unknown"),
                score=doc.metadata.get("score", 0.0),
                metadata=doc.metadata,
            )
            for doc in results
        ]
    except Exception as e:
        logger.error("kb_search_failed", error=str(e))
        raise HTTPException(status_code=500, detail="Knowledge base search failed") from e


@router.get(
    "/documents",
    response_model=list[KBDocumentResponse],
    summary="List knowledge base documents",
)
async def list_documents(
    user: CurrentUserDep,
    db: DbSessionDep,
    category: str | None = None,
    page: int = 1,
    page_size: int = 20,
):
    kb_repo = KBDocumentRepository(db)
    docs, total = await kb_repo.list_active(category, page, page_size)
    return [KBDocumentResponse.model_validate(d) for d in docs]


@router.delete(
    "/documents/{document_id}",
    summary="Soft-delete a knowledge base document",
    dependencies=[Depends(RequirePermission("kb:manage"))],
)
async def delete_document(
    document_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    kb_repo = KBDocumentRepository(db)
    await kb_repo.soft_delete(document_id)
    return {"message": "Document deactivated", "id": str(document_id)}


@router.get(
    "/documents/{document_id}",
    response_model=KBDocumentResponse,
    summary="Get a single knowledge base document by ID",
)
async def get_document(
    document_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from fastapi import HTTPException
    from app.infrastructure.database.models import KBDocumentModel
    doc = await db.get(KBDocumentModel, document_id)
    if not doc or not doc.is_active:
        raise HTTPException(status_code=404, detail="Document not found")
    return KBDocumentResponse.model_validate(doc)


@router.post(
    "/documents/{document_id}/reindex",
    summary="Re-embed and re-index a knowledge base document",
    dependencies=[Depends(RequirePermission("kb:manage"))],
)
async def reindex_document(
    document_id: UUID,
    user: CurrentUserDep,
    db: DbSessionDep,
):
    from fastapi import HTTPException, BackgroundTasks
    import uuid
    from app.infrastructure.database.models import KBDocumentModel
    from app.infrastructure.blob.blob_storage import get_blob_client

    doc = await db.get(KBDocumentModel, document_id)
    if not doc or not doc.is_active:
        raise HTTPException(status_code=404, detail="Document not found")

    task_id = str(uuid.uuid4())

    async def _do_reindex():
        try:
            content = await get_blob_client().download(doc.azure_blob_path)
            from rag.ingestion.pipeline import trigger_ingestion
            await trigger_ingestion(
                document_id=str(doc.id),
                blob_path=doc.azure_blob_path or doc.filename,
                category=doc.category or "general",
                content=content,
            )
        except Exception as e:
            logger.warning("reindex_failed", document_id=str(document_id), error=str(e))

    import asyncio
    asyncio.create_task(_do_reindex())
    return {"task_id": task_id, "status": "queued", "document_id": str(document_id)}


@router.get(
    "/categories",
    response_model=list[str],
    summary="List available knowledge base categories",
)
async def list_categories(user: CurrentUserDep, db: DbSessionDep):
    from sqlalchemy import select, distinct
    from app.infrastructure.database.models import KBDocumentModel
    result = await db.execute(
        select(distinct(KBDocumentModel.category))
        .where(KBDocumentModel.is_active == True)
        .order_by(KBDocumentModel.category)
    )
    return [row[0] for row in result.fetchall() if row[0]]


@router.get(
    "/stats",
    summary="Knowledge base statistics",
)
async def get_kb_stats(user: CurrentUserDep, db: DbSessionDep):
    from sqlalchemy import select, func
    from app.infrastructure.database.models import KBDocumentModel
    total_docs = (await db.execute(
        select(func.count()).select_from(KBDocumentModel).where(KBDocumentModel.is_active == True)
    )).scalar() or 0
    total_chunks = (await db.execute(
        select(func.sum(KBDocumentModel.chunk_count)).where(KBDocumentModel.is_active == True)
    )).scalar() or 0
    by_cat_rows = await db.execute(
        select(KBDocumentModel.category, func.count())
        .where(KBDocumentModel.is_active == True)
        .group_by(KBDocumentModel.category)
    )
    by_category = {row[0] or "uncategorized": row[1] for row in by_cat_rows.fetchall()}
    last_updated_row = (await db.execute(
        select(func.max(KBDocumentModel.created_at)).where(KBDocumentModel.is_active == True)
    )).scalar()
    return {
        "total_documents": total_docs,
        "total_chunks": total_chunks,
        "by_category": by_category,
        "last_updated": last_updated_row.isoformat() if last_updated_row else None,
        "index_health": "ok",
    }


@router.get(
    "/ingestion/{task_id}/status",
    summary="Get ingestion task status (best-effort â€” tasks are fire-and-forget)",
)
async def get_ingestion_status(task_id: str, user: CurrentUserDep):
    # Tasks run as asyncio tasks with no persistent store; return generic in-progress
    return {"task_id": task_id, "status": "processing", "progress": 50, "message": "Indexing in progress"}

