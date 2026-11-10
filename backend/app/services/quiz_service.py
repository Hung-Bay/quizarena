"""Tầng nghiệp vụ (Service) cho Quiz và Question."""

import uuid
from collections.abc import Sequence

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.gateway_interface import GeminiGatewayProtocol
from app.ai.schemas import AiGenerationParams
from app.core.errors import AppError
from app.core.rbac import RoleName
from app.models.quiz import Question, QuestionOption, Quiz
from app.repositories import game_repository
from app.repositories import quiz_repository as repo
from app.schemas.quiz import (
    QuestionCreate,
    QuestionRegenerateRequest,
    QuestionReorderRequest,
    QuestionUpdate,
    QuizCloneRequest,
    QuizCreate,
    QuizUpdate,
)
from app.services.gemini_rate_limiter import acquire_gemini_permit
from app.services.quiz_rules import validate_question_data, validate_quiz_for_publishing


def _check_quiz_access(quiz: Quiz | None, user_id: uuid.UUID, user_roles: Sequence[str]) -> Quiz:
    """Kiểm tra quyền truy cập và sở hữu của Quiz.

    FR-AUTHZ-03: Nếu Host B truy cập Quiz của Host A, trả về 404 NOT FOUND (không lộ sự tồn tại).
    ADMIN có quyền quản trị toàn bộ quiz.
    """
    if quiz is None:
        raise AppError("Quiz không tồn tại", code="QUIZ_NOT_FOUND", status_code=404)

    is_admin = RoleName.ADMIN.value in user_roles
    if not is_admin and quiz.owner_id != user_id:
        raise AppError("Quiz không tồn tại", code="QUIZ_NOT_FOUND", status_code=404)

    return quiz


async def _bump_version_if_has_games(session: AsyncSession, quiz: Quiz | None) -> None:
    """Tự động tăng version nếu quiz đã từng có trận đấu mở trước đó (QZ-4, FR-QUIZ-06)."""
    if quiz is None:
        return
    has_games = await game_repository.count_sessions_by_quiz_id(session, quiz.id) > 0
    if has_games:
        quiz.version += 1


async def create_quiz(session: AsyncSession, owner_id: uuid.UUID, data: QuizCreate) -> Quiz:
    """Tạo bộ Quiz mới ở trạng thái 'draft' (QZ-1, FR-QUIZ-01)."""
    quiz = Quiz(
        owner_id=owner_id,
        title=data.title.strip(),
        description=data.description.strip() if data.description else None,
        visibility=data.visibility,
        status="draft",
        version=1,
        tags=[t.strip() for t in data.tags if t.strip()],
        play_count=0,
    )
    return await repo.create(session, quiz)


async def get_quiz(
    session: AsyncSession, quiz_id: uuid.UUID, user_id: uuid.UUID, user_roles: Sequence[str]
) -> Quiz:
    """Lấy chi tiết Quiz kèm danh sách câu hỏi và lựa chọn (FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=True)
    return _check_quiz_access(quiz, user_id, user_roles)


async def list_user_quizzes(
    session: AsyncSession, owner_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> Sequence[Quiz]:
    """Lấy danh sách Quiz của chính host đang đăng nhập."""
    return await repo.list_by_owner(session, owner_id, skip=skip, limit=limit)


async def update_quiz(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
    data: QuizUpdate,
) -> Quiz:
    """Chỉnh sửa thông tin chung của Quiz (FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    quiz = _check_quiz_access(quiz, user_id, user_roles)

    if data.title is not None:
        quiz.title = data.title.strip()
    if data.description is not None:
        quiz.description = data.description.strip() if data.description else None
    if data.visibility is not None:
        quiz.visibility = data.visibility
    if data.tags is not None:
        quiz.tags = [t.strip() for t in data.tags if t.strip()]

    await _bump_version_if_has_games(session, quiz)
    return await repo.update(session, quiz)


async def delete_quiz(
    session: AsyncSession, quiz_id: uuid.UUID, user_id: uuid.UUID, user_roles: Sequence[str]
) -> None:
    """Xóa Quiz và toàn bộ câu hỏi liên quan (FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    quiz = _check_quiz_access(quiz, user_id, user_roles)
    await repo.delete_quiz(session, quiz)


async def publish_quiz(
    session: AsyncSession, quiz_id: uuid.UUID, user_id: uuid.UUID, user_roles: Sequence[str]
) -> Quiz:
    """Xuất bản Quiz (QZ-3, FR-QUIZ-05).

    Điều kiện bắt buộc:
    - Quiz phải có >= 3 câu hỏi.
    - Mọi câu hỏi phải thỏa mãn luật QZ-2 (thời gian, điểm số, lựa chọn, v.v.).
    - Vi phạm -> 422 VALIDATION_ERROR kèm lý do chi tiết.
    """
    quiz = await repo.get_by_id(session, quiz_id, include_questions=True)
    quiz = _check_quiz_access(quiz, user_id, user_roles)

    errors = validate_quiz_for_publishing(quiz.questions)
    if errors:
        raise AppError(
            f"Không đủ điều kiện xuất bản quiz: {'; '.join(errors)}",
            code="PUBLISH_VALIDATION_FAILED",
            status_code=422,
        )

    quiz.status = "published"
    return await repo.update(session, quiz)


async def add_question(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
    data: QuestionCreate,
) -> Question:
    """Thêm một câu hỏi mới vào Quiz (FR-QUIZ-02, 03, FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    _check_quiz_access(quiz, user_id, user_roles)

    # 1. Xác thực dữ liệu câu hỏi theo bộ luật QZ-2
    validation = validate_question_data(
        question_type=data.type,
        content=data.content,
        time_limit_s=data.time_limit_s,
        points=data.points,
        options=data.options,
        source_ref=data.source_ref,
    )
    if not validation.is_valid:
        raise AppError(
            f"Dữ liệu câu hỏi không hợp lệ: {'; '.join(validation.errors)}",
            code="VALIDATION_ERROR",
            status_code=422,
        )

    # 2. Tính position tiếp theo
    max_pos = await repo.get_max_question_position(session, quiz_id)
    next_pos = max_pos + 1

    # 3. Tạo Question và QuestionOptions
    question = Question(
        quiz_id=quiz_id,
        position=next_pos,
        type=data.type,
        content=data.content.strip(),
        time_limit_s=data.time_limit_s,
        points=data.points,
        explanation=data.explanation.strip() if data.explanation else None,
        topic=data.topic.strip() if data.topic else None,
        source_ref=data.source_ref.strip() if data.source_ref else None,
        ai_generated=False,
    )
    question.options = [
        QuestionOption(position=idx, content=opt.content.strip(), is_correct=opt.is_correct)
        for idx, opt in enumerate(data.options, start=1)
    ]

    await _bump_version_if_has_games(session, quiz)
    return await repo.add_question(session, question)


async def get_question(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    question_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
) -> Question:
    """Lấy chi tiết một câu hỏi (FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    _check_quiz_access(quiz, user_id, user_roles)

    question = await repo.get_question(session, question_id, quiz_id=quiz_id)
    if question is None:
        raise AppError(
            "Câu hỏi không tồn tại trong quiz", code="QUESTION_NOT_FOUND", status_code=404
        )

    return question


async def update_question(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    question_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
    data: QuestionUpdate,
) -> Question:
    """Chỉnh sửa câu hỏi trong Quiz (FR-QUIZ-02, 03, FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    _check_quiz_access(quiz, user_id, user_roles)

    question = await repo.get_question(session, question_id, quiz_id=quiz_id)
    if question is None:
        raise AppError(
            "Câu hỏi không tồn tại trong quiz", code="QUESTION_NOT_FOUND", status_code=404
        )

    # Tổng hợp dữ liệu mới để validate
    target_type = data.type if data.type is not None else question.type
    target_content = data.content if data.content is not None else question.content
    target_time_limit = (
        data.time_limit_s if data.time_limit_s is not None else question.time_limit_s
    )
    target_points = data.points if data.points is not None else question.points
    target_source_ref = data.source_ref if data.source_ref is not None else question.source_ref

    if data.options is not None:
        target_options = data.options
    else:
        from app.schemas.quiz import QuestionOptionCreate

        target_options = [
            QuestionOptionCreate(content=o.content, is_correct=o.is_correct)
            for o in question.options
        ]

    # Validate
    validation = validate_question_data(
        question_type=target_type,
        content=target_content,
        time_limit_s=target_time_limit,
        points=target_points,
        options=target_options,
        source_ref=target_source_ref,
    )
    if not validation.is_valid:
        raise AppError(
            f"Dữ liệu câu hỏi cập nhật không hợp lệ: {'; '.join(validation.errors)}",
            code="VALIDATION_ERROR",
            status_code=422,
        )

    # Cập nhật các trường
    question.type = target_type
    question.content = target_content.strip()
    question.time_limit_s = target_time_limit
    question.points = target_points
    if data.explanation is not None:
        question.explanation = data.explanation.strip() if data.explanation else None
    if data.topic is not None:
        question.topic = data.topic.strip() if data.topic else None
    if data.source_ref is not None:
        question.source_ref = data.source_ref.strip() if data.source_ref else None

    # Nếu cập nhật options
    if data.options is not None:
        new_opts = [
            QuestionOption(
                question_id=question.id,
                position=idx,
                content=opt.content.strip(),
                is_correct=opt.is_correct,
            )
            for idx, opt in enumerate(data.options, start=1)
        ]
        await repo.replace_question_options(session, question.id, new_opts)
        await session.refresh(question, ["options"])

    await _bump_version_if_has_games(session, quiz)
    await session.flush()
    return question


async def delete_question(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    question_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
) -> None:
    """Xóa một câu hỏi khỏi Quiz (FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    _check_quiz_access(quiz, user_id, user_roles)

    question = await repo.get_question(session, question_id, quiz_id=quiz_id)
    if question is None:
        raise AppError(
            "Câu hỏi không tồn tại trong quiz", code="QUESTION_NOT_FOUND", status_code=404
        )

    await _bump_version_if_has_games(session, quiz)
    await repo.delete_question(session, question)


async def reorder_questions(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
    data: QuestionReorderRequest,
) -> Quiz:
    """Sắp xếp lại thứ tự câu hỏi trong Quiz (FR-QUIZ-04, FR-AUTHZ-03)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=True)
    quiz = _check_quiz_access(quiz, user_id, user_roles)

    existing_ids = {q.id for q in quiz.questions}
    requested_ids = data.question_ids

    # Kiểm tra xem danh sách ID có khớp đầy đủ với số câu hỏi hiện có không
    if set(requested_ids) != existing_ids or len(requested_ids) != len(existing_ids):
        raise AppError(
            "Danh sách question_ids phải chứa chính xác toàn bộ ID các câu hỏi của quiz",
            code="VALIDATION_ERROR",
            status_code=422,
        )

    await repo.reorder_questions(session, quiz_id, requested_ids)
    await _bump_version_if_has_games(session, quiz)
    # Lấy lại quiz với thứ tự câu hỏi mới
    quiz_updated = await repo.get_by_id(session, quiz_id, include_questions=True)
    assert quiz_updated is not None
    return quiz_updated


async def list_public_quizzes(
    session: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    tag: str | None = None,
    search: str | None = None,
) -> Sequence[Quiz]:
    """Lấy danh sách các Quiz công khai và đã xuất bản (QZ-5)."""
    return await repo.list_public(session, skip=skip, limit=limit, tag=tag, search=search)


async def clone_quiz(
    session: AsyncSession,
    quiz_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
    data: QuizCloneRequest,
) -> Quiz:
    """Sao chép Quiz thành bản nháp mới thuộc sở hữu của người gọi (QZ-5)."""
    original = await repo.get_by_id(session, quiz_id, include_questions=True)
    if not original:
        raise AppError("Quiz không tồn tại", code="QUIZ_NOT_FOUND", status_code=404)

    is_admin = RoleName.ADMIN.value in user_roles
    is_owner = original.owner_id == user_id
    is_public = original.visibility == "public" and original.status == "published"

    if not (is_admin or is_owner or is_public):
        raise AppError(
            "Quiz không tồn tại hoặc không ở chế độ công khai",
            code="QUIZ_NOT_FOUND",
            status_code=404,
        )

    new_title = data.title.strip() if data.title else f"Bản sao của {original.title}"
    cloned_quiz = Quiz(
        owner_id=user_id,
        title=new_title,
        description=original.description,
        visibility="private",
        status="draft",
        version=1,
        tags=list(original.tags or []),
        play_count=0,
    )
    session.add(cloned_quiz)
    await session.flush()

    for q in original.questions:
        cloned_q = Question(
            quiz_id=cloned_quiz.id,
            position=q.position,
            type=q.type,
            content=q.content,
            explanation=q.explanation,
            topic=q.topic,
            time_limit_s=q.time_limit_s,
            points=q.points,
            source_ref=q.source_ref,
            ai_generated=q.ai_generated,
        )
        session.add(cloned_q)
        await session.flush()

        for opt in q.options:
            cloned_opt = QuestionOption(
                question_id=cloned_q.id,
                position=opt.position,
                content=opt.content,
                is_correct=opt.is_correct,
            )
            session.add(cloned_opt)

    await session.flush()
    return await repo.get_by_id(session, cloned_quiz.id, include_questions=True) or cloned_quiz


async def regenerate_question(
    session: AsyncSession,
    redis: Redis,
    gateway: GeminiGatewayProtocol,
    quiz_id: uuid.UUID,
    question_id: uuid.UUID,
    user_id: uuid.UUID,
    user_roles: Sequence[str],
    data: QuestionRegenerateRequest,
) -> Question:
    """Yêu cầu AI sinh lại một câu hỏi cụ thể trong Quiz (FR-AIGEN-09)."""
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    _check_quiz_access(quiz, user_id, user_roles)

    question = await repo.get_question(session, question_id, quiz_id=quiz_id)
    if not question:
        raise AppError(
            "Câu hỏi không tồn tại trong quiz", code="QUESTION_NOT_FOUND", status_code=404
        )

    # Xin cấp permit từ rate limiter
    await acquire_gemini_permit(redis, priority="high")

    # Chuẩn bị tham số
    target_type = data.type or question.type
    ai_params = AiGenerationParams(
        num_questions=1,
        difficulty=data.difficulty or "medium",
        allowed_types=[target_type],
        note=data.notes,
    )

    current_opts = [opt.content for opt in question.options]
    draft_q = await gateway.regenerate_single_question(
        current_question=question.content,
        current_options=current_opts,
        params=ai_params,
        context=question.source_ref,
    )

    # Validate Lớp 2 (Nghiệp vụ QZ-2)
    options_data = [
        {"content": opt.content, "is_correct": opt.is_correct} for opt in draft_q.options
    ]
    val_res = validate_question_data(
        question_type=draft_q.type,
        content=draft_q.content,
        time_limit_s=draft_q.time_limit_s,
        points=draft_q.points,
        options=options_data,
        source_ref=draft_q.source_ref,
    )
    if not val_res.is_valid:
        raise AppError(
            f"Câu hỏi sinh lại không hợp lệ theo quy tắc nghiệp vụ: {', '.join(val_res.errors)}",
            code="INVALID_QUESTION_DATA",
            status_code=422,
        )

    # Cập nhật câu hỏi
    question.content = draft_q.content.strip()
    question.type = draft_q.type
    question.explanation = draft_q.explanation.strip() if draft_q.explanation else None
    question.topic = draft_q.topic.strip() if draft_q.topic else None
    question.time_limit_s = draft_q.time_limit_s
    question.points = draft_q.points
    question.ai_generated = True

    new_opts = [
        QuestionOption(
            question_id=question.id,
            position=idx,
            content=opt.content.strip(),
            is_correct=opt.is_correct,
        )
        for idx, opt in enumerate(draft_q.options, start=1)
    ]
    await repo.replace_question_options(session, question.id, new_opts)
    quiz = await repo.get_by_id(session, quiz_id, include_questions=False)
    if quiz:
        await _bump_version_if_has_games(session, quiz)
    await session.flush()
    await session.refresh(question, ["options"])
    return question
