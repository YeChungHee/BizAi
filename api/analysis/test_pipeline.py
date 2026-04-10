"""
Phase 2 통합 테스트.

sample_c26.json 데이터로 전체 분석 파이프라인을 검증합니다:
  1. 재무비율 계산 (ratio_calculator)
  2. 재무 스코어링 (financial_scorer)
  3. Red Flag 탐지 (red_flag_detector)
  4. 상담 분석 — 수동 입력 (consultation_analyzer)
  5. 교차검증 (cross_validator)
  6. 종합 등급 (grade_calculator)

실행: cd api/analysis && python test_pipeline.py
"""

import json
import sys
from pathlib import Path

# 경로 설정
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "schema"))
sys.path.insert(0, str(API_DIR / "benchmark"))
sys.path.insert(0, str(API_DIR / "analysis"))

from models import FinancialStatement
from lookup import Benchmark

# analysis 모듈은 패키지 내 상대 임포트를 사용하므로
# 직접 임포트 대신 패키지로 처리
import importlib
import analysis.ratio_calculator as ratio_calc
import analysis.financial_scorer as fin_scorer
import analysis.red_flag_detector as rf_detector
import analysis.consultation_analyzer as con_analyzer
import analysis.cross_validator as xv
import analysis.grade_calculator as grade_calc


def main():
    print("=" * 60)
    print("  BizAI Phase 2 통합 테스트")
    print("=" * 60)

    # ── 0. 데이터 로드 ──
    sample_path = API_DIR / "schema" / "examples" / "sample_c26.json"
    data = json.loads(sample_path.read_text(encoding="utf-8"))
    fs = FinancialStatement.from_dict(data)

    print(f"\n기업: {fs.company.name}")
    print(f"업종: {fs.company.ksic_code} ({fs.company.ksic_name})")
    print(f"규모: {fs.company.size_code} ({fs.company.size_label})")
    print(f"연도: {fs.years()}")

    stmt_2024 = fs.get_statement(2024)
    stmt_2023 = fs.get_statement(2023)
    assert stmt_2024 is not None
    assert stmt_2023 is not None

    # ── 1. 재무비율 계산 ──
    print("\n" + "─" * 60)
    print("Step 1: 재무비율 계산")
    print("─" * 60)

    ratios = ratio_calc.calculate_ratios(stmt_2024, stmt_2023)
    computed = {k: v for k, v in ratios.items() if v is not None}
    print(f"  계산된 지표: {len(computed)}/{len(ratios)}개")

    for cat, codes in ratio_calc.CATEGORY_CODES.items():
        cat_ratios = {c: ratios[c] for c in codes if ratios.get(c) is not None}
        print(f"  [{cat}] {len(cat_ratios)}/{len(codes)}개")
        for code, val in cat_ratios.items():
            meta = ratio_calc.RATIO_META[code]
            print(f"    {code} {meta.name:20s} {val:>10.2f} {meta.unit}")

    assert ratios["501"] is not None, "성장성 지표 계산 실패"
    assert ratios["602"] is not None, "수익성 지표 계산 실패"
    assert ratios["707"] is not None, "안정성 지표 계산 실패"
    print("  ✅ 재무비율 계산 통과")

    # ── 2. 재무 스코어링 ──
    print("\n" + "─" * 60)
    print("Step 2: 재무 스코어링 (ECOS peer 비교)")
    print("─" * 60)

    bm = Benchmark()
    scorer = fin_scorer.FinancialScorer(bm)
    fin_result = scorer.score(
        ratios,
        industry=fs.company.ksic_code or "C26",
        size=fs.company.size_code or "M",
        year=2024,
    )

    print(scorer.score_summary(fin_result))
    assert 0 <= fin_result.overall <= 100, f"종합 점수 범위 오류: {fin_result.overall}"
    print(f"\n  ✅ 재무 스코어링 통과 (종합: {fin_result.overall:.1f}점 [{fin_result.grade_band}])")

    # ── 3. Red Flag 탐지 ──
    print("\n" + "─" * 60)
    print("Step 3: Red Flag 탐지")
    print("─" * 60)

    flags = rf_detector.detect_red_flags(stmt_2024, stmt_2023)
    print(rf_detector.red_flag_summary(flags))
    print(f"\n  ✅ Red Flag 탐지 완료 ({len(flags)}건)")

    # ── 4. 상담 분석 (수동 입력 시뮬레이션) ──
    print("\n" + "─" * 60)
    print("Step 4: 상담 분석 (수동 입력)")
    print("─" * 60)

    consultation = con_analyzer.manual_analysis(
        management_score=7, management_evidence="창업 12년, 반도체 업계 전문가, 주요 고객사와 장기 관계",
        business_model_score=6, business_model_evidence="부품 제조 하청, 기술 차별화 제한적",
        customer_concentration_score=5, customer_concentration_evidence="상위 3개 고객이 매출의 70% 차지",
        fund_purpose_score=7, fund_purpose_evidence="신규 생산라인 증설, 매출 성장 대응",
        repayment_plan_score=5, repayment_plan_evidence="매출 증가로 상환 가능하다고 주장, 구체적 계획 부족",
        risk_awareness_score=6, risk_awareness_evidence="원자재 가격 상승 리스크 인지, 환율 리스크 미언급",
        consistency_score=7, consistency_evidence="전반적으로 일관, 수익성 부분에서 약간의 모호함",
        key_quotes=[
            "매출은 꾸준히 성장하고 있고 올해도 15% 이상 성장 예상합니다",
            "신규 라인 가동되면 마진이 개선될 것으로 봅니다",
            "상위 3개 고객이 핵심이지만 신규 거래처도 발굴 중입니다",
        ],
    )

    print(con_analyzer.consultation_summary(consultation))
    assert 0 <= consultation.overall <= 100
    print(f"\n  ✅ 상담 분석 통과 (종합: {consultation.overall:.1f}점)")

    # ── 5. 교차검증 ──
    print("\n" + "─" * 60)
    print("Step 5: 재무↔상담 교차검증")
    print("─" * 60)

    mismatches = xv.cross_validate(ratios, consultation, stmt_2024, stmt_2023)
    print(xv.cross_validation_summary(mismatches))
    print(f"\n  ✅ 교차검증 완료 ({len(mismatches)}건 불일치)")

    # ── 6. 종합 등급 ──
    print("\n" + "─" * 60)
    print("Step 6: 종합 등급 산출")
    print("─" * 60)

    grade = grade_calc.calculate_grade(
        financial_score=fin_result,
        consultation=consultation,
        red_flags=flags,
        mismatches=mismatches,
        audit=stmt_2024.audit,
    )

    print(grade_calc.grade_summary(grade))
    assert grade.grade in [g[1] for g in grade_calc.GRADE_TABLE]
    assert 0 <= grade.total_score <= 100
    print(f"\n  ✅ 종합 등급 산출 통과")

    # ── 최종 요약 ──
    print("\n" + "=" * 60)
    print("  통합 테스트 완료")
    print("=" * 60)
    print(f"""
  기업: {fs.company.name}
  재무 스코어: {fin_result.overall:.1f}점 [{fin_result.grade_band}]
  비재무 스코어: {consultation.overall:.1f}점
  Red Flag: {len(flags)}건
  교차검증 불일치: {len(mismatches)}건
  ──────────────────
  종합 등급: {grade.grade} ({grade.total_score:.1f}점)
  {grade.grade_description}
""")

    bm.close()
    print("✅ 모든 테스트 통과!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
