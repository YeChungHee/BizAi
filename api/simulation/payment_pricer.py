"""
결제조건별 가격결정 엔진 (Payment Terms Pricer).

CreditRiskProfile + 마진 래더 → 결제일별 적용 마진율 + 안전 납품 규모를 산출합니다.

파이프라인 위치:
    credit_risk → ★payment_pricer★ → (최종 견적)

핵심 공식:
    기간 PD = 1 - (1 - 연간PD)^(n일/365)
    기대손실(EL) = 기간PD × LGD
    위험프리미엄 = max(0, EL - 래더내재위험)
    최종마진 = 래더기준마진 + 위험프리미엄

사용:
    from simulation.credit_risk import assess_credit_risk
    from simulation.payment_pricer import price_payment_terms, MarginLadder

    profile = assess_credit_risk(grade="BB-", ...)
    result = price_payment_terms(profile, payment_days=60)

    print(result.primary.final_margin_pct)   # 8.54%
    print(result.ceiling.recommended)        # 10,000,000원
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from .credit_risk import CreditRiskProfile


# ─────────────────────────────────────────────────────────────
# 마진 래더
# ─────────────────────────────────────────────────────────────

@dataclass
class MarginLadder:
    """
    결제일 → 기본 마진율 매핑 (선형 보간).

    예: {3: 3.0, 60: 6.0, 90: 9.0}
        → 30일 = 4.4%, 75일 = 7.5%
    """
    breakpoints: dict[int, float]
    embedded_risk_pct: float = 0.5  # 래더에 이미 내재된 일반 신용 위험 (%)

    def interpolate(self, days: int) -> float:
        """선형 보간으로 임의 결제일의 기준 마진율 산출."""
        pts = sorted(self.breakpoints.items())
        if days <= pts[0][0]:
            return pts[0][1]
        if days >= pts[-1][0]:
            return pts[-1][1]
        for i in range(len(pts) - 1):
            lo_d, lo_m = pts[i]
            hi_d, hi_m = pts[i + 1]
            if lo_d <= days <= hi_d:
                t = (days - lo_d) / (hi_d - lo_d)
                return round(lo_m + t * (hi_m - lo_m), 2)
        return pts[-1][1]  # fallback


# 기본 래더 (FlowBizAi 표준)
DEFAULT_LADDER = MarginLadder(
    breakpoints={3: 3.0, 60: 6.0, 90: 9.0},
    embedded_risk_pct=0.5,
)


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class PaymentTermQuote:
    """특정 결제일에 대한 가격 견적."""
    payment_days: int
    ladder_margin_pct: float    # 래더 기준 마진 (%)
    period_pd_pct: float        # n일 기간 PD (%)
    expected_loss_pct: float    # EL = 기간PD × LGD (%)
    risk_premium_pct: float     # 추가 위험 프리미엄 (%)
    final_margin_pct: float     # 최종 적용 마진율 (%)


@dataclass
class ContractCeiling:
    """안전 납품 규모 산출 결과."""
    el_based: float             # 기대손실 역산 기반 (원)
    cash_based: float | None    # 현금 기반 Hard Ceiling (원)
    revenue_based: float | None # 매출 대비 신용한도 (원)
    recommended: float          # 최종 권고 (원)
    basis: str                  # 권고 근거 설명


@dataclass
class PaymentPricingResult:
    """결제조건 가격결정 종합 결과."""
    # 요청한 결제일 기준 견적
    primary: PaymentTermQuote

    # 전 결제일 비교 테이블
    all_terms: list[PaymentTermQuote]

    # 안전 계약 규모
    ceiling: ContractCeiling

    # 소스 데이터
    credit_risk: CreditRiskProfile
    ladder: MarginLadder

    def to_dict(self) -> dict:
        return {
            "primary": {
                "payment_days": self.primary.payment_days,
                "ladder_margin_pct": self.primary.ladder_margin_pct,
                "period_pd_pct": self.primary.period_pd_pct,
                "expected_loss_pct": self.primary.expected_loss_pct,
                "risk_premium_pct": self.primary.risk_premium_pct,
                "final_margin_pct": self.primary.final_margin_pct,
            },
            "all_terms": [
                {
                    "days": q.payment_days,
                    "margin_pct": q.final_margin_pct,
                    "pd_pct": q.period_pd_pct,
                    "el_pct": q.expected_loss_pct,
                }
                for q in self.all_terms
            ],
            "ceiling": {
                "el_based": self.ceiling.el_based,
                "cash_based": self.ceiling.cash_based,
                "revenue_based": self.ceiling.revenue_based,
                "recommended": self.ceiling.recommended,
                "basis": self.ceiling.basis,
            },
            "credit_risk": self.credit_risk.to_dict(),
        }

    def summary(self) -> str:
        p = self.primary
        c = self.ceiling
        lines = [
            f"═══ 결제조건 가격결정 ({p.payment_days}일) ═══",
            f"  등급: {self.credit_risk.grade} | PD: {self.credit_risk.adjusted_pd_pct:.1f}% | LGD: {self.credit_risk.lgd_pct:.0f}%",
            "",
            f"  결제일  래더기준   기간PD     EL    프리미엄   적용마진",
            f"  {'─'*6}  {'─'*6}  {'─'*7}  {'─'*7}  {'─'*7}  {'─'*7}",
        ]
        for q in self.all_terms:
            marker = " ◀" if q.payment_days == p.payment_days else ""
            lines.append(
                f"  {q.payment_days:>4}일  {q.ladder_margin_pct:>5.1f}%  "
                f"{q.period_pd_pct:>6.2f}%  {q.expected_loss_pct:>6.2f}%  "
                f"+{q.risk_premium_pct:>5.2f}%  {q.final_margin_pct:>6.2f}%{marker}"
            )
        lines.append("")
        lines.append(f"  안전 납품 규모: {c.recommended:,.0f}원 ({c.basis})")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 핵심 계산
# ─────────────────────────────────────────────────────────────

def _period_pd(annual_pd_pct: float, days: int) -> float:
    """연간 PD(%) → n일 기간 PD(%) 변환 (이산 복리)."""
    return round((1 - (1 - annual_pd_pct / 100) ** (days / 365)) * 100, 4)


def _quote_for_days(
    days: int,
    annual_pd_pct: float,
    lgd_pct: float,
    ladder: MarginLadder,
) -> PaymentTermQuote:
    """단일 결제일에 대한 견적 생성."""
    base = ladder.interpolate(days)
    pd_n = _period_pd(annual_pd_pct, days)
    el = round(pd_n * lgd_pct / 100, 4)
    premium = round(max(0.0, el - ladder.embedded_risk_pct), 2)
    final = round(base + premium, 2)

    return PaymentTermQuote(
        payment_days=days,
        ladder_margin_pct=base,
        period_pd_pct=pd_n,
        expected_loss_pct=el,
        risk_premium_pct=premium,
        final_margin_pct=final,
    )


def _calculate_ceiling(
    primary_quote: PaymentTermQuote,
    credit_risk: CreditRiskProfile,
    max_acceptable_loss: float,
    cash_available: float | None,
    annual_revenue: float | None,
) -> ContractCeiling:
    """안전 납품 규모 산출."""
    # [A] 기대손실 역산
    el_pct = primary_quote.expected_loss_pct
    el_based = max_acceptable_loss / (el_pct / 100) if el_pct > 0 else float('inf')
    el_based = round(el_based)

    # [B] 현금 기반 (가용현금의 30%)
    cash_based = None
    if cash_available and cash_available > 0:
        cash_based = round(cash_available * 0.30)

    # [C] 매출 대비 (등급별 비율)
    revenue_based = None
    rev_pct_map = {
        "AAA": 0.15, "AA": 0.12, "A": 0.08,
        "BBB": 0.05, "BB": 0.03, "BB-": 0.02,
        "B+": 0.02, "B": 0.015, "B-": 0.01,
        "CCC": 0.008, "CC": 0.005, "C": 0.003, "D": 0.0,
    }
    if annual_revenue and annual_revenue > 0:
        pct = rev_pct_map.get(credit_risk.grade, 0.01)
        revenue_based = round(annual_revenue * pct)

    # 최종 권고: 세 기준 중 가장 보수적(최소) 값
    candidates = [("기대손실 역산", el_based)]
    if cash_based is not None:
        candidates.append(("현금 기반", cash_based))
    if revenue_based is not None:
        candidates.append(("매출 대비", revenue_based))

    basis, recommended = min(candidates, key=lambda x: x[1])

    return ContractCeiling(
        el_based=el_based,
        cash_based=cash_based,
        revenue_based=revenue_based,
        recommended=recommended,
        basis=basis,
    )


# ─────────────────────────────────────────────────────────────
# 메인 함수
# ─────────────────────────────────────────────────────────────

def price_payment_terms(
    credit_risk: CreditRiskProfile,
    payment_days: int = 60,
    ladder: MarginLadder = DEFAULT_LADDER,
    max_acceptable_loss: float = 3_000_000,
    compare_days: list[int] | None = None,
) -> PaymentPricingResult:
    """
    신용 리스크 프로필 + 결제일 → 최종 마진율 + 안전 규모.

    Args:
        credit_risk: CreditRiskProfile (assess_credit_risk 결과)
        payment_days: 결제 유예 일수 (기본 60일)
        ladder: 마진 래더 (기본 3일=3%, 60일=6%, 90일=9%)
        max_acceptable_loss: 1회 허용 최대 손실 (원, 기본 300만원)
        compare_days: 비교 테이블에 포함할 결제일 목록
            None이면 래더 breakpoint + 요청일 자동 구성

    Returns:
        PaymentPricingResult
    """
    pd_pct = credit_risk.adjusted_pd_pct
    lgd_pct = credit_risk.lgd_pct

    # 비교 결제일 목록 자동 구성
    if compare_days is None:
        days_set = set(ladder.breakpoints.keys())
        days_set.add(payment_days)
        if credit_risk.ar_days is not None:
            days_set.add(int(credit_risk.ar_days))
        compare_days = sorted(days_set)

    # 전 결제일 견적 생성
    all_quotes = [
        _quote_for_days(d, pd_pct, lgd_pct, ladder)
        for d in compare_days
    ]

    # 요청 결제일 견적
    primary = _quote_for_days(payment_days, pd_pct, lgd_pct, ladder)

    # 안전 납품 규모
    ceiling = _calculate_ceiling(
        primary_quote=primary,
        credit_risk=credit_risk,
        max_acceptable_loss=max_acceptable_loss,
        cash_available=credit_risk.cash_available,
        annual_revenue=credit_risk.annual_revenue,
    )

    return PaymentPricingResult(
        primary=primary,
        all_terms=all_quotes,
        ceiling=ceiling,
        credit_risk=credit_risk,
        ladder=ladder,
    )
