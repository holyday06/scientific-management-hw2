"""
Common modules shared across all agents.

PG1, PG2, PG3 모두 이 패키지에서 import:
    from common.schemas import Question, ExamBlueprint, ...
    from common.enums import QuestionType, DifficultyLevel, ...
    from common.difficulty import compute_slot_difficulty
    from common.gemini_client import GeminiClient
    from common.envelope import wrap_payload, new_session_id
"""
