"""API endpoints cho AI Jobs (tải tài liệu, sinh quiz và theo dõi tiến độ SSE).

Tuân thủ:
- FR-AIGEN-01: Upload PDF/tài liệu kiểm tra dung lượng <= 10MB, trang <= 30, magic bytes.
- FR-AIGEN-02: Tham số num_questions, difficulty, language, question_types, notes.
- FR-AIGEN-03: Trả ngay 202 Accepted (< 500ms), luồng SSE theo dõi tiến độ.
- FR-AIGEN-13: Giới hạn N lần sinh/ngày, trả 429 kèm thời điểm reset.
- FR-AUTHZ-03: Host B truy cập job của Host A trả về 404.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, UploadFile, status
from fastapi.responses import StreamingResponse
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_redis, get_session, get_token_claims, require_permissions
from app.core.errors import AppError
from app.core.rbac import PermissionCode
from app.schemas.ai import (
    AiJobDetailResponse,
    AiJobEnqueueResponse,
    AiJobParams,
)
from app.services import ai_job_service

router = APIRouter(tags=["AI Jobs"])


def _parse_question_types(raw_types: list[str] | None) -> list[str]:
    """Phân giải danh sách question_types từ Form data."""
    if not raw_types:
        return ["single"]
    result = []
    for item in raw_types:
        for piece in item.split(","):
            cleaned = piece.strip()
            if cleaned:
                result.append(cleaned)
    return result or ["single"]


@router.post(
    "/ai/quiz-generations",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AiJobEnqueueResponse,
    summary="Tải tài liệu PDF và yêu cầu sinh Quiz bằng AI",
)
@router.post(
    "/ai-jobs/upload",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=AiJobEnqueueResponse,
    include_in_schema=False,
)
async def generate_quiz_from_document(
    file: Annotated[
        UploadFile, File(description="Tệp PDF cần sinh quiz (tối đa 10 MB, <= 30 trang)")
    ],
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    claims: Annotated[
        dict[str, Any], Depends(require_permissions(PermissionCode.QUIZ_AI_GENERATE))
    ],
    num_questions: Annotated[int, Form(description="Số lượng câu hỏi cần sinh (5-20)")] = 5,
    difficulty: Annotated[str, Form(description="Độ khó (easy, medium, hard)")] = "medium",
    language: Annotated[str, Form(description="Ngôn ngữ (vi, en)")] = "vi",
    question_types: Annotated[
        list[str] | None, Form(description="Loại câu hỏi (single, multiple, true_false)")
    ] = None,
    notes: Annotated[str | None, Form(description="Ghi chú thêm cho AI (tối đa 300 ký tự)")] = None,
) -> AiJobEnqueueResponse:
    """Tiếp nhận tệp tài liệu và bắt đầu tác vụ sinh câu hỏi ngầm trong ARQ worker."""
    user_id = uuid.UUID(claims["sub"])
    parsed_types = _parse_question_types(question_types)

    # Validate tham số đầu vào bằng Pydantic (FR-AIGEN-02)
    try:
        params = AiJobParams(
            num_questions=num_questions,
            difficulty=difficulty,  # type: ignore[arg-type]
            language=language,  # type: ignore[arg-type]
            question_types=parsed_types,  # type: ignore[arg-type]
            notes=notes,
        )
    except Exception as exc:
        raise AppError(
            f"Tham số sinh quiz không hợp lệ: {exc}",
            code="INVALID_AI_PARAMS",
            status_code=422,
        ) from exc

    # Đọc nội dung tệp
    file_bytes = await file.read()
    filename = file.filename or "uploaded_document.pdf"

    # Enqueue job trong transaction
    async with session.begin():
        res = await ai_job_service.enqueue_quiz_generation_job(
            session=session,
            redis=redis,
            user_id=user_id,
            file_bytes=file_bytes,
            filename=filename,
            params=params,
        )

    return res


@router.get(
    "/ai-jobs/{id}",
    response_model=AiJobDetailResponse,
    summary="Xem chi tiết trạng thái tác vụ nền AI",
)
async def get_ai_job_status(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    claims: Annotated[dict[str, Any], Depends(get_token_claims)],
) -> Any:
    """Lấy thông tin và tiến độ của tác vụ AI. BOLA: Trả 404 nếu không phải chủ sở hữu."""
    user_id = uuid.UUID(claims["sub"])
    return await ai_job_service.get_job_detail_for_user(session, id, user_id)


@router.get(
    "/ai-jobs/{id}/events",
    summary="Theo dõi tiến độ tác vụ AI qua Server-Sent Events (SSE)",
)
async def subscribe_ai_job_events(
    id: uuid.UUID,
    session: Annotated[AsyncSession, Depends(get_session)],
    redis: Annotated[Redis, Depends(get_redis)],
    claims: Annotated[dict[str, Any], Depends(get_token_claims)],
) -> StreamingResponse:
    """Stream sự kiện trạng thái (queued -> running -> succeeded / failed).

    Trả về header X-Accel-Buffering: no để tránh Nginx proxy gom bộ đệm.
    """
    user_id = uuid.UUID(claims["sub"])
    job = await ai_job_service.get_job_detail_for_user(session, id, user_id)
    event_generator = ai_job_service.stream_job_events(redis, job)
    return StreamingResponse(
        event_generator,
        media_type="text/event-stream",
        headers={
            "X-Accel-Buffering": "no",
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
