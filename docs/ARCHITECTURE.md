# BizAi 아키텍처 문서

> 재무제표·상담 기반 기업 평가 및 영업 자동화 파이프라인
> 최종 수정: 2026-04-09

---

## 1. 시스템 개요

BizAi는 **4단계 파이프라인**으로 구성됩니다.

```
[Input]                      [Analysis]              [Output]
  ├─ 재무제표 PDF              Step 1                  ├─ 리포트
  └─ 상담 음성/텍스트    ─→   재무/비재무 평가    ─→   ├─ 점수 (영역별)
                                                        └─ 등급 (AAA~D)
                                    ↓
                              Step 2
                              마진 시뮬레이션
                              (리스크 프리미엄)
                                    ↓
                              Step 3
                              이메일 제안서 생성
                                    ↓
                              Step 4
                              영업 트리거 & CRM 연동
```

---

## 2. 디렉토리 구조

```
BizAi/
├── index.html                      # GitHub Pages 데모 (기존)
├── README.md
├── docs/
│   └── ARCHITECTURE.md              # 이 문서
│
├── api/                             # Python 백엔드
│   ├── main.py                      # FastAPI 엔트리포인트
│   ├── config.py                    # 환경변수/시크릿
│   │
│   ├── schema/                      # 표준 JSON 스키마 (Option B)
│   │   ├── financial_statement.schema.json
│   │   ├── models.py                # 파이썬 dataclass
│   │   ├── validator.py             # 검증 + 비율 자동계산
│   │   └── examples/
│   │       └── sample_c26.json      # 3개년 샘플
│   │
│   ├── benchmark/                   # ECOS 벤치마크 (Option A ✅)
│   │   ├── ecos_loader.py           # ECOS API 수집
│   │   ├── lookup.py                # 조회 + 스코어링 헬퍼
│   │   ├── benchmark.db             # SQLite 데이터
│   │   └── benchmark.json           # JSON 백업
│   │
│   ├── ingest/                      # Step 0: 입력 파이프라인
│   │   ├── pdf_parser.py            # 재무제표 PDF → 표준 JSON
│   │   ├── dart_client.py           # (추후) DART API 클라이언트
│   │   ├── audio_transcriber.py     # 음성 → 텍스트 (call-report 연동)
│   │   └── unit_normalizer.py       # 백만원/천원 → 원 변환
│   │
│   ├── analysis/                    # Step 1: 평가 엔진
│   │   ├── ratio_calculator.py      # 재무비율 계산 (ROA/ROE/부채비율 등)
│   │   ├── financial_scorer.py      # 재무 스코어링 (ECOS peer 비교)
│   │   ├── red_flag_detector.py     # Red Flag 룰 엔진
│   │   ├── consultation_analyzer.py # LLM 기반 상담 구조화
│   │   ├── cross_validator.py       # 재무 ↔ 상담 교차검증
│   │   └── grade_calculator.py      # 종합 점수 → AAA~D 등급
│   │
│   ├── simulation/                  # Step 2: 마진 시뮬레이션
│   │   ├── risk_premium.py          # 등급 → 리스크 프리미엄 테이블
│   │   └── margin_simulator.py      # 시나리오 생성
│   │
│   ├── proposal/                    # Step 3: 제안서 생성
│   │   ├── template_selector.py     # 등급별 템플릿 분기
│   │   ├── proposal_generator.py    # LLM 기반 문서 생성
│   │   └── email_drafter.py         # Gmail MCP 연동
│   │
│   ├── trigger/                     # Step 4: 영업 트리거
│   │   ├── trigger_engine.py        # 규칙 기반 트리거
│   │   ├── salesmap_sync.py         # Salesmap CRM 연동
│   │   ├── slack_notifier.py        # Slack 알림
│   │   └── scheduler.py             # 주기 실행 (scheduled-tasks)
│   │
│   ├── storage/                     # 영속화
│   │   ├── company_repo.py          # 기업 정보 저장소
│   │   ├── report_repo.py           # 분석 리포트 저장소
│   │   └── db.py                    # SQLite/PostgreSQL 초기화
│   │
│   └── tests/
│       ├── test_ratio_calculator.py
│       ├── test_scorer.py
│       └── fixtures/
│           └── sample_statements.json
│
└── frontend/                        # (Phase 3) React 대시보드
    └── ...
```

---

## 3. 데이터 흐름 (End-to-End)

### 3.1 입력 단계
```
사용자 업로드
  ├─ 재무제표 PDF (예: "2024_삼성전자_감사보고서.pdf")
  └─ 상담 자료   (예: "2026-04-01_A사_미팅.m4a")

    ↓ ingest/pdf_parser.py
    ↓ ingest/audio_transcriber.py

표준 JSON (schema/financial_statement.schema.json 검증)
  + 상담 원문 텍스트 (JSON에 metadata 포함)
```

### 3.2 분석 단계 (Step 1)
```
표준 JSON
  ↓
[analysis/ratio_calculator.py]
  → 25개 재무비율 계산 (성장/수익/안정/활동/생산)
  ↓
[analysis/financial_scorer.py]
  → benchmark.lookup.Benchmark() 로 peer 조회
  → 지표별 0~100점 산출 + 가중평균
  ↓
[analysis/red_flag_detector.py]
  → 이익의 질, 매출채권 급증, 이자보상 < 1 등 10개 룰
  ↓
[analysis/consultation_analyzer.py]
  → Claude API로 상담 원문에서 8개 카테고리 구조화 추출
  → 경영진/사업모델/리스크/자금용도/상환계획 등
  ↓
[analysis/cross_validator.py]
  → "매출 성장" 발언 ↔ 재무 수치 대조
  → 불일치 플래그 생성
  ↓
[analysis/grade_calculator.py]
  → 재무(60) + 비재무(30) − 레드플래그(10) = 0~100
  → AAA/AA/A/BBB/BB/B/CCC/CC/C/D 10단계
```

### 3.3 시뮬레이션 단계 (Step 2)
```
Step 1 결과 (등급 + 리스크 플래그)
  ↓
[simulation/risk_premium.py]
  등급별 테이블:
    AAA~AA: +0.0%p
    A:      +0.5%p
    BBB:    +1.0%p
    BB:     +2.0%p
    B:      +4.0%p
    CCC~:   +8.0%p
  + Red Flag 개수별 +0.5%p/개
  ↓
[simulation/margin_simulator.py]
  Min / Likely / Max 3가지 시나리오 생성
  → 권장 마진율 + 근거 텍스트
```

### 3.4 제안서 생성 (Step 3)
```
분석 결과 + 마진 시뮬레이션
  ↓
[proposal/template_selector.py]
  등급 → 템플릿 분기:
    - A급↑: 경쟁력 가격 / 장기 파트너십
    - BBB~BB: 표준 조건 / 단계 거래
    - B↓: 선급금 / 담보 / 보증
  ↓
[proposal/proposal_generator.py]
  Claude API로 4섹션 생성:
    1. 현황진단 (재무/비재무 요약)
    2. 맞춤 제안 (상담 니즈 반영)
    3. 조건 (가격/납기/결제)
    4. Next Action
  ↓
[proposal/email_drafter.py]
  Gmail MCP로 draft 생성 (담당자 검토 후 발송)
```

### 3.5 트리거 & CRM (Step 4)
```
분석 결과
  ↓
[trigger/trigger_engine.py]
  룰:
    - A급 + 관심 표명  → 즉시 클로징 미팅 제안
    - BBB급 + 가격 이슈 → 1주 후 팔로업
    - B급 이하         → 담보/보증 협의 필요
    - Red Flag 3+      → 내부 심사 에스컬레이션
  ↓
[trigger/salesmap_sync.py]
  Salesmap MCP로 Deal/Lead 자동 생성/업데이트
  ↓
[trigger/slack_notifier.py]
  Slack DM으로 담당자 알림
  ↓
[trigger/scheduler.py]
  scheduled-tasks MCP로 팔로업 자동 실행
```

---

## 4. 핵심 모듈 계약

### 4.1 `ingest/pdf_parser.py`
```python
def parse_financial_pdf(pdf_path: Path) -> FinancialStatement:
    """
    재무제표 PDF → 표준 스키마 객체.

    내부:
      1. pdf 스킬로 페이지별 텍스트/표 추출
      2. Claude API로 계정과목 매핑 (LLM parsing)
      3. 단위 정규화 (unit_normalizer)
      4. Tier1 필수 필드 검증 → 누락 시 quality.missing_fields
      5. 신뢰도 스코어 계산
    """
```

### 4.2 `analysis/ratio_calculator.py`
```python
def calculate_ratios(statement: Statement) -> dict[str, float]:
    """
    Statement → 25개 재무비율.
    ECOS 지표 코드와 1:1 매칭:
      "501": 총자산증가율, "506": 매출액증가율,
      "602": ROA, "606": ROE, "611": 영업이익률,
      "701": 자기자본비율, "707": 부채비율, ...
    """
```

### 4.3 `analysis/financial_scorer.py`
```python
def score_financial(
    ratios: dict[str, float],
    company: Company,
    year: int,
) -> FinancialScore:
    """
    benchmark.Benchmark()로 peer 비교 후 영역별 점수.
    반환:
      FinancialScore(
        growth=72.5, profitability=85.1, stability=61.0,
        activity=70.3, productivity=68.9,
        overall=71.6,
        details=[ScoreResult, ...]  # 지표별 상세
      )
    """
```

### 4.4 `analysis/consultation_analyzer.py`
```python
def analyze_consultation(transcript: str) -> ConsultationAnalysis:
    """
    LLM 프롬프트 (고정):
      "다음 상담 내용에서 아래 8개 항목을 구조화 추출:
       1. 경영진 역량 (1-10점 + 근거)
       2. 사업모델 명확성
       3. 고객집중도
       4. 자금용도
       5. 상환계획
       6. 리스크 언급
       7. 발언 일관성
       8. 주요 quote 3개"
    """
```

### 4.5 `analysis/grade_calculator.py`
```python
def calculate_grade(
    financial: FinancialScore,
    consultation: ConsultationAnalysis,
    red_flags: list[RedFlag],
    audit: AuditInfo,
) -> Grade:
    """
    산식:
      base = financial.overall * 0.6 + consultation.overall * 0.3
      penalty = min(10, len(red_flags) * 3)
      if audit.opinion == "한정": penalty += 10
      if audit.opinion == "부적정": penalty += 30

      score = max(0, base - penalty)
      → 10단계 등급 매핑
    """
```

---

## 5. 의존성 맵 (MCP & 외부)

| 모듈 | 의존 MCP/API |
|---|---|
| `ingest/pdf_parser` | anthropic-skills:pdf, Claude API |
| `ingest/audio_transcriber` | anthropic-skills:call-report |
| `ingest/dart_client` | DART 오픈API (준비중) |
| `benchmark/ecos_loader` | ECOS API (BOK) ✅ 연동완료 |
| `analysis/consultation_analyzer` | Claude API |
| `proposal/proposal_generator` | Claude API |
| `proposal/email_drafter` | Gmail MCP |
| `trigger/salesmap_sync` | Salesmap MCP |
| `trigger/slack_notifier` | Slack MCP |
| `trigger/scheduler` | scheduled-tasks MCP |
| 기업 정보 저장 | Notion MCP (선택) |

---

## 6. 데이터 저장소 설계

### 6.1 SQLite (Phase 1~2)
```
benchmark.db         # ECOS 벤치마크 (✅ 구축됨)
bizai_main.db
  ├─ companies          # 기업 마스터
  ├─ financial_reports  # 표준 JSON 원본 + 계산된 비율
  ├─ consultations      # 상담 원문 + 구조화 결과
  ├─ evaluations        # 통합 평가 결과 (등급, 점수)
  ├─ proposals          # 생성된 제안서 이력
  └─ triggers           # 실행된 트리거 로그
```

### 6.2 이전 경로 (Phase 4)
SQLite → PostgreSQL 이전 시 스키마 호환 유지. 현재 JSON Schema가 PK 역할.

---

## 7. 개발 단계 & 마일스톤

| Phase | 기간 | 모듈 | 산출물 |
|---|---|---|---|
| **Phase 1a** | W1 | `benchmark/*` | ✅ ECOS 벤치마크 DB & lookup |
| **Phase 1b** | W1 | `schema/*` | 표준 JSON 스키마 v1 (진행중) |
| **Phase 2a** | W2 | `ingest/pdf_parser` | PDF → 표준 JSON |
| **Phase 2b** | W2 | `analysis/ratio_calculator` + `financial_scorer` | 재무 점수 |
| **Phase 2c** | W3 | `analysis/consultation_analyzer` + `cross_validator` | 비재무 점수 |
| **Phase 2d** | W3 | `analysis/red_flag_detector` + `grade_calculator` | 통합 등급 |
| **Phase 3** | W4 | `simulation/*` | ✅ 마진 시뮬레이션 |
| **Phase 4** | W5 | `proposal/*` | 이메일 제안서 생성 |
| **Phase 5** | W6 | `trigger/*` | CRM 연동 + 자동 트리거 |
| **Phase 6** | W7+ | 백테스트·튜닝 + DART 전환 | 운영 배포 |

---

## 8. 스코어링 최종 공식 (참조)

```
──────────────────────────────────────
 재무 스코어 (60%)
──────────────────────────────────────
  성장성  (가중 15%)  = avg(25개 지표 중 성장성 4개)
  수익성  (가중 25%)  = avg(수익성 8개)
  안정성  (가중 30%)  = avg(안정성 5개)
  활동성  (가중 15%)  = avg(활동성 4개)
  생산성  (가중 15%)  = avg(생산성 4개)

──────────────────────────────────────
 비재무 스코어 (30%)
──────────────────────────────────────
  경영진 역량          × 0.25
  사업모델 명확성      × 0.20
  고객 다변화          × 0.15
  자금용도·상환계획    × 0.20
  리스크 인지 & 일관성 × 0.20

──────────────────────────────────────
 페널티 (-10%)
──────────────────────────────────────
  Red Flag 수 × 3 (max 30)
  감사의견 한정   → -10
  감사의견 부적정 → -30
  상담↔재무 불일치 → -5 per 건

──────────────────────────────────────
 최종 = (재무×0.6 + 비재무×0.3) − 페널티
       → 0~100 → 10단계 등급
```

---

## 9. 진행 현황 체크리스트

- [x] ECOS 벤치마크 DB (17개 샘플 업종, 2022~2024)
- [ ] ECOS 전체 업종 확장 (진행중)
- [x] 표준 JSON 스키마 v1 (financial_statement.schema.json)
- [x] Python dataclass 모델 (models.py)
- [x] 스키마 validator + 비율 자동계산 (validator.py)
- [x] 예시 JSON (3개년 C26 샘플)
- [ ] PDF 파서
- [x] 재무비율 계산기 (analysis/ratio_calculator.py — 25개 지표, 5영역)
- [x] 재무 스코어링 엔진 (analysis/financial_scorer.py — ECOS peer 비교, 가중평균)
- [x] Red Flag 탐지기 (analysis/red_flag_detector.py — 10개 룰)
- [x] 상담 분석 프롬프트 (analysis/consultation_analyzer.py — 8개 카테고리, LLM+수동)
- [x] 교차검증 엔진 (analysis/cross_validator.py — 7개 룰)
- [x] 통합 등급 계산기 (analysis/grade_calculator.py — AAA~D 10단계)
- [x] Phase 2 통합 테스트 통과 (test_pipeline.py)
- [x] 리스크 프리미엄 (simulation/risk_premium.py — 등급+RF+감사의견 → %p)
- [x] 마진 시뮬레이터 (simulation/margin_simulator.py — Min/Likely/Max 3시나리오)
- [x] Phase 3 통합 테스트 통과 (test_simulation.py)
- [ ] 제안서 생성기
- [ ] 영업 트리거 엔진
