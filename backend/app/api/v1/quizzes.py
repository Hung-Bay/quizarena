"""API Quản lý Quiz và Câu hỏi (QZ-1...QZ-3, FR-QUIZ-01...05, FR-AUTHZ-03)."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_session, require_permissions
from app.core.rbac import PermissionCode
from app.models.quiz import Question, Quiz
from app.schemas.quiz import (
    QuestionCreate,
    QuestionOptionResponse,
    QuestionReorderRequest,
    QuestionResponse,
    QuestionUpdate,
    QuizCreate,
    QuizDetailResponse,
    QuizResponse,
    QuizUpdate,
)
from app.services import quiz_service

router = APIRouter(prefix="/quizzes", tags=["quizzes"])

RequireQuizWrite = Annotated[
    dict[str, Any], Depends(require_permissions(PermissionCode.QUIZ_WRITE_OWN))
]
DatabaseSession = Annotated[AsyncSession, Depends(get_session)]


def _to_question_response(q: Question) -> QuestionResponse:
    return QuestionResponse(
        id=q.id,
        quiz_id=q.quiz_id,
        position=q.position,
        type=q.type,
        content=q.content,
        time_limit_s=q.time_limit_s,
        points=q.points,
        explanation=q.explanation,
        topic=q.topic,
        source_ref=q.source_ref,
        ai_generated=q.ai_generated,
        created_at=q.created_at,
        options=[
            QuestionOptionResponse(
                id=opt.id,
                position=opt.position,
                content=opt.content,
                is_correct=opt.is_correct,
            )
            for opt in q.options
        ],
    )


def _to_quiz_response(quiz: Quiz) -> QuizResponse:
    q_count = len(quiz.questions) if "questions" in quiz.__dict__ and quiz.questions else 0
    return QuizResponse(
        id=quiz.id,
        owner_id=quiz.owner_id,
        title=quiz.title,
        description=quiz.description,
        visibility=quiz.visibility,
        status=quiz.status,
        version=quiz.version,
        tags=quiz.tags,
        play_count=quiz.play_count,
        question_count=q_count,
        created_at=quiz.created_at,
        updated_at=quiz.updated_at,
    )


def _to_quiz_detail_response(quiz: Quiz) -> QuizDetailResponse:
    questions = [_to_question_response(q) for q in (quiz.questions or [])]
    return QuizDetailResponse(
        id=quiz.id,
        owner_id=quiz.owner_id,
        title=quiz.title,
        description=quiz.description,
        visibility=quiz.visibility,
        status=quiz.status,
        version=quiz.version,
        tags=quiz.tags,
        play_count=quiz.play_count,
        question_count=len(questions),
        created_at=quiz.created_at,
        updated_at=quiz.updated_at,
        questions=questions,
    )


@router.post("", response_model=QuizDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_quiz(
    body: QuizCreate,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuizDetailResponse:
    """Tạo mới một bộ Quiz ở trạng thái draft (QZ-1, FR-QUIZ-01)."""
    user_id = uuid.UUID(claims["sub"])
    async with session.begin():
        quiz = await quiz_service.create_quiz(session, user_id, body)
    return _to_quiz_detail_response(quiz)


@router.get("", response_model=list[QuizResponse])
async def list_quizzes(
    claims: RequireQuizWrite,
    session: DatabaseSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
) -> list[QuizResponse]:
    """Lấy danh sách các Quiz do chính host đang đăng nhập tạo ra."""
    user_id = uuid.UUID(claims["sub"])
    async with session.begin():
        quizzes = await quiz_service.list_user_quizzes(session, user_id, skip=skip, limit=limit)
    return [_to_quiz_response(q) for q in quizzes]


@router.get("/{id}", response_model=QuizDetailResponse)
async def get_quiz(
    id: uuid.UUID,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuizDetailResponse:
    """Lấy thông tin chi tiết Quiz kèm danh sách câu hỏi (FR-AUTHZ-03: host khác -> 404)."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        quiz = await quiz_service.get_quiz(session, id, user_id, user_roles)
    return _to_quiz_detail_response(quiz)


@router.patch("/{id}", response_model=QuizResponse)
async def update_quiz(
    id: uuid.UUID,
    body: QuizUpdate,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuizResponse:
    """Cập nhật thông tin tiêu đề, mô tả, visibility, tags của Quiz."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        quiz = await quiz_service.update_quiz(session, id, user_id, user_roles, body)
    return _to_quiz_response(quiz)


@router.delete("/{id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_quiz(
    id: uuid.UUID,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> Response:
    """Xóa bộ Quiz (FR-AUTHZ-03)."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        await quiz_service.delete_quiz(session, id, user_id, user_roles)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{id}/publish", response_model=QuizDetailResponse)
async def publish_quiz(
    id: uuid.UUID,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuizDetailResponse:
    """Xuất bản Quiz khi đạt đủ điều kiện (>= 3 câu và mọi câu hợp lệ) (QZ-3, FR-QUIZ-05)."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        quiz = await quiz_service.publish_quiz(session, id, user_id, user_roles)
    return _to_quiz_detail_response(quiz)


@router.post(
    "/{id}/questions", response_model=QuestionResponse, status_code=status.HTTP_201_CREATED
)
async def add_question(
    id: uuid.UUID,
    body: QuestionCreate,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuestionResponse:
    """Thêm một câu hỏi vào Quiz (kiểm tra bộ luật QZ-2, vi phạm -> 422)."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        question = await quiz_service.add_question(session, id, user_id, user_roles, body)
    return _to_question_response(question)


@router.put("/{id}/questions/reorder", response_model=QuizDetailResponse)
async def reorder_questions(
    id: uuid.UUID,
    body: QuestionReorderRequest,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuizDetailResponse:
    """Đổi thứ tự danh sách câu hỏi trong Quiz (QZ-3, FR-QUIZ-04)."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        quiz = await quiz_service.reorder_questions(session, id, user_id, user_roles, body)
    return _to_quiz_detail_response(quiz)


@router.get("/{id}/questions/{qid}", response_model=QuestionResponse)
async def get_question(
    id: uuid.UUID,
    qid: uuid.UUID,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuestionResponse:
    """Xem chi tiết một câu hỏi trong Quiz."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        question = await quiz_service.get_question(session, id, qid, user_id, user_roles)
    return _to_question_response(question)


@router.put("/{id}/questions/{qid}", response_model=QuestionResponse)
async def update_question(
    id: uuid.UUID,
    qid: uuid.UUID,
    body: QuestionUpdate,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> QuestionResponse:
    """Sửa câu hỏi trong Quiz (kiểm tra bộ luật QZ-2, vi phạm -> 422)."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        question = await quiz_service.update_question(session, id, qid, user_id, user_roles, body)
    return _to_question_response(question)


@router.delete("/{id}/questions/{qid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_question(
    id: uuid.UUID,
    qid: uuid.UUID,
    claims: RequireQuizWrite,
    session: DatabaseSession,
) -> Response:
    """Xóa một câu hỏi khỏi Quiz."""
    user_id = uuid.UUID(claims["sub"])
    user_roles = claims.get("roles", [])
    async with session.begin():
        await quiz_service.delete_question(session, id, qid, user_id, user_roles)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
