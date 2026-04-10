# BizAI — AI 기업정보 영업 인텔리전스 플랫폼

> FlowPay B2B 영업을 위한 AI 기반 기업 정보 수집 및 Trapped Cash 분석 플랫폼

**🌐 라이브 데모:** https://yechunghee.github.io/BizAi

---

## 서비스 개요

BizAI는 FlowPay의 B2B 영업 담당자가 타겟 기업의 재무 상태를 분석하고, 즉시 활용 가능한 맞춤형 제안서를 자동 생성하는 AI 영업 인텔리전스 플랫폼입니다.

---

## 핵심 기능

### 1. 💰 Trapped Cash 시뮬레이터
- 재무제표 핵심 지표(외상매출금, 미수금, 유동부채) 입력
- T+1 정산 전환 시 즉시 확보 가능한 현금 자동 산출
- 연간 이자비용 절감 효과 계산

### 2. 📋 제안서 자동 생성
- 기업 정보 + 재무 데이터 기반 4섹션 제안서 즉시 생성
- 섹션 구성: 현황진단 → 잠긴돈찾기 → 성장기회 → Action

### 3. 🚨 영업 트리거 알림
- 투자유치 / 신제품 런칭 / 재무위험 3종 트리거 분류
- 복사 즉시 발송 가능한 Hook 메시지 자동 생성
- Slack 알림 자동 발송

### 4. ⚙️ 자동화 파이프라인
- DART 전자공시 API 실시간 모니터링
- Salesmap CRM 리드 자동 생성
- Notion DB 제안서 동기화
- 스케줄러 기반 매일 자동 스캔

---

## 개발 로드맵

| Phase | 기간 | 내용 |
|-------|------|------|
| Phase 1 | Week 1–3 | 데이터 수집 & 트리거 엔진 (DART API + Slack 알림) |
| Phase 2 | Week 4–6 | 재무 분석 & 제안서 자동 생성 |
| Phase 3 | Week 7–9 | 인터랙티브 대시보드 (React) |
| Phase 4 | Week 10–12 | CRM 연동 & 풀 자동화 |

---

## 기술 스택

- **Frontend:** HTML5 / React / Tailwind CSS
- **Backend:** Python / FastAPI
- **AI:** Claude API (claude-sonnet-4-6)
- **Data:** DART 전자공시 API, 네이버 뉴스 API
- **연동:** Slack MCP, Notion MCP, Salesmap CRM, Gmail MCP

---

## DART API 연동 정보

- **서비스명:** BizAI
- **서비스 URL:** https://yechunghee.github.io/BizAi
- **용도:** 기업 공시 데이터 수집을 통한 B2B 영업 인텔리전스 분석
- **개발사:** 276홀딩스

---

## 파일 구조

```
BizAi/
├── index.html          # 메인 데모 페이지 (GitHub Pages 배포)
├── README.md           # 프로젝트 설명
└── (추가 예정)
    ├── src/            # 소스 코드
    ├── api/            # Python 백엔드
    └── docs/           # 문서
```

---

© 2026 276홀딩스 | appler@276holdings.com
