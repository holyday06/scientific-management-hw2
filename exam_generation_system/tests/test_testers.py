"""
Unit tests for FactfulnessTester and DifficultyTester.

Mock 모드로 LLM 호출 없이 실행.
13개 테스트:
  1~6: FactfulnessTester
  7~11: DifficultyTester
  12~13: BaseTester 공통
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from agents.difficulty_tester import DifficultyTester
from agents.factfulness_tester import FactfulnessTester
from common.enums import (
    DifficultyLevel,
    EvaluationFocus,
    QuestionType,
    RoutingStatus,
    TesterName,
    Verdict,
)
from common.gemini_client import GeminiClient, _tracker, reset_usage
from common.schemas import (
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
    target_level: DifficultyLevel,
    expected_X1: float,
    expected_X2: float,
    expected_D: float,
    question_type: QuestionType = QuestionType.LONG_ANSWER,
    concept_ids: list[str] | None = None,
) -> QuestionSlot:
    if concept_ids is None:
        concept_ids = ["M1_4_taylor_principles"]
    return QuestionSlot(
        slot_id="Q_TEST",
        section_id="S1",
        question_type=question_type,
        target_difficulty_level=target_level,
        expected_X1=expected_X1,
        expected_X2=expected_X2,
        expected_D=expected_D,
        points=10,
        primary_concept_ids=concept_ids,
    )


def make_question(slot: QuestionSlot | None = None) -> Question:
    q_type = slot.question_type if slot else QuestionType.LONG_ANSWER
    d_level = slot.target_difficulty_level if slot else DifficultyLevel.L3
    return Question(
        question_id="Q_TEST_001",
        slot_id="Q_TEST",
        session_id="sess-test",
        question_type=q_type,
        target_difficulty_level=d_level,
        points=10,
        prompt="Taylor의 과학적 관리 4원칙을 설명하고, Pig Iron Case와 연관하여 서술하시오.",
        reference_answer="Taylor는 과학적 관리 4원칙(과학화, 선발/훈련, 협력, 분업)을 제시했다...",
        answer_steps=["1. 4원칙 나열", "2. Pig Iron Case 적용"],
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


def make_factfulness_responder(
    citation: float = 0.95,
    grounding: float = 0.95,
    scope: float = 0.95,
    citation_issues: list[str] | None = None,
    grounding_issues: list[str] | None = None,
    scope_issues: list[str] | None = None,
    overall_reasons: list[str] | None = None,
):
    def responder(system: str, user: str) -> str:
        return json.dumps({
            "citation_validity": citation,
            "citation_issues": citation_issues or [],
            "answer_grounding": grounding,
            "grounding_issues": grounding_issues or [],
            "scope_compliance": scope,
            "scope_issues": scope_issues or [],
            "overall_reasons": overall_reasons or ["전반적으로 충실함"],
        })
    return responder


def make_difficulty_responder(x1: float, x2: float):
    def responder(system: str, user: str) -> str:
        return json.dumps({
            "estimated_X1": x1,
            "estimated_X2": x2,
            "x1_reasoning": f"개념 범위 X1={x1} 추정",
            "x2_reasoning": f"문항 유형 인지 부하 X2={x2} 추정",
        })
    return responder


# ────────────────────────────────────────────────────────────
# FactfulnessTester 테스트
# ────────────────────────────────────────────────────────────
def test_factfulness_pass():
    """세 축 모두 0.95 → PASS, score≈0.95, next_action None."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=make_factfulness_responder()))
    out = tester.test(question, slot, struct, "sess-1")

    assert out.verdict == Verdict.PASS
    assert out.next_action is None
    assert abs(out.score - 0.95) < 0.001
    print("✓ test_factfulness_pass")


def test_factfulness_fail_citation():
    """citation=0.3, 나머지 0.9 → FAIL, [citation] reason, target=QA_Generator."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    responder = make_factfulness_responder(
        citation=0.3,
        grounding=0.9,
        scope=0.9,
        citation_issues=["답안에 출처 불명확한 수치 포함"],
    )
    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=responder))
    out = tester.test(question, slot, struct, "sess-2")

    assert out.verdict == Verdict.FAIL
    assert out.next_action is not None
    assert out.next_action.routing_status == RoutingStatus.QUESTION_REWORK
    assert out.next_action.target_agent == "QA_Generator"
    assert any("[citation]" in r for r in out.reasons)
    print("✓ test_factfulness_fail_citation")


def test_factfulness_fail_grounding():
    """grounding=0.2 → FAIL, reasons에 [grounding] 포함."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    responder = make_factfulness_responder(
        citation=0.9,
        grounding=0.2,
        scope=0.9,
        grounding_issues=["모범 답안이 개념에 없는 내용 포함"],
    )
    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=responder))
    out = tester.test(question, slot, struct, "sess-3")

    assert out.verdict == Verdict.FAIL
    assert any("[grounding]" in r for r in out.reasons)
    print("✓ test_factfulness_fail_grounding")


def test_factfulness_fail_scope():
    """scope=0.0 → FAIL, reasons에 [scope] 포함."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    responder = make_factfulness_responder(
        citation=0.9,
        grounding=0.9,
        scope=0.0,
        scope_issues=["시험 범위 외 모듈 내용 요구"],
    )
    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=responder))
    out = tester.test(question, slot, struct, "sess-4")

    assert out.verdict == Verdict.FAIL
    assert any("[scope]" in r for r in out.reasons)
    print("✓ test_factfulness_fail_scope")


def test_factfulness_meta_fields():
    """tester_name, threshold_used, question_id, session_id 정확히 채워짐."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=make_factfulness_responder()))
    out = tester.test(question, slot, struct, "meta-sess")

    assert out.tester_name == TesterName.FACTFULNESS
    assert out.threshold_used == 0.80
    assert out.question_id == question.question_id
    assert out.session_id == "meta-sess"
    print("✓ test_factfulness_meta_fields")


def test_factfulness_uses_heavy_model():
    """usage_tracker에서 heavy model 호출 1건 확인."""
    reset_usage()
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=make_factfulness_responder()))
    tester.test(question, slot, struct, "heavy-sess")

    heavy_model = LLM_CONFIG["models"]["heavy"]
    records = [r for r in _tracker.records if r.agent_name == TesterName.FACTFULNESS.value]
    assert len(records) == 1
    assert heavy_model in records[0].model
    print("✓ test_factfulness_uses_heavy_model")


# ────────────────────────────────────────────────────────────
# DifficultyTester 테스트
# ────────────────────────────────────────────────────────────
def test_difficulty_pass_aligned():
    """slot L3 (expected_D=0.5), mock X1=0.5 X2=0.5 → estimated L3, PASS."""
    struct = load_knowledge_structure()
    # BALANCED: D = 0.5*0.5 + 0.5*0.5 = 0.5 → L3
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    tester = DifficultyTester(GeminiClient(mock=True, mock_responder=make_difficulty_responder(0.5, 0.5)))
    out = tester.test(question, slot, struct, "diff-sess-1")

    assert out.verdict == Verdict.PASS
    assert out.next_action is None
    assert out.estimated_difficulty_level == DifficultyLevel.L3
    # score = 1 - |0.5 - 0.5| = 1.0 >= 0.60
    assert out.score >= 0.60
    print("✓ test_difficulty_pass_aligned")


def test_difficulty_fail_too_easy():
    """slot L4, mock X1=0.1 X2=0.1 → estimated L1, FAIL, target=Exam_Planner."""
    struct = load_knowledge_structure()
    # slot L4: expected_D=0.725 (X1=0.75, X2=0.7, BALANCED)
    slot = make_slot(DifficultyLevel.L4, 0.75, 0.7, 0.725)
    question = make_question(slot)

    tester = DifficultyTester(GeminiClient(mock=True, mock_responder=make_difficulty_responder(0.1, 0.1)))
    out = tester.test(question, slot, struct, "diff-sess-2")

    assert out.verdict == Verdict.FAIL
    assert out.next_action is not None
    assert out.next_action.routing_status == RoutingStatus.DIFFICULTY_CORRECTION
    assert out.next_action.target_agent == "Exam_Planner"
    # estimated_D = 0.5*0.1 + 0.5*0.1 = 0.1 → L1
    assert out.estimated_difficulty_level == DifficultyLevel.L1
    print("✓ test_difficulty_fail_too_easy")


def test_difficulty_fail_too_hard():
    """slot L2, mock X1=0.9 X2=0.9 → estimated L5, FAIL."""
    struct = load_knowledge_structure()
    # slot L2: expected_D=0.325 (X1=0.25, X2=0.4, BALANCED)
    slot = make_slot(DifficultyLevel.L2, 0.25, 0.4, 0.325, question_type=QuestionType.SHORT_ANSWER)
    question = make_question(slot)

    tester = DifficultyTester(GeminiClient(mock=True, mock_responder=make_difficulty_responder(0.9, 0.9)))
    out = tester.test(question, slot, struct, "diff-sess-3")

    assert out.verdict == Verdict.FAIL
    # estimated_D = 0.5*0.9 + 0.5*0.9 = 0.9 → L5
    assert out.estimated_difficulty_level == DifficultyLevel.L5
    print("✓ test_difficulty_fail_too_hard")


def test_difficulty_extra_fields():
    """TesterOutput에 estimated_X1/X2/D/level 모두 채워짐."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    tester = DifficultyTester(GeminiClient(mock=True, mock_responder=make_difficulty_responder(0.5, 0.5)))
    out = tester.test(question, slot, struct, "extra-sess")

    assert out.estimated_X1 == 0.5
    assert out.estimated_X2 == 0.5
    assert out.estimated_D is not None
    assert abs(out.estimated_D - 0.5) < 0.001
    assert out.estimated_difficulty_level == DifficultyLevel.L3
    print("✓ test_difficulty_extra_fields")


def test_difficulty_uses_heavy_model():
    """usage_tracker에서 heavy model 호출 1건 확인."""
    reset_usage()
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    tester = DifficultyTester(GeminiClient(mock=True, mock_responder=make_difficulty_responder(0.5, 0.5)))
    tester.test(question, slot, struct, "heavy-diff-sess")

    heavy_model = LLM_CONFIG["models"]["heavy"]
    records = [r for r in _tracker.records if r.agent_name == TesterName.DIFFICULTY.value]
    assert len(records) == 1
    assert heavy_model in records[0].model
    print("✓ test_difficulty_uses_heavy_model")


# ────────────────────────────────────────────────────────────
# BaseTester 공통 테스트
# ────────────────────────────────────────────────────────────
def test_base_invalid_llm_json():
    """mock이 깨진 JSON 반환 → RuntimeError (3회 재시도 후)."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    def broken_responder(system: str, user: str) -> str:
        return "{invalid json!!!"

    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=broken_responder))

    with pytest.raises((RuntimeError, Exception)):
        tester.test(question, slot, struct, "err-sess")
    print("✓ test_base_invalid_llm_json")


def test_base_missing_field():
    """mock이 필수 필드 빠진 JSON 반환 → ValidationError."""
    struct = load_knowledge_structure()
    slot = make_slot(DifficultyLevel.L3, 0.5, 0.5, 0.5)
    question = make_question(slot)

    def missing_field_responder(system: str, user: str) -> str:
        # citation_validity 누락
        return json.dumps({
            "citation_issues": [],
            "answer_grounding": 0.9,
            "grounding_issues": [],
            "scope_compliance": 0.9,
            "scope_issues": [],
            "overall_reasons": ["ok"],
        })

    tester = FactfulnessTester(GeminiClient(mock=True, mock_responder=missing_field_responder))

    with pytest.raises(ValidationError):
        tester.test(question, slot, struct, "err-sess2")
    print("✓ test_base_missing_field")


# ────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_factfulness_pass()
    test_factfulness_fail_citation()
    test_factfulness_fail_grounding()
    test_factfulness_fail_scope()
    test_factfulness_meta_fields()
    test_factfulness_uses_heavy_model()

    test_difficulty_pass_aligned()
    test_difficulty_fail_too_easy()
    test_difficulty_fail_too_hard()
    test_difficulty_extra_fields()
    test_difficulty_uses_heavy_model()

    test_base_invalid_llm_json()
    test_base_missing_field()

    print("\n✓ All Tester tests passed (13/13)")
