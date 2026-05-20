"""
Gemini Vertex AI wrapper.

모든 agent가 LLM을 호출할 때 사용하는 단일 진입점.
- JSON 출력 강제 (response_mime_type)
- 재시도 로직
- 사용량 로깅
- Mock 모드 (GCP 없이 단위 테스트 가능)
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from config.defaults import (
    DEBUG_MODE,
    ENABLE_USAGE_LOGGING,
    LLM_CONFIG,
)

logger = logging.getLogger(__name__)
if DEBUG_MODE:
    logging.basicConfig(level=logging.DEBUG)


# ────────────────────────────────────────────────────────────
# Usage tracking
# ────────────────────────────────────────────────────────────
@dataclass
class UsageRecord:
    """단일 LLM 호출의 사용량."""
    agent_name: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    duration_seconds: float = 0.0
    success: bool = True


@dataclass
class UsageTracker:
    """세션 전체의 누적 사용량."""
    records: list[UsageRecord] = field(default_factory=list)

    def add(self, record: UsageRecord) -> None:
        self.records.append(record)
        if ENABLE_USAGE_LOGGING:
            logger.info(
                f"[{record.agent_name}] model={record.model} "
                f"tokens={record.total_tokens} "
                f"duration={record.duration_seconds:.2f}s"
            )

    def summary(self) -> dict[str, Any]:
        total_tokens = sum(r.total_tokens for r in self.records)
        total_calls = len(self.records)
        per_agent: dict[str, dict[str, int]] = {}
        for r in self.records:
            agg = per_agent.setdefault(
                r.agent_name, {"calls": 0, "tokens": 0}
            )
            agg["calls"] += 1
            agg["tokens"] += r.total_tokens
        return {
            "total_calls": total_calls,
            "total_tokens": total_tokens,
            "per_agent": per_agent,
        }


# 모듈 전역 트래커
_tracker = UsageTracker()


def get_usage_summary() -> dict[str, Any]:
    """현재까지의 LLM 사용량 요약."""
    return _tracker.summary()


def reset_usage() -> None:
    """누적 사용량 초기화."""
    _tracker.records.clear()


# ────────────────────────────────────────────────────────────
# Client
# ────────────────────────────────────────────────────────────
class GeminiClient:
    """Vertex AI Gemini wrapper.

    실 호출은 google-genai SDK 사용 (Vertex AI 모드).
    mock=True로 생성하면 실제 호출 없이 stub 응답 반환.
    """

    def __init__(
        self,
        *,
        mock: bool = False,
        mock_responder: Optional[Callable[[str, str], str]] = None,
    ):
        """
        Args:
            mock: True면 GCP 호출 없이 mock 응답 사용.
            mock_responder: mock 모드 시 (system_prompt, user_prompt) → str 반환 함수.
                            None이면 빈 JSON 객체 반환.
        """
        self.mock = mock
        self.mock_responder = mock_responder or _default_mock_responder

        if not mock:
            self._client = self._init_real_client()

    def _init_real_client(self):
        """실제 google-genai 클라이언트 초기화."""
        try:
            from google import genai  # type: ignore
        except ImportError as e:
            raise ImportError(
                "google-genai가 설치되지 않았습니다. "
                "pip install google-genai 를 실행하세요."
            ) from e

        project_id = LLM_CONFIG["project_id"]
        region = LLM_CONFIG["region"]

        if not project_id:
            raise RuntimeError(
                "GCP_PROJECT_ID가 설정되지 않았습니다. "
                ".env 파일을 확인하거나 mock=True로 생성하세요."
            )

        # Vertex AI 모드로 클라이언트 생성
        return genai.Client(
            vertexai=True,
            project=project_id,
            location=region,
        )

    def _resolve_model(self, agent_name: str, override: Optional[str]) -> str:
        """agent_name → 모델명 매핑.

        override가 있으면 그것 사용, 없으면 config의 assignment 사용.
        """
        if override:
            return override

        assignment = LLM_CONFIG["agent_model_assignment"]
        weight_class = assignment.get(agent_name, "light")  # 기본 light
        return LLM_CONFIG["models"][weight_class]

    def generate_json(
        self,
        *,
        agent_name: str,
        system_prompt: str,
        user_prompt: str,
        model_override: Optional[str] = None,
        max_retries: int = 3,
        temperature: float = 0.0,
    ) -> dict[str, Any]:
        """JSON 출력을 강제하는 LLM 호출.

        Returns:
            파싱된 dict. 실패 시 RuntimeError.
        """
        model = self._resolve_model(agent_name, model_override)
        start = time.time()

        if self.mock:
            response_text = self.mock_responder(system_prompt, user_prompt)
            duration = time.time() - start
            _tracker.add(UsageRecord(
                agent_name=agent_name, model=f"mock:{model}",
                duration_seconds=duration,
            ))
            return json.loads(response_text)

        last_error: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                response = self._call_real_api(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    temperature=temperature,
                )

                # 사용량 기록
                usage = getattr(response, "usage_metadata", None)
                prompt_tok = getattr(usage, "prompt_token_count", 0) if usage else 0
                comp_tok = getattr(usage, "candidates_token_count", 0) if usage else 0
                total_tok = getattr(usage, "total_token_count", 0) if usage else 0

                _tracker.add(UsageRecord(
                    agent_name=agent_name, model=model,
                    prompt_tokens=prompt_tok,
                    completion_tokens=comp_tok,
                    total_tokens=total_tok,
                    duration_seconds=time.time() - start,
                ))

                # JSON 파싱
                text = response.text
                return json.loads(text)

            except json.JSONDecodeError as e:
                last_error = e
                logger.warning(
                    f"[{agent_name}] JSON parse fail (attempt {attempt}): {e}"
                )
                if attempt < max_retries:
                    time.sleep(0.5 * attempt)  # exponential-ish backoff
            except Exception as e:
                last_error = e
                logger.warning(
                    f"[{agent_name}] API error (attempt {attempt}): {e}"
                )
                if attempt < max_retries:
                    time.sleep(1.0 * attempt)

        _tracker.add(UsageRecord(
            agent_name=agent_name, model=model,
            duration_seconds=time.time() - start,
            success=False,
        ))
        raise RuntimeError(
            f"[{agent_name}] LLM 호출 {max_retries}회 실패: {last_error}"
        )

    def _call_real_api(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
    ):
        """실제 google-genai API 호출."""
        from google.genai import types  # type: ignore

        config = types.GenerateContentConfig(
            system_instruction=system_prompt,
            temperature=temperature,
            response_mime_type="application/json",
        )

        return self._client.models.generate_content(
            model=model,
            contents=user_prompt,
            config=config,
        )


# ────────────────────────────────────────────────────────────
# Default mock responder
# ────────────────────────────────────────────────────────────
def _default_mock_responder(system_prompt: str, user_prompt: str) -> str:
    """Mock 모드의 기본 응답: 빈 JSON 객체.

    실제 단위 테스트에선 agent별로 커스텀 mock_responder를 주입.
    """
    return "{}"


# ────────────────────────────────────────────────────────────
# Smoke test
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # mock 모드 테스트
    def echo_responder(system: str, user: str) -> str:
        return json.dumps({
            "received_system_chars": len(system),
            "received_user_chars": len(user),
            "echoed_back": "ok",
        })

    client = GeminiClient(mock=True, mock_responder=echo_responder)
    result = client.generate_json(
        agent_name="TestAgent",
        system_prompt="You are a test agent.",
        user_prompt="Hello, world.",
    )
    print(f"✓ Mock call result: {result}")
    print(f"✓ Usage summary: {get_usage_summary()}")
