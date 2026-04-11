"""
통합 파이프라인 테스트 — 실 기업 데이터 검증.

(주)쿳션 + (주)씨랩 실 재무데이터를 사용하여
credit_risk → payment_pricer → margin_simulator 전 파이프라인 검증.

실행:
    cd api && python3 -m simulation.test_full_pipeline
"""

from __future__ import annotations

import sys
from pathlib import Path

# 패키지 경로 설정
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from simulation.credit_risk import assess_credit_risk, CreditRiskProfile
from simulation.payment_pricer import price_payment_terms, MarginLadder, DEFAULT_LADDER
from simulation.margin_simulator import simulate_from_credit_risk, margin_summary
from simulation.risk_premium import calculate_risk_premium, risk_premium_summary


# ─────────────────────────────────────────────────────────────
# (주)쿳션 2024 재무 데이터 (천원)
# ─────────────────────────────────────────────────────────────

def test_cushion():
    """(주)쿳션 — BB- 등급, 고위험 기업."""
    print("=" * 60)
    print("  (주)쿳션 통합 파이프라인 테스트")
    print("=" * 60)

    profile = assess_credit_risk(
        grade="BB-",
        grade_score=42.0,
        # BS (2024, 천원)
        total_assets=1_908_325,
        current_assets=1_096_004,
        current_liabilities=1_399_753,
        total_liabilities=1_510_870,
        total_equity=397_455,
        cash_and_equivalents=15_765,
        short_term_deposits=0,
        trade_receivables=414_937,
        trade_receivables_prior=286_822,
        retained_earnings=-60_975,
        short_term_debt=547_618,
        long_term_debt=111_117,
        inventories=488_032,
        # IS (2024, 천원)
        revenue=2_717_688,
        revenue_prior=1_597_040,
        operating_profit=89_696,
        net_profit=32_741,
        interest_expense=50_082,
        # 정성
        cash_flow_grade="불량",
        company_age_years=10,
        employee_count=25,
        red_flag_count=4,
        consecutive_profit_years=1,
        consecutive_loss_years=0,
        # 거래 조건
        has_advance_payment=False,
        advance_payment_pct=0.0,
        has_collateral=False,
    )

    print("\n[1] 신용 리스크 프로필")
    print(profile.summary())

    # ── 검증 ──
    assert profile.grade == "BB-"
    assert profile.z_prime_score is not None, "Z-Score 계산 실패"
    assert profile.z_zone in ("safe", "grey", "distress")
    assert 0 < profile.adjusted_pd_pct <= 60, f"PD 범위 이상: {profile.adjusted_pd_pct}"
    assert 20 <= profile.lgd_pct <= 90, f"LGD 범위 이상: {profile.lgd_pct}"
    assert profile.annual_el_pct > 0, "기대손실 0 이하"

    print(f"\n  ✓ Z'={profile.z_prime_score:.3f} ({profile.z_zone})")
    print(f"  ✓ PD={profile.adjusted_pd_pct:.1f}% | LGD={profile.lgd_pct:.0f}% | EL={profile.annual_el_pct:.2f}%")

    # ── Payment Pricer (60일 유예) ──
    print("\n[2] 결제조건 가격결정 (60일)")
    pricing = price_payment_terms(
        credit_risk=profile,
        payment_days=60,
        ladder=DEFAULT_LADDER,
        max_acceptable_loss=3_000_000,
    )
    print(pricing.summary())

    assert pricing.primary.payment_days == 60
    assert pricing.primary.final_margin_pct > 0, "최종 마진율 0 이하"
    assert pricing.ceiling.recommended > 0, "안전 규모 0 이하"
    assert len(pricing.all_terms) >= 3, "비교 테이블 부족"

    print(f"\n  ✓ 최종 마진: {pricing.primary.final_margin_pct:.2f}%")
    print(f"  ✓ 안전 규모: {pricing.ceiling.recommended:,.0f}원")

    # ── Margin Simulator (3 시나리오) ──
    print("\n[3] 마진 시뮬레이션 (3 시나리오)")
    sim = simulate_from_credit_risk(
        credit_risk=profile,
        transaction_amount=50_000_000,
        industry_code="C",
        competition_factor=0.0,
    )
    print(margin_summary(sim))

    assert sim.min_scenario.margin_rate > 0
    assert sim.likely.margin_rate >= sim.min_scenario.margin_rate
    assert sim.max_scenario.margin_rate >= sim.likely.margin_rate
    if sim.likely.expected_profit is not None:
        assert sim.likely.expected_profit > 0

    print(f"\n  ✓ Min={sim.min_scenario.margin_rate:.2f}% / "
          f"Likely={sim.likely.margin_rate:.2f}% / "
          f"Max={sim.max_scenario.margin_rate:.2f}%")

    return profile, pricing, sim


# ─────────────────────────────────────────────────────────────
# (주)씨랩 2024 재무 데이터 (천원)
# ─────────────────────────────────────────────────────────────

def test_clab():
    """(주)씨랩 — BB 등급, 중위험 기업."""
    print("\n" + "=" * 60)
    print("  (주)씨랩 통합 파이프라인 테스트")
    print("=" * 60)

    profile = assess_credit_risk(
        grade="BB",
        grade_score=48.0,
        # BS (2024, 천원)
        total_assets=3_234_157,
        current_assets=2_515_889,
        current_liabilities=1_822_946,
        total_liabilities=2_289_124,
        total_equity=945_033,
        cash_and_equivalents=325_870,
        short_term_deposits=0,
        trade_receivables=826_791,
        trade_receivables_prior=643_120,
        retained_earnings=275_033,
        short_term_debt=820_000,
        long_term_debt=466_178,
        inventories=654_820,
        # IS (2024, 천원)
        revenue=5_126_340,
        revenue_prior=3_891_250,
        operating_profit=298_520,
        net_profit=142_670,
        interest_expense=87_450,
        # 정성
        cash_flow_grade="보통",
        company_age_years=15,
        employee_count=65,
        red_flag_count=2,
        consecutive_profit_years=3,
        consecutive_loss_years=0,
        # 거래 조건
        has_advance_payment=False,
        advance_payment_pct=0.0,
        has_collateral=False,
    )

    print("\n[1] 신용 리스크 프로필")
    print(profile.summary())

    assert profile.grade == "BB"
    assert profile.z_prime_score is not None
    assert 0 < profile.adjusted_pd_pct <= 60
    assert 20 <= profile.lgd_pct <= 90

    print(f"\n  ✓ Z'={profile.z_prime_score:.3f} ({profile.z_zone})")
    print(f"  ✓ PD={profile.adjusted_pd_pct:.1f}% | LGD={profile.lgd_pct:.0f}% | EL={profile.annual_el_pct:.2f}%")

    # ── Payment Pricer (60일 유예) ──
    print("\n[2] 결제조건 가격결정 (60일)")
    pricing = price_payment_terms(
        credit_risk=profile,
        payment_days=60,
        ladder=DEFAULT_LADDER,
        max_acceptable_loss=3_000_000,
    )
    print(pricing.summary())

    assert pricing.primary.final_margin_pct > 0
    assert pricing.ceiling.recommended > 0

    print(f"\n  ✓ 최종 마진: {pricing.primary.final_margin_pct:.2f}%")
    print(f"  ✓ 안전 규모: {pricing.ceiling.recommended:,.0f}원")

    # ── Margin Simulator ──
    print("\n[3] 마진 시뮬레이션 (3 시나리오)")
    sim = simulate_from_credit_risk(
        credit_risk=profile,
        transaction_amount=100_000_000,
        industry_code="C",
        competition_factor=0.0,
    )
    print(margin_summary(sim))

    assert sim.min_scenario.margin_rate > 0
    assert sim.max_scenario.margin_rate >= sim.likely.margin_rate

    print(f"\n  ✓ Min={sim.min_scenario.margin_rate:.2f}% / "
          f"Likely={sim.likely.margin_rate:.2f}% / "
          f"Max={sim.max_scenario.margin_rate:.2f}%")

    return profile, pricing, sim


# ─────────────────────────────────────────────────────────────
# 비교 요약
# ─────────────────────────────────────────────────────────────

def compare_results(
    label_a: str, profile_a: CreditRiskProfile, pricing_a, sim_a,
    label_b: str, profile_b: CreditRiskProfile, pricing_b, sim_b,
):
    """두 기업 비교 요약 출력."""
    print("\n" + "=" * 60)
    print("  기업 비교 요약")
    print("=" * 60)

    header = f"{'항목':20s}  {label_a:>14s}  {label_b:>14s}"
    print(header)
    print("─" * len(header))

    rows = [
        ("등급", profile_a.grade, profile_b.grade),
        ("PD (%)", f"{profile_a.adjusted_pd_pct:.1f}", f"{profile_b.adjusted_pd_pct:.1f}"),
        ("LGD (%)", f"{profile_a.lgd_pct:.0f}", f"{profile_b.lgd_pct:.0f}"),
        ("EL (%)", f"{profile_a.annual_el_pct:.2f}", f"{profile_b.annual_el_pct:.2f}"),
        ("Z' Score", f"{profile_a.z_prime_score:.3f}" if profile_a.z_prime_score else "N/A",
                     f"{profile_b.z_prime_score:.3f}" if profile_b.z_prime_score else "N/A"),
        ("Z' Zone", profile_a.z_zone, profile_b.z_zone),
        ("60일 마진 (%)", f"{pricing_a.primary.final_margin_pct:.2f}",
                         f"{pricing_b.primary.final_margin_pct:.2f}"),
        ("안전 규모 (원)", f"{pricing_a.ceiling.recommended:>12,.0f}",
                          f"{pricing_b.ceiling.recommended:>12,.0f}"),
        ("시뮬 Likely (%)", f"{sim_a.likely.margin_rate:.2f}",
                            f"{sim_b.likely.margin_rate:.2f}"),
    ]

    for label, va, vb in rows:
        print(f"  {label:20s}  {va:>14s}  {vb:>14s}")

    print()


# ─────────────────────────────────────────────────────────────
# 실행
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    p1, pr1, s1 = test_cushion()
    p2, pr2, s2 = test_clab()
    compare_results("(주)쿳션", p1, pr1, s1, "(주)씨랩", p2, pr2, s2)

    print("\n✅ 모든 파이프라인 테스트 통과!")
