"""
재무 스코어링 엔진.

ECOS 벤치마크 peer 비교를 기반으로 5개 영역별 가중 점수를 산출합니다.

산식 (ARCHITECTURE.md §8):
    성장성 (15%) = avg(501,502,505,506)
    수익성 (25%) = avg(602,606,610,611,612,615,625,627)
    안정성 (30%) = avg(701,702,703,707,710)
    활동성 (15%) = avg(801,806,808,809)
    생산성 (15%) = avg(9034,9044,9064,9074)

사용:
    from analysis.financial_scorer import FinancialScorer

    scorer = FinancialScorer(benchmark)
    result = scorer.score(ratios, industry="C26", size="M", year=2024)
    print(result.overall, result.grade_band)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benchmark"))
from lookup import Benchmark, ScoreResult  # noqa: E402

from .ratio_calculator import CATEGORY_CODES, RATIO_META  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 가중치 설정 (ARCHITECTURE.md §8)
# ─────────────────────────────────────────────────────────────

CATEGORY_WEIGHTS: dict[str, float] = {
    "성장성": 0.15,
    "수익성": 0.25,
    "안정성": 0.30,
    "활동성": 0.15,
    "생산성": 0.15,
}


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class CategoryScore:
    category: str
    weight: float
    score: float              # 0~100
    details: list[ScoreResult]
    computed_count: int       # 실제 계산된 지표 수
    total_count: int          # 카테고리 총 지표 수


@dataclass
class FinancialScore:
    growth: float             # 성장성 0~100
    profitability: float      # 수익성
    stability: float          # 안정성
    activity: float           # 활동성
    productivity: float       # 생산성
    overall: float            # 가중 종합 0~100
    categories: list[CategoryScore] = field(default_factory=list)
    details: list[ScoreResult] = field(default_factory=list)

    @property
    def grade_band(self) -> str:
        """종합 점수 → 5단계 밴드."""
        if self.overall >= 85:
            return "우수"
        if self.overall >= 70:
            return "양호"
        if self.overall >= 45:
            return "평균"
        if self.overall >= 25:
            return "주의"
        return "미흡"


# ─────────────────────────────────────────────────────────────
# 스코어러
# ─────────────────────────────────────────────────────────────

class FinancialScorer:
    def __init__(self, benchmark: Benchmark):
        self.bm = benchmark

    def score(
        self,
        ratios: dict[str, float | None],
        industry: str,
        size: str,
        year: int,
    ) -> FinancialScore:
        """
        재무비율 dict를 받아 영역별 + 종합 점수를 산출.

        Args:
            ratios: ratio_calculator.calculate_ratios() 의 반환값
            industry: KSIC 업종 코드 (예: "C26")
            size: ECOS 규모 코드 (예: "M")
            year: 평가 연도

        Returns:
            FinancialScore
        """
        all_details: list[ScoreResult] = []
        cat_scores: dict[str, CategoryScore] = {}

        for cat, codes in CATEGORY_CODES.items():
            cat_details: list[ScoreResult] = []
            for code in codes:
                val = ratios.get(code)
                if val is None:
                    continue
                result = self.bm.score_indicator(
                    industry, size, year, code, val
                )
                if result is not None:
                    cat_details.append(result)
                    all_details.append(result)

            avg = (
                sum(d.score for d in cat_details) / len(cat_details)
                if cat_details
                else 50.0  # 데이터 없으면 중립점
            )
            cat_scores[cat] = CategoryScore(
                category=cat,
                weight=CATEGORY_WEIGHTS[cat],
                score=round(avg, 1),
                details=cat_details,
                computed_count=len(cat_details),
                total_count=len(codes),
            )

        # 가중 평균
        weighted_sum = sum(
            cat_scores[cat].score * CATEGORY_WEIGHTS[cat]
            for cat in CATEGORY_WEIGHTS
        )
        overall = round(weighted_sum, 1)

        return FinancialScore(
            growth=cat_scores["성장성"].score,
            profitability=cat_scores["수익성"].score,
            stability=cat_scores["안정성"].score,
            activity=cat_scores["활동성"].score,
            productivity=cat_scores["생산성"].score,
            overall=overall,
            categories=list(cat_scores.values()),
            details=all_details,
        )

    def score_summary(self, fs: FinancialScore) -> str:
        """사람이 읽기 쉬운 점수 요약 텍스트 생성."""
        lines = [
            f"═══ 재무 스코어 종합: {fs.overall:.1f}점 [{fs.grade_band}] ═══\n",
        ]
        for cat in fs.categories:
            bar = _bar(cat.score)
            lines.append(
                f"  {cat.category} ({cat.weight*100:.0f}%): "
                f"{cat.score:>5.1f}점  {bar}  "
                f"({cat.computed_count}/{cat.total_count}개 지표)"
            )
        lines.append("")
        lines.append("── 지표별 상세 ──")
        for d in fs.details:
            lines.append(
                f"  {d.indicator_code} {d.indicator_name:20s} "
                f"회사: {d.company_value:>8.2f}{d.unit} | "
                f"peer: {d.peer_value:>8.2f}{d.unit} | "
                f"{d.score:>5.1f}점 [{d.band}] "
                f"(peer 대비 {d.delta_pct:+.1f}%)"
            )
        return "\n".join(lines)


def _bar(score: float, width: int = 20) -> str:
    filled = int(score / 100 * width)
    return "█" * filled + "░" * (width - filled)
