"""Repository truy vấn cơ sở dữ liệu cho Quiz, Question, QuestionOption."""

import uuid
from collections.abc import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.quiz import Question, QuestionOption, Quiz


async def get_by_id(
    session: AsyncSession, quiz_id: uuid.UUID, *, include_questions: bool = True
) -> Quiz | None:
    """Lấy thông tin Quiz theo ID, tùy chọn nạp sẵn danh sách câu hỏi và lựa chọn."""
    stmt = select(Quiz).where(Quiz.id == quiz_id)
    if include_questions:
        stmt = stmt.options(selectinload(Quiz.questions).selectinload(Question.options))
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def list_by_owner(
    session: AsyncSession, owner_id: uuid.UUID, *, skip: int = 0, limit: int = 50
) -> Sequence[Quiz]:
    """Lấy danh sách Quiz do một user tạo ra (sắp xếp giảm dần theo thời gian tạo)."""
    stmt = (
        select(Quiz)
        .where(Quiz.owner_id == owner_id)
        .options(selectinload(Quiz.questions))
        .order_by(Quiz.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    result = await session.execute(stmt)
    return result.scalars().all()


async def list_public(
    session: AsyncSession,
    *,
    skip: int = 0,
    limit: int = 50,
    tag: str | None = None,
    search: str | None = None,
) -> Sequence[Quiz]:
    """Lấy danh sách Quiz công khai và đã xuất bản (QZ-5)."""
    stmt = (
        select(Quiz)
        .where(Quiz.visibility == "public", Quiz.status == "published")
        .options(selectinload(Quiz.questions))
    )
    if tag:
        stmt = stmt.where(Quiz.tags.contains([tag]))
    if search:
        stmt = stmt.where(Quiz.title.ilike(f"%{search.strip()}%"))
    stmt = stmt.order_by(Quiz.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    return result.scalars().all()


async def create(session: AsyncSession, quiz: Quiz) -> Quiz:
    """Tạo mới bộ Quiz."""
    session.add(quiz)
    await session.flush()
    await session.refresh(quiz, ["questions"])
    return quiz


async def update(session: AsyncSession, quiz: Quiz) -> Quiz:
    """Cập nhật thông tin Quiz."""
    await session.flush()
    return quiz


async def delete_quiz(session: AsyncSession, quiz: Quiz) -> None:
    """Xóa bộ Quiz (cascade tự động xóa questions và options)."""
    await session.delete(quiz)
    await session.flush()


async def get_max_question_position(session: AsyncSession, quiz_id: uuid.UUID) -> int:
    """Lấy số thứ tự (position) lớn nhất hiện tại của các câu hỏi trong Quiz."""
    stmt = select(func.coalesce(func.max(Question.position), 0)).where(Question.quiz_id == quiz_id)
    result = await session.execute(stmt)
    return int(result.scalar_one())


async def get_question(
    session: AsyncSession, question_id: uuid.UUID, quiz_id: uuid.UUID | None = None
) -> Question | None:
    """Lấy thông tin một câu hỏi kèm các lựa chọn."""
    stmt = (
        select(Question).where(Question.id == question_id).options(selectinload(Question.options))
    )
    if quiz_id is not None:
        stmt = stmt.where(Question.quiz_id == quiz_id)
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


async def add_question(session: AsyncSession, question: Question) -> Question:
    """Thêm một câu hỏi mới vào Quiz."""
    session.add(question)
    await session.flush()
    await session.refresh(question, ["options"])
    return question


async def delete_question(session: AsyncSession, question: Question) -> None:
    """Xóa một câu hỏi và tự động cập nhật lại thứ tự position của các câu còn lại."""
    quiz_id = question.quiz_id
    deleted_pos = question.position
    await session.delete(question)
    await session.flush()

    # Giảm position của các câu hỏi đứng sau câu vừa bị xóa
    stmt = (
        select(Question)
        .where(Question.quiz_id == quiz_id, Question.position > deleted_pos)
        .order_by(Question.position)
    )
    res = await session.execute(stmt)
    for q in res.scalars():
        q.position -= 1
    await session.flush()


async def reorder_questions(
    session: AsyncSession, quiz_id: uuid.UUID, question_ids_in_order: list[uuid.UUID]
) -> None:
    """Cập nhật thứ tự các câu hỏi theo danh sách ID chỉ định."""
    stmt = select(Question).where(Question.quiz_id == quiz_id)
    res = await session.execute(stmt)
    questions_by_id = {q.id: q for q in res.scalars()}

    for new_pos, qid in enumerate(question_ids_in_order, start=1):
        if qid in questions_by_id:
            questions_by_id[qid].position = new_pos

    await session.flush()


async def replace_question_options(
    session: AsyncSession, question_id: uuid.UUID, options: list[QuestionOption]
) -> None:
    """Thay thế toàn bộ options cũ bằng options mới cho câu hỏi."""
    await session.execute(delete(QuestionOption).where(QuestionOption.question_id == question_id))
    for opt in options:
        opt.question_id = question_id
        session.add(opt)
    await session.flush()
