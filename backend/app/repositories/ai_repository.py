"""Repository thao tác dữ liệu với bảng documents và ai_jobs."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.ai import AiJob, Document


async def create_document(
    session: AsyncSession,
    *,
    uploaded_by: uuid.UUID,
    filename: str,
    mime: str,
    size_bytes: int,
    pages: int,
    sha256: str,
    storage_path: str,
) -> Document:
    """Tạo bản ghi tài liệu mới."""
    doc = Document(
        uploaded_by=uploaded_by,
        filename=filename,
        mime=mime,
        size_bytes=size_bytes,
        pages=pages,
        sha256=sha256,
        storage_path=storage_path,
    )
    session.add(doc)
    await session.flush()
    return doc


async def get_document_by_id(
    session: AsyncSession,
    document_id: uuid.UUID,
) -> Document | None:
    """Lấy thông tin tài liệu theo ID."""
    stmt = select(Document).where(Document.id == document_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def find_document_by_sha256(
    session: AsyncSession,
    sha256: str,
) -> Document | None:
    """Tìm tài liệu theo mã băm sha256."""
    stmt = select(Document).where(Document.sha256 == sha256).order_by(Document.created_at.desc())
    result = await session.execute(stmt)
    return result.scalars().first()


async def create_ai_job(
    session: AsyncSession,
    *,
    kind: str = "quiz_generation",
    created_by: uuid.UUID,
    document_id: uuid.UUID | None,
    params: dict[str, Any],
) -> AiJob:
    """Tạo mới một tác vụ nền AI với trạng thái khởi tạo là 'queued'."""
    job = AiJob(
        kind=kind,
        status="queued",
        created_by=created_by,
        document_id=document_id,
        params=params,
    )
    session.add(job)
    await session.flush()
    return job


async def get_ai_job_by_id(
    session: AsyncSession,
    job_id: uuid.UUID,
) -> AiJob | None:
    """Lấy thông tin tác vụ AI theo ID, nạp kèm tài liệu liên quan."""
    stmt = select(AiJob).options(selectinload(AiJob.document)).where(AiJob.id == job_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def update_ai_job_status(
    session: AsyncSession,
    job_id: uuid.UUID,
    *,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_hit: bool = False,
    finished: bool = False,
) -> AiJob | None:
    """Cập nhật trạng thái và kết quả của tác vụ AI."""
    job = await get_ai_job_by_id(session, job_id)
    if not job:
        return None

    job.status = status
    if result is not None:
        job.result = result
    if error is not None:
        job.error = error
    if input_tokens > 0:
        job.input_tokens = input_tokens
    if output_tokens > 0:
        job.output_tokens = output_tokens
    if cache_hit:
        job.cache_hit = cache_hit
    if finished:
        job.finished_at = datetime.now(UTC)

    await session.flush()
    return job
