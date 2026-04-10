"""
상담 분석 모듈 (LLM 기반).

상담 원문(텍스트/음성 전사본)에서 8개 카테고리를 구조화 추출합니다.
Claude API를 호출하여 분석하며, API 없이도 사용할 수 있도록
수동 입력 인터페이스도 제공합니다.

ARCHITECTURE.md §4.4 참조.

사용:
    from analysis.consultation_analyzer import (
        ConsultationAnalysis, analyze_consultation, manual_analysis
    )

    # LLM 분석 (Claude API 필요)
    result = await analyze_consultation(transcript_text, api_key="sk-...")

    # 수동 입력 (API 없이)
    result = manual_analysis(
        management=CategoryScore(score=7, evidence="창업 10년 경력, 업계 인맥"),
        business_model=CategoryScore(score=8, evidence="B2B SaaS, 구독 모델"),
        ...
    )
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from typing import Optional


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class CategoryAssessment:
    """개별 카테고리 평가 결과."""
    category: str      # 카테고리명
    score: float       # 1~10
    evidence: str      # 근거 (상담 원문 인용 또는 설명)
    confidence: float = 1.0  # 0~1 (LLM 분석 시 신뢰도)


@dataclass
class ConsultationAnalysis:
    """상담 분석 종합 결과."""
    management: CategoryAssessment       # 1. 경영진 역량
    business_model: CategoryAssessment   # 2. 사업모델 명확성
    customer_concentration: CategoryAssessment  # 3. 고객집중도 (다변화)
    fund_purpose: CategoryAssessment     # 4. 자금용도
    repayment_plan: CategoryAssessment   # 5. 상환계획
    risk_awareness: CategoryAssessment   # 6. 리스크 인지
    consistency: CategoryAssessment      # 7. 발언 일관성
    key_quotes: list[str] = field(default_factory=list)  # 8. 주요 인용구 3개
    raw_transcript: Optional[str] = None
    analysis_method: str = "manual"      # manual | llm

    @property
    def overall(self) -> float:
        """
        가중 종합 점수 (0~100 스케일).

        가중치 (ARCHITECTURE.md §8 비재무):
            경영진 역량: 25%
            사업모델: 20%
            고객다변화: 15%
            자금용도+상환: 20%
            리스크+일관성: 20%
        """
        weighted = (
            self.management.score * 0.25
            + self.business_model.score * 0.20
            + self.customer_concentration.score * 0.15
            + (self.fund_purpose.score + self.repayment_plan.score) / 2 * 0.20
            + (self.risk_awareness.score + self.consistency.score) / 2 * 0.20
        )
        return round(weighted * 10, 1)  # 1~10 → 0~100

    @property
    def categories(self) -> list[CategoryAssessment]:
        return [
            self.management,
            self.business_model,
            self.customer_concentration,
            self.fund_purpose,
            self.repayment_plan,
            self.risk_awareness,
            self.consistency,
        ]

    def to_dict(self) -> dict:
        return {
            "overall": self.overall,
            "analysis_method": self.analysis_method,
            "categories": [asdict(c) for c in self.categories],
            "key_quotes": self.key_quotes,
        }


# ─────────────────────────────────────────────────────────────
# 수동 분석 (API 불필요)
# ─────────────────────────────────────────────────────────────

def manual_analysis(
    management_score: float, management_evidence: str,
    business_model_score: float, business_model_evidence: str,
    customer_concentration_score: float, customer_concentration_evidence: str,
    fund_purpose_score: float, fund_purpose_evidence: str,
    repayment_plan_score: float, repayment_plan_evidence: str,
    risk_awareness_score: float, risk_awareness_evidence: str,
    consistency_score: float, consistency_evidence: str,
    key_quotes: Optional[list[str]] = None,
) -> ConsultationAnalysis:
    """수동으로 상담 분석 결과를 생성."""
    return ConsultationAnalysis(
        management=CategoryAssessment("경영진 역량", management_score, management_evidence),
        business_model=CategoryAssessment("사업모델 명확성", business_model_score, business_model_evidence),
        customer_concentration=CategoryAssessment("고객집중도", customer_concentration_score, customer_concentration_evidence),
        fund_purpose=CategoryAssessment("자금용도", fund_purpose_score, fund_purpose_evidence),
        repayment_plan=CategoryAssessment("상환계획", repayment_plan_score, repayment_plan_evidence),
        risk_awareness=CategoryAssessment("리스크 인지", risk_awareness_score, risk_awareness_evidence),
        consistency=CategoryAssessment("발언 일관성", consistency_score, consistency_evidence),
        key_quotes=key_quotes or [],
        analysis_method="manual",
    )


# ─────────────────────────────────────────────────────────────
# LLM 분석 프롬프트 & 파서
# ─────────────────────────────────────────────────────────────

ANALYSIS_PROMPT = """당신은 B2B 여신 심사 전문가입니다. 아래 상담 내용에서 다음 8개 항목을 구조화하여 추출하세요.

각 항목에 대해 1~10점 점수와 근거(원문 인용 포함)를 제시하세요.

1. **경영진 역량** (경험, 전문성, 리더십, 업계 평판)
2. **사업모델 명확성** (수익 구조, 경쟁 우위, 시장 포지셔닝)
3. **고객집중도** (매출 다변화, 주요 고객 의존도)
4. **자금용도** (명확성, 합리성, 사업 연관성)
5. **상환계획** (구체성, 실현 가능성, 현금흐름 기반 여부)
6. **리스크 인지** (경영진의 리스크 인식 수준, 대응 계획)
7. **발언 일관성** (상담 중 발언의 논리적 일관성, 모순 여부)
8. **주요 인용구** (핵심 발언 3개를 원문 그대로 추출)

응답은 반드시 아래 JSON 형식으로:

```json
{
  "management": {"score": 7, "evidence": "..."},
  "business_model": {"score": 8, "evidence": "..."},
  "customer_concentration": {"score": 6, "evidence": "..."},
  "fund_purpose": {"score": 7, "evidence": "..."},
  "repayment_plan": {"score": 5, "evidence": "..."},
  "risk_awareness": {"score": 6, "evidence": "..."},
  "consistency": {"score": 8, "evidence": "..."},
  "key_quotes": ["인용1", "인용2", "인용3"]
}
```

상담 내용:
---
{transcript}
---
"""


def parse_llm_response(response_text: str, transcript: str = "") -> ConsultationAnalysis:
    """
    LLM 응답(JSON)을 파싱하여 ConsultationAnalysis 객체 생성.

    Args:
        response_text: LLM이 반환한 JSON 문자열
        transcript: 원본 상담 텍스트

    Raises:
        ValueError: JSON 파싱 실패 시
    """
    # JSON 블록 추출 (```json ... ``` 형태 처리)
    text = response_text.strip()
    if "```json" in text:
        text = text.split("```json")[1].split("```")[0].strip()
    elif "```" in text:
        text = text.split("```")[1].split("```")[0].strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"LLM 응답 JSON 파싱 실패: {e}\n원문: {text[:200]}")

    def _cat(key: str, name: str) -> CategoryAssessment:
        item = data.get(key, {})
        return CategoryAssessment(
            category=name,
            score=float(item.get("score", 5)),
            evidence=str(item.get("evidence", "정보 없음")),
            confidence=float(item.get("confidence", 0.8)),
        )

    return ConsultationAnalysis(
        management=_cat("management", "경영진 역량"),
        business_model=_cat("business_model", "사업모델 명확성"),
        customer_concentration=_cat("customer_concentration", "고객집중도"),
        fund_purpose=_cat("fund_purpose", "자금용도"),
        repayment_plan=_cat("repayment_plan", "상환계획"),
        risk_awareness=_cat("risk_awareness", "리스크 인지"),
        consistency=_cat("consistency", "발언 일관성"),
        key_quotes=data.get("key_quotes", []),
        raw_transcript=transcript,
        analysis_method="llm",
    )


async def analyze_consultation(
    transcript: str,
    api_key: Optional[str] = None,
    model: str = "claude-sonnet-4-6",
) -> ConsultationAnalysis:
    """
    Claude API를 호출하여 상담 분석 수행.

    주의: anthropic 패키지가 설치되어 있어야 합니다.
          pip install anthropic

    Args:
        transcript: 상담 원문 텍스트
        api_key: Anthropic API 키 (없으면 ANTHROPIC_API_KEY 환경변수)
        model: 사용할 모델

    Returns:
        ConsultationAnalysis
    """
    try:
        import anthropic
    except ImportError:
        raise ImportError(
            "anthropic 패키지가 필요합니다: pip install anthropic"
        )

    import os
    key = api_key or os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        raise ValueError("API 키가 필요합니다 (인자 또는 ANTHROPIC_API_KEY 환경변수)")

    client = anthropic.AsyncAnthropic(api_key=key)
    prompt = ANALYSIS_PROMPT.format(transcript=transcript)

    message = await client.messages.create(
        model=model,
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}],
    )

    response_text = message.content[0].text
    return parse_llm_response(response_text, transcript)


def consultation_summary(analysis: ConsultationAnalysis) -> str:
    """상담 분석 요약 텍스트 생성."""
    lines = [
        f"═══ 비재무 분석 ({analysis.analysis_method}) ═══",
        f"종합: {analysis.overall:.1f}/100\n",
    ]

    WEIGHTS = {
        "경영진 역량": 25, "사업모델 명확성": 20, "고객집중도": 15,
        "자금용도": 20, "상환계획": 20, "리스크 인지": 20, "발언 일관성": 20,
    }
    for cat in analysis.categories:
        w = WEIGHTS.get(cat.category, 0)
        bar = "█" * int(cat.score) + "░" * (10 - int(cat.score))
        lines.append(
            f"  {cat.category:12s} {cat.score:>4.1f}/10 {bar}"
        )
        lines.append(f"    → {cat.evidence[:80]}")

    if analysis.key_quotes:
        lines.append("\n── 주요 발언 ──")
        for i, q in enumerate(analysis.key_quotes, 1):
            lines.append(f"  {i}. \"{q}\"")

    return "\n".join(lines)
