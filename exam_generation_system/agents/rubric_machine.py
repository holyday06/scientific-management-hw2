"""
Rubric Machine — 채점 기준(AnswerRubric) 생성 에이전트.

light 모델 사용. 총점 불일치 시 1회 재시도, 그래도 실패하면 fallback 사용.
"""
from __future__ import annotations

import functools
import json
import logging
from pathlib import Path
from typing import Optional

from pydantic import Field, ValidationError

from common.concept_utils import node_to_llm_context
from common.enums import EvaluationFocus, QuestionType
from common.gemini_client import GeminiClient
from common.schemas import (
    AnswerRubric,
    ConceptKnowledgeStructure,
    Question,
    QuestionSlot,
    RubricCriterion,
    StrictBase,
)
from config.defaults import PROJECT_ROOT

logger = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────
# LLM raw output 검증 모델
# ────────────────────────────────────────────────────────────
class RubricCriterionRaw(StrictBase):
    description: str
    points: int = Field(ge=1)
    key_points: list[str] = Field(min_length=1)
    partial_credit_guide: str


class RubricRawOutput(StrictBase):
    criteria: list[RubricCriterionRaw] = Field(min_length=1)


# ────────────────────────────────────────────────────────────
# 프롬프트 캐시
# ────────────────────────────────────────────────────────────
@functools.lru_cache(maxsize=None)
def _load_prompt_file(path_str: str) -> str:
    return Path(path_str).read_text(encoding="utf-8")


# ────────────────────────────────────────────────────────────
# Fallback 기준 데이터
# ────────────────────────────────────────────────────────────
_FALLBACK_SPECS: dict[str, list[tuple[str, list[str], str]]] = {
    "short_answer": [
        ("정확성", ["답안의 핵심 내용이 모범답안과 일치"], "부분 점수: 핵심 키워드 포함 시 절반 부여"),
        ("완결성", ["요구된 모든 요소 포함"], "부분 점수: 일부 요소 누락 시 절반 부여"),
    ],
    "long_answer": [
        ("핵심 개념 파악", ["핵심 개념 정확히 서술"], "부분 점수: 개념 언급하나 불완전 시 절반"),
        ("논리 전개", ["논리적 흐름이 명확"], "부분 점수: 흐름 있으나 비약 있을 때 절반"),
        ("근거 제시", ["강의 내용 기반 근거 포함"], "부분 점수: 근거 일부만 제시 시 절반"),
    ],
    "case_analysis": [
        ("상황 분석", ["주어진 상황의 핵심 파악"], "부분 점수: 일부 파악 시 절반"),
        ("개념 적용", ["관련 개념을 상황에 올바르게 적용"], "부분 점수: 개념 언급하나 적용 미흡 시 절반"),
        ("결론 도출", ["분석에 기반한 타당한 결론"], "부분 점수: 결론 있으나 근거 미약 시 절반"),
    ],
}


def _distribute_points(total: int, n: int) -> list[int]:
    """total 점수를 n개에 균등 분배. 나머지는 첫 항목에 추가."""
    base = total // n
    rem = total - base * n
    return [base + (1 if i < rem else 0) for i in range(n)]


class RubricMachine:
    PROMPT_PATH = "config/prompts/rubric_machine.md"
    MAX_RETRIES = 1
    DEFAULT_CRITERIA_COUNT: dict[str, int] = {
        "short_answer": 2,
        "long_answer": 3,
        "case_analysis": 3,
    }

    def __init__(self, client: GeminiClient) -> None:
        self.client = client

    # ────────────────────────────────────────────────────────────
    # 공개 인터페이스
    # ────────────────────────────────────────────────────────────
    def generate(
        self,
        question: Question,
        slot: QuestionSlot,
        knowledge_structure: ConceptKnowledgeStructure,
        session_id: str,
        evaluation_focus: EvaluationFocus = EvaluationFocus.BALANCED,
    ) -> AnswerRubric:
        total_points = question.points
        prompt = self._load_prompt()
        context = self._build_context(question, slot, knowledge_structure, evaluation_focus)

        prev_total: Optional[int] = None
        for attempt in range(self.MAX_RETRIES + 1):
            if attempt > 0 and prev_total is not None:
                context["retry_hint"] = (
                    f"previous attempt total was {prev_total}, must be {total_points}"
                )
            try:
                raw = self._call_llm(prompt, context)
                validated = RubricRawOutput.model_validate(raw)
                actual_total = sum(c.points for c in validated.criteria)
                if actual_total == total_points:
                    return self._build_rubric(question, session_id, validated.criteria)
                prev_total = actual_total
                logger.warning(
                    "[RubricMachine] %s: total mismatch (got %d, expected %d)",
                    question.question_id, actual_total, total_points,
                )
            except Exception as exc:
                prev_total = prev_total or 0
                logger.warning("[RubricMachine] attempt %d failed: %s", attempt + 1, exc)

        logger.warning("[RubricMachine] fallback rubric for %s", question.question_id)
        return self._fallback_rubric(question, session_id)

    # ────────────────────────────────────────────────────────────
    # 내부 메서드
    # ────────────────────────────────────────────────────────────
    def _load_prompt(self) -> str:
        full_path = str(PROJECT_ROOT / self.PROMPT_PATH)
        return _load_prompt_file(full_path)

    def _build_context(
        self,
        question: Question,
        slot: QuestionSlot,
        knowledge_structure: ConceptKnowledgeStructure,
        evaluation_focus: EvaluationFocus = EvaluationFocus.BALANCED,
    ) -> dict:
        qtype = (
            question.question_type
            if isinstance(question.question_type, str)
            else question.question_type.value
        )
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
                "question_type": qtype,
                "prompt": question.prompt,
                "reference_answer": question.reference_answer,
                "answer_steps": question.answer_steps,
            },
            "assigned_concepts": assigned_concepts,
            "total_points": question.points,
            "target_criteria_count": self.DEFAULT_CRITERIA_COUNT.get(qtype, 3),
            "evaluation_focus": evaluation_focus.value,
        }

    def _call_llm(self, prompt: str, context: dict) -> dict:
        return self.client.generate_json(
            agent_name="Rubric_Machine",
            system_prompt=prompt,
            user_prompt=json.dumps(context, ensure_ascii=False),
        )

    def _build_rubric(
        self,
        question: Question,
        session_id: str,
        raw_criteria: list[RubricCriterionRaw],
    ) -> AnswerRubric:
        criteria = [
            RubricCriterion(
                criterion_id=f"C{i + 1}",
                description=c.description,
                points=c.points,
                key_points=c.key_points,
                partial_credit_guide=c.partial_credit_guide,
            )
            for i, c in enumerate(raw_criteria)
        ]
        return AnswerRubric(
            question_id=question.question_id,
            session_id=session_id,
            total_points=question.points,
            criteria=criteria,
        )

    def _fallback_rubric(self, question: Question, session_id: str) -> AnswerRubric:
        qtype = (
            question.question_type
            if isinstance(question.question_type, str)
            else question.question_type.value
        )
        pts = question.points
        specs = _FALLBACK_SPECS.get(qtype, _FALLBACK_SPECS["long_answer"])
        point_alloc = _distribute_points(pts, len(specs))

        criteria = [
            RubricCriterion(
                criterion_id=f"C{i + 1}",
                description=desc,
                points=p,
                key_points=kp,
                partial_credit_guide=pcg,
            )
            for i, (p, (desc, kp, pcg)) in enumerate(zip(point_alloc, specs))
        ]
        return AnswerRubric(
            question_id=question.question_id,
            session_id=session_id,
            total_points=pts,
            criteria=criteria,
            warnings=["fallback_rubric_used"],
        )
