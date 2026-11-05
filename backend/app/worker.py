"""ARQ Worker: Xử lý các tác vụ bất đồng bộ nền trong hệ thống QuizArena.

Chức năng:
- Lắng nghe hàng đợi Redis từ ARQ.
- Tích hợp Limiter toàn cục trên Redis (Lua script) kiểm soát RPM và RPD.
- Thực thi tác vụ sinh câu hỏi từ tài liệu bằng AI (Gemini / FakeGemini).
- Xác thực 2 lớp: Lớp 1 (Schema Pydantic), Lớp 2 (Nghiệp vụ QZ-2).
- Tự động retry 1 lần kèm phản hồi lỗi nếu số câu hợp lệ < 50% yêu cầu.
- Ghi nhận token usage (input/output) và attempts vào bảng ai_jobs.
- Lưu bản nháp Quiz, Questions và QuestionOptions vào cơ sở dữ liệu.
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
from app.ai.schemas import AiGenerationParams, QuestionDraft, QuizDraft
from app.core.config import get_settings
from app.models.ai import Document
from app.models.quiz import Question, QuestionOption, Quiz
from app.repositories import ai_repository
from app.services.gemini_rate_limiter import acquire_gemini_permit
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

    # Khởi tạo Redis client phục vụ pub/sub sự kiện và rate limiter
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


def _validate_draft_questions(
    draft: QuizDraft, doc_pages: int
) -> tuple[list[tuple[QuestionDraft, list[dict[str, Any]]]], list[dict[str, Any]]]:
    """Kiểm tra câu hỏi qua Lớp 2 (Nghiệp vụ QZ-2).

    Trả về:
    - valid_questions: danh sách (QuestionDraft, options_data) đạt chuẩn
    - rejected_questions: danh sách các câu bị loại kèm lý do lỗi
    """
    valid_questions = []
    rejected_questions = []

    for idx, q_draft in enumerate(draft.questions, start=1):
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
                "Loại câu %d '%s' do vi phạm luật nghiệp vụ: %s",
                idx,
                q_draft.content[:30],
                val_res.errors,
            )
            rejected_questions.append(
                {
                    "question_index": idx,
                    "content": q_draft.content,
                    "errors": val_res.errors,
                }
            )
        else:
            valid_questions.append((q_draft, options_data))

    return valid_questions, rejected_questions


async def generate_quiz_task(ctx: dict[str, Any], job_id_str: str) -> dict[str, Any]:
    """Tác vụ nền ARQ: Sinh quiz từ tài liệu đã tải lên bằng AI Gateway.

    Các bước xử lý:
    1. Chuyển trạng thái sang 'running', thông báo qua Redis Pub/Sub.
    2. Nạp dữ liệu AiJob và Document tương ứng.
    3. Xin cấp quota (permit) từ Redis Token Bucket Limiter toàn cục.
    4. Đọc tệp PDF và gọi GeminiGatewayProtocol (tự động retry 429 nếu có).
    5. Kiểm tra chất lượng câu hỏi Lớp 2 (Nghiệp vụ QZ-2).
       Nếu số câu hợp lệ < 50% yêu cầu: tự động retry 1 lần với feedback lỗi.
    6. Lưu bản nháp Quiz, Questions và QuestionOptions vào DB.
    7. Chuyển trạng thái sang 'succeeded', ghi nhận tokens & attempts, phát sự kiện hoàn tất.
    """
    session_factory: async_sessionmaker[AsyncSession] = ctx["session_factory"]
    redis: Redis = ctx["redis"]
    gateway = ctx.get("gateway") or create_gemini_gateway()
    settings = get_settings()

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

        # Bước 3: Xin permit từ Limiter toàn cục (RPM + RPD)
        await acquire_gemini_permit(
            redis,
            priority="high",
            cost=1,
            max_wait_s=60.0,
            rpm_limit=settings.gemini_rpm,
            rpd_limit=settings.gemini_rpd,
        )

        # Bước 4: Đọc file và gọi AI Gateway
        file_bytes = await asyncio.to_thread(Path(storage_path).read_bytes)

        ai_params = AiGenerationParams(
            num_questions=params_dict.get("num_questions", 5),
            difficulty=params_dict.get("difficulty", "medium"),
            language=params_dict.get("language", "vi"),
            allowed_types=params_dict.get("question_types", ["single"]),
            note=params_dict.get("notes"),
        )

        attempts_count = 1
        log.info(
            "Gọi AI Gateway cho job %s (model: %s, lần %d)",
            job_id,
            gateway.get_model_info().get("model_name"),
            attempts_count,
        )
        res = await gateway.generate_quiz_from_file(
            file_bytes=file_bytes,
            mime_type=doc.mime,
            params=ai_params,
        )

        in_tokens = res.usage.input_tokens if res.usage else 0
        out_tokens = res.usage.output_tokens if res.usage else 0
        draft = res.draft

        # Bước 5: Validate Lớp 2 (Nghiệp vụ QZ-2)
        valid_questions, rejected_questions = _validate_draft_questions(draft, doc_pages)
        required_count = ai_params.num_questions

        # Kiểm tra dung sai: Nếu số câu hợp lệ < 50% yêu cầu -> Retry 1 lần kèm phản hồi lỗi
        if len(valid_questions) < (required_count * 0.5):
            log.warning(
                "Job %s: Chỉ có %d/%d câu hợp lệ (< 50%%). Tiến hành retry 1 lần với Gemini kèm phản hồi lỗi.",
                job_id,
                len(valid_questions),
                required_count,
            )
            attempts_count += 1

            # Xin cấp permit cho lần gọi retry
            await acquire_gemini_permit(
                redis,
                priority="high",
                cost=1,
                max_wait_s=60.0,
                rpm_limit=settings.gemini_rpm,
                rpd_limit=settings.gemini_rpd,
            )

            # Tạo lời nhắc phản hồi lỗi chi tiết
            err_details = "\n".join(
                f"- Câu '{r['content'][:40]}...': {', '.join(r['errors'])}"
                for r in rejected_questions
            )
            feedback_prompt = (
                f"[PHẢN HỒI LỖI LẦN TRƯỚC]: Có {len(rejected_questions)}/{len(draft.questions)} câu bị loại vì vi phạm luật:\n"
                f"{err_details}\n"
                f"Hãy sinh lại đủ {required_count} câu hỏi hoàn toàn tuân thủ các quy tắc nghiệp vụ."
            )
            retry_note = (
                f"{ai_params.note}\n\n{feedback_prompt}" if ai_params.note else feedback_prompt
            )
            retry_params = ai_params.model_copy(update={"note": retry_note})

            # Gọi lại Gemini
            res_retry = await gateway.generate_quiz_from_file(
                file_bytes=file_bytes,
                mime_type=doc.mime,
                params=retry_params,
            )
            if res_retry.usage:
                in_tokens += res_retry.usage.input_tokens
                out_tokens += res_retry.usage.output_tokens

            retry_valid, retry_rejected = _validate_draft_questions(res_retry.draft, doc_pages)

            # Nếu lần thử lại tốt hơn, lấy kết quả lần thử lại
            if len(retry_valid) >= len(valid_questions):
                draft = res_retry.draft
                valid_questions = retry_valid
                rejected_questions = retry_rejected

            # Kiểm tra lại sau retry: nếu vẫn < 50% và không đủ tối thiểu 3 câu cho 1 quiz -> Báo lỗi
            if len(valid_questions) < (required_count * 0.5) and len(valid_questions) < 3:
                raise ValueError(
                    f"Không thể sinh đủ số câu hỏi đạt chuẩn (< 50% số câu yêu cầu đạt quy định sau 2 lần thử, chỉ có {len(valid_questions)}/{required_count} câu hợp lệ)."
                )

        # Bước 6: Lưu Quiz bản nháp vào Database
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

                # Cập nhật kết quả AiJob thành công kèm tokens và attempts
                result_payload = {
                    "quiz_id": str(quiz.id),
                    "title": quiz.title,
                    "total_generated": len(draft.questions),
                    "valid_questions": len(valid_questions),
                    "rejected_questions": rejected_questions,
                    "attempts": attempts_count,
                }

                await ai_repository.update_ai_job_status(
                    session,
                    job_id=job_id,
                    status="succeeded",
                    result=result_payload,
                    input_tokens=in_tokens,
                    output_tokens=out_tokens,
                    attempts=attempts_count,
                    finished=True,
                )

        log.info(
            "AI Job %s hoàn tất thành công, Quiz ID: %s (attempts=%d, valid=%d/%d)",
            job_id,
            quiz.id,
            attempts_count,
            len(valid_questions),
            required_count,
        )
        await _publish_job_status(redis, job_id_str, "succeeded", quiz_id=str(quiz.id))
        return {"status": "succeeded", "quiz_id": str(quiz.id), "attempts": attempts_count}

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
