"""
Repository Layer — Data access abstraction following the Repository Pattern.
All queries go through here; services never touch SQLAlchemy directly.
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Sequence

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from structlog import get_logger

from app.infrastructure.database.models import (
    ApprovalLogModel,
    AuditLogModel,
    KBDocumentModel,
    ReviewCommentModel,
    TestCaseModel,
    TestGenerationSessionModel,
    UserModel,
)

logger = get_logger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# User Repository
# ─────────────────────────────────────────────────────────────────────────────
class UserRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_or_create_by_oid(
        self, azure_oid: str, email: str, display_name: str, roles: list[str]
    ) -> UserModel:
        stmt = select(UserModel).where(UserModel.azure_oid == azure_oid)
        result = await self.session.execute(stmt)
        user = result.scalar_one_or_none()
        if user:
            user.email = email
            user.display_name = display_name
            user.roles = roles
            return user
        user = UserModel(
            azure_oid=azure_oid,
            email=email,
            display_name=display_name,
            roles=roles,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_by_id(self, user_id: uuid.UUID) -> UserModel | None:
        return await self.session.get(UserModel, user_id)

    async def get_by_oid(self, azure_oid: str) -> UserModel | None:
        stmt = select(UserModel).where(UserModel.azure_oid == azure_oid)
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()


# ─────────────────────────────────────────────────────────────────────────────
# Session Repository (Test Generation Sessions)
# ─────────────────────────────────────────────────────────────────────────────
class SessionRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        user_story_id: str,
        project_key: str,
        created_by: uuid.UUID | None = None,
    ) -> TestGenerationSessionModel:
        model = TestGenerationSessionModel(
            user_story_id=user_story_id,
            project_key=project_key,
            created_by=created_by,
            status="DRAFT",
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def get_by_id(
        self, session_id: uuid.UUID
    ) -> TestGenerationSessionModel | None:
        return await self.session.get(TestGenerationSessionModel, session_id)

    async def update_status(
        self, session_id: uuid.UUID, status: str
    ) -> None:
        stmt = (
            update(TestGenerationSessionModel)
            .where(TestGenerationSessionModel.id == session_id)
            .values(status=status, updated_at=func.now())
        )
        await self.session.execute(stmt)

    async def update_data(
        self,
        session_id: uuid.UUID,
        **kwargs: Any,
    ) -> None:
        stmt = (
            update(TestGenerationSessionModel)
            .where(TestGenerationSessionModel.id == session_id)
            .values(**kwargs, updated_at=func.now())
        )
        await self.session.execute(stmt)

    async def list_by_status(
        self,
        status: str,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[TestGenerationSessionModel], int]:
        count_stmt = (
            select(func.count())
            .select_from(TestGenerationSessionModel)
            .where(TestGenerationSessionModel.status == status)
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(TestGenerationSessionModel)
            .where(TestGenerationSessionModel.status == status)
            .order_by(TestGenerationSessionModel.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def list_by_user(
        self,
        user_id: uuid.UUID,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[TestGenerationSessionModel], int]:
        count_stmt = (
            select(func.count())
            .select_from(TestGenerationSessionModel)
            .where(TestGenerationSessionModel.created_by == user_id)
        )
        total = (await self.session.execute(count_stmt)).scalar() or 0

        stmt = (
            select(TestGenerationSessionModel)
            .where(TestGenerationSessionModel.created_by == user_id)
            .order_by(TestGenerationSessionModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total


# ─────────────────────────────────────────────────────────────────────────────
# Test Case Repository
# ─────────────────────────────────────────────────────────────────────────────
class TestCaseRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def bulk_create(
        self,
        session_id: uuid.UUID,
        test_cases: list[dict],
    ) -> list[TestCaseModel]:
        models = []
        for tc in test_cases:
            model = TestCaseModel(session_id=session_id, **tc)
            self.session.add(model)
            models.append(model)
        await self.session.flush()
        return models

    async def get_by_session(
        self, session_id: uuid.UUID
    ) -> Sequence[TestCaseModel]:
        stmt = (
            select(TestCaseModel)
            .where(TestCaseModel.session_id == session_id)
            .order_by(TestCaseModel.type, TestCaseModel.title)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def delete_by_session(self, session_id: uuid.UUID) -> int:
        from sqlalchemy import delete

        stmt = delete(TestCaseModel).where(
            TestCaseModel.session_id == session_id
        )
        result = await self.session.execute(stmt)
        return result.rowcount


# ─────────────────────────────────────────────────────────────────────────────
# Audit Log Repository (append-only)
# ─────────────────────────────────────────────────────────────────────────────
class AuditLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        actor_id: uuid.UUID | None = None,
        action: str = "unknown",
        session_id: uuid.UUID | None = None,
        entity_type: str | None = None,
        entity_id: uuid.UUID | None = None,
        payload: dict | None = None,
        ip_address: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLogModel:
        model = AuditLogModel(
            session_id=session_id,
            actor_id=actor_id,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            payload=payload,
            ip_address=ip_address,
            user_agent=user_agent,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def list_by_session(
        self, session_id: uuid.UUID
    ) -> Sequence[AuditLogModel]:
        stmt = (
            select(AuditLogModel)
            .where(AuditLogModel.session_id == session_id)
            .order_by(AuditLogModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()

    async def list_filtered(
        self,
        actor_id: uuid.UUID | None = None,
        action: str | None = None,
        session_id: uuid.UUID | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> tuple[Sequence[AuditLogModel], int]:
        base = select(AuditLogModel)
        count_base = select(func.count()).select_from(AuditLogModel)

        conditions = []
        if actor_id:
            conditions.append(AuditLogModel.actor_id == actor_id)
        if action:
            conditions.append(AuditLogModel.action == action)
        if session_id:
            conditions.append(AuditLogModel.session_id == session_id)
        if from_date:
            conditions.append(AuditLogModel.created_at >= from_date)
        if to_date:
            conditions.append(AuditLogModel.created_at <= to_date)

        for cond in conditions:
            base = base.where(cond)
            count_base = count_base.where(cond)

        total = (await self.session.execute(count_base)).scalar() or 0

        stmt = (
            base.order_by(AuditLogModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total


# ─────────────────────────────────────────────────────────────────────────────
# Review Comment Repository
# ─────────────────────────────────────────────────────────────────────────────
class ReviewCommentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        session_id: uuid.UUID,
        author_id: uuid.UUID,
        comment: str,
        test_case_id: uuid.UUID | None = None,
    ) -> ReviewCommentModel:
        model = ReviewCommentModel(
            session_id=session_id,
            author_id=author_id,
            comment=comment,
            test_case_id=test_case_id,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def list_by_session(
        self, session_id: uuid.UUID
    ) -> Sequence[ReviewCommentModel]:
        stmt = (
            select(ReviewCommentModel)
            .where(ReviewCommentModel.session_id == session_id)
            .order_by(ReviewCommentModel.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


# ─────────────────────────────────────────────────────────────────────────────
# KB Document Repository
# ─────────────────────────────────────────────────────────────────────────────
class KBDocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, **kwargs: Any) -> KBDocumentModel:
        model = KBDocumentModel(**kwargs)
        self.session.add(model)
        await self.session.flush()
        return model

    async def list_active(
        self,
        category: str | None = None,
        page: int = 1,
        page_size: int = 20,
    ) -> tuple[Sequence[KBDocumentModel], int]:
        base = select(KBDocumentModel).where(KBDocumentModel.is_active == True)
        count_base = (
            select(func.count())
            .select_from(KBDocumentModel)
            .where(KBDocumentModel.is_active == True)
        )
        if category:
            base = base.where(KBDocumentModel.category == category)
            count_base = count_base.where(KBDocumentModel.category == category)

        total = (await self.session.execute(count_base)).scalar() or 0
        stmt = (
            base.order_by(KBDocumentModel.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        result = await self.session.execute(stmt)
        return result.scalars().all(), total

    async def soft_delete(self, doc_id: uuid.UUID) -> None:
        stmt = (
            update(KBDocumentModel)
            .where(KBDocumentModel.id == doc_id)
            .values(is_active=False)
        )
        await self.session.execute(stmt)


# ─────────────────────────────────────────────────────────────────────────────
# Approval Log Repository
# ─────────────────────────────────────────────────────────────────────────────
class ApprovalLogRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self,
        session_id: uuid.UUID,
        action: str,
        actor_id: uuid.UUID,
        comment: str | None,
        previous_status: str,
        new_status: str,
    ) -> ApprovalLogModel:
        model = ApprovalLogModel(
            session_id=session_id,
            action=action,
            actor_id=actor_id,
            comment=comment,
            previous_status=previous_status,
            new_status=new_status,
        )
        self.session.add(model)
        await self.session.flush()
        return model

    async def list_by_session(
        self, session_id: uuid.UUID
    ) -> Sequence[ApprovalLogModel]:
        stmt = (
            select(ApprovalLogModel)
            .where(ApprovalLogModel.session_id == session_id)
            .order_by(ApprovalLogModel.created_at.desc())
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()
