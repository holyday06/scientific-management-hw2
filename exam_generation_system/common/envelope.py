"""
Message envelope helpers.

agent 간 통신 시 MessageEnvelope을 생성·전달·검증하는 유틸.
PG3 Supervisor가 envelope을 보고 routing 결정.
"""
from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel

from common.enums import RoutingStatus
from common.schemas import MessageEnvelope


def wrap_payload(
    *,
    session_id: str,
    source_agent: str,
    target_agent: str,
    payload: BaseModel | dict[str, Any],
    routing_status: RoutingStatus = RoutingStatus.FLOW,
) -> MessageEnvelope:
    """payload(Pydantic 모델 또는 dict)를 envelope으로 감싸기.

    Pydantic 모델이 들어오면 model_dump()로 dict 변환 후 envelope에 담음.
    """
    payload_dict = (
        payload.model_dump() if isinstance(payload, BaseModel) else payload
    )
    return MessageEnvelope(
        session_id=session_id,
        source_agent=source_agent,
        target_agent=target_agent,
        payload=payload_dict,
        routing_status=routing_status,
    )


def new_session_id() -> str:
    """새 세션 ID 발급."""
    return str(uuid.uuid4())


def unwrap_payload(
    envelope: MessageEnvelope,
    model_cls: type[BaseModel],
) -> BaseModel:
    """envelope의 payload를 지정한 Pydantic 모델로 역직렬화.

    >>> # envelope.payload가 dict이고, model_cls가 ReqVector 클래스인 경우
    >>> # → ReqVector 인스턴스 반환
    """
    return model_cls.model_validate(envelope.payload)


if __name__ == "__main__":
    from common.schemas import (
        DifficultyDistributionGroup,
        QuestionTypeDistribution,
        ReqVector,
    )

    # 기본 사용
    session = new_session_id()
    req = ReqVector(
        exam_title="Smoke Test",
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
    print(f"✓ Wrapped: {env.message_id[:8]}... routing={env.routing_status}")

    # Round-trip
    unwrapped = unwrap_payload(env, ReqVector)
    assert unwrapped.exam_title == req.exam_title
    print("✓ Round-trip OK")
