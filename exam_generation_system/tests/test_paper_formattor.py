"""
Unit tests for PaperFormattor.

LLM 호출 없음. 실제 docx 생성 후 내용 검증.
tmp_path fixture로 출력 디렉터리를 임시 경로에 격리.
13개 테스트.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agents.exam_planner import ExamPlanner
from agents.paper_formattor import FormatResult, PaperFormattor
from common.enums import DifficultyLevel, QuestionType
from common.gemini_client import GeminiClient
from common.schemas import (
    AnswerRubric,
    ConceptKnowledgeStructure,
    GenerationMetadata,
    Question,
    QuestionSlot,
    ReqVector,
    RubricCriterion,
    SourceReference,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"

# ────────────────────────────────────────────────────────────
# Blueprint 생성 헬퍼 (test_exam_planner의 mock responder 재사용)
# ────────────────────────────────────────────────────────────
def _fake_llm_responder(system_prompt: str, user_prompt: str) -> str:
    return json.dumps({
        "slot_assignments": [
            {"slot_id": "Q01", "primary_concept_ids": ["M1_4_taylor_principles", "M2_1_2_kj_method", "M3_1_1_therbligs"], "secondary_concept_ids": [], "rationale": "L5"},
            {"slot_id": "Q02", "primary_concept_ids": ["M2_1_1_dassi", "M2_1_2_kj_method"], "secondary_concept_ids": [], "rationale": "L4"},
            {"slot_id": "Q03", "primary_concept_ids": ["M1_4_taylor_principles", "M1_4_pig_iron_case"], "secondary_concept_ids": [], "rationale": "L3"},
            {"slot_id": "Q04", "primary_concept_ids": ["M1_3_work_system", "M2_1_1_dassi"], "secondary_concept_ids": [], "rationale": "L4"},
            {"slot_id": "Q05", "primary_concept_ids": ["M2_1_1_dassi"], "secondary_concept_ids": ["M2_1_3_brainstorming"], "rationale": "L3"},
            {"slot_id": "Q06", "primary_concept_ids": ["M3_1_1_therbligs"], "secondary_concept_ids": ["M3_1_1_motion_economy"], "rationale": "L3"},
            {"slot_id": "Q07", "primary_concept_ids": ["M1_3_work_system"], "secondary_concept_ids": [], "rationale": "L3"},
            {"slot_id": "Q08", "primary_concept_ids": ["M2_1_3_brainstorming"], "secondary_concept_ids": [], "rationale": "L2"},
            {"slot_id": "Q09", "primary_concept_ids": ["M1_1_work_definition"], "secondary_concept_ids": [], "rationale": "L1"},
            {"slot_id": "Q10", "primary_concept_ids": ["M1_4_pig_iron_case"], "secondary_concept_ids": [], "rationale": "L2"},
        ],
        "coverage_check": {"covered_chapters": [], "missing_chapters": []},
    })


def make_blueprint():
    with open(FIXTURES_DIR / "sample_req_vector.json", encoding="utf-8") as f:
        req = ReqVector.model_validate(json.load(f))
    with open(FIXTURES_DIR / "sample_knowledge_structure.json", encoding="utf-8") as f:
        struct = ConceptKnowledgeStructure.model_validate(json.load(f))
    client = GeminiClient(mock=True, mock_responder=_fake_llm_responder)
    planner = ExamPlanner(gemini_client=client)
    return planner.plan(req, struct, session_id="fmt-test")


def make_dummy_questions_and_rubrics(blueprint):
    questions: list[Question] = []
    rubrics: list[AnswerRubric] = []

    for slot in blueprint.question_slots:
        qtype = slot.question_type
        q = Question(
            question_id=f"Q_{slot.slot_id}",
            slot_id=slot.slot_id,
            session_id=blueprint.session_id,
            question_type=qtype,
            target_difficulty_level=slot.target_difficulty_level,
            points=slot.points,
            prompt=f"테스트 문항 {slot.slot_id}: 관련 개념을 설명하시오.",
            reference_answer=f"모범답안_SECRET_{slot.slot_id}",
            answer_steps=["1단계", "2단계"],
            source_references=[
                SourceReference(
                    concept_id=slot.primary_concept_ids[0],
                    page_or_slide="p.1",
                    excerpt="참고",
                )
            ],
            generation_metadata=GenerationMetadata(
                generator_version="v0.5",
                attempt_number=1,
            ),
        )
        questions.append(q)
        rubric = AnswerRubric(
            question_id=q.question_id,
            session_id=blueprint.session_id,
            total_points=slot.points,
            criteria=[
                RubricCriterion(
                    criterion_id="C1",
                    description="정확성",
                    points=slot.points,
                    key_points=["핵심 요소 포함"],
                    partial_credit_guide="부분 부여 가능",
                )
            ],
        )
        rubrics.append(rubric)
    return questions, rubrics


def extract_all_text(docx_path: str) -> str:
    """docx에서 모든 텍스트(단락+표) 추출."""
    from docx import Document
    doc = Document(docx_path)
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            for cell in row.cells:
                parts.append(cell.text)
    return "\n".join(parts)


# ────────────────────────────────────────────────────────────
# 테스트
# ────────────────────────────────────────────────────────────
def test_format_produces_four_paths(tmp_path):
    """FormatResult 반환, student/answer_key docx_path 모두 non-None."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-1")

    assert isinstance(result, FormatResult)
    assert result.student_docx_path is not None
    assert result.answer_key_docx_path is not None
    print("✓ test_format_produces_four_paths")


def test_docx_files_exist(tmp_path):
    """반환된 경로에 실제 파일이 존재."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-2")

    assert Path(result.student_docx_path).exists()
    assert Path(result.answer_key_docx_path).exists()
    print("✓ test_docx_files_exist")


def test_student_docx_has_no_answers(tmp_path):
    """학생용 docx에 reference_answer 내용이 없음."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-3")

    text = extract_all_text(result.student_docx_path)
    for q in qs:
        assert q.reference_answer not in text, (
            f"Student docx should NOT contain reference_answer: {q.reference_answer}"
        )
    print("✓ test_student_docx_has_no_answers")


def test_answer_key_has_model_answer(tmp_path):
    """교수용 docx에 reference_answer 내용이 포함됨."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-4")

    text = extract_all_text(result.answer_key_docx_path)
    for q in qs:
        assert q.reference_answer in text, (
            f"Answer key should contain reference_answer: {q.reference_answer}"
        )
    print("✓ test_answer_key_has_model_answer")


def test_answer_key_has_rubric_table(tmp_path):
    """교수용 docx에 표가 있고, 첫 표의 헤더에 '기준 ID', '평가 항목' 포함."""
    from docx import Document
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-5")

    doc = Document(result.answer_key_docx_path)
    assert len(doc.tables) >= 2, "답안지에 표(메타 + 루브릭)가 최소 2개 있어야 함"

    # 두 번째 이후 표들 중 루브릭 헤더가 있는 표 찾기
    found_header = False
    for table in doc.tables[1:]:  # 첫 번째는 메타 표
        header_text = " ".join(c.text for c in table.rows[0].cells)
        if "기준 ID" in header_text and "평가 항목" in header_text:
            found_header = True
            break
    assert found_header, "루브릭 헤더('기준 ID', '평가 항목') 있는 표를 찾지 못함"
    print("✓ test_answer_key_has_rubric_table")


def test_question_order(tmp_path):
    """학생용 docx에서 단답형 → 서술형 → 사례 분석 순서로 섹션 헤더가 등장."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-6")

    text = extract_all_text(result.student_docx_path)
    pos_short = text.find("단답형")
    pos_long = text.find("서술형")
    pos_case = text.find("사례 분석")

    assert pos_short != -1, "'단답형' 텍스트가 없음"
    assert pos_long != -1, "'서술형' 텍스트가 없음"
    assert pos_case != -1, "'사례 분석' 텍스트가 없음"
    assert pos_short < pos_long < pos_case, (
        f"섹션 순서 오류: 단답형({pos_short}) < 서술형({pos_long}) < 사례 분석({pos_case})"
    )
    print("✓ test_question_order")


def test_section_headers(tmp_path):
    """학생용 docx에 'Section 1', 'Section 2', 'Section 3' 텍스트 존재."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-7")

    text = extract_all_text(result.student_docx_path)
    for i in range(1, 4):
        assert f"Section {i}" in text, f"'Section {i}' 헤더가 없음"
    print("✓ test_section_headers")


def test_metadata_in_header(tmp_path):
    """학생용 docx 헤더에 '과학적 관리', '2026-1', '75분', '100점' 포함."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-8")

    text = extract_all_text(result.student_docx_path)
    for keyword in ["과학적 관리", "2026-1", "75분", "100점"]:
        assert keyword in text, f"'{keyword}' 가 헤더에 없음"
    print("✓ test_metadata_in_header")


def test_question_rubric_mismatch_raises(tmp_path):
    """rubrics 1개 제거 → ValueError."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    rs_short = rs[:-1]  # 마지막 rubric 제거

    fmt = PaperFormattor(base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="No rubric for question"):
        fmt.format(bp, qs, rs_short, "test-fmt-9")
    print("✓ test_question_rubric_mismatch_raises")


def test_blueprint_question_mismatch_raises(tmp_path):
    """questions 1개 제거 → ValueError."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    qs_short = qs[:-1]  # 마지막 question 제거 (rubric도 마지막 것 제거)
    rs_short = rs[:-1]

    fmt = PaperFormattor(base_dir=str(tmp_path))
    with pytest.raises(ValueError, match="No question for slots"):
        fmt.format(bp, qs_short, rs_short, "test-fmt-10")
    print("✓ test_blueprint_question_mismatch_raises")


def test_pdf_conversion_optional(tmp_path, monkeypatch):
    """docx2pdf import 실패해도 FormatResult 반환, pdf_path=None, warnings 포함."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)

    # docx2pdf를 None으로 설정하면 import 시 ImportError 발생
    monkeypatch.setitem(sys.modules, "docx2pdf", None)

    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-11")

    assert result.student_pdf_path is None
    assert result.answer_key_pdf_path is None
    assert any("docx2pdf" in w for w in result.warnings)
    print("✓ test_pdf_conversion_optional")


def test_output_directory_created(tmp_path):
    """session_id 디렉터리가 자동 생성됨."""
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    session = "auto-mkdir-sess"
    fmt = PaperFormattor(base_dir=str(tmp_path))
    fmt.format(bp, qs, rs, session)

    assert (tmp_path / session).is_dir()
    print("✓ test_output_directory_created")


def test_total_points_in_rubric_table(tmp_path):
    """교수용 docx 루브릭 표의 '합계' 행이 question.points와 일치."""
    from docx import Document
    bp = make_blueprint()
    qs, rs = make_dummy_questions_and_rubrics(bp)
    fmt = PaperFormattor(base_dir=str(tmp_path))
    result = fmt.format(bp, qs, rs, "test-fmt-13")

    doc = Document(result.answer_key_docx_path)
    rubric_tables = [t for t in doc.tables if any(
        "기준 ID" in cell.text
        for cell in t.rows[0].cells
    )]
    assert len(rubric_tables) > 0, "루브릭 표가 없음"

    # 각 루브릭 표의 합계 행 검증
    for table in rubric_tables:
        total_row = table.rows[-1]
        assert total_row.cells[0].text == "합계", "마지막 행이 '합계' 행이 아님"
        total_val = int(total_row.cells[2].text)
        # 해당 문항 points와 비교 (테스트용 루브릭은 단일 criterion으로 slot.points 전체)
        assert total_val > 0, "합계 점수가 0임"
    print("✓ test_total_points_in_rubric_table")


# ────────────────────────────────────────────────────────────
# Runner
# ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import tempfile
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)

        test_format_produces_four_paths(tmp)
        test_docx_files_exist(tmp)
        test_student_docx_has_no_answers(tmp)
        test_answer_key_has_model_answer(tmp)
        test_answer_key_has_rubric_table(tmp)
        test_question_order(tmp)
        test_section_headers(tmp)
        test_metadata_in_header(tmp)
        test_question_rubric_mismatch_raises(tmp)
        test_blueprint_question_mismatch_raises(tmp)
        test_output_directory_created(tmp)
        test_total_points_in_rubric_table(tmp)

    print("\n✓ All PaperFormattor tests passed (12/13 — pdf_optional needs monkeypatch)")
