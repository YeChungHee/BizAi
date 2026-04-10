"""
마진 시뮬레이터.

리스크 프리미엄과 기본 마진율을 결합하여
Min / Likely / Max 3가지 시나리오를 생성합니다.

ARCHITECTURE.md §3.3:
    [simulation/margin_simulator.py]
    Min / Likely / Max 3가지 시나리오 생성
    → 권장 마진율 + 근거 텍스트

사용:
    from simulation.margin_simulator import simulate_margin
    from simulation.risk_premium import calculate_risk_premium

    rp = calculate_risk_premium("BB", red_flag_count=1)
    result = simulate_margin(
        risk_premium=rp,
        base_margin=3.0,           # 업종 기본 마진율 (%)
        transaction_amount=500_000_000,  # 거래 규모 (원)
    )
    print(result.likely.margin_rate, result.likely.expected_profit)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .risk_premium import RiskPremium


# ─────────────────────────────────────────────────────────────
# 기본 마진율 (업종별 FlowPay 기준)
# ─────────────────────────────────────────────────────────────

DEFAULT_BASE_MARGINS: dict[str, float] = {
    # KSIC 대분류 → FlowPay 기본 마진율 (%)
    "C": 3.0,    # 제조업
    "G": 2.5,    # 도소매
    "F": 2.0,    # 건설
    "J": 4.0,    # IT/소프트웨어
    "H": 2.5,    # 운수/물류
    "I": 3.5,    # 숙박/음식
    "K": 5.0,    # 금융/보험
    "M": 4.5,    # 전문서비스
    "N": 3.0,    # 사업지원
}

DEFAULT_MARGIN = 3.0  # 기본값


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class ScenarioResult:
    label: str             # Min / Likely / Max
    margin_rate: float     # 최종 마진율 (%)
    risk_premium: float    # 리스크 프리미엄 (%p)
    base_margin: float     # 기본 마진율 (%)
    adjustment: float      # 시나리오 조정 (%p)
    expected_profit: float | None  # 예상 이익 (원), 거래규모 있을 때
    rationale: str         # 근거 텍스트


@dataclass
class MarginSimulation:
    # 3개 시나리오
    min_scenario: ScenarioResult     # 보수적 (최소 마진)
    likely: ScenarioResult           # 기본 (권장)
    max_scenario: ScenarioResult     # 공격적 (최대 마진)

    # 메타
    grade: str
    risk_premium_total: float
    base_margin: float
    transaction_amount: float | None
    recommended: str         # min | likely | max

    def scenarios(self) -> list[ScenarioResult]:
        return [self.min_scenario, self.likely, self.max_scenario]

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "risk_premium_total": self.risk_premium_total,
            "base_margin": self.base_margin,
            "transaction_amount": self.transaction_amount,
            "recommended": self.recommended,
            "scenarios": [
                {
                    "label": s.label,
                    "margin_rate": s.margin_rate,
                    "risk_premium": s.risk_premium,
                    "base_margin": s.base_margin,
                    "adjustment": s.adjustment,
                    "expected_profit": s.expected_profit,
                    "rationale": s.rationale,
                }
                for s in self.scenarios()
            ],
        }


# ─────────────────────────────────────────────────────────────
# 시뮬레이션
# ─────────────────────────────────────────────────────────────

def simulate_margin(
    risk_premium: RiskPremium,
    base_margin: float | None = None,
    industry_code: str | None = None,
    transaction_amount: float | None = None,
    competition_factor: float = 0.0,  # -1.0 (치열) ~ +1.0 (독점)
) -> MarginSimulation:
    """
    3가지 시나리오의 마진율을 시뮬레이션.

    Args:
        risk_premium: RiskPremium 결과
        base_margin: 기본 마진율 (%). None이면 업종코드로 추정
        industry_code: KSIC 업종 코드 (base_margin 추정용)
        transaction_amount: 거래 규모 (원, 선택)
        competition_factor: 경쟁 조정 (-1~+1)

    Returns:
        MarginSimulation (3 시나리오)
    """
    # 기본 마진율 결정
    if base_margin is None:
        if industry_code:
            prefix = industry_code[0] if industry_code else "C"
            base_margin = DEFAULT_BASE_MARGINS.get(prefix, DEFAULT_MARGIN)
        else:
            base_margin = DEFAULT_MARGIN

    rp = risk_premium.total_premium
    grade = risk_premium.grade

    # ── 시나리오 조정값 ──
    # Min: 리스크 프리미엄 100% 반영 + 경쟁 할인
    # Likely: 리스크 프리미엄 100% 반영
    # Max: 리스크 프리미엄 120% 반영 + 거래 독점 프리미엄

    competition_adj = competition_factor * 0.5  # ±0.5%p

    min_adj = competition_adj - 0.5  # 약간의 할인 여지
    likely_adj = competition_adj
    max_adj = competition_adj + rp * 0.2 + 0.5  # 추가 20% + 0.5%p

    min_rate = round(base_margin + rp + min_adj, 2)
    likely_rate = round(base_margin + rp + likely_adj, 2)
    max_rate = round(base_margin + rp + max_adj, 2)

    # 최소 마진 보장 (0.5% 이상)
    min_rate = max(0.5, min_rate)
    likely_rate = max(1.0, likely_rate)
    max_rate = max(1.5, max_rate)

    # 이익 계산
    def _profit(rate: float) -> float | None:
        if transaction_amount is None:
            return None
        return round(transaction_amount * rate / 100)

    # 근거 텍스트 생성
    min_scenario = ScenarioResult(
        label="Min (보수적)",
        margin_rate=min_rate,
        risk_premium=rp,
        base_margin=base_margin,
        adjustment=min_adj,
        expected_profit=_profit(min_rate),
        rationale=_build_rationale(grade, base_margin, rp, min_adj, "min", risk_premium),
    )

    likely_scenario = ScenarioResult(
        label="Likely (권장)",
        margin_rate=likely_rate,
        risk_premium=rp,
        base_margin=base_margin,
        adjustment=likely_adj,
        expected_profit=_profit(likely_rate),
        rationale=_build_rationale(grade, base_margin, rp, likely_adj, "likely", risk_premium),
    )

    max_scenario = ScenarioResult(
        label="Max (공격적)",
        margin_rate=max_rate,
        risk_premium=rp,
        base_margin=base_margin,
        adjustment=max_adj,
        expected_profit=_profit(max_rate),
        rationale=_build_rationale(grade, base_margin, rp, max_adj, "max", risk_premium),
    )

    # 권장 시나리오 결정
    if grade in ("AAA", "AA", "A"):
        recommended = "min"  # 우량 고객에겐 경쟁력 있는 가격
    elif grade in ("BBB", "BB"):
        recommended = "likely"
    else:
        recommended = "max"  # 고위험 고객에겐 충분한 마진

    return MarginSimulation(
        min_scenario=min_scenario,
        likely=likely_scenario,
        max_scenario=max_scenario,
        grade=grade,
        risk_premium_total=rp,
        base_margin=base_margin,
        transaction_amount=transaction_amount,
        recommended=recommended,
    )


def _build_rationale(
    grade: str, base: float, rp: float,
    adj: float, scenario: str, risk_premium: RiskPremium,
) -> str:
    """시나리오별 근거 텍스트 생성."""
    parts = [f"기본 마진 {base:.1f}%"]
    parts.append(f"+ 리스크 프리미엄 {rp:.1f}%p (등급 {grade})")

    if risk_premium.red_flag_count > 0:
        parts.append(f"  (Red Flag {risk_premium.red_flag_count}건 반영)")

    if adj != 0:
        direction = "할인" if adj < 0 else "추가"
        parts.append(f"+ {direction} 조정 {adj:+.1f}%p")

    if scenario == "min":
        parts.append("→ 경쟁 대응 최소 마진 (수주 확보 우선)")
    elif scenario == "likely":
        parts.append("→ 리스크 대비 적정 마진 (균형)")
    else:
        parts.append("→ 리스크 보전 최대 마진 (이익 우선)")

    if risk_premium.requires_advance_payment:
        parts.append("※ 선급금 조건 필수")
    if risk_premium.requires_collateral:
        parts.append("※ 담보/보증 검토 필요")

    return " | ".join(parts)


def margin_summary(sim: MarginSimulation) -> str:
    """마진 시뮬레이션 요약."""
    lines = [
        f"═══ 마진 시뮬레이션 (등급: {sim.grade}) ═══\n",
        f"  기본 마진: {sim.base_margin:.1f}%  |  리스크 프리미엄: +{sim.risk_premium_total:.1f}%p",
    ]
    if sim.transaction_amount:
        lines.append(f"  거래 규모: {sim.transaction_amount:,.0f}원\n")
    else:
        lines.append("")

    rec_map = {"min": "▸", "likely": "▸", "max": "▸"}
    for s in sim.scenarios():
        label_key = "min" if "Min" in s.label else ("max" if "Max" in s.label else "likely")
        marker = "★" if label_key == sim.recommended else " "
        profit_str = f"  이익: {s.expected_profit:>12,.0f}원" if s.expected_profit else ""
        lines.append(
            f"  {marker} {s.label:16s}  마진: {s.margin_rate:>5.2f}%{profit_str}"
        )
        lines.append(f"     {s.rationale}")

    rec_labels = {"min": "Min (보수적)", "likely": "Likely (권장)", "max": "Max (공격적)"}
    lines.append(f"\n  ★ 권장: {rec_labels[sim.recommended]}")

    return "\n".join(lines)
