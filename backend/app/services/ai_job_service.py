"""Dịch vụ điều phối nghiệp vụ cho AI Jobs và Documents.

Quản lý luồng:
1. Upload & Kiểm tra file PDF (dung lượng, magic bytes, trang).
2. Content Caching (AG-8): Tra cứu cache theo (sha256, params_hash).
   - Nếu Cache Hit: Tạo bản nháp riêng cho Host từ kết quả cũ, 0 lần gọi Gemini,
     không tính quota N lần/ngày, ai_jobs.cache_hit = True.
3. Nếu Cache Miss: Kiểm soát hạn mức quota host theo ngày giờ Pacific (FR-AIGEN-13).
4. Lưu trữ tệp và tạo bản ghi DB (Document, AiJob).
5. Đẩy tác vụ vào hàng đợi ARQ worker và phát thông báo SSE ban đầu.
6. Truyền phát sự kiện tiến độ SSE qua Redis Pub/Sub theo chuẩn W3C EventSource.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

from arq import ArqRedis, create_pool
from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.errors import AppError
from app.models.ai import AiJob
from app.models.quiz import Question, QuestionOption, Quiz
from app.repositories import ai_repository, quiz_repository
from app.schemas.ai import AiJobEnqueueResponse, AiJobParams
from app.services.document_service import save_uploaded_file, validate_pdf_content
from app.services.quota_service import check_and_increment_host_ai_quota

log = logging.getLogger("app.services.ai_job")

_arq_pool: ArqRedis | None = None


def compute_params_hash(params: AiJobParams) -> str:
    """Tạo mã băm đại diện duy nhất cho bộ tham số sinh quiz (canonical JSON)."""
    canonical = {
        "num_questions": params.num_questions,
        "difficulty": params.difficulty,
        "language": params.language,
        "question_types": sorted(params.question_types),
        "notes": (params.notes or "").strip(),
    }
    canonical_str = json.dumps(canonical, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical_str.encode("utf-8")).hexdigest()


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

    # 1. Kiểm tra tính hợp lệ của tài liệu PDF (FR-AIGEN-01)
    pages_count, sha256 = validate_pdf_content(file_bytes)

    # 2. Tính toán params_hash phục vụ Content Caching (AG-8)
    p_hash = compute_params_hash(params)
    params_dict = params.model_dump()
    params_dict["_params_hash"] = p_hash

    # 3. Kiểm tra Content Cache: (sha256, params_hash)
    cached_job = await ai_repository.find_cached_ai_job(session, sha256=sha256, params_hash=p_hash)
    if cached_job and cached_job.result and cached_job.result.get("quiz_id"):
        cached_quiz_id = uuid.UUID(str(cached_job.result["quiz_id"]))
        cached_quiz = await quiz_repository.get_by_id(
            session, cached_quiz_id, include_questions=True
        )
        if cached_quiz:
            log.info(
                "Content Cache Hit: sha256=%s, params_hash=%s. Không gọi Gemini, không tốn quota.",
                sha256,
                p_hash,
            )
            # Lưu tài liệu cho host hiện tại
            storage_path = await asyncio.to_thread(
                save_uploaded_file, file_bytes, actual_upload_dir, sha256
            )
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

            # Sao chép Quiz và Questions thành bản nháp độc lập cho Host này
            new_quiz = Quiz(
                owner_id=user_id,
                title=cached_quiz.title,
                description=cached_quiz.description,
                status="draft",
                visibility="private",
                version=1,
                tags=list(cached_quiz.tags or ["ai-generated", "cached"]),
            )
            session.add(new_quiz)
            await session.flush()

            for q in cached_quiz.questions:
                new_q = Question(
                    quiz_id=new_quiz.id,
                    position=q.position,
                    type=q.type,
                    content=q.content,
                    explanation=q.explanation,
                    topic=q.topic,
                    time_limit_s=q.time_limit_s,
                    points=q.points,
                    source_ref=q.source_ref,
                    ai_generated=True,
                )
                session.add(new_q)
                await session.flush()

                for opt in q.options:
                    new_opt = QuestionOption(
                        question_id=new_q.id,
                        position=opt.position,
                        content=opt.content,
                        is_correct=opt.is_correct,
                    )
                    session.add(new_opt)

            await session.flush()

            # Tạo AiJob đánh dấu cache_hit=True, status="succeeded"
            new_job = await ai_repository.create_ai_job(
                session,
                kind="quiz_generation",
                created_by=user_id,
                document_id=doc.id,
                params=params_dict,
            )
            new_job.status = "succeeded"
            new_job.cache_hit = True
            new_job.finished_at = datetime.now(UTC)
            new_job.result = {
                "quiz_id": str(new_quiz.id),
                "title": new_quiz.title,
                "total_generated": len(cached_quiz.questions),
                "valid_questions": len(cached_quiz.questions),
                "cached_from_job_id": str(cached_job.id),
                "cache_hit": True,
            }
            await session.flush()

            # Phát sự kiện qua Redis Pub/Sub
            await redis.publish(
                f"ai:job:{new_job.id}",
                json.dumps(
                    {
                        "job_id": str(new_job.id),
                        "status": "succeeded",
                        "quiz_id": str(new_quiz.id),
                        "cache_hit": True,
                        "timestamp": datetime.now(UTC).isoformat(),
                    }
                ),
            )

            # Phản hồi 202 thành công ngay, không tăng quota host
            return AiJobEnqueueResponse(
                job_id=new_job.id,
                status="succeeded",
                document_id=doc.id,
                message="Bản nháp quiz đã được tạo thành công từ bộ nhớ đệm (Cache Hit - 0 lần gọi Gemini)",
            )

    # 4. Cache Miss: Kiểm tra quota của host (FR-AIGEN-13, 429 nếu hết hạn mức)
    await check_and_increment_host_ai_quota(
        redis=redis,
        user_id=user_id,
        daily_limit=settings.ai_generations_per_host_per_day,
    )

    # 5. Lưu tệp vào ổ đĩa / volume dùng chung
    storage_path = await asyncio.to_thread(
        save_uploaded_file, file_bytes, actual_upload_dir, sha256
    )

    # 6. Ghi nhận vào cơ sở dữ liệu
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

    job = await ai_repository.create_ai_job(
        session,
        kind="quiz_generation",
        created_by=user_id,
        document_id=doc.id,
        params=params_dict,
    )

    # 7. Đẩy tác vụ vào hàng đợi ARQ
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

    # 8. Phát sự kiện ban đầu 'queued' lên Redis Pub/Sub
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
