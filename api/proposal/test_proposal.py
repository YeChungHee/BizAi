"""
Phase 4 제안서 생성 통합 테스트.

Phase 2(등급 산출) + Phase 3(마진 시뮬레이션) 결과를 받아
제안서 생성 + 이메일 초안까지 실행합니다.

실행: cd api && python -m proposal.test_proposal
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

from proposal.template_selector import select_template, template_summary
from proposal.proposal_generator import generate_proposal, proposal_summary
from proposal.email_drafter import draft_email, email_summary


def run_pipeline(company_name, grade_str, red_flags, consultation, fin_result, grade, rp, sim):
    """Phase 4 파이프라인 실행."""

    # Step 9: 템플릿 선택
    print(f"\n{'─' * 60}")
    print(f"Step 9: 제안서 템플릿 선택")
    print(f"{'─' * 60}")

    template = select_template(
        grade=grade.grade,
        red_flag_count=len(red_flags),
    )
    print(template_summary(template))

    assert template.tier in ("premium", "standard", "cautious", "restricted")
    print(f"\n  ✅ 템플릿 선택 통과 (tier: {template.tier})")

    # Step 10: 제안서 생성
    print(f"\n{'─' * 60}")
    print(f"Step 10: 4섹션 제안서 생성")
    print(f"{'─' * 60}")

    proposal = generate_proposal(
        company_name=company_name,
        grade=grade,
        financial_score=fin_result,
        margin_sim=sim,
        risk_premium=rp,
        template=template,
        consultation=consultation,
        red_flags=red_flags,
        proposal_date="2026-04-10",
    )
    print(proposal_summary(proposal))

    # 검증
    assert proposal.company_name == company_name
    assert proposal.grade == grade.grade
    assert len(proposal.sections()) == 4
    for s in proposal.sections():
        assert len(s.title) > 0
        assert len(s.content) > 0
    assert len(proposal.key_metrics) >= 5
    print(f"\n  ✅ 제안서 생성 통과 (4섹션, {sum(len(s.content) for s in proposal.sections())}자)")

    # Step 11: 이메일 초안 생성
    print(f"\n{'─' * 60}")
    print(f"Step 11: 이메일 초안 생성")
    print(f"{'─' * 60}")

    draft = draft_email(
        proposal=proposal,
        recipient_name="김영업",
        recipient_email="kim@company.com",
        sender_name="박플로우",
        sender_email="park@flowpay.co.kr",
        cc=["team@flowpay.co.kr"],
    )
    print(email_summary(draft))

    # 검증
    assert draft.subject != ""
    assert draft.body != ""
    assert draft.recipient_email == "kim@company.com"
    assert len(draft.body) > 100
    print(f"\n  ✅ 이메일 초안 통과 (본문 {len(draft.body)}자)")

    return proposal, draft


def main():
    print("=" * 60)
    print("  BizAI Phase 4 통합 테스트 (제안서 생성)")
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

    # ── Phase 3: 리스크 프리미엄 + 마진 ──
    rp = risk_prem.calculate_risk_premium(
        grade=grade.grade,
        red_flag_count=len(flags),
        audit_opinion=grade.audit_opinion,
    )

    sim = margin_sim.simulate_margin(
        risk_premium=rp,
        industry_code=fs.company.ksic_code,
        transaction_amount=500_000_000,
        competition_factor=0.0,
    )

    print(f"  리스크 프리미엄: +{rp.total_premium:.1f}%p")
    print(f"  권장 마진: {sim.likely.margin_rate:.2f}%")

    # ── Phase 4: 제안서 생성 ──
    proposal, draft = run_pipeline(
        company_name=fs.company.name,
        grade_str=grade.grade,
        red_flags=flags,
        consultation=consultation,
        fin_result=fin_result,
        grade=grade,
        rp=rp,
        sim=sim,
    )

    # ── 전체 제안서 출력 ──
    print(f"\n{'=' * 60}")
    print("  [전체 제안서 텍스트]")
    print(f"{'=' * 60}")
    print(proposal.to_text())

    # ── 다양한 등급별 제안서 비교 ──
    print(f"\n{'=' * 60}")
    print("  등급별 제안서 템플릿 비교")
    print(f"{'=' * 60}")

    test_cases = [
        ("AAA", 0, "적정",  "우량기업 A"),
        ("BBB", 1, "적정",  "보통기업 B"),
        ("B",   3, "한정",  "주의기업 C"),
        ("CC",  6, "부적정", "위험기업 D"),
    ]

    print(f"\n  {'등급':>5s} {'RF':>3s} {'감사':6s} {'템플릿':12s} {'톤':30s} {'CTA':25s}")
    print("  " + "─" * 85)

    for g, rf, audit, name in test_cases:
        tpl = select_template(g, rf)
        print(
            f"  {g:>5s} {rf:>3d} {audit:6s} "
            f"{tpl.tier:12s} "
            f"{tpl.tone[:28]:30s} "
            f"{tpl.call_to_action}"
        )

    bm.close()

    print(f"\n{'=' * 60}")
    print("  ✅ Phase 4 통합 테스트 완료!")
    print(f"{'=' * 60}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
