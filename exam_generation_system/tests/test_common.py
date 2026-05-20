"""
Smoke tests for common modules.

각 모듈 단독 테스트는 모듈 파일 내 if __name__ == "__main__"에 있고,
이 파일은 모듈 간 상호작용을 검증.
"""
from __future__ import annotations

import json

from common.difficulty import (
    compute_slot_difficulty,
    d_to_level,
    difficulty_alignment_score,
    is_valid_type_level_combo,
)
from common.enums import (
    DifficultyGroup,
    DifficultyLevel,
    EvaluationFocus,
    QuestionType,
    RoutingStatus,
)
from common.envelope import new_session_id, unwrap_payload, wrap_payload
from common.gemini_client import GeminiClient, get_usage_summary, reset_usage
from common.schemas import (
    ConceptEdge,
    ConceptKnowledgeStructure,
    ConceptNode,
    DifficultyDistributionGroup,
    QuestionTypeDistribution,
    ReqVector,
)
from common.enums import EdgeRelation


def test_difficulty_pipeline():
    """req_vector의 evaluation_focus가 D 계산에 반영되는지."""
    result_balanced = compute_slot_difficulty(
        depth_or_hop=2,
        question_type=QuestionType.LONG_ANSWER,
        focus=EvaluationFocus.BALANCED,
    )
    # X1=0.5, X2=0.7, balanced(0.5, 0.5) → D = 0.5*0.5 + 0.5*0.7 = 0.6 → L4
    assert result_balanced["d"] == 0.6
    assert result_balanced["level"] == DifficultyLevel.L4

    result_app = compute_slot_difficulty(
        depth_or_hop=2,
        question_type=QuestionType.LONG_ANSWER,
        focus=EvaluationFocus.APPLICATION_FOCUSED,
    )
    # X1=0.5, X2=0.7, app(0.3, 0.7) → D = 0.3*0.5 + 0.7*0.7 = 0.64 → L4
    assert result_app["d"] == 0.64
    print("✓ test_difficulty_pipeline")


def test_type_level_constraint():
    """case_analysis는 L1, L2 불가."""
    assert not is_valid_type_level_combo(
        QuestionType.CASE_ANALYSIS, DifficultyLevel.L1
    )
    assert not is_valid_type_level_combo(
        QuestionType.CASE_ANALYSIS, DifficultyLevel.L2
    )
    assert is_valid_type_level_combo(
        QuestionType.CASE_ANALYSIS, DifficultyLevel.L3
    )
    # short_answer는 L5 불가
    assert not is_valid_type_level_combo(
        QuestionType.SHORT_ANSWER, DifficultyLevel.L5
    )
    print("✓ test_type_level_constraint")


def test_alignment_score():
    """Difficulty Tester가 사용할 정렬도."""
    assert difficulty_alignment_score(0.5, 0.5) == 1.0
    assert difficulty_alignment_score(0.5, 0.7) == 0.8
    assert difficulty_alignment_score(0.0, 1.0) == 0.0
    print("✓ test_alignment_score")


def test_concept_graph():
    """ConceptKnowledgeStructure의 그래프 거리 계산."""
    nodes = [
        ConceptNode(
            concept_id="M1_4_taylor_principles",
            concept_name="Taylor's 4 Principles",
            depth_in_tree=1,
            intrinsic_difficulty_level=DifficultyLevel.L3,
            suitable_question_types=[QuestionType.LONG_ANSWER],
        ),
        ConceptNode(
            concept_id="M1_4_pig_iron_case",
            concept_name="Pig Iron Case",
            depth_in_tree=2,
            parent_concept_id="M1_4_taylor_principles",
            intrinsic_difficulty_level=DifficultyLevel.L2,
            suitable_question_types=[QuestionType.SHORT_ANSWER],
        ),
        ConceptNode(
            concept_id="M3_1_1_therbligs",
            concept_name="Therbligs",
            depth_in_tree=1,
            intrinsic_difficulty_level=DifficultyLevel.L2,
            suitable_question_types=[QuestionType.SHORT_ANSWER],
        ),
    ]
    edges = [
        ConceptEdge(
            from_concept_id="M1_4_taylor_principles",
            to_concept_id="M1_4_pig_iron_case",
            relation=EdgeRelation.PARENT_OF,
        ),
        ConceptEdge(
            from_concept_id="M1_4_taylor_principles",
            to_concept_id="M3_1_1_therbligs",
            relation=EdgeRelation.RELATED,
        ),
    ]
    structure = ConceptKnowledgeStructure(nodes=nodes, edges=edges)

    assert structure.get_node("M1_4_taylor_principles") is not None
    assert structure.get_depth_between(
        "M1_4_pig_iron_case", "M3_1_1_therbligs"
    ) == 2  # via taylor_principles
    print("✓ test_concept_graph")


def test_envelope_roundtrip():
    """envelope 봉투에 담아서 dump → load."""
    session = new_session_id()
    req = ReqVector(
        exam_title="Round-trip Test",
        exam_type="midterm",
        target_chapters_or_concepts=["M1_1"],
        total_points=100,
        duration_minutes=75,
        difficulty_distribution_group=DifficultyDistributionGroup(
            easy=2, medium=6, hard=2
        ),
        question_type_distribution=QuestionTypeDistribution(
            short_answer=2, long_answer=5, case_analysis=3
        ),
    )
    env = wrap_payload(
        session_id=session,
        source_agent="Req_Parser",
        target_agent="Topic_Prioritizer",
        payload=req,
    )
    # JSON 직렬화 → 역직렬화
    serialized = env.model_dump_json()
    rehydrated_env_dict = json.loads(serialized)
    from common.schemas import MessageEnvelope
    env2 = MessageEnvelope.model_validate(rehydrated_env_dict)
    req2 = unwrap_payload(env2, ReqVector)

    assert req2.exam_title == req.exam_title
    assert req2.evaluation_focus == req.evaluation_focus.value
    print("✓ test_envelope_roundtrip")


def test_gemini_mock():
    """Gemini client mock 모드로 dict 응답 받기."""
    reset_usage()

    def fake_planner_response(system: str, user: str) -> str:
        return json.dumps({
            "blueprint_id": "test-uuid",
            "session_id": "sess-1",
            "slots_count": 10,
        })

    client = GeminiClient(mock=True, mock_responder=fake_planner_response)
    result = client.generate_json(
        agent_name="Exam_Planner",
        system_prompt="test system",
        user_prompt="test user",
    )
    assert result["slots_count"] == 10

    summary = get_usage_summary()
    assert summary["total_calls"] == 1
    assert "Exam_Planner" in summary["per_agent"]
    print("✓ test_gemini_mock")


if __name__ == "__main__":
    test_difficulty_pipeline()
    test_type_level_constraint()
    test_alignment_score()
    test_concept_graph()
    test_envelope_roundtrip()
    test_gemini_mock()
    print("\n✓ All common module integration tests passed")
