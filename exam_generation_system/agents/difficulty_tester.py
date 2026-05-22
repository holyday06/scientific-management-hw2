"""
Difficulty Tester — LLM이 추정한 X1/X2로 난이도 정렬도를 검증.

expected_D는 슬롯의 slot.expected_D 사용 (Planner가 계산한 값).
estimated_D = α·X1 + β·X2 (BALANCED 가중치 기본값).
threshold = 0.60 미달 시 Exam_Planner에 난이도 재조정 요청.
"""
from __future__ import annotations

from common.concept_utils import get_max_distance_in_subset, node_to_llm_context
from common.difficulty import (
    d_to_level,
    difficulty_alignment_score,
    get_weights,
)
from common.enums import EvaluationFocus, RoutingStatus, TesterName
from common.schemas import (
    ConceptKnowledgeStructure,
    Question,
    QuestionSlot,
    StrictBase,
)

from agents.base_tester import BaseTester

_DEFAULT_FOCUS = EvaluationFocus.BALANCED


class DifficultyRawOutput(StrictBase):
    """LLM raw 출력 검증 모델."""
    estimated_X1: float
    estimated_X2: float
    x1_reasoning: str
    x2_reasoning: str

    from pydantic import field_validator

    @field_validator("estimated_X1", "estimated_X2")
    @classmethod
    def _clamp(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"value must be in [0, 1], got {v}")
        return v


class DifficultyTester(BaseTester):
    tester_name = TesterName.DIFFICULTY
    threshold = 0.60
    prompt_path = "config/prompts/difficulty_tester.md"
    failure_routing = RoutingStatus.DIFFICULTY_CORRECTION
    failure_target = "Exam_Planner"

    def _build_llm_context(
        self,
        question: Question,
        slot: QuestionSlot,
        knowledge_structure: ConceptKnowledgeStructure,
    ) -> dict:
        concept_ids = slot.primary_concept_ids
        nodes = [
            knowledge_structure.get_node(cid)
            for cid in concept_ids
            if knowledge_structure.get_node(cid) is not None
        ]
        max_dist = get_max_distance_in_subset(knowledge_structure, concept_ids)

        assigned_concepts = []
        for node in nodes:
            ctx = node_to_llm_context(node, include_embedding=False)
            ctx["depth_in_graph"] = node.depth_in_tree
            assigned_concepts.append(ctx)

        return {
            "question": {
                "question_id": question.question_id,
                "question_type": (
                    question.question_type
                    if isinstance(question.question_type, str)
                    else question.question_type.value
                ),
                "prompt": question.prompt,
                "reference_answer": question.reference_answer,
            },
            "assigned_concepts": assigned_concepts,
            "max_distance_in_subset": max_dist,
            "expected_slot": {
                "difficulty_level": f"L{int(slot.target_difficulty_level)}",
                "evaluation_focus": _DEFAULT_FOCUS.value,
                "concept_ids": concept_ids,
            },
        }

    def _parse_and_score(
        self,
        raw: dict,
        slot: QuestionSlot,
    ) -> tuple[float, dict, list[str]]:
        validated = DifficultyRawOutput.model_validate(raw)

        alpha, beta = get_weights(_DEFAULT_FOCUS)
        estimated_D = alpha * validated.estimated_X1 + beta * validated.estimated_X2
        estimated_level = d_to_level(estimated_D)

        expected_D = slot.expected_D
        score = difficulty_alignment_score(expected_D, estimated_D)

        reasons = [
            f"[x1] {validated.x1_reasoning}",
            f"[x2] {validated.x2_reasoning}",
            (
                f"[summary] expected=L{int(slot.target_difficulty_level)} "
                f"(D={expected_D:.2f}), "
                f"estimated=L{int(estimated_level)} "
                f"(D={estimated_D:.2f})"
            ),
        ]

        extra_fields = {
            "estimated_X1": validated.estimated_X1,
            "estimated_X2": validated.estimated_X2,
            "estimated_D": round(estimated_D, 4),
            "estimated_difficulty_level": estimated_level,
        }

        return score, extra_fields, reasons
