"""
BaseTester — Critic Agent의 추상 베이스 클래스.

Template Method 패턴:
  test() → _load_prompt() + _build_llm_context() + _call_llm() + _parse_and_score() + _build_output()

서브클래스가 반드시 구현해야 하는 메서드:
  _build_llm_context(question, slot, knowledge_structure) -> dict
  _parse_and_score(raw, slot) -> (score, extra_fields, reasons)
"""
from __future__ import annotations

import functools
import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional

from common.enums import RoutingStatus, TesterName, Verdict
from common.gemini_client import GeminiClient
from common.schemas import (
    ConceptKnowledgeStructure,
    NextAction,
    Question,
    QuestionSlot,
    TesterOutput,
)
from config.defaults import LLM_CONFIG, PROJECT_ROOT


@functools.lru_cache(maxsize=None)
def _load_prompt_file(path_str: str) -> str:
    """프롬프트 파일을 읽어 캐시. path_str은 절대 경로."""
    return Path(path_str).read_text(encoding="utf-8")


class BaseTester(ABC):
    tester_name: TesterName
    threshold: float
    prompt_path: str          # 예: "config/prompts/factfulness_tester.md"
    failure_routing: RoutingStatus
    failure_target: str       # "QA_Generator" | "Exam_Planner"
    use_heavy_model: bool = True

    def __init__(self, client: GeminiClient) -> None:
        self.client = client

    # ────────────────────────────────────────────────────────────
    # 공개 인터페이스
    # ────────────────────────────────────────────────────────────
    def test(
        self,
        question: Question,
        slot: QuestionSlot,
        knowledge_structure: ConceptKnowledgeStructure,
        session_id: str,
    ) -> TesterOutput:
        prompt = self._load_prompt()
        context = self._build_llm_context(question, slot, knowledge_structure)
        raw = self._call_llm(prompt, context)
        score, extra_fields, reasons = self._parse_and_score(raw, slot)
        return self._build_output(question.question_id, session_id, score, extra_fields, reasons)

    # ────────────────────────────────────────────────────────────
    # 내부 메서드 — 베이스 구현
    # ────────────────────────────────────────────────────────────
    def _load_prompt(self) -> str:
        full_path = str(PROJECT_ROOT / self.prompt_path)
        return _load_prompt_file(full_path)

    def _call_llm(self, prompt: str, context: dict) -> dict:
        model_override: Optional[str] = (
            LLM_CONFIG["models"]["heavy"] if self.use_heavy_model else None
        )
        return self.client.generate_json(
            agent_name=self.tester_name.value,
            system_prompt=prompt,
            user_prompt=json.dumps(context, ensure_ascii=False),
            model_override=model_override,
        )

    def _build_output(
        self,
        question_id: str,
        session_id: str,
        score: float,
        extra_fields: dict,
        reasons: list[str],
    ) -> TesterOutput:
        if score >= self.threshold:
            verdict = Verdict.PASS
            next_action = None
        else:
            verdict = Verdict.FAIL
            next_action = NextAction(
                routing_status=self.failure_routing,
                target_agent=self.failure_target,
            )

        return TesterOutput(
            tester_name=self.tester_name,
            question_id=question_id,
            session_id=session_id,
            score=round(score, 4),
            verdict=verdict,
            threshold_used=self.threshold,
            next_action=next_action,
            reasons=reasons,
            **extra_fields,
        )

    # ────────────────────────────────────────────────────────────
    # 추상 메서드 — 서브클래스 구현
    # ────────────────────────────────────────────────────────────
    @abstractmethod
    def _build_llm_context(
        self,
        question: Question,
        slot: QuestionSlot,
        knowledge_structure: ConceptKnowledgeStructure,
    ) -> dict:
        ...

    @abstractmethod
    def _parse_and_score(
        self,
        raw: dict,
        slot: QuestionSlot,
    ) -> tuple[float, dict, list[str]]:
        """raw LLM JSON을 파싱해 (score, extra_fields, reasons) 반환."""
        ...
