"""
재무 ↔ 상담 교차검증 모듈.

재무제표 수치와 상담 발언 사이의 불일치를 탐지합니다.
예: "매출이 크게 성장했다" ↔ 실제 매출 감소 → 불일치 플래그

ARCHITECTURE.md §3.2:
    → "매출 성장" 발언 ↔ 재무 수치 대조
    → 불일치 플래그 생성
    → 불일치 건당 -5점 페널티

사용:
    from analysis.cross_validator import cross_validate

    mismatches = cross_validate(ratios, consultation, stmt_current)
    # → [Mismatch(code="XV01", ...), ...]
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .consultation_analyzer import ConsultationAnalysis
from .ratio_calculator import calculate_ratios

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "schema"))
from models import Statement  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 불일치 결과
# ─────────────────────────────────────────────────────────────

@dataclass
class Mismatch:
    code: str           # XV01 ~ XV07
    category: str       # 검증 영역
    claim: str          # 상담에서의 발언/주장
    reality: str        # 실제 재무 수치
    severity: str       # high | medium | low
    penalty: float = 5.0  # 기본 페널티 점수


# ─────────────────────────────────────────────────────────────
# 교차검증 룰
# ─────────────────────────────────────────────────────────────

def cross_validate(
    ratios: dict[str, float | None],
    consultation: ConsultationAnalysis,
    current: Statement,
    prior: Optional[Statement] = None,
) -> list[Mismatch]:
    """
    재무비율과 상담 분석 결과를 교차검증.

    Args:
        ratios: calculate_ratios() 반환값
        consultation: ConsultationAnalysis 결과
        current: 당기 재무제표
        prior: 전기 재무제표 (선택)

    Returns:
        탐지된 불일치 리스트
    """
    mismatches: list[Mismatch] = []
    bs = current.balance_sheet
    is_ = current.income_statement

    # 상담 발언 텍스트 결합 (검색용)
    all_evidence = " ".join(
        cat.evidence.lower() for cat in consultation.categories
    )
    all_quotes = " ".join(q.lower() for q in consultation.key_quotes)
    text_pool = all_evidence + " " + all_quotes

    # ── XV01: 매출 성장 주장 ↔ 실제 매출 감소 ──
    growth_keywords = ["매출 성장", "매출 증가", "매출이 늘", "성장세", "급성장"]
    if any(kw in text_pool for kw in growth_keywords):
        rev_growth = ratios.get("506")
        if rev_growth is not None and rev_growth < 0:
            mismatches.append(Mismatch(
                code="XV01",
                category="매출 성장",
                claim="상담에서 매출 성장/증가 언급",
                reality=f"실제 매출액증가율: {rev_growth:.1f}% (감소)",
                severity="high",
            ))

    # ── XV02: 수익성 양호 주장 ↔ 영업이익 적자 ──
    profit_keywords = ["수익성 좋", "마진 좋", "흑자", "영업이익 개선", "이익 증가"]
    if any(kw in text_pool for kw in profit_keywords):
        if is_.operating_profit < 0:
            mismatches.append(Mismatch(
                code="XV02",
                category="수익성",
                claim="상담에서 수익성 양호/흑자 언급",
                reality=f"실제 영업이익: {is_.operating_profit:,.0f} (적자)",
                severity="high",
            ))

    # ── XV03: 재무안정 주장 ↔ 고부채비율 ──
    stability_keywords = ["재무 안정", "부채 적", "건전한 재무", "재무구조 양호"]
    if any(kw in text_pool for kw in stability_keywords):
        debt_ratio = ratios.get("707")
        if debt_ratio is not None and debt_ratio > 200:
            mismatches.append(Mismatch(
                code="XV03",
                category="재무안정성",
                claim="상담에서 재무 안정성 언급",
                reality=f"실제 부채비율: {debt_ratio:.1f}% (200% 초과)",
                severity="high",
            ))

    # ── XV04: 현금 풍부 주장 ↔ 유동비율 저조 ──
    cash_keywords = ["현금 충분", "자금 여유", "유동성 양호", "현금 보유"]
    if any(kw in text_pool for kw in cash_keywords):
        current_ratio = ratios.get("702")
        if current_ratio is not None and current_ratio < 100:
            mismatches.append(Mismatch(
                code="XV04",
                category="유동성",
                claim="상담에서 현금 충분/유동성 양호 언급",
                reality=f"실제 유동비율: {current_ratio:.1f}% (100% 미만)",
                severity="medium",
            ))

    # ── XV05: 고객 다변화 주장 ↔ 상담 점수 저조 ──
    diversify_keywords = ["고객 다양", "다변화", "여러 거래처", "분산"]
    if any(kw in text_pool for kw in diversify_keywords):
        if consultation.customer_concentration.score <= 4:
            mismatches.append(Mismatch(
                code="XV05",
                category="고객집중도",
                claim="상담에서 고객 다변화 주장",
                reality=f"상담 분석 고객집중도 점수: {consultation.customer_concentration.score}/10 (낮음)",
                severity="medium",
                penalty=3.0,
            ))

    # ── XV06: 상환 자신감 ↔ 영업CF 부족 ──
    repay_keywords = ["상환 가능", "갚을 수 있", "현금흐름으로 상환", "문제없"]
    if any(kw in text_pool for kw in repay_keywords):
        cf = current.cash_flow
        total_debt = bs.total_debt
        if total_debt and total_debt > 0 and cf.operating_cf < total_debt * 0.15:
            mismatches.append(Mismatch(
                code="XV06",
                category="상환능력",
                claim="상담에서 상환 자신감 표현",
                reality=(
                    f"영업CF({cf.operating_cf:,.0f}) < "
                    f"총차입금의 15%({total_debt*0.15:,.0f})"
                ),
                severity="high",
            ))

    # ── XV07: 경영진 점수 ↔ 재무 전반 불일치 ──
    # 경영진 역량 점수가 높은데 재무 성과가 전반적으로 저조
    if consultation.management.score >= 8:
        poor_indicators = sum(
            1 for code in ["602", "606", "611"]
            if ratios.get(code) is not None and ratios[code] < 0  # type: ignore
        )
        if poor_indicators >= 2:
            mismatches.append(Mismatch(
                code="XV07",
                category="경영 성과 괴리",
                claim=f"경영진 역량 점수: {consultation.management.score}/10 (높음)",
                reality=f"수익성 지표 {poor_indicators}개 음수 (ROA/ROE/영업이익률)",
                severity="medium",
                penalty=3.0,
            ))

    return mismatches


def cross_validation_summary(mismatches: list[Mismatch]) -> str:
    """교차검증 결과 요약."""
    if not mismatches:
        return "✅ 교차검증: 재무-상담 간 불일치 없음"

    total_penalty = sum(m.penalty for m in mismatches)
    lines = [
        f"⚠️  교차검증: {len(mismatches)}건 불일치 (총 페널티: -{total_penalty:.0f}점)\n"
    ]
    severity_emoji = {"high": "🔴", "medium": "🟡", "low": "⚪"}
    for m in mismatches:
        emoji = severity_emoji.get(m.severity, "⚪")
        lines.append(f"  {emoji} [{m.code}] {m.category}")
        lines.append(f"     발언: {m.claim}")
        lines.append(f"     실제: {m.reality}")
        lines.append(f"     페널티: -{m.penalty:.0f}점")
    return "\n".join(lines)
