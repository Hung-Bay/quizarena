"""Dịch vụ điều phối nghiệp vụ cho AI Jobs và Documents.

Quản lý luồng:
1. Upload & Kiểm tra file PDF (dung lượng, magic bytes, trang).
2. Kiểm soát hạn mức quota host theo ngày giờ Pacific (FR-AIGEN-13).
3. Lưu trữ tệp và tạo bản ghi DB (Document, AiJob).
4. Đẩy tác vụ vào hàng đợi ARQ worker và phát thông báo SSE ban đầu.
5. Truyền phát sự kiện tiến độ SSE qua Redis Pub/Sub theo chuẩn W3C EventSource.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from collections.abc import AsyncIterator

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.ai import AiJob
from app.repositories import ai_repository
from app.schemas.ai import AiJobEnqueueResponse, AiJobParams
from app.services.document_service import save_uploaded_file, validate_pdf_content
from app.services.quota_service import check_and_increment_host_ai_quota

log = logging.getLogger("app.services.ai_job")

_arq_pool: ArqRedis | None = None


async def get_arq_redis_pool() -> ArqRedis:
    """Khởi tạo và tái sử dụng kết nối ArqRedis client pool."""
    global _arq_pool
    if _arq_pool is None:
        settings = get_settings()
        _arq_pool = await create_pool(RedisSettings.from_dsn(settings.redis_url.get_secret_value()))
    return _arq_pool


async def enqueue_quiz_generation_job(
    session: AsyncSession,
    redis: Redis,
    *,
    user_id: uuid.UUID,
    file_bytes: bytes,
    filename: str,
    params: AiJobParams,
    upload_dir: str | None = None,
    arq_pool: ArqRedis | None = None,
) -> AiJobEnqueueResponse:
    """Tiếp nhận tệp, xác thực điều kiện và đưa job vào hàng đợi ARQ.

    Đảm bảo phản hồi nhanh (< 500ms) theo yêu cầu FR-AIGEN-03.
    """
    settings = get_settings()
    actual_upload_dir = upload_dir or settings.upload_dir

    # 1. Kiểm tra quota của host (FR-AIGEN-13, 429 nếu hết hạn mức)
    await check_and_increment_host_ai_quota(
        redis=redis,
        user_id=user_id,
        daily_limit=settings.ai_generations_per_host_per_day,
    )

    # 2. Kiểm tra tính hợp lệ của tài liệu PDF (FR-AIGEN-01)
    pages_count, sha256 = validate_pdf_content(file_bytes)

    # 3. Lưu tệp vào ổ đĩa / volume dùng chung
    storage_path = await asyncio.to_thread(
        save_uploaded_file, file_bytes, actual_upload_dir, sha256
    )

    # 4. Ghi nhận vào cơ sở dữ liệu
    doc = await ai_repository.create_document(
        session,
        uploaded_by=user_id,
        filename=filename,
        mime="application/pdf",
        size_bytes=len(file_bytes),
        pages=pages_count,
        sha256=sha256,
        storage_path=storage_path,
    )

    params_dict = params.model_dump()
    job = await ai_repository.create_ai_job(
        session,
        kind="quiz_generation",
        created_by=user_id,
        document_id=doc.id,
        params=params_dict,
    )

    # 5. Đẩy tác vụ vào hàng đợi ARQ
    try:
        pool = arq_pool or await get_arq_redis_pool()
        await pool.enqueue_job(
            "generate_quiz_task",
            str(job.id),
            _job_id=str(job.id),
        )
    except Exception as exc:
        log.error("Lỗi khi enqueue ARQ job: %s", exc)
        # Nếu Redis arq queue lỗi, vẫn phát pub/sub để client biết
        pass

    # 6. Phát sự kiện ban đầu 'queued' lên Redis Pub/Sub
    init_event = {
        "job_id": str(job.id),
        "status": "queued",
        "quiz_id": None,
        "error": None,
    }
    await redis.publish(f"ai:job:{job.id}", json.dumps(init_event))

    return AiJobEnqueueResponse(
        job_id=job.id,
        status="queued",
        document_id=doc.id,
        message="Tác vụ sinh quiz đã được đưa vào hàng đợi xử lý",
    )


async def get_job_detail_for_user(
    session: AsyncSession,
    job_id: uuid.UUID,
    user_id: uuid.UUID,
) -> AiJob:
    """Lấy chi tiết tác vụ AI và kiểm tra quyền sở hữu (FR-AUTHZ-03)."""
    job = await ai_repository.get_ai_job_by_id(session, job_id)
    if not job or job.created_by != user_id:
        # BOLA: Trả 404 để không để lộ sự tồn tại của job
        raise AppError("Không tìm thấy tác vụ AI", code="NOT_FOUND", status_code=404)
    return job


async def stream_job_events(
    redis: Redis,
    job: AiJob,
) -> AsyncIterator[str]:
    """Tạo luồng sự kiện SSE (Server-Sent Events) theo dõi tiến độ công việc."""
    # 1. Phát sự kiện ban đầu dựa trên trạng thái DB
    initial_payload = {
        "job_id": str(job.id),
        "status": job.status,
        "quiz_id": str(job.result.get("quiz_id"))
        if (job.result and isinstance(job.result, dict))
        else None,
        "error": job.error,
    }
    yield f"data: {json.dumps(initial_payload)}\n\n"

    # Nếu công việc đã kết thúc (succeeded hoặc failed), dừng luồng ngay
    if job.status in ("succeeded", "failed"):
        return

    # 2. Đăng ký nhận sự kiện realtime từ kênh Redis Pub/Sub
    pubsub = redis.pubsub()
    channel = f"ai:job:{job.id}"
    await pubsub.subscribe(channel)

    try:
        async for message in pubsub.listen():
            if message["type"] == "message":
                data = message["data"]
                if isinstance(data, bytes):
                    data = data.decode("utf-8")
                yield f"data: {data}\n\n"

                # Kiểm tra trạng thái kết thúc để đóng kết nối SSE
                try:
                    payload = json.loads(data)
                    if payload.get("status") in ("succeeded", "failed"):
                        break
                except Exception:
                    pass
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
