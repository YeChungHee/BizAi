"""
등급별 리스크 프리미엄 테이블.

종합 등급(AAA~D)을 기반으로 리스크 프리미엄(%p)을 산출합니다.
Red Flag 개수에 따른 추가 프리미엄도 적용합니다.

ARCHITECTURE.md §3.3:
    AAA~AA: +0.0%p
    A:      +0.5%p
    BBB:    +1.0%p
    BB:     +2.0%p
    B:      +4.0%p
    CCC~:   +8.0%p
    + Red Flag 개수별 +0.5%p/개

사용:
    from simulation.risk_premium import calculate_risk_premium

    premium = calculate_risk_premium(grade="BB", red_flag_count=2)
    print(premium.total_premium)  # 3.0%p
"""

from __future__ import annotations

from dataclasses import dataclass


# ─────────────────────────────────────────────────────────────
# 등급별 기본 프리미엄 테이블
# ─────────────────────────────────────────────────────────────

GRADE_PREMIUM: dict[str, float] = {
    "AAA": 0.0,
    "AA":  0.0,
    "A":   0.5,
    "BBB": 1.0,
    "BB":  2.0,
    "B":   4.0,
    "CCC": 8.0,
    "CC":  12.0,
    "C":   16.0,
    "D":   20.0,  # 사실상 거래 불가이나 참고용
}

# Red Flag 추가 프리미엄
RF_PREMIUM_PER_FLAG = 0.5  # %p/개
RF_PREMIUM_MAX = 5.0       # 최대 5.0%p

# 감사의견별 추가 프리미엄
AUDIT_PREMIUM: dict[str, float] = {
    "적정": 0.0,
    "한정": 2.0,
    "부적정": 10.0,
    "의견거절": 10.0,
}


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class RiskPremium:
    grade: str
    base_premium: float          # 등급 기본 프리미엄 (%p)
    red_flag_premium: float      # RF 추가 프리미엄 (%p)
    audit_premium: float         # 감사의견 추가 프리미엄 (%p)
    total_premium: float         # 총 리스크 프리미엄 (%p)
    red_flag_count: int
    audit_opinion: str | None

    # 거래 조건 관련
    recommended_payment_terms: str   # 결제 조건 권고
    recommended_credit_limit: str    # 신용한도 권고
    requires_collateral: bool        # 담보 필요 여부
    requires_advance_payment: bool   # 선급금 필요 여부

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "base_premium": self.base_premium,
            "red_flag_premium": self.red_flag_premium,
            "audit_premium": self.audit_premium,
            "total_premium": self.total_premium,
            "red_flag_count": self.red_flag_count,
            "audit_opinion": self.audit_opinion,
            "recommended_payment_terms": self.recommended_payment_terms,
            "recommended_credit_limit": self.recommended_credit_limit,
            "requires_collateral": self.requires_collateral,
            "requires_advance_payment": self.requires_advance_payment,
        }


# ─────────────────────────────────────────────────────────────
# 거래 조건 매핑
# ─────────────────────────────────────────────────────────────

PAYMENT_TERMS: dict[str, str] = {
    "AAA": "T+30~60일 후불",
    "AA":  "T+30~60일 후불",
    "A":   "T+30일 후불",
    "BBB": "T+15~30일 후불",
    "BB":  "T+7~15일 후불 또는 COD",
    "B":   "COD(인도 시 결제) 또는 선급금 30%",
    "CCC": "선급금 50% + 잔금 인도 시",
    "CC":  "선급금 70% + 잔금 인도 시",
    "C":   "선급금 100%",
    "D":   "거래 보류 권고",
}

CREDIT_LIMIT: dict[str, str] = {
    "AAA": "매출의 10~15% (장기 한도 가능)",
    "AA":  "매출의 8~12%",
    "A":   "매출의 5~8%",
    "BBB": "매출의 3~5%",
    "BB":  "매출의 1~3%",
    "B":   "매출의 0.5~1% (단건 기준)",
    "CCC": "건별 소액 한도",
    "CC":  "시범 거래 (1건 완결 후 검토)",
    "C":   "신용 거래 불가",
    "D":   "거래 불가",
}


# ─────────────────────────────────────────────────────────────
# 프리미엄 계산
# ─────────────────────────────────────────────────────────────

def calculate_risk_premium(
    grade: str,
    red_flag_count: int = 0,
    audit_opinion: str | None = None,
) -> RiskPremium:
    """
    등급 + Red Flag + 감사의견을 종합하여 리스크 프리미엄 산출.

    Args:
        grade: 종합 등급 (AAA~D)
        red_flag_count: Red Flag 개수
        audit_opinion: 감사의견 (적정/한정/부적정/의견거절)

    Returns:
        RiskPremium
    """
    base = GRADE_PREMIUM.get(grade, 10.0)
    rf_premium = min(RF_PREMIUM_MAX, red_flag_count * RF_PREMIUM_PER_FLAG)
    audit_prem = AUDIT_PREMIUM.get(audit_opinion or "적정", 0.0)
    total = round(base + rf_premium + audit_prem, 2)

    # 담보/선급금 필요 여부
    requires_collateral = grade in ("B", "CCC", "CC", "C", "D")
    requires_advance = grade in ("CCC", "CC", "C", "D")

    return RiskPremium(
        grade=grade,
        base_premium=base,
        red_flag_premium=rf_premium,
        audit_premium=audit_prem,
        total_premium=total,
        red_flag_count=red_flag_count,
        audit_opinion=audit_opinion,
        recommended_payment_terms=PAYMENT_TERMS.get(grade, "거래 조건 협의 필요"),
        recommended_credit_limit=CREDIT_LIMIT.get(grade, "별도 심사 필요"),
        requires_collateral=requires_collateral,
        requires_advance_payment=requires_advance,
    )


def risk_premium_summary(rp: RiskPremium) -> str:
    """리스크 프리미엄 요약 텍스트."""
    lines = [
        f"═══ 리스크 프리미엄: +{rp.total_premium:.1f}%p ═══\n",
        f"  등급 기본:    +{rp.base_premium:.1f}%p ({rp.grade})",
        f"  Red Flag({rp.red_flag_count}건): +{rp.red_flag_premium:.1f}%p",
        f"  감사의견:     +{rp.audit_premium:.1f}%p ({rp.audit_opinion or '적정'})",
        f"  ─────────────────",
        f"  총 프리미엄:  +{rp.total_premium:.1f}%p\n",
        f"  결제 조건: {rp.recommended_payment_terms}",
        f"  신용 한도: {rp.recommended_credit_limit}",
    ]
    if rp.requires_collateral:
        lines.append("  ⚠️ 담보/보증 필요")
    if rp.requires_advance_payment:
        lines.append("  ⚠️ 선급금 필요")
    return "\n".join(lines)
