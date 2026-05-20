# Agentic Exam Generation System

2026-1 Scientific Management — HW2 Team 7

LLM 기반 multi-agent 시스템으로, 강의자료와 교수 요구사항을 입력받아 시험 문항과 모범답안을 자동 생성합니다.

## 시스템 구조

총 10개의 agent + Human Reviewer로 구성된 agentic work system:

- **PG1 (R3)**: Material Collector, Req Parser, Topic Analyzer, Topic Prioritizer
- **PG2 (R4)**: Exam Planner, Q&A Generator, Factfulness Tester, Difficulty Tester, Rubric Machine, Paper Formattor
- **PG3 (R5)**: Supervisor (orchestration)

자세한 설계는 `agentic_system_schema_v0.4.json` 참고.

## 설치 및 실행

```bash
# 1. 가상환경 생성
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 2. 의존성 설치
pip install -r requirements.txt

# 3. 환경변수 설정
cp .env.example .env
# .env 파일을 열어 GCP 정보 입력

# 4. 인증
gcloud auth application-default login
# 또는 GOOGLE_APPLICATION_CREDENTIALS에 service account key 경로 설정
```

## 디렉토리 구조

```
exam_generation_system/
├── config/         # 설정값 + system prompts
├── common/         # 공통 모듈 (스키마, Gemini wrapper)
├── agents/         # 각 agent 구현
├── tests/          # 단위 테스트
└── notebooks/      # Colab 시연용
```

## 사용 예시

(통합 테스트 후 작성 예정)
