"""Unit test cho models Quiz, Question, QuestionOption."""

import uuid

from app.models.quiz import Question, QuestionOption, Quiz


def test_tao_quiz_model_co_ban():
    owner_id = uuid.uuid4()
    quiz = Quiz(
        owner_id=owner_id,
        title="Bộ đề ôn tập Python",
        description="Ôn tập kiến thức cơ bản",
        visibility="private",
        status="draft",
        tags=["python", "backend"],
    )

    assert quiz.title == "Bộ đề ôn tập Python"
    assert quiz.status == "draft"
    assert quiz.tags == ["python", "backend"]
    assert quiz.description == "Ôn tập kiến thức cơ bản"


def test_tao_question_va_options_model():
    quiz_id = uuid.uuid4()
    q = Question(
        quiz_id=quiz_id,
        position=1,
        type="single",
        content="Từ khóa nào dùng để định nghĩa hàm trong Python?",
        time_limit_s=20,
        points=1000,
        explanation="Từ khóa 'def' được dùng để khai báo hàm.",
    )

    opt1 = QuestionOption(question_id=q.id, position=1, content="def", is_correct=True)
    opt2 = QuestionOption(question_id=q.id, position=2, content="func", is_correct=False)
    q.options = [opt1, opt2]

    assert q.type == "single"
    assert len(q.options) == 2
    assert q.options[0].is_correct is True
    assert q.options[1].is_correct is False
