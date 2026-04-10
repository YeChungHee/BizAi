"""
Phase 3 시뮬레이션 통합 테스트.

Phase 2 파이프라인(등급 산출) 결과를 받아
리스크 프리미엄 + 마진 시뮬레이션까지 실행합니다.

실행: cd api && python -m simulation.test_simulation
"""

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "schema"))
sys.path.insert(0, str(API_DIR / "benchmark"))

from models import FinancialStatement
from lookup import Benchmark

import analysis.ratio_calculator as ratio_calc
import analysis.financial_scorer as fin_scorer
import analysis.red_flag_detector as rf_detector
import analysis.consultation_analyzer as con_analyzer
import analysis.cross_validator as xv
import analysis.grade_calculator as grade_calc

import simulation.risk_premium as risk_prem
import simulation.margin_simulator as margin_sim


def main():
    print("=" * 60)
    print("  BizAI Phase 3 통합 테스트 (시뮬레이션)")
    print("=" * 60)

    # ── Phase 2 파이프라인 실행 (요약) ──
    sample_path = API_DIR / "schema" / "examples" / "sample_c26.json"
    data = json.loads(sample_path.read_text(encoding="utf-8"))
    fs = FinancialStatement.from_dict(data)

    stmt_2024 = fs.get_statement(2024)
    stmt_2023 = fs.get_statement(2023)

    ratios = ratio_calc.calculate_ratios(stmt_2024, stmt_2023)

    bm = Benchmark()
    scorer = fin_scorer.FinancialScorer(bm)
    fin_result = scorer.score(
        ratios,
        industry=fs.company.ksic_code or "C26",
        size=fs.company.size_code or "M",
        year=2024,
    )

    flags = rf_detector.detect_red_flags(stmt_2024, stmt_2023)

    consultation = con_analyzer.manual_analysis(
        management_score=7, management_evidence="창업 12년 경력",
        business_model_score=6, business_model_evidence="부품 제조 하청",
        customer_concentration_score=5, customer_concentration_evidence="상위 3사 70%",
        fund_purpose_score=7, fund_purpose_evidence="신규 생산라인 증설",
        repayment_plan_score=5, repayment_plan_evidence="구체적 계획 부족",
        risk_awareness_score=6, risk_awareness_evidence="원자재 리스크 인지",
        consistency_score=7, consistency_evidence="전반적 일관",
        key_quotes=["매출 15% 성장 예상", "마진 개선 전망", "신규 거래처 발굴 중"],
    )

    mismatches = xv.cross_validate(ratios, consultation, stmt_2024, stmt_2023)

    grade = grade_calc.calculate_grade(
        financial_score=fin_result,
        consultation=consultation,
        red_flags=flags,
        mismatches=mismatches,
        audit=stmt_2024.audit,
    )

    print(f"\n  Phase 2 결과: {fs.company.name}")
    print(f"  등급: {grade.grade} ({grade.total_score:.1f}점)")
    print(f"  Red Flag: {len(flags)}건")

    # ── Phase 3: 리스크 프리미엄 ──
    print("\n" + "─" * 60)
    print("Step 7: 리스크 프리미엄 산출")
    print("─" * 60)

    rp = risk_prem.calculate_risk_premium(
        grade=grade.grade,
        red_flag_count=len(flags),
        audit_opinion=grade.audit_opinion,
    )
    print(risk_prem.risk_premium_summary(rp))

    assert rp.total_premium >= 0, "프리미엄이 음수"
    assert rp.grade == grade.grade
    print(f"\n  ✅ 리스크 프리미엄 통과 (+{rp.total_premium:.1f}%p)")

    # ── Phase 3: 마진 시뮬레이션 ──
    print("\n" + "─" * 60)
    print("Step 8: 마진 시뮬레이션 (3 시나리오)")
    print("─" * 60)

    sim = margin_sim.simulate_margin(
        risk_premium=rp,
        industry_code=fs.company.ksic_code,
        transaction_amount=500_000_000,  # 5억원 거래
        competition_factor=0.0,
    )
    print(margin_sim.margin_summary(sim))

    # 검증
    assert sim.min_scenario.margin_rate <= sim.likely.margin_rate <= sim.max_scenario.margin_rate
    assert sim.recommended in ("min", "likely", "max")
    for s in sim.scenarios():
        assert s.margin_rate >= 0.5, f"{s.label} 마진율 너무 낮음: {s.margin_rate}"
        if s.expected_profit is not None:
            assert s.expected_profit > 0, f"{s.label} 이익이 0 이하"
    print(f"\n  ✅ 마진 시뮬레이션 통과 (권장: {sim.recommended})")

    # ── 다양한 등급별 테스트 ──
    print("\n" + "─" * 60)
    print("등급별 시뮬레이션 비교")
    print("─" * 60)

    test_grades = [
        ("AAA", 0, "적정"),
        ("A", 0, "적정"),
        ("BBB", 1, "적정"),
        ("BB", 2, "적정"),
        ("B", 3, "한정"),
        ("CCC", 5, "부적정"),
    ]

    print(f"\n  {'등급':>5s} {'RF':>3s} {'감사':6s} {'프리미엄':>8s} "
          f"{'Min':>7s} {'Likely':>7s} {'Max':>7s} {'권장':6s} {'결제조건':20s}")
    print("  " + "─" * 85)

    for g, rf, audit in test_grades:
        rp_test = risk_prem.calculate_risk_premium(g, rf, audit)
        sim_test = margin_sim.simulate_margin(
            risk_premium=rp_test,
            base_margin=3.0,
            transaction_amount=500_000_000,
        )
        print(
            f"  {g:>5s} {rf:>3d} {audit:6s} "
            f"+{rp_test.total_premium:>6.1f}%p "
            f"{sim_test.min_scenario.margin_rate:>6.2f}% "
            f"{sim_test.likely.margin_rate:>6.2f}% "
            f"{sim_test.max_scenario.margin_rate:>6.2f}% "
            f"{sim_test.recommended:6s} "
            f"{rp_test.recommended_payment_terms}"
        )

    bm.close()

    print("\n" + "=" * 60)
    print("  ✅ Phase 3 통합 테스트 완료!")
    print("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
