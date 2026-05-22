"""
Factfulness Tester — 문항의 사실성(인용 타당성·답안 근거·범위 준수)을 검증.

LLM이 세 축을 각각 0~1로 평가 → 평균을 최종 score로 사용.
threshold = 0.80 미달 시 QA_Generator에게 재작성 요청.
"""
from __future__ import annotations

from common.concept_utils import node_to_llm_context
from common.enums import RoutingStatus, TesterName
from common.schemas import (
    ConceptKnowledgeStructure,
    Question,
    QuestionSlot,
    StrictBase,
)
from config.defaults import EXAM_SCOPE_MODULES

from agents.base_tester import BaseTester


class FactfulnessRawOutput(StrictBase):
    """LLM raw 출력 검증 모델."""
    citation_validity: float
    citation_issues: list[str]
    answer_grounding: float
    grounding_issues: list[str]
    scope_compliance: float
    scope_issues: list[str]
    overall_reasons: list[str]

    from pydantic import field_validator

    @field_validator("citation_validity", "answer_grounding", "scope_compliance")
    @classmethod
    def _clamp(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"score must be in [0, 1], got {v}")
        return v


class FactfulnessTester(BaseTester):
    tester_name = TesterName.FACTFULNESS
    threshold = 0.80
    prompt_path = "config/prompts/factfulness_tester.md"
    failure_routing = RoutingStatus.QUESTION_REWORK
    failure_target = "QA_Generator"

    def _build_llm_context(
        self,
        question: Question,
        slot: QuestionSlot,
        knowledge_structure: ConceptKnowledgeStructure,
    ) -> dict:
        nodes = [
            knowledge_structure.get_node(cid)
            for cid in slot.primary_concept_ids
            if knowledge_structure.get_node(cid) is not None
        ]
        assigned_concepts = [
            node_to_llm_context(node, include_embedding=False)
            for node in nodes
        ]
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
                "source_references": [
                    sr.model_dump() for sr in question.source_references
                ],
            },
            "assigned_concepts": assigned_concepts,
            "exam_scope": {
                "modules": EXAM_SCOPE_MODULES,
            },
        }

    def _parse_and_score(
        self,
        raw: dict,
        slot: QuestionSlot,
    ) -> tuple[float, dict, list[str]]:
        validated = FactfulnessRawOutput.model_validate(raw)

        score = (
            validated.citation_validity
            + validated.answer_grounding
            + validated.scope_compliance
        ) / 3.0

        reasons: list[str] = []
        for issue in validated.citation_issues:
            reasons.append(f"[citation] {issue}")
        for issue in validated.grounding_issues:
            reasons.append(f"[grounding] {issue}")
        for issue in validated.scope_issues:
            reasons.append(f"[scope] {issue}")
        for reason in validated.overall_reasons:
            reasons.append(f"[summary] {reason}")

        return score, {}, reasons
