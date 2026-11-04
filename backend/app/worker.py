"""ARQ Worker: Xử lý các tác vụ bất đồng bộ nền trong hệ thống QuizArena.

Chức năng:
- Lắng nghe hàng đợi Redis từ ARQ.
- Thực thi tác vụ sinh câu hỏi từ tài liệu bằng AI (Gemini / FakeGemini).
- Cập nhật tiến độ vào database (bảng ai_jobs, quizzes, questions, question_options).
- Phát sự kiện trạng thái qua Redis Pub/Sub (kênh ai:job:{id}) phục vụ SSE.
- Ràng buộc: max_tries=1 với job Gemini để tránh ARQ tự động retry gây lãng phí quota.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from arq.connections import RedisSettings
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.ai.gemini_gateway import create_gemini_gateway
from app.ai.schemas import AiGenerationParams
from app.core.config import get_settings
from app.models.ai import Document
from app.models.quiz import Question, QuestionOption, Quiz
from app.repositories import ai_repository
from app.services.quiz_rules import validate_question_data

log = logging.getLogger("arq.quizarena")


async def startup(ctx: dict[str, Any]) -> None:
    """Khởi tạo tài nguyên dùng chung khi worker khởi động."""
    settings = get_settings()
    log.info("Worker khởi động trong môi trường: %s", settings.app_env)

    # Khởi tạo DB Engine & Session factory riêng cho Worker
    db_url = settings.database_url.get_secret_value()
    engine = create_async_engine(db_url, pool_pre_ping=True, echo=False)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)

    # Khởi tạo Redis client phục vụ pub/sub sự kiện
    redis_client = Redis.from_url(
        settings.redis_url.get_secret_value(),
        decode_responses=True,
    )

    # Khởi tạo AI Gateway (mặc định Fake hoặc Real theo cấu hình)
    gateway = create_gemini_gateway(settings)

    ctx["engine"] = engine
    ctx["session_factory"] = session_factory
    ctx["redis"] = redis_client
    ctx["gateway"] = gateway


async def shutdown(ctx: dict[str, Any]) -> None:
    """Giải phóng tài nguyên khi worker dừng."""
    log.info("Worker đang tắt...")
    if "redis" in ctx:
        await ctx["redis"].aclose()
    if "engine" in ctx:
        await ctx["engine"].dispose()
    log.info("Worker đã dừng an toàn.")


async def ping(ctx: dict[str, Any], who: str = "world") -> str:
    """Job thử nghiệm kiểm tra worker."""
    log.info("ping nhận từ %s", who)
    return f"pong {who}"


async def _publish_job_status(
    redis: Redis,
    job_id: str,
    status: str,
    quiz_id: str | None = None,
    error: str | None = None,
) -> None:
    """Phát sự kiện trạng thái qua Redis Pub/Sub."""
    payload = {
        "job_id": job_id,
        "status": status,
        "quiz_id": quiz_id,
        "error": error,
        "timestamp": datetime.now(UTC).isoformat(),
    }
    channel = f"ai:job:{job_id}"
    await redis.publish(channel, json.dumps(payload))
    log.debug("Đã publish tới kênh %s: %s", channel, payload)


async def generate_quiz_task(ctx: dict[str, Any], job_id_str: str) -> dict[str, Any]:
    """Tác vụ nền ARQ: Sinh quiz từ tài liệu đã tải lên bằng AI Gateway.

    Các bước xử lý:
    1. Chuyển trạng thái sang 'running', thông báo qua Redis Pub/Sub.
    2. Nạp dữ liệu AiJob và Document tương ứng.
    3. Đọc tệp PDF và gọi GeminiGatewayProtocol.
    4. Kiểm tra chất lượng câu hỏi (Lớp 2: nghiệp vụ QZ-2).
    5. Lưu bản nháp Quiz, Questions và QuestionOptions vào DB.
    6. Chuyển trạng thái sang 'succeeded', phát sự kiện hoàn thành kèm quiz_id.
    """
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    redis: Redis = ctx["redis"]
    gateway = ctx.get("gateway") or create_gemini_gateway()

    job_id = uuid.UUID(job_id_str)
    log.info("Bắt đầu xử lý AI Job: %s", job_id)

    # Bước 1: Đánh dấu 'running'
    await _publish_job_status(redis, job_id_str, "running")
    async with session_factory() as session:
        async with session.begin():
            await ai_repository.update_ai_job_status(session, job_id=job_id, status="running")

    try:
        # Bước 2: Lấy dữ liệu job & document
        async with session_factory() as session:
            job = await ai_repository.get_ai_job_by_id(session, job_id)
            if not job:
                raise ValueError(f"Không tìm thấy AiJob với ID {job_id}")

            doc: Document | None = job.document
            if not doc:
                raise ValueError(f"AiJob {job_id} không có tài liệu liên kết")

            storage_path = doc.storage_path
            doc_pages = doc.pages
            user_id = job.created_by
            params_dict = job.params

        # Bước 3: Đọc file và gọi AI Gateway
        file_bytes = await asyncio.to_thread(Path(storage_path).read_bytes)

        ai_params = AiGenerationParams(
            num_questions=params_dict.get("num_questions", 5),
            difficulty=params_dict.get("difficulty", "medium"),
            language=params_dict.get("language", "vi"),
            allowed_types=params_dict.get("question_types", ["single"]),
            note=params_dict.get("notes"),
        )

        log.info(
            "Gọi AI Gateway cho job %s (model: %s)",
            job_id,
            gateway.get_model_info().get("model_name"),
        )
        res = await gateway.generate_quiz_from_file(
            file_bytes=file_bytes,
            mime_type=doc.mime,
            params=ai_params,
        )

        draft = res.draft

        # Bước 4: Validate lớp 2 (Nghiệp vụ câu hỏi QZ-2)
        valid_questions = []
        for q_draft in draft.questions:
            options_data = [
                {"content": opt.content, "is_correct": opt.is_correct} for opt in q_draft.options
            ]
            val_res = validate_question_data(
                question_type=q_draft.type,
                content=q_draft.content,
                time_limit_s=q_draft.time_limit_s,
                points=q_draft.points,
                options=options_data,
                max_doc_pages=doc_pages,
                source_ref=q_draft.source_ref,
            )
            if not val_res.is_valid:
                log.warning(
                    "Loại câu '%s' do vi phạm luật nghiệp vụ: %s",
                    q_draft.content[:30],
                    val_res.errors,
                )
                continue

            valid_questions.append((q_draft, options_data))

        # Bước 5: Lưu Quiz bản nháp vào Database
        async with session_factory() as session:
            async with session.begin():
                quiz = Quiz(
                    owner_id=user_id,
                    title=draft.title or f"Quiz từ {doc.filename}",
                    description=draft.description
                    or f"Được sinh tự động bởi AI từ tệp {doc.filename}",
                    status="draft",
                    visibility="private",
                    version=1,
                    tags=["ai-generated"],
                )
                session.add(quiz)
                await session.flush()

                for pos, (q_draft, opt_list) in enumerate(valid_questions, start=1):
                    q_model = Question(
                        quiz_id=quiz.id,
                        position=pos,
                        type=q_draft.type,
                        content=q_draft.content,
                        explanation=q_draft.explanation,
                        topic=q_draft.topic,
                        time_limit_s=q_draft.time_limit_s,
                        points=q_draft.points,
                        source_ref=q_draft.source_ref,
                        ai_generated=True,
                    )
                    session.add(q_model)
                    await session.flush()

                    for opt_pos, opt_dict in enumerate(opt_list, start=1):
                        opt_model = QuestionOption(
                            question_id=q_model.id,
                            position=opt_pos,
                            content=opt_dict["content"],
                            is_correct=opt_dict["is_correct"],
                        )
                        session.add(opt_model)

                # Cập nhật kết quả AiJob thành công
                in_tokens = res.usage.input_tokens if res.usage else 0
                out_tokens = res.usage.output_tokens if res.usage else 0
                result_payload = {
                    "quiz_id": str(quiz.id),
                    "title": quiz.title,
                    "total_generated": len(draft.questions),
                    "valid_questions": len(valid_questions),
                }

                await ai_repository.update_ai_job_status(
                    session,
                    job_id=job_id,
                    status="succeeded",
                    result=result_payload,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    finished=True,
                )

        log.info("AI Job %s hoàn tất thành công, Quiz ID: %s", job_id, quiz.id)
        await _publish_job_status(redis, job_id_str, "succeeded", quiz_id=str(quiz.id))
        return {"status": "succeeded", "quiz_id": str(quiz.id)}

    except Exception as exc:
        log.exception("Lỗi trong quá trình xử lý AI Job %s: %s", job_id, exc)
        async with session_factory() as session:
            async with session.begin():
                await ai_repository.update_ai_job_status(
                    session,
                    job_id=job_id,
                    status="failed",
                    error=str(exc),
                    finished=True,
                )

        await _publish_job_status(redis, job_id_str, "failed", error=str(exc))
        return {"status": "failed", "error": str(exc)}


class WorkerSettings:
    """Cấu hình khởi chạy của ARQ Worker."""

    functions = [ping, generate_quiz_task]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = RedisSettings.from_dsn(get_settings().redis_url.get_secret_value())
    max_tries = 1  # Không tự động thử lại ở tầng ARQ để bảo vệ quota Gemini
