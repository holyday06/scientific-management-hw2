"""
Unit tests for Exam Planner.

Mock 모드로 LLM 호출 없이 단위 테스트.
실제 LLM 호출 테스트는 별도 (test_exam_planner_live.py에서)
"""
from __future__ import annotations

import json
from pathlib import Path

from agents.exam_planner import (
    ExamPlanner,
    build_concept_assignment_prompt,
    build_section_structure,
    plan_slot_assignments,
    split_group_to_levels,
)
from common.enums import DifficultyLevel, QuestionType, EvaluationFocus
from common.gemini_client import GeminiClient
from common.schemas import (
    ConceptKnowledgeStructure,
    DifficultyDistributionGroup,
    ExamBlueprint,
    QuestionTypeDistribution,
    ReqVector,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ────────────────────────────────────────────────────────────
# Helpers
# ────────────────────────────────────────────────────────────
def load_fixtures() -> tuple[ReqVector, ConceptKnowledgeStructure]:
    with open(FIXTURES_DIR / "sample_req_vector.json", encoding="utf-8") as f:
        req = ReqVector.model_validate(json.load(f))
    with open(FIXTURES_DIR / "sample_knowledge_structure.json", encoding="utf-8") as f:
        struct = ConceptKnowledgeStructure.model_validate(json.load(f))
    return req, struct


def fake_llm_responder_for_v05(system_prompt: str, user_prompt: str) -> str:
    """v0.5 표준 시나리오용 mock 응답.

    10개 슬롯 (Q01~Q10)에 fixture의 노드들을 적절히 배정.
    실제 LLM 호출 대신 이 함수가 결정론적 응답 제공.
    """
    return json.dumps({
        "slot_assignments": [
            # case_analysis가 먼저 (우선순위) — L5, L4, L3 채움
            {"slot_id": "Q01", "primary_concept_ids": ["M1_4_taylor_principles", "M2_1_2_kj_method", "M3_1_1_therbligs"], "secondary_concept_ids": [], "rationale": "L5: 광범위 통합"},
            {"slot_id": "Q02", "primary_concept_ids": ["M2_1_1_dassi", "M2_1_2_kj_method"], "secondary_concept_ids": [], "rationale": "L4: 다른 범주 교차"},
            {"slot_id": "Q03", "primary_concept_ids": ["M1_4_taylor_principles", "M1_4_pig_iron_case"], "secondary_concept_ids": [], "rationale": "L3"},
            # long_answer (5개): L5, L4, L3, L3, L3
            {"slot_id": "Q04", "primary_concept_ids": ["M1_3_work_system", "M2_1_1_dassi"], "secondary_concept_ids": [], "rationale": "L4: 통합"},
            {"slot_id": "Q05", "primary_concept_ids": ["M2_1_1_dassi"], "secondary_concept_ids": ["M2_1_3_brainstorming"], "rationale": "L3"},
            {"slot_id": "Q06", "primary_concept_ids": ["M3_1_1_therbligs"], "secondary_concept_ids": ["M3_1_1_motion_economy"], "rationale": "L3"},
            {"slot_id": "Q07", "primary_concept_ids": ["M1_3_work_system"], "secondary_concept_ids": [], "rationale": "L3"},
            {"slot_id": "Q08", "primary_concept_ids": ["M2_1_3_brainstorming"], "secondary_concept_ids": [], "rationale": "L2"},
            # short_answer (2개): L2, L1
            {"slot_id": "Q09", "primary_concept_ids": ["M1_1_work_definition"], "secondary_concept_ids": [], "rationale": "L1"},
            {"slot_id": "Q10", "primary_concept_ids": ["M1_4_pig_iron_case"], "secondary_concept_ids": [], "rationale": "L2"},
        ],
        "coverage_check": {
            "covered_chapters": [
                "M1_1_work_definition", "M1_3_work_system", "M1_4_taylor_principles",
                "M2_1_1_dassi", "M2_1_2_kj_method", "M3_1_1_therbligs"
            ],
            "missing_chapters": []
        }
    })


# ────────────────────────────────────────────────────────────
# 결정론적 헬퍼 테스트
# ────────────────────────────────────────────────────────────
def test_split_group_to_levels_v05():
    result = split_group_to_levels(
        DifficultyDistributionGroup(easy=2, medium=6, hard=2)
    )
    assert result == {"L1": 1, "L2": 1, "L3": 6, "L4": 1, "L5": 1}
    print("✓ split_group_to_levels (v0.5 표준)")


def test_split_group_odd_easy():
    """easy 홀수면 L1 우대."""
    result = split_group_to_levels(
        DifficultyDistributionGroup(easy=3, medium=4, hard=3)
    )
    assert result == {"L1": 2, "L2": 1, "L3": 4, "L4": 2, "L5": 1}
    print("✓ split_group_to_levels (odd easy)")


def test_build_section_structure():
    req, _ = load_fixtures()
    sections = build_section_structure(req)
    # short_answer 2 + long_answer 5 + case_analysis 3 = 3개 섹션
    assert len(sections) == 3
    assert sum(s.num_questions for s in sections) == 10
    # case_analysis 섹션의 ponts_per_question
    case_section = next(s for s in sections if s.question_type == "case_analysis")
    assert case_section.num_questions == 3
    print("✓ build_section_structure")


def test_plan_slot_assignments_v05():
    """v0.5 표준 시나리오에서 슬롯 사전 배정이 제약을 만족하는지."""
    req, _ = load_fixtures()
    sections = build_section_structure(req)
    level_dist = split_group_to_levels(req.difficulty_distribution_group)

    assignments = plan_slot_assignments(sections, level_dist)
    assert len(assignments) == 10  # 총 10개 슬롯

    # 모든 (type, level) 조합이 유효한지
    from common.difficulty import is_valid_type_level_combo
    for slot_id, section_id, qtype, level in assignments:
        assert is_valid_type_level_combo(qtype, level), \
            f"{slot_id}: {qtype.value} × L{int(level)} 제약 위반"

    # 레벨 분포가 정확한지
    level_counts = {}
    for _, _, _, level in assignments:
        level_counts[int(level)] = level_counts.get(int(level), 0) + 1
    assert level_counts == {1: 1, 2: 1, 3: 6, 4: 1, 5: 1}
    print("✓ plan_slot_assignments (v0.5 표준)")


def test_plan_slot_assignments_infeasible():
    """모든 case_analysis가 L1만 필요한 불가능한 케이스 → ValueError."""
    sections = build_section_structure(ReqVector(
        exam_title="x", exam_type="midterm",
        target_chapters_or_concepts=["c"],
        total_points=10, duration_minutes=10,
        difficulty_distribution_group=DifficultyDistributionGroup(
            easy=1, medium=0, hard=0
        ),
        question_type_distribution=QuestionTypeDistribution(
            case_analysis=1
        ),
    ))
    try:
        plan_slot_assignments(sections, {"L1": 1, "L2": 0, "L3": 0, "L4": 0, "L5": 0})
        assert False, "Should have raised ValueError"
    except ValueError as e:
        assert "유형-레벨 제약" in str(e) or "남은 레벨" in str(e)
    print("✓ plan_slot_assignments (infeasible → ValueError)")


# ────────────────────────────────────────────────────────────
# LLM 호출 통합 (mock)
# ────────────────────────────────────────────────────────────
def test_planner_full_flow_mock():
    """전체 plan() 흐름 — mock LLM 사용."""
    req, struct = load_fixtures()
    client = GeminiClient(mock=True, mock_responder=fake_llm_responder_for_v05)
    planner = ExamPlanner(gemini_client=client)

    blueprint = planner.plan(req, struct, session_id="test-session")

    # 기본 검증
    assert isinstance(blueprint, ExamBlueprint)
    assert blueprint.session_id == "test-session"
    assert blueprint.exam_meta.exam_title == req.exam_title
    assert blueprint.exam_meta.total_points == 100

    # 가중치 적용
    assert blueprint.exam_meta.weights_applied["alpha"] == 0.5
    assert blueprint.exam_meta.weights_applied["beta"] == 0.5

    # 레벨 분포
    assert blueprint.difficulty_policy.target_distribution_level == {
        "L1": 1, "L2": 1, "L3": 6, "L4": 1, "L5": 1
    }

    # 슬롯 수
    assert len(blueprint.question_slots) == 10

    # 각 슬롯이 ROOT_DOCUMENT를 포함하지 않음
    for slot in blueprint.question_slots:
        assert "ROOT_DOCUMENT" not in slot.primary_concept_ids
        assert "ROOT_DOCUMENT" not in slot.secondary_concept_ids

    # 모든 슬롯이 유형-레벨 제약 만족
    from common.difficulty import is_valid_type_level_combo
    for slot in blueprint.question_slots:
        assert is_valid_type_level_combo(
            slot.question_type, slot.target_difficulty_level
        ), f"{slot.slot_id} 제약 위반"

    # L5 슬롯은 primary가 2개 이상
    l5_slots = [
        s for s in blueprint.question_slots
        if s.target_difficulty_level == 5
    ]
    for slot in l5_slots:
        assert len(slot.primary_concept_ids) >= 2, \
            f"L5 슬롯 {slot.slot_id}의 primary가 부족"

    print("✓ planner_full_flow_mock")
    print(f"   warnings: {len(blueprint.warnings)}개")
    if blueprint.warnings:
        for w in blueprint.warnings[:3]:
            print(f"     - {w}")


def test_planner_serialization():
    """Blueprint이 JSON 직렬화·역직렬화 가능."""
    req, struct = load_fixtures()
    client = GeminiClient(mock=True, mock_responder=fake_llm_responder_for_v05)
    planner = ExamPlanner(gemini_client=client)

    blueprint = planner.plan(req, struct, session_id="ser-test")
    serialized = blueprint.model_dump_json()
    rehydrated = ExamBlueprint.model_validate_json(serialized)

    assert rehydrated.blueprint_id == blueprint.blueprint_id
    assert len(rehydrated.question_slots) == len(blueprint.question_slots)
    print("✓ planner_serialization")


# ────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    test_split_group_to_levels_v05()
    test_split_group_odd_easy()
    test_build_section_structure()
    test_plan_slot_assignments_v05()
    test_plan_slot_assignments_infeasible()
    test_planner_full_flow_mock()
    test_planner_serialization()
    print("\n✓ All Exam Planner tests passed")
