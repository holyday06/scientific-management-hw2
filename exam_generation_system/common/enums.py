"""
Shared enums used across all agents.

v0.4 스키마의 shared_enums를 Python으로 변환.
Pydantic 모델과 함수 시그니처에서 타입 안전하게 사용.
"""
from __future__ import annotations

from enum import Enum, IntEnum


class DifficultyLevel(IntEnum):
    """5단계 난이도 척도 (L1~L5).

    IntEnum이라 1~5 정수와 호환됨. e.g., DifficultyLevel.L3 == 3.
    """
    L1 = 1  # 하 (기초)
    L2 = 2  # 중하 (기본)
    L3 = 3  # 중 (표준 — baseline)
    L4 = 4  # 중상 (심화)
    L5 = 5  # 상 (최상)

    @property
    def group(self) -> "DifficultyGroup":
        """레벨을 3그룹(easy/medium/hard)으로 매핑."""
        if self in (DifficultyLevel.L1, DifficultyLevel.L2):
            return DifficultyGroup.EASY
        elif self == DifficultyLevel.L3:
            return DifficultyGroup.MEDIUM
        else:  # L4, L5
            return DifficultyGroup.HARD

    @property
    def label_ko(self) -> str:
        """한국어 라벨."""
        return {
            DifficultyLevel.L1: "하 (기초)",
            DifficultyLevel.L2: "중하 (기본)",
            DifficultyLevel.L3: "중 (표준)",
            DifficultyLevel.L4: "중상 (심화)",
            DifficultyLevel.L5: "상 (최상)",
        }[self]


class DifficultyGroup(str, Enum):
    """req_vector가 받는 그룹 단위 난이도."""
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


class QuestionType(str, Enum):
    """문항 유형.

    v0.5 결정: MCQ_multiple, calculation 제외.
    MCQ_single은 enum에 남겨두되 실제 사용 안 함 (호환용).
    """
    MCQ_SINGLE = "MCQ_single"
    SHORT_ANSWER = "short_answer"
    LONG_ANSWER = "long_answer"
    CASE_ANALYSIS = "case_analysis"


class EvaluationFocus(str, Enum):
    """시험 평가 목표. α/β 가중치를 결정."""
    BALANCED = "balanced"
    KNOWLEDGE_FOCUSED = "knowledge_focused"
    APPLICATION_FOCUSED = "application_focused"


class RoutingStatus(str, Enum):
    """메시지의 라우팅 상태.

    v0.5: difficulty_review를 제거하고 단일 difficulty_correction으로 통합.
    """
    FLOW = "flow"
    QUESTION_REWORK = "question_rework"
    DIFFICULTY_CORRECTION = "difficulty_correction"


class Verdict(str, Enum):
    """Tester의 판정 결과."""
    PASS = "pass"
    FAIL = "fail"


class TesterName(str, Enum):
    """Tester 종류 — tester_output에서 사용."""
    FACTFULNESS = "Factfulness_Tester"
    DIFFICULTY = "Difficulty_Tester"


class EdgeRelation(str, Enum):
    """concept_knowledge_structure의 edge 종류.

    R3 답변: 단방향 edge 한 쌍으로 양방향 관계를 표현.
    예) A가 B의 상위 개념: parent_of(A→B), child_of(B→A) 두 edge 동시 생성.
    """
    PREREQUISITE = "prerequisite"     # A→B: A를 먼저 알아야 B 학습 가능
    DEPENDS_ON = "depends_on"         # A→B: A가 B의 후속 (prerequisite의 역방향)
    PARENT_OF = "parent_of"           # A→B: A가 B의 상위
    CHILD_OF = "child_of"             # A→B: A가 B의 하위 (parent_of의 역방향)
    RELATED = "related"               # 양방향 (역방향 별도 X)


class Importance(str, Enum):
    """concept 노드의 중요도."""
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
