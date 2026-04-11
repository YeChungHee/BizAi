"""
거시경제 지표 기반 마진/한도 조정기

MacroSnapshot (한국은행 ECOS 지표) 를 받아서
마진율 조정값(margin_delta)과 신용한도 배수(credit_limit_factor)를 계산합니다.

조정 로직:

[마진율 조정 — margin_delta(%p)]
  1. 기준금리 조정:  (금리 - 중립금리 2.5%) × 0.4
                     금리 3.5% → +0.4%p, 2.0% → -0.2%p
  2. GDP 조정:       성장률 < 1% → +0.3%p / 1~2% → +0.1%p / ≥2% → 0
  3. CPI 조정:       CPI > 4% → +0.2%p / CPI < 0 → +0.1%p / 0~4% → 0

[신용한도 배수 — credit_limit_factor]
  4. BSI 배수 (업종별):
       BSI < 70  → ×0.6  (40% 축소)
       BSI 70~85 → ×0.8  (20% 축소)
       BSI 85~100→ ×1.0  (기준)
       BSI > 100 → ×1.2  (20% 확대)
  5. 산업생산 배수 (제조업 C만 적용):
       YoY < -5% → ×0.9  / YoY > +5% → ×1.1 / 그 외 → ×1.0

사용:
    from simulation.macro_adjuster import calculate_macro_adjustment
    from benchmark.macro_loader import MacroLoader

    snap = MacroLoader().cached_snapshot()
    adj = calculate_macro_adjustment(snap, industry_code="C26")

    print(adj.margin_delta)          # 예: +0.7%p
    print(adj.credit_limit_factor)   # 예: 0.8 (한도 20% 축소)
    print(adj.rationale)
"""

from __future__ import annotations

from dataclasses import dataclass

# 순환 임포트 방지: MacroSnapshot은 benchmark에 있으므로 TYPE_CHECKING만 사용
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from benchmark.macro_loader import MacroSnapshot


# ─────────────────────────────────────────────────────────────
# 조정 파라미터 (사업 정책에 따라 조정 가능)
# ─────────────────────────────────────────────────────────────

NEUTRAL_RATE = 2.5          # 중립 금리 (%): 이 이상이면 마진 상향 압력
RATE_SENSITIVITY = 0.4      # 금리 1%p 변화 시 마진 조정값 (%p)
RATE_MAX_DELTA = 2.0        # 금리 기인 마진 최대 조정 상한 (%p)

GDP_THRESHOLDS = [
    (1.0, 0.3),             # GDP < 1%  → +0.3%p (경기 침체)
    (2.0, 0.1),             # GDP < 2%  → +0.1%p (경기 둔화)
]                           # GDP >= 2% → 0

CPI_HIGH_THRESHOLD = 4.0    # CPI > 4%: 고인플레 조정
CPI_LOW_THRESHOLD = 0.0     # CPI < 0:  디플레 조정
CPI_HIGH_DELTA = 0.2
CPI_LOW_DELTA = 0.1

BSI_BANDS = [
    (70,  0.6),             # BSI < 70  → ×0.6
    (85,  0.8),             # BSI < 85  → ×0.8
    (100, 1.0),             # BSI < 100 → ×1.0
    (999, 1.2),             # BSI >= 100 → ×1.2
]

IP_HIGH_THRESHOLD = 5.0     # 산업생산 YoY > +5% → ×1.1 (제조업)
IP_LOW_THRESHOLD = -5.0     # 산업생산 YoY < -5% → ×0.9 (제조업)


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class MacroAdjustment:
    """거시경제 기반 마진·한도 조정값"""

    # ── 마진율 조정 ──────────────────────────────────────────
    margin_delta: float          # 총 마진율 조정 (%p, 양수=상향)

    rate_delta: float            # 기준금리 기인 조정 (%p)
    gdp_delta: float             # GDP 기인 조정 (%p)
    cpi_delta: float             # CPI 기인 조정 (%p)

    # ── 신용한도 배수 ────────────────────────────────────────
    credit_limit_factor: float   # 최종 신용한도 배수 (1.0 = 기준)

    bsi_factor: float            # BSI 기반 배수
    ip_factor: float             # 산업생산 기반 배수 (제조업만)

    # ── 메타 ─────────────────────────────────────────────────
    rationale: str               # 근거 텍스트 (마진 + 한도)
    snapshot_date: str           # 사용된 지표 기준 기간

    def to_dict(self) -> dict:
        return {
            "margin_delta": self.margin_delta,
            "rate_delta": self.rate_delta,
            "gdp_delta": self.gdp_delta,
            "cpi_delta": self.cpi_delta,
            "credit_limit_factor": self.credit_limit_factor,
            "bsi_factor": self.bsi_factor,
            "ip_factor": self.ip_factor,
            "rationale": self.rationale,
            "snapshot_date": self.snapshot_date,
        }

    @classmethod
    def neutral(cls) -> "MacroAdjustment":
        """거시지표 없을 때 사용하는 중립 조정 (아무것도 조정 안함)"""
        return cls(
            margin_delta=0.0, rate_delta=0.0, gdp_delta=0.0, cpi_delta=0.0,
            credit_limit_factor=1.0, bsi_factor=1.0, ip_factor=1.0,
            rationale="거시지표 미적용 (중립)",
            snapshot_date="N/A",
        )


# ─────────────────────────────────────────────────────────────
# 계산
# ─────────────────────────────────────────────────────────────

def calculate_macro_adjustment(
    snapshot: "MacroSnapshot",
    industry_code: str | None = None,
) -> MacroAdjustment:
    """
    MacroSnapshot + 업종 코드 → MacroAdjustment.

    Args:
        snapshot: ECOS에서 수집한 거시경제 스냅샷
        industry_code: KSIC 업종 코드 (제조업 여부 판단에 사용)

    Returns:
        MacroAdjustment — 마진 조정값 + 신용한도 배수
    """
    is_manufacturing = bool(
        industry_code and industry_code.upper().startswith("C")
    )

    # ── 1. 기준금리 조정 ──────────────────────────────────────
    if snapshot.base_rate is not None:
        raw_rate_delta = (snapshot.base_rate - NEUTRAL_RATE) * RATE_SENSITIVITY
        rate_delta = round(max(-RATE_MAX_DELTA, min(RATE_MAX_DELTA, raw_rate_delta)), 2)
    else:
        rate_delta = 0.0

    # ── 2. GDP 조정 ───────────────────────────────────────────
    if snapshot.gdp_growth is not None:
        gdp_delta = 0.0
        for threshold, delta in GDP_THRESHOLDS:
            if snapshot.gdp_growth < threshold:
                gdp_delta = delta
                break
    else:
        gdp_delta = 0.0

    # ── 3. CPI 조정 ───────────────────────────────────────────
    if snapshot.cpi_yoy is not None:
        if snapshot.cpi_yoy > CPI_HIGH_THRESHOLD:
            cpi_delta = CPI_HIGH_DELTA
        elif snapshot.cpi_yoy < CPI_LOW_THRESHOLD:
            cpi_delta = CPI_LOW_DELTA
        else:
            cpi_delta = 0.0
    else:
        cpi_delta = 0.0

    margin_delta = round(rate_delta + gdp_delta + cpi_delta, 2)

    # ── 4. BSI 기반 신용한도 배수 ─────────────────────────────
    # 제조업이면 제조업 BSI 우선, 없으면 전산업 BSI
    if is_manufacturing and snapshot.bsi_mfg is not None:
        bsi_value = snapshot.bsi_mfg
        bsi_label = "제조업 BSI"
    elif snapshot.bsi_all is not None:
        bsi_value = snapshot.bsi_all
        bsi_label = "전산업 BSI"
    else:
        bsi_value = None
        bsi_label = "BSI"

    if bsi_value is not None:
        bsi_factor = 1.0
        for upper, factor in BSI_BANDS:
            if bsi_value < upper:
                bsi_factor = factor
                break
    else:
        bsi_factor = 1.0

    # ── 5. 산업생산 기반 한도 배수 (제조업 전용) ──────────────
    if is_manufacturing and snapshot.ip_yoy is not None:
        if snapshot.ip_yoy > IP_HIGH_THRESHOLD:
            ip_factor = 1.1
        elif snapshot.ip_yoy < IP_LOW_THRESHOLD:
            ip_factor = 0.9
        else:
            ip_factor = 1.0
    else:
        ip_factor = 1.0

    credit_limit_factor = round(bsi_factor * ip_factor, 2)

    rationale = _build_rationale(
        snapshot=snapshot,
        is_manufacturing=is_manufacturing,
        rate_delta=rate_delta,
        gdp_delta=gdp_delta,
        cpi_delta=cpi_delta,
        margin_delta=margin_delta,
        bsi_value=bsi_value,
        bsi_label=bsi_label,
        bsi_factor=bsi_factor,
        ip_factor=ip_factor,
        credit_limit_factor=credit_limit_factor,
    )

    return MacroAdjustment(
        margin_delta=margin_delta,
        rate_delta=rate_delta,
        gdp_delta=gdp_delta,
        cpi_delta=cpi_delta,
        credit_limit_factor=credit_limit_factor,
        bsi_factor=bsi_factor,
        ip_factor=ip_factor,
        rationale=rationale,
        snapshot_date=snapshot.reference_date,
    )


def _build_rationale(
    snapshot: "MacroSnapshot",
    is_manufacturing: bool,
    rate_delta: float,
    gdp_delta: float,
    cpi_delta: float,
    margin_delta: float,
    bsi_value: float | None,
    bsi_label: str,
    bsi_factor: float,
    ip_factor: float,
    credit_limit_factor: float,
) -> str:
    parts = []

    # 마진 조정 근거
    margin_parts = []
    if snapshot.base_rate is not None:
        direction = "↑" if rate_delta > 0 else ("↓" if rate_delta < 0 else "→")
        margin_parts.append(
            f"기준금리 {snapshot.base_rate:.2f}% {direction} 마진 {rate_delta:+.2f}%p"
        )
    if gdp_delta != 0 and snapshot.gdp_growth is not None:
        margin_parts.append(
            f"GDP {snapshot.gdp_growth:.1f}% (둔화) → +{gdp_delta:.1f}%p"
        )
    if cpi_delta != 0 and snapshot.cpi_yoy is not None:
        label = "고인플레" if snapshot.cpi_yoy > CPI_HIGH_THRESHOLD else "디플레"
        margin_parts.append(
            f"CPI {snapshot.cpi_yoy:.1f}% ({label}) → +{cpi_delta:.1f}%p"
        )

    if margin_parts:
        parts.append(f"[마진 {margin_delta:+.2f}%p] " + " | ".join(margin_parts))
    else:
        parts.append("[마진 조정 없음] 거시지표 중립 수준")

    # 한도 조정 근거
    limit_parts = []
    if bsi_value is not None:
        if bsi_factor < 1.0:
            limit_parts.append(
                f"{bsi_label} {bsi_value:.0f}P → 한도 {int(bsi_factor*100)}% (축소)"
            )
        elif bsi_factor > 1.0:
            limit_parts.append(
                f"{bsi_label} {bsi_value:.0f}P → 한도 {int(bsi_factor*100)}% (확대)"
            )
        else:
            limit_parts.append(f"{bsi_label} {bsi_value:.0f}P → 한도 기준")

    if ip_factor != 1.0 and is_manufacturing and snapshot.ip_yoy is not None:
        direction = "상승" if ip_factor > 1.0 else "하락"
        limit_parts.append(
            f"산업생산 YoY {snapshot.ip_yoy:.1f}% ({direction}) → ×{ip_factor}"
        )

    if limit_parts:
        factor_pct = int(credit_limit_factor * 100)
        parts.append(f"[한도 ×{credit_limit_factor:.1f}] " + " | ".join(limit_parts))

    return " / ".join(parts)


# ─────────────────────────────────────────────────────────────
# 자체 검증 (python macro_adjuster.py)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    sys.path.insert(0, str(__file__.rsplit("/api/", 1)[0] + "/api"))

    # MacroSnapshot 직접 임포트
    from benchmark.macro_loader import MacroSnapshot  # noqa

    print("=== MacroAdjuster 자체 검증 ===\n")

    cases = [
        # (label, base_rate, gdp, cpi, bsi_all, bsi_mfg, ip_yoy, industry)
        ("고금리 + 경기침체 + 제조업 BSI 하락",
         4.5, 0.5, 2.0, 68.0, 65.0, -6.0, "C26"),
        ("저금리 + 경기호황 + 서비스 BSI 양호",
         1.5, 3.5, 1.5, 102.0, None, None, "G"),
        ("중립 (기준금리=중립, GDP=양호, BSI=100)",
         2.5, 2.5, 2.0, 100.0, 100.0, 0.0, "C"),
        ("고인플레 + 금리 상승",
         3.75, 2.2, 5.2, 88.0, None, None, None),
    ]

    for label, rate, gdp, cpi, bsi_all, bsi_mfg, ip_yoy, ind in cases:
        snap = MacroSnapshot(
            base_rate=rate, gdp_growth=gdp, cpi_yoy=cpi,
            bsi_all=bsi_all, bsi_mfg=bsi_mfg, ip_yoy=ip_yoy,
            reference_date="202412", fetched_at="2024-12-01T00:00:00",
        )
        adj = calculate_macro_adjustment(snap, industry_code=ind)
        print(f"[{label}]")
        print(f"  margin_delta:        {adj.margin_delta:+.2f}%p  "
              f"(금리{adj.rate_delta:+.2f} / GDP{adj.gdp_delta:+.2f} / CPI{adj.cpi_delta:+.2f})")
        print(f"  credit_limit_factor: ×{adj.credit_limit_factor:.2f}  "
              f"(BSI×{adj.bsi_factor:.1f} / 산업생산×{adj.ip_factor:.1f})")
        print(f"  근거: {adj.rationale}\n")
