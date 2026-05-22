#!/usr/bin/env python3
"""
Live API test — Google Vertex AI Gemini 연결 + agent별 1회 호출.

Budget target : < $1
Runtime target: < 30 min

실행 전 .env에 아래 두 값 설정 필요:
    GCP_PROJECT_ID=your-actual-project-id
    GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account-key.json

실행:
    python live_api_test.py
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

# ────────────────────────────────────────────────────────────
# 최소 입력용 데이터 (토큰 절약)
# ────────────────────────────────────────────────────────────
_MINIMAL_STRUCT_JSON = {
    "nodes": [
        {
            "concept_id": "ROOT_DOCUMENT",
            "concept_name": "(root)",
            "depth_in_tree": 0,
            "intrinsic_difficulty_level": 1,
            "suitable_question_types": [],
        },
        {
            "concept_id": "M1_4_taylor_principles",
            "concept_name": "Taylor의 과학적 관리 4원칙",
            "depth_in_tree": 1,
            "parent_concept_id": "ROOT_DOCUMENT",
            "importance": "high",
            "intrinsic_difficulty_level": 3,
            "suitable_question_types": ["short_answer", "long_answer"],
            "source_pages": ["M1.4 p.3"],
        },
    ],
    "edges": [
        {"from_concept_id": "ROOT_DOCUMENT", "to_concept_id": "M1_4_taylor_principles",
         "relation": "parent_of", "weight": 1.0},
        {"from_concept_id": "M1_4_taylor_principles", "to_concept_id": "ROOT_DOCUMENT",
         "relation": "child_of", "weight": 1.0},
    ],
}

PASS = "✓"
FAIL = "✗"

results: list[dict] = []


def _record(name: str, ok: bool, elapsed: float, detail: str = "") -> None:
    results.append({"name": name, "ok": ok, "elapsed": elapsed, "detail": detail})
    mark = PASS if ok else FAIL
    status = "PASS" if ok else "FAIL"
    line = f"  [{mark}] {status}  {elapsed:.1f}s"
    if detail:
        line += f"  — {detail}"
    print(line)


# ────────────────────────────────────────────────────────────
# 0. 환경 확인
# ────────────────────────────────────────────────────────────
def check_env() -> bool:
    print("\n[환경 확인]")
    project_id = os.getenv("GCP_PROJECT_ID", "")
    creds_path = os.getenv("GOOGLE_APPLICATION_CREDENTIALS", "")

    ok = True
    if not project_id or project_id == "your-project-id-here":
        print("  [!] GCP_PROJECT_ID 미설정 — .env에 실제 프로젝트 ID를 입력하세요.")
        ok = False
    else:
        print(f"  [OK] GCP_PROJECT_ID = {project_id}")

    if not creds_path:
        print("  [!] GOOGLE_APPLICATION_CREDENTIALS 미설정")
        ok = False
    elif not Path(creds_path).exists():
        print(f"  [!] 서비스 계정 키 파일 없음: {creds_path}")
        ok = False
    else:
        print(f"  [OK] GOOGLE_APPLICATION_CREDENTIALS = {creds_path}")

    return ok


# ────────────────────────────────────────────────────────────
# Test 0: Ping (flash, 최소 프롬프트)
# ────────────────────────────────────────────────────────────
def test_ping(client) -> bool:
    print("\n[Test 0] Ping — gemini-2.5-flash 연결 확인")
    from config.defaults import LLM_CONFIG
    t0 = time.time()
    try:
        result = client.generate_json(
            agent_name="ping_test",
            system_prompt="You are a minimal test assistant.",
            user_prompt='Return JSON: {"status": "ok", "message": "PONG"}',
            model_override=LLM_CONFIG["models"]["light"],
        )
        elapsed = time.time() - t0
        ok = result.get("status") == "ok"
        _record("Ping (flash)", ok, elapsed, str(result))
        return ok
    except Exception as exc:
        elapsed = time.time() - t0
        _record("Ping (flash)", False, elapsed, str(exc)[:120])
        return False


# ────────────────────────────────────────────────────────────
# Test 1: ExamPlanner — 1문항 short_answer blueprint
# ────────────────────────────────────────────────────────────
def test_exam_planner(client) -> bool:
    print("\n[Test 1] ExamPlanner — 1문항 blueprint 생성 (heavy)")
    from agents.exam_planner import ExamPlanner
    from common.schemas import (
        ConceptKnowledgeStructure,
        DifficultyDistributionGroup,
        QuestionTypeDistribution,
        ReqVector,
    )

    req = ReqVector(
        exam_title="[LIVE TEST] Scientific Management",
        exam_type="midterm",
        target_chapters_or_concepts=["M1_4_taylor_principles"],
        total_points=10,
        duration_minutes=20,
        difficulty_distribution_group=DifficultyDistributionGroup(easy=0, medium=1, hard=0),
        question_type_distribution=QuestionTypeDistribution(short_answer=1),
    )
    struct = ConceptKnowledgeStructure.model_validate(_MINIMAL_STRUCT_JSON)
    planner = ExamPlanner(gemini_client=client)

    t0 = time.time()
    try:
        bp = planner.plan(req, struct, session_id="live-test-planner")
        elapsed = time.time() - t0
        slot = bp.question_slots[0]
        detail = (
            f"slots={len(bp.question_slots)}, "
            f"slot0=({slot.question_type}/{slot.target_difficulty_level})"
        )
        _record("ExamPlanner", True, elapsed, detail)
        return True
    except Exception as exc:
        elapsed = time.time() - t0
        _record("ExamPlanner", False, elapsed, str(exc)[:120])
        return False


# ────────────────────────────────────────────────────────────
# Test 2: FactfulnessTester
# ────────────────────────────────────────────────────────────
def test_factfulness_tester(client) -> bool:
    print("\n[Test 2] FactfulnessTester (heavy)")
    from agents.factfulness_tester import FactfulnessTester
    from common.enums import DifficultyLevel, QuestionType
    from common.schemas import (
        ConceptKnowledgeStructure,
        GenerationMetadata,
        Question,
        QuestionSlot,
        SourceReference,
    )

    struct = ConceptKnowledgeStructure.model_validate(_MINIMAL_STRUCT_JSON)
    slot = QuestionSlot(
        slot_id="S01",
        section_id="SEC1",
        question_type=QuestionType.SHORT_ANSWER,
        target_difficulty_level=DifficultyLevel.L3,
        expected_X1=0.25,
        expected_X2=0.4,
        expected_D=0.325,
        points=10,
        primary_concept_ids=["M1_4_taylor_principles"],
    )
    question = Question(
        question_id="Q_LIVE_01",
        slot_id="S01",
        session_id="live-test",
        question_type=QuestionType.SHORT_ANSWER,
        target_difficulty_level=DifficultyLevel.L3,
        points=10,
        prompt="Taylor의 과학적 관리 4원칙을 나열하시오.",
        reference_answer=(
            "Taylor의 4원칙: (1) 작업의 과학화, (2) 과학적 선발·훈련, "
            "(3) 노사 협력, (4) 분업 (관리·노동 분리)."
        ),
        answer_steps=["4원칙 나열"],
        source_references=[
            SourceReference(
                concept_id="M1_4_taylor_principles",
                page_or_slide="M1.4 p.3",
                excerpt="Taylor의 과학적 관리 4원칙",
            )
        ],
        generation_metadata=GenerationMetadata(
            generator_version="v0.5", attempt_number=1
        ),
    )

    tester = FactfulnessTester(client)
    t0 = time.time()
    try:
        out = tester.test(question, slot, struct, "live-test")
        elapsed = time.time() - t0
        detail = f"verdict={out.verdict}  score={out.score:.3f}"
        _record("FactfulnessTester", True, elapsed, detail)
        return True
    except Exception as exc:
        elapsed = time.time() - t0
        _record("FactfulnessTester", False, elapsed, str(exc)[:120])
        return False


# ────────────────────────────────────────────────────────────
# Test 3: DifficultyTester
# ────────────────────────────────────────────────────────────
def test_difficulty_tester(client) -> bool:
    print("\n[Test 3] DifficultyTester (heavy)")
    from agents.difficulty_tester import DifficultyTester
    from common.enums import DifficultyLevel, QuestionType
    from common.schemas import (
        ConceptKnowledgeStructure,
        GenerationMetadata,
        Question,
        QuestionSlot,
        SourceReference,
    )

    struct = ConceptKnowledgeStructure.model_validate(_MINIMAL_STRUCT_JSON)
    slot = QuestionSlot(
        slot_id="S01",
        section_id="SEC1",
        question_type=QuestionType.SHORT_ANSWER,
        target_difficulty_level=DifficultyLevel.L2,
        expected_X1=0.25,
        expected_X2=0.4,
        expected_D=0.325,
        points=10,
        primary_concept_ids=["M1_4_taylor_principles"],
    )
    question = Question(
        question_id="Q_LIVE_02",
        slot_id="S01",
        session_id="live-test",
        question_type=QuestionType.SHORT_ANSWER,
        target_difficulty_level=DifficultyLevel.L2,
        points=10,
        prompt="Taylor의 과학적 관리 원칙에서 '과학적 선발'이란 무엇인가?",
        reference_answer="과학적 선발이란 직무에 가장 적합한 근로자를 체계적으로 선발·훈련하는 것이다.",
        answer_steps=["개념 정의"],
        source_references=[
            SourceReference(
                concept_id="M1_4_taylor_principles",
                page_or_slide="M1.4 p.3",
                excerpt="Taylor 선발·훈련",
            )
        ],
        generation_metadata=GenerationMetadata(
            generator_version="v0.5", attempt_number=1
        ),
    )

    tester = DifficultyTester(client)
    t0 = time.time()
    try:
        out = tester.test(question, slot, struct, "live-test")
        elapsed = time.time() - t0
        detail = (
            f"verdict={out.verdict}  score={out.score:.3f}  "
            f"estimated=L{int(out.estimated_difficulty_level)}"
            if out.estimated_difficulty_level else f"verdict={out.verdict}"
        )
        _record("DifficultyTester", True, elapsed, detail)
        return True
    except Exception as exc:
        elapsed = time.time() - t0
        _record("DifficultyTester", False, elapsed, str(exc)[:120])
        return False


# ────────────────────────────────────────────────────────────
# Test 4: RubricMachine (light)
# ────────────────────────────────────────────────────────────
def test_rubric_machine(client) -> bool:
    print("\n[Test 4] RubricMachine (light)")
    from agents.rubric_machine import RubricMachine
    from common.enums import DifficultyLevel, QuestionType
    from common.schemas import (
        ConceptKnowledgeStructure,
        GenerationMetadata,
        Question,
        QuestionSlot,
        SourceReference,
    )

    struct = ConceptKnowledgeStructure.model_validate(_MINIMAL_STRUCT_JSON)
    slot = QuestionSlot(
        slot_id="S01",
        section_id="SEC1",
        question_type=QuestionType.SHORT_ANSWER,
        target_difficulty_level=DifficultyLevel.L2,
        expected_X1=0.25,
        expected_X2=0.4,
        expected_D=0.325,
        points=10,
        primary_concept_ids=["M1_4_taylor_principles"],
    )
    question = Question(
        question_id="Q_LIVE_03",
        slot_id="S01",
        session_id="live-test",
        question_type=QuestionType.SHORT_ANSWER,
        target_difficulty_level=DifficultyLevel.L2,
        points=10,
        prompt="Taylor의 과학적 관리 4원칙을 간략히 나열하시오.",
        reference_answer=(
            "(1) 작업의 과학화, (2) 과학적 선발·훈련, "
            "(3) 노사 협력, (4) 관리·노동 분업."
        ),
        answer_steps=["4원칙 나열"],
        source_references=[
            SourceReference(
                concept_id="M1_4_taylor_principles",
                page_or_slide="M1.4 p.3",
                excerpt="Taylor 4원칙",
            )
        ],
        generation_metadata=GenerationMetadata(
            generator_version="v0.5", attempt_number=1
        ),
    )

    machine = RubricMachine(client)
    t0 = time.time()
    try:
        rubric = machine.generate(question, slot, struct, "live-test")
        elapsed = time.time() - t0
        total = sum(c.points for c in rubric.criteria)
        detail = (
            f"criteria={len(rubric.criteria)}  "
            f"total_pts={total}/{question.points}  "
            f"{'[fallback]' if rubric.warnings else '[LLM]'}"
        )
        ok = total == question.points
        _record("RubricMachine", ok, elapsed, detail)
        return ok
    except Exception as exc:
        elapsed = time.time() - t0
        _record("RubricMachine", False, elapsed, str(exc)[:120])
        return False


# ────────────────────────────────────────────────────────────
# 메인
# ────────────────────────────────────────────────────────────
def main() -> None:
    print("=" * 60)
    print("  Live API Test — exam_generation_system")
    print("=" * 60)

    if not check_env():
        print(
            "\n[중단] .env 설정 후 재실행하세요.\n"
            "  1) GCP_PROJECT_ID=<실제 프로젝트 ID>\n"
            "  2) GOOGLE_APPLICATION_CREDENTIALS=<서비스 계정 키 파일 절대 경로>"
        )
        sys.exit(1)

    from common.gemini_client import GeminiClient, get_usage_summary, reset_usage

    reset_usage()
    client = GeminiClient(mock=False)
    total_start = time.time()

    test_ping(client)
    test_exam_planner(client)
    test_factfulness_tester(client)
    test_difficulty_tester(client)
    test_rubric_machine(client)

    total_elapsed = time.time() - total_start
    usage = get_usage_summary()

    print("\n" + "=" * 60)
    print(f"  완료  총 소요: {total_elapsed:.1f}s")
    print(f"  LLM 호출: {usage['total_calls']}건  토큰: {usage['total_tokens']:,}")
    passed = sum(1 for r in results if r["ok"])
    print(f"  결과: {passed}/{len(results)} PASS")
    print("=" * 60)

    # 개별 결과 요약
    print("\n[결과 요약]")
    for r in results:
        mark = PASS if r["ok"] else FAIL
        print(f"  {mark} {r['name']:<25} {r['elapsed']:5.1f}s  {r['detail']}")

    # per-agent 사용량
    print("\n[Agent별 호출 내역]")
    for agent, info in usage["per_agent"].items():
        print(f"  {agent:<30} calls={info['calls']}  tokens={info['tokens']:,}")

    if passed < len(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
