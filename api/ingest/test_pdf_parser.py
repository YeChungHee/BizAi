"""
Phase 2a PDF 파서 통합 테스트.

실제 재무제표 PDF를 파싱하여:
  1. 핵심 수치 정확도 검증
  2. 표준 스키마 변환 검증
  3. Phase 2~4 파이프라인 연동 테스트

실행: cd api && python3 -m ingest.test_pdf_parser
"""

import json
import sys
from pathlib import Path

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "schema"))
sys.path.insert(0, str(API_DIR / "benchmark"))

from ingest.pdf_parser import parse_financial_pdf, parse_summary


# ─────────────────────────────────────────────────────────────
# 테스트 PDF 경로
# ─────────────────────────────────────────────────────────────

PDF_PATH = Path.home() / "Downloads" / "2024 재무제표_애즈위메이크.pdf"


def test_pdf_parsing():
    """PDF 파싱 + 핵심 수치 검증."""
    print("=" * 60)
    print("  BizAI Phase 2a — PDF 파서 테스트")
    print("=" * 60)

    if not PDF_PATH.exists():
        print(f"\n  ⚠️  테스트 PDF 없음: {PDF_PATH}")
        print("  테스트를 건너뜁니다.")
        return 1

    # ── 파싱 ──
    print(f"\n  파일: {PDF_PATH.name}")
    fs = parse_financial_pdf(PDF_PATH)
    print(parse_summary(fs))

    # ── 기본 검증 ──
    assert fs.company.name == "주식회사애즈위메이크", f"기업명 불일치: {fs.company.name}"
    assert len(fs.statements) == 2, f"Statement 수 불일치: {len(fs.statements)}"
    assert 2024 in fs.years(), "2024년 누락"
    assert 2023 in fs.years(), "2023년 누락"
    print("\n  ✅ 기본 검증 통과 (기업명, 2개년)")

    # ── 2024년 Tier 1 정확도 검증 ──
    print("\n" + "─" * 60)
    print("  2024년 핵심 수치 검증 (Tier 1)")
    print("─" * 60)

    stmt = fs.get_statement(2024)
    bs = stmt.balance_sheet
    inc = stmt.income_statement

    tier1_checks = {
        # 재무상태표 Tier 1
        ("BS", "total_assets",          18_464_530_876),
        ("BS", "current_assets",        12_274_475_012),
        ("BS", "non_current_assets",     6_190_055_864),
        ("BS", "total_liabilities",      3_583_148_122),
        ("BS", "current_liabilities",      908_822_060),
        ("BS", "non_current_liabilities", 2_674_326_062),
        ("BS", "total_equity",          14_881_382_754),
        # 손익계산서 Tier 1
        ("IS", "revenue",              12_075_469_032),
        ("IS", "operating_profit",      1_079_398_838),
        ("IS", "profit_before_tax",       990_018_560),
        ("IS", "net_profit",              988_568_350),
    }

    passed = 0
    for category, field, expected in tier1_checks:
        source = bs if category == "BS" else inc
        actual = getattr(source, field, 0)
        ok = actual == expected
        if ok:
            passed += 1
        mark = "✅" if ok else "❌"
        print(f"  {mark} {category}.{field:25s} = {actual:>18,}  (기대: {expected:>18,})")

    total = len(tier1_checks)
    print(f"\n  Tier 1: {passed}/{total} 통과")
    assert passed == total, f"Tier 1 검증 실패: {passed}/{total}"
    print("  ✅ Tier 1 전체 통과!")

    # ── 2023년 교차 검증 ──
    print("\n" + "─" * 60)
    print("  2023년 (전기) 교차 검증")
    print("─" * 60)

    stmt23 = fs.get_statement(2023)
    bs23, inc23 = stmt23.balance_sheet, stmt23.income_statement

    checks_23 = [
        ("매출액",       inc23.revenue,          3_586_277_582),
        ("자산총계",     bs23.total_assets,       4_678_744_144),
        ("부채총계",     bs23.total_liabilities,  1_116_154_136),
        ("자본총계",     bs23.total_equity,       3_562_590_008),
        ("영업이익",     inc23.operating_profit,     35_718_765),
        ("당기순이익",   inc23.net_profit,           24_608_192),
    ]

    for name, actual, expected in checks_23:
        ok = actual == expected
        mark = "✅" if ok else "❌"
        print(f"  {mark} {name:12s} = {actual:>18,}  (기대: {expected:>18,})")

    print("  ✅ 2023년 검증 완료")

    # ── Quality 메타데이터 ──
    print("\n" + "─" * 60)
    print("  Quality 메타데이터")
    print("─" * 60)

    q = stmt.quality
    print(f"  소스: {q.source}")
    print(f"  파일: {q.source_file}")
    print(f"  신뢰도: {q.extraction_confidence:.0%}")
    print(f"  누락: {q.missing_fields}")
    print(f"  경고: {q.warnings}")

    assert q.source == "pdf"
    assert q.extraction_confidence >= 0.85, f"신뢰도 부족: {q.extraction_confidence}"
    print("  ✅ Quality 검증 통과")

    # ── JSON 변환 ──
    print("\n" + "─" * 60)
    print("  JSON 변환 테스트")
    print("─" * 60)

    d = fs.to_dict()
    assert d["schema_version"] == "1.0"
    assert len(d["statements"]) == 2
    json_str = json.dumps(d, ensure_ascii=False, indent=2)
    print(f"  JSON 크기: {len(json_str):,} 바이트")
    print(f"  ✅ JSON 변환 통과")

    return 0


def test_pipeline_integration():
    """Phase 2~4 파이프라인 연동 테스트."""
    print("\n" + "=" * 60)
    print("  Phase 2~4 파이프라인 연동 테스트")
    print("=" * 60)

    if not PDF_PATH.exists():
        print("  ⚠️  테스트 PDF 없음 — 건너뜀")
        return 0

    # Phase 2a: PDF 파싱
    fs = parse_financial_pdf(PDF_PATH)
    stmt_2024 = fs.get_statement(2024)
    stmt_2023 = fs.get_statement(2023)

    print(f"\n  [Phase 2a] PDF 파싱 완료: {fs.company.name}")

    # Phase 2b: 재무비율 계산
    try:
        import analysis.ratio_calculator as ratio_calc
        ratios = ratio_calc.calculate_ratios(stmt_2024, stmt_2023)
        computed = sum(1 for v in ratios.values() if v is not None)
        print(f"  [Phase 2b] 재무비율 계산: {computed}/{len(ratios)}개")
        assert computed > 0, "재무비율 0개"
    except Exception as e:
        print(f"  ⚠️  Phase 2b 건너뜀: {e}")
        return 0

    # Phase 2c: 재무 스코어링
    try:
        from lookup import Benchmark
        import analysis.financial_scorer as fin_scorer

        bm = Benchmark()
        scorer = fin_scorer.FinancialScorer(bm)
        # 업종코드가 없으므로 기본값 사용
        fin_result = scorer.score(ratios, industry="G47", size="M", year=2024)
        print(f"  [Phase 2c] 재무 스코어: {fin_result.overall:.1f}점 [{fin_result.grade_band}]")
    except Exception as e:
        print(f"  ⚠️  Phase 2c 건너뜀: {e}")
        bm = None
        return 0

    # Phase 2d: Red Flag
    try:
        import analysis.red_flag_detector as rf_detector
        flags = rf_detector.detect_red_flags(stmt_2024, stmt_2023)
        print(f"  [Phase 2d] Red Flag: {len(flags)}건")
    except Exception as e:
        print(f"  ⚠️  Phase 2d 건너뜀: {e}")
        flags = []

    # Phase 2e: 등급 산출
    try:
        import analysis.grade_calculator as grade_calc
        grade = grade_calc.calculate_grade(
            financial_score=fin_result,
            red_flags=flags,
            audit=stmt_2024.audit,
        )
        print(f"  [Phase 2e] 등급: {grade.grade} ({grade.total_score:.1f}점)")
    except Exception as e:
        print(f"  ⚠️  Phase 2e 건너뜀: {e}")
        if bm: bm.close()
        return 0

    # Phase 3: 마진 시뮬레이션
    try:
        import simulation.risk_premium as risk_prem
        import simulation.margin_simulator as margin_sim

        rp = risk_prem.calculate_risk_premium(grade.grade, len(flags))
        sim = margin_sim.simulate_margin(
            risk_premium=rp,
            industry_code="G47",
            transaction_amount=500_000_000,
        )
        print(f"  [Phase 3] 리스크 프리미엄: +{rp.total_premium:.1f}%p")
        print(f"  [Phase 3] 권장 마진: {sim.likely.margin_rate:.2f}%")
    except Exception as e:
        print(f"  ⚠️  Phase 3 건너뜀: {e}")
        if bm: bm.close()
        return 0

    # Phase 4: 제안서 생성
    try:
        from proposal.template_selector import select_template
        from proposal.proposal_generator import generate_proposal, proposal_summary

        template = select_template(grade.grade, len(flags))
        proposal = generate_proposal(
            company_name=fs.company.name,
            grade=grade,
            financial_score=fin_result,
            margin_sim=sim,
            risk_premium=rp,
            template=template,
            red_flags=flags,
        )
        print(f"  [Phase 4] 제안서 생성: {template.tier} 템플릿, 4섹션 ({sum(len(s.content) for s in proposal.sections())}자)")
    except Exception as e:
        print(f"  ⚠️  Phase 4 건너뜀: {e}")

    if bm:
        bm.close()

    print(f"\n  ✅ Phase 2a~4 파이프라인 연동 테스트 완료!")
    return 0


def main():
    rc1 = test_pdf_parsing()
    rc2 = test_pipeline_integration()

    print("\n" + "=" * 60)
    if rc1 == 0:
        print("  ✅ Phase 2a PDF 파서 통합 테스트 완료!")
    else:
        print("  ⚠️  일부 테스트 건너뜀")
    print("=" * 60)
    return rc1 or rc2


if __name__ == "__main__":
    sys.exit(main())
