"""
Unit tests for RubricMachine.

Mock 모드. 12개 테스트.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.rubric_machine import RubricMachine
from common.enums import DifficultyLevel, EvaluationFocus, QuestionType
from common.gemini_client import GeminiClient, _tracker, reset_usage
from common.schemas import (
    AnswerRubric,
    ConceptKnowledgeStructure,
    GenerationMetadata,
    Question,
    QuestionSlot,
    SourceReference,
)
from config.defaults import LLM_CONFIG

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ────────────────────────────────────────────────────────────
# Fixtures 헬퍼
# ────────────────────────────────────────────────────────────
def load_knowledge_structure() -> ConceptKnowledgeStructure:
    with open(FIXTURES_DIR / "sample_knowledge_structure.json", encoding="utf-8") as f:
        return ConceptKnowledgeStructure.model_validate(json.load(f))


def make_slot(
    qtype: QuestionType,
    level: DifficultyLevel,
    points: int,
    concept_ids: list[str] | None = None,
) -> QuestionSlot:
    if concept_ids is None:
        concept_ids = ["M1_4_taylor_principles"]
    return QuestionSlot(
        slot_id="Q_TEST",
        section_id="S1",
        question_type=qtype,
        target_difficulty_level=level,
        expected_X1=0.5,
        expected_X2=0.5,
        expected_D=0.5,
        points=points,
        primary_concept_ids=concept_ids,
    )


def make_question(qtype: QuestionType, points: int, slot: QuestionSlot | None = None) -> Question:
    return Question(
        question_id="Q_RUBRIC_001",
        slot_id="Q_TEST",
        session_id="sess-rubric",
        question_type=qtype,
        target_difficulty_level=DifficultyLevel.L3,
        points=points,
        prompt="Taylor의 과학적 관리 4원칙을 설명하시오.",
        reference_answer="Taylor는 과학화, 선발/훈련, 협력, 분업 4원칙을 제시했다.",
        answer_steps=["1. 4원칙 나열", "2. 각 원칙 설명"],
        source_references=[
            SourceReference(
                concept_id="M1_4_taylor_principles",
                page_or_slide="M1.4 p.3",
                excerpt="Taylor의 과학적 관리 4원칙",
            )
        ],
        generation_metadata=GenerationMetadata(
            generator_version="v0.5",
            attempt_number=1,
        ),
    )


def make_criteria_responder(criteria_list: list[dict]):
    """criteria_list의 합계가 total_points인 mock 응답."""
    def responder(system: str, user: str) -> str:
        return json.dumps({"criteria": criteria_list})
    return responder


def make_retry_responder(first_criteria: list[dict], second_criteria: list[dict]):
    """첫 호출은 first_criteria, 두 번째는 second_criteria 반환."""
    calls = [0]
    def responder(system: str, user: str) -> str:
        calls[0] += 1
        if calls[0] == 1:
            return json.dumps({"criteria": first_criteria})
        return json.dumps({"criteria": second_criteria})
    return responder


def make_always_wrong_responder(wrong_total: int):
    """항상 잘못된 총점을 반환하는 mock."""
    def responder(system: str, user: str) -> str:
        return json.dumps({"criteria": [
            {
                "description": "잘못된 기준",
                "points": wrong_total,
                "key_points": ["x"],
                "partial_credit_guide": "없음",
            }
        ]})
    return responder


# ────────────────────────────────────────────────────────────
# 테스트 케이스
# ────────────────────────────────────────────────────────────
def test_short_answer_rubric():
    """short_answer(10점) → criteria 2~3개, 총점 10."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.SHORT_ANSWER, DifficultyLevel.L2, 10)
    question = make_question(QuestionType.SHORT_ANSWER, 10, slot)

    responder = make_criteria_responder([
        {"description": "정확성", "points": 5, "key_points": ["핵심 내용 일치"], "partial_credit_guide": "절반 부여"},
        {"description": "완결성", "points": 5, "key_points": ["모든 요소 포함"], "partial_credit_guide": "절반 부여"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-1")

    assert isinstance(rubric, AnswerRubric)
    assert 2 <= len(rubric.criteria) <= 3
    assert sum(c.points for c in rubric.criteria) == 10
    print("✓ test_short_answer_rubric")


def test_long_answer_rubric():
    """long_answer(12점) → criteria 2~4개, 총점 12."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.LONG_ANSWER, DifficultyLevel.L3, 12)
    question = make_question(QuestionType.LONG_ANSWER, 12, slot)

    responder = make_criteria_responder([
        {"description": "개념 파악", "points": 4, "key_points": ["개념 정확"], "partial_credit_guide": "절반"},
        {"description": "논리 전개", "points": 4, "key_points": ["흐름 명확"], "partial_credit_guide": "절반"},
        {"description": "근거 제시", "points": 4, "key_points": ["근거 포함"], "partial_credit_guide": "절반"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-2")

    assert 2 <= len(rubric.criteria) <= 4
    assert sum(c.points for c in rubric.criteria) == 12
    print("✓ test_long_answer_rubric")


def test_case_analysis_rubric():
    """case_analysis(7점) → criteria 2~4개, 총점 7."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.CASE_ANALYSIS, DifficultyLevel.L4, 7)
    question = make_question(QuestionType.CASE_ANALYSIS, 7, slot)

    responder = make_criteria_responder([
        {"description": "상황 분석", "points": 2, "key_points": ["핵심 파악"], "partial_credit_guide": "절반"},
        {"description": "개념 적용", "points": 3, "key_points": ["올바른 적용"], "partial_credit_guide": "절반"},
        {"description": "결론 도출", "points": 2, "key_points": ["타당한 결론"], "partial_credit_guide": "절반"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-3")

    assert 2 <= len(rubric.criteria) <= 4
    assert sum(c.points for c in rubric.criteria) == 7
    print("✓ test_case_analysis_rubric")


def test_criterion_id_auto_assigned():
    """criterion_id가 'C1', 'C2', ... 형식으로 자동 부여됨."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.LONG_ANSWER, DifficultyLevel.L3, 12)
    question = make_question(QuestionType.LONG_ANSWER, 12, slot)

    responder = make_criteria_responder([
        {"description": "A", "points": 6, "key_points": ["x"], "partial_credit_guide": "p"},
        {"description": "B", "points": 6, "key_points": ["y"], "partial_credit_guide": "q"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-4")

    ids = [c.criterion_id for c in rubric.criteria]
    assert ids == ["C1", "C2"]
    print("✓ test_criterion_id_auto_assigned")


def test_total_points_enforced_on_first_success():
    """첫 시도에 정확한 총점 반환 → LLM 호출 1건만."""
    reset_usage()
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.SHORT_ANSWER, DifficultyLevel.L2, 10)
    question = make_question(QuestionType.SHORT_ANSWER, 10, slot)

    responder = make_criteria_responder([
        {"description": "정확성", "points": 5, "key_points": ["x"], "partial_credit_guide": "p"},
        {"description": "완결성", "points": 5, "key_points": ["y"], "partial_credit_guide": "q"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-5")

    records = [r for r in _tracker.records if r.agent_name == "Rubric_Machine"]
    assert len(records) == 1
    assert sum(c.points for c in rubric.criteria) == 10
    print("✓ test_total_points_enforced_on_first_success")


def test_retry_on_total_mismatch():
    """첫 시도 총점 8(요구 10) → 재시도 후 두 번째 결과(10) 사용."""
    reset_usage()
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.SHORT_ANSWER, DifficultyLevel.L2, 10)
    question = make_question(QuestionType.SHORT_ANSWER, 10, slot)

    first = [{"description": "기준1", "points": 8, "key_points": ["x"], "partial_credit_guide": "p"}]
    second = [
        {"description": "정확성", "points": 5, "key_points": ["x"], "partial_credit_guide": "p"},
        {"description": "완결성", "points": 5, "key_points": ["y"], "partial_credit_guide": "q"},
    ]
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=make_retry_responder(first, second)))
    rubric = tester.generate(question, slot, struct, "sess-6")

    records = [r for r in _tracker.records if r.agent_name == "Rubric_Machine"]
    assert len(records) == 2  # 2번 호출
    assert sum(c.points for c in rubric.criteria) == 10
    assert len(rubric.warnings) == 0  # fallback 아님
    print("✓ test_retry_on_total_mismatch")


def test_fallback_on_persistent_mismatch():
    """항상 잘못된 총점 반환 → fallback rubric 사용."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.SHORT_ANSWER, DifficultyLevel.L2, 10)
    question = make_question(QuestionType.SHORT_ANSWER, 10, slot)

    tester = RubricMachine(GeminiClient(mock=True, mock_responder=make_always_wrong_responder(8)))
    rubric = tester.generate(question, slot, struct, "sess-7")

    assert rubric is not None
    assert rubric.question_id == question.question_id
    assert sum(c.points for c in rubric.criteria) == 10
    assert "fallback_rubric_used" in rubric.warnings
    print("✓ test_fallback_on_persistent_mismatch")


def test_uses_light_model():
    """usage_tracker에서 light model 호출 1건 확인."""
    reset_usage()
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.LONG_ANSWER, DifficultyLevel.L3, 12)
    question = make_question(QuestionType.LONG_ANSWER, 12, slot)

    responder = make_criteria_responder([
        {"description": "A", "points": 4, "key_points": ["x"], "partial_credit_guide": "p"},
        {"description": "B", "points": 4, "key_points": ["y"], "partial_credit_guide": "q"},
        {"description": "C", "points": 4, "key_points": ["z"], "partial_credit_guide": "r"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    tester.generate(question, slot, struct, "sess-light")

    light_model = LLM_CONFIG["models"]["light"]
    records = [r for r in _tracker.records if r.agent_name == "Rubric_Machine"]
    assert len(records) == 1
    assert light_model in records[0].model
    print("✓ test_uses_light_model")


def test_question_id_preserved():
    """LLM 출력과 관계없이 입력 question.question_id로 강제됨."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.LONG_ANSWER, DifficultyLevel.L3, 12)
    question = make_question(QuestionType.LONG_ANSWER, 12, slot)

    responder = make_criteria_responder([
        {"description": "A", "points": 6, "key_points": ["x"], "partial_credit_guide": "p"},
        {"description": "B", "points": 6, "key_points": ["y"], "partial_credit_guide": "q"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-qid")

    assert rubric.question_id == question.question_id
    print("✓ test_question_id_preserved")


def test_evaluation_focus_in_context():
    """_build_context에 evaluation_focus가 올바르게 포함됨."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.LONG_ANSWER, DifficultyLevel.L3, 12)
    question = make_question(QuestionType.LONG_ANSWER, 12, slot)

    tester = RubricMachine(GeminiClient(mock=True))

    for focus, expected_val in [
        (EvaluationFocus.BALANCED, "balanced"),
        (EvaluationFocus.KNOWLEDGE_FOCUSED, "knowledge_focused"),
        (EvaluationFocus.APPLICATION_FOCUSED, "application_focused"),
    ]:
        ctx = tester._build_context(question, slot, struct, focus)
        assert ctx["evaluation_focus"] == expected_val, \
            f"Expected {expected_val}, got {ctx['evaluation_focus']}"

    print("✓ test_evaluation_focus_in_context")


def test_required_fields():
    """모든 RubricCriterion에 description, points, key_points, partial_credit_guide 채워짐."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.CASE_ANALYSIS, DifficultyLevel.L4, 7)
    question = make_question(QuestionType.CASE_ANALYSIS, 7, slot)

    responder = make_criteria_responder([
        {"description": "상황 분석", "points": 2, "key_points": ["핵심 파악", "배경 이해"], "partial_credit_guide": "절반"},
        {"description": "개념 적용", "points": 3, "key_points": ["올바른 적용"], "partial_credit_guide": "없음"},
        {"description": "결론 도출", "points": 2, "key_points": ["타당한 결론"], "partial_credit_guide": "절반"},
    ])
    tester = RubricMachine(GeminiClient(mock=True, mock_responder=responder))
    rubric = tester.generate(question, slot, struct, "sess-fields")

    for criterion in rubric.criteria:
        assert criterion.description
        assert criterion.points >= 1
        assert len(criterion.key_points) >= 1
        assert isinstance(criterion.partial_credit_guide, str)
    print("✓ test_required_fields")


def test_pydantic_validation_error():
    """필수 필드 누락한 JSON 반환 → 재시도 후 fallback."""
    struct = load_knowledge_structure()
    slot = make_slot(QuestionType.SHORT_ANSWER, DifficultyLevel.L2, 10)
    question = make_question(QuestionType.SHORT_ANSWER, 10, slot)

    def missing_field_responder(system: str, user: str) -> str:
        # key_points, partial_credit_guide 누락
        return json.dumps({"criteria": [
            {"description": "기준1", "points": 10}
        ]})

    tester = RubricMachine(GeminiClient(mock=True, mock_responder=missing_field_responder))
    rubric = tester.generate(question, slot, struct, "sess-err")

    # fallback 사용
    assert rubric is not None
    assert rubric.question_id == question.question_id
    assert sum(c.points for c in rubric.criteria) == 10
    assert "fallback_rubric_used" in rubric.warnings
    print("✓ test_pydantic_validation_error")


# ────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_short_answer_rubric()
    test_long_answer_rubric()
    test_case_analysis_rubric()
    test_criterion_id_auto_assigned()
    test_total_points_enforced_on_first_success()
    test_retry_on_total_mismatch()
    test_fallback_on_persistent_mismatch()
    test_uses_light_model()
    test_question_id_preserved()
    test_evaluation_focus_in_context()
    test_required_fields()
    test_pydantic_validation_error()

    print("\n✓ All RubricMachine tests passed (12/12)")
