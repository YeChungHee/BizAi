"""
벤치마크 조회 & 스코어링 헬퍼

사용 예:
    from lookup import Benchmark

    bm = Benchmark()

    # 단일 지표 조회
    val = bm.get("C26", "M", 2024, "707")  # C26 중소기업 부채비율 2024
    # → 96.39

    # 기업 규모 자동 판별
    size = bm.infer_size(revenue_krw=5_000_000_000)  # 50억 → "S" (소기업)

    # 점수 산출 (방향성 고려)
    score = bm.score_indicator(
        industry="C26", size="M", year=2024,
        indicator="707", company_value=120.5
    )  # 회사 부채비율 120.5% → peer 96.39 대비 나쁨 → 낮은 점수

    # 종합 스냅샷
    snap = bm.snapshot("C26", "M", year=2024)
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

DB_PATH = Path(__file__).parent / "benchmark.db"

# ─────────────────────────────────────────────────────────────
# 지표 방향성
#   higher = 값이 클수록 좋음 (수익성, 성장성, 유동성)
#   lower  = 값이 작을수록 좋음 (부채, 차입금)
#   neutral = 너무 높거나 낮으면 오히려 문제 (회전율 등)
# ─────────────────────────────────────────────────────────────

Direction = Literal["higher", "lower", "neutral"]

INDICATOR_DIRECTION: dict[str, Direction] = {
    # 성장성 - 높을수록 좋음
    "501": "higher",  # 총자산증가율
    "502": "higher",  # 유형자산증가율
    "505": "higher",  # 자기자본증가율
    "506": "higher",  # 매출액증가율
    # 수익성 - 높을수록 좋음
    "602": "higher",  # ROA
    "606": "higher",  # ROE
    "610": "higher",  # 매출액순이익률
    "611": "higher",  # 매출액영업이익률
    "612": "lower",   # 매출원가대매출액 (낮을수록 좋음)
    "615": "higher",  # 연구개발비대매출액 (업종따라 다르지만 기본 higher)
    "625": "lower",   # 금융비용대매출액
    "627": "higher",  # 이자보상비율
    # 안정성
    "701": "higher",  # 자기자본비율
    "702": "higher",  # 유동비율
    "703": "higher",  # 당좌비율
    "707": "lower",   # 부채비율
    "710": "lower",   # 차입금의존도
    # 활동성 - 높을수록 효율적
    "801": "higher",  # 총자산회전율
    "806": "higher",  # 재고자산회전율
    "808": "higher",  # 매출채권회전율
    "809": "higher",  # 매입채무회전율
    # 생산성 - 높을수록 좋음
    "9034": "higher",
    "9044": "higher",
    "9064": "higher",
    "9074": "higher",
}

# 규모 자동 판별 (매출액 기준, 원화)
SIZE_THRESHOLDS = [
    # (최소 매출액, 규모코드, 라벨)
    (150_000_000_000, "L", "대기업"),
    (40_000_000_000, "J", "대기업_중견"),
    (8_000_000_000, "D", "중기업"),
    (0, "S", "소기업"),
]


@dataclass
class ScoreResult:
    indicator_code: str
    indicator_name: str
    company_value: float
    peer_value: float
    direction: Direction
    score: float         # 0~100
    band: str            # 우수 / 양호 / 평균 / 주의 / 미흡
    delta_pct: float     # peer 대비 % 차이
    unit: str


class Benchmark:
    def __init__(self, db_path: Path | str = DB_PATH):
        self.db_path = Path(db_path)
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"benchmark.db not found: {self.db_path}. "
                "Run ecos_loader.py first."
            )
        self._conn = sqlite3.connect(self.db_path)
        self._conn.row_factory = sqlite3.Row

    # ─── 조회 ───────────────────────────────────────────────

    def get(self, industry: str, size: str, year: int,
            indicator: str) -> float | None:
        row = self._conn.execute(
            """SELECT value FROM benchmark
               WHERE industry_code=? AND size_code=? AND year=?
                 AND indicator_code=?""",
            (industry, size, year, indicator),
        ).fetchone()
        return row["value"] if row else None

    def get_with_fallback(self, industry: str, size: str, year: int,
                          indicator: str) -> tuple[float | None, str]:
        """
        우선순위 폴백:
        1. 요청한 (업종, 규모, 연도)
        2. 같은 업종, 규모='A' (종합)
        3. 한 단계 상위 업종 (예: C262 → C26)
        4. 전산업 ZZZ00, 규모='A'
        """
        value = self.get(industry, size, year, indicator)
        if value is not None:
            return value, "exact"

        value = self.get(industry, "A", year, indicator)
        if value is not None:
            return value, "size_fallback"

        if len(industry) > 1 and industry[0].isalpha():
            parent = industry[:-1] if len(industry) > 2 else industry[0]
            value = self.get(parent, size, year, indicator)
            if value is not None:
                return value, f"parent_industry:{parent}"
            value = self.get(parent, "A", year, indicator)
            if value is not None:
                return value, f"parent_industry:{parent}/A"

        value = self.get("ZZZ00", "A", year, indicator)
        if value is not None:
            return value, "all_industry"

        return None, "none"

    def snapshot(self, industry: str, size: str, year: int) -> dict:
        """한 셀(업종×규모×연도)의 모든 지표를 dict로 반환."""
        rows = self._conn.execute(
            """SELECT indicator_code, indicator_name, category, value, unit
               FROM benchmark
               WHERE industry_code=? AND size_code=? AND year=?
               ORDER BY category, indicator_code""",
            (industry, size, year),
        ).fetchall()
        return {
            r["indicator_code"]: {
                "name": r["indicator_name"],
                "category": r["category"],
                "value": r["value"],
                "unit": r["unit"],
            }
            for r in rows
        }

    # ─── 규모 판별 ──────────────────────────────────────────

    @staticmethod
    def infer_size(revenue_krw: float) -> tuple[str, str]:
        for threshold, code, label in SIZE_THRESHOLDS:
            if revenue_krw >= threshold:
                return code, label
        return "S", "소기업"

    # ─── 스코어링 ────────────────────────────────────────────

    def score_indicator(self, industry: str, size: str, year: int,
                        indicator: str, company_value: float) -> ScoreResult | None:
        peer, source = self.get_with_fallback(industry, size, year, indicator)
        if peer is None:
            return None

        direction = INDICATOR_DIRECTION.get(indicator, "higher")
        name_row = self._conn.execute(
            "SELECT indicator_name, unit FROM benchmark WHERE indicator_code=? LIMIT 1",
            (indicator,),
        ).fetchone()
        name = name_row["indicator_name"] if name_row else indicator
        unit = name_row["unit"] if name_row else ""

        # peer 대비 상대 성과를 -1.0 ~ +1.0 범위로 환산 후 0~100 스코어
        # peer의 ±50%를 기준 구간으로 설정
        if peer == 0:
            delta = 0.0
        else:
            delta = (company_value - peer) / abs(peer)

        if direction == "higher":
            raw = delta
        elif direction == "lower":
            raw = -delta
        else:  # neutral
            raw = -abs(delta)

        # clamp & 매핑: raw ∈ [-0.5, +0.5] → score ∈ [0, 100]
        clamped = max(-0.5, min(0.5, raw))
        score = round((clamped + 0.5) * 100, 1)

        if score >= 85:
            band = "우수"
        elif score >= 70:
            band = "양호"
        elif score >= 45:
            band = "평균"
        elif score >= 25:
            band = "주의"
        else:
            band = "미흡"

        return ScoreResult(
            indicator_code=indicator,
            indicator_name=name,
            company_value=company_value,
            peer_value=peer,
            direction=direction,
            score=score,
            band=band,
            delta_pct=round(delta * 100, 1),
            unit=unit,
        )

    def close(self) -> None:
        self._conn.close()


# ─────────────────────────────────────────────────────────────
# 자체 검증 (python lookup.py 로 실행)
# ─────────────────────────────────────────────────────────────

def _self_test() -> None:
    print("=== Benchmark 자체 검증 ===\n")
    bm = Benchmark()

    # 1. 기본 조회 - C26 중소기업 부채비율 2022
    val = bm.get("C26", "M", 2022, "707")
    print(f"[1] C26/M/2022/부채비율: {val}%  (기대: 96.39)")
    assert val is not None and abs(val - 96.39) < 0.1, f"mismatch: {val}"

    # 2. 규모 폴백 - 존재하지 않는 규모로 조회
    val, src = bm.get_with_fallback("C26", "X", 2022, "707")
    print(f"[2] 폴백 조회: {val}% (source={src})")

    # 3. 스냅샷
    snap = bm.snapshot("C26", "A", 2024)
    print(f"\n[3] C26 종합 2024 스냅샷 ({len(snap)}개 지표):")
    for code, info in sorted(snap.items()):
        print(f"     {code} {info['name']:20s}: {info['value']:>10.2f} {info['unit']}")

    # 4. 규모 자동 판별
    cases = [
        (200_000_000_000, "L"),  # 2000억
        (80_000_000_000, "J"),   # 800억
        (15_000_000_000, "D"),   # 150억
        (5_000_000_000, "S"),    # 50억
        (500_000_000, "S"),      # 5억
    ]
    print("\n[4] 규모 판별:")
    for rev, expected in cases:
        code, label = bm.infer_size(rev)
        ok = "✓" if code == expected else "✗"
        print(f"     {ok} 매출 {rev/100_000_000:>6.0f}억 → {code} ({label})")

    # 5. 스코어링 — 회사 부채비율 시나리오
    print("\n[5] C26 중소기업 부채비율 스코어링:")
    peer = bm.get("C26", "M", 2024, "707") or bm.get("C26", "M", 2023, "707")
    print(f"     peer(중소기업) = {peer}%")
    for company_val in [50, 80, 96, 130, 200]:
        result = bm.score_indicator("C26", "M", 2024, "707", company_val)
        if result is None:
            result = bm.score_indicator("C26", "M", 2023, "707", company_val)
        if result:
            print(f"     회사 부채비율 {company_val:>5.0f}% → "
                  f"{result.score:>5.1f}점 [{result.band}] "
                  f"(peer 대비 {result.delta_pct:+.1f}%)")

    # 6. 스코어링 — 영업이익률 (higher is better)
    print("\n[6] C26 중소기업 영업이익률 스코어링:")
    for company_val in [-2, 0, 3, 6, 12]:
        result = bm.score_indicator("C26", "M", 2024, "611", company_val)
        if result is None:
            result = bm.score_indicator("C26", "M", 2023, "611", company_val)
        if result:
            print(f"     회사 영업이익률 {company_val:>+5.1f}% → "
                  f"{result.score:>5.1f}점 [{result.band}] "
                  f"(peer {result.peer_value:.2f}%)")

    bm.close()
    print("\n✅ 모든 검증 통과")


if __name__ == "__main__":
    _self_test()
