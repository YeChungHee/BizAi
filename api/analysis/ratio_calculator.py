"""
재무비율 계산 모듈.

ECOS 벤치마크 지표 코드와 1:1 매칭되는 25개 재무비율을 계산합니다.
validator.py의 calculate_ratios 를 독립 모듈로 확장하고,
카테고리별 그룹핑 + 생산성 지표(9034/9044/9064/9074)를 추가합니다.

사용:
    from analysis.ratio_calculator import calculate_ratios, RATIO_META

    ratios = calculate_ratios(stmt_2024, stmt_2023)
    # → {"501": 10.73, "602": 1.38, ...}
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "schema"))
from models import Statement  # noqa: E402


# ─────────────────────────────────────────────────────────────
# 지표 메타데이터
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RatioMeta:
    code: str
    name: str
    category: str       # 성장성 | 수익성 | 안정성 | 활동성 | 생산성
    unit: str           # % | 회 | 배
    description: str


RATIO_META: dict[str, RatioMeta] = {
    # ── 성장성 ──
    "501": RatioMeta("501", "총자산증가율", "성장성", "%", "(당기총자산-전기총자산)/전기총자산×100"),
    "502": RatioMeta("502", "유형자산증가율", "성장성", "%", "(당기유형자산-전기유형자산)/전기유형자산×100"),
    "505": RatioMeta("505", "자기자본증가율", "성장성", "%", "(당기자기자본-전기자기자본)/전기자기자본×100"),
    "506": RatioMeta("506", "매출액증가율", "성장성", "%", "(당기매출-전기매출)/전기매출×100"),
    # ── 수익성 ──
    "602": RatioMeta("602", "총자산순이익률(ROA)", "수익성", "%", "당기순이익/총자산×100"),
    "606": RatioMeta("606", "자기자본순이익률(ROE)", "수익성", "%", "당기순이익/자기자본×100"),
    "610": RatioMeta("610", "매출액순이익률", "수익성", "%", "당기순이익/매출액×100"),
    "611": RatioMeta("611", "매출액영업이익률", "수익성", "%", "영업이익/매출액×100"),
    "612": RatioMeta("612", "매출원가대매출액", "수익성", "%", "매출원가/매출액×100 (낮을수록 좋음)"),
    "615": RatioMeta("615", "연구개발비대매출액", "수익성", "%", "R&D비/매출액×100"),
    "625": RatioMeta("625", "금융비용대매출액", "수익성", "%", "금융비용/매출액×100 (낮을수록 좋음)"),
    "627": RatioMeta("627", "이자보상비율", "수익성", "%", "영업이익/이자비용×100"),
    # ── 안정성 ──
    "701": RatioMeta("701", "자기자본비율", "안정성", "%", "자기자본/총자산×100"),
    "702": RatioMeta("702", "유동비율", "안정성", "%", "유동자산/유동부채×100"),
    "703": RatioMeta("703", "당좌비율", "안정성", "%", "(유동자산-재고)/유동부채×100"),
    "707": RatioMeta("707", "부채비율", "안정성", "%", "총부채/자기자본×100 (낮을수록 좋음)"),
    "710": RatioMeta("710", "차입금의존도", "안정성", "%", "총차입금/총자산×100 (낮을수록 좋음)"),
    # ── 활동성 ──
    "801": RatioMeta("801", "총자산회전율", "활동성", "회", "매출액/총자산"),
    "806": RatioMeta("806", "재고자산회전율", "활동성", "회", "매출원가/재고자산"),
    "808": RatioMeta("808", "매출채권회전율", "활동성", "회", "매출액/매출채권"),
    "809": RatioMeta("809", "매입채무회전율", "활동성", "회", "매출원가/매입채무"),
    # ── 생산성 ──
    "9034": RatioMeta("9034", "총자본투자효율", "생산성", "%", "부가가치/총자본×100"),
    "9044": RatioMeta("9044", "설비투자효율", "생산성", "%", "부가가치/유형자산×100"),
    "9064": RatioMeta("9064", "부가가치율", "생산성", "%", "부가가치/매출액×100"),
    "9074": RatioMeta("9074", "노동소득분배율", "생산성", "%", "인건비/부가가치×100"),
}

# 카테고리 → 지표코드 목록
CATEGORY_CODES = {
    "성장성": ["501", "502", "505", "506"],
    "수익성": ["602", "606", "610", "611", "612", "615", "625", "627"],
    "안정성": ["701", "702", "703", "707", "710"],
    "활동성": ["801", "806", "808", "809"],
    "생산성": ["9034", "9044", "9064", "9074"],
}


# ─────────────────────────────────────────────────────────────
# 계산 함수
# ─────────────────────────────────────────────────────────────

def calculate_ratios(
    current: Statement,
    prior: Optional[Statement] = None,
) -> dict[str, float | None]:
    """
    ECOS 지표 코드 기준으로 25개 재무비율 계산.

    Args:
        current: 당기 재무제표
        prior: 전기 재무제표 (성장성 지표 계산에 필요)

    Returns:
        dict  코드 → 비율값 (계산불가 시 None)
    """
    bs = current.balance_sheet
    is_ = current.income_statement
    r: dict[str, float | None] = {}

    # ── 성장성 ──
    if prior is not None:
        p_bs = prior.balance_sheet
        p_is = prior.income_statement
        r["501"] = _pct_change(p_bs.total_assets, bs.total_assets)
        r["502"] = _pct_change(p_bs.ppe, bs.ppe)
        r["505"] = _pct_change(p_bs.total_equity, bs.total_equity)
        r["506"] = _pct_change(p_is.revenue, is_.revenue)
    else:
        r.update({"501": None, "502": None, "505": None, "506": None})

    # ── 수익성 ──
    r["602"] = _pct(is_.net_profit, bs.total_assets)
    r["606"] = _pct(is_.net_profit, bs.total_equity)
    r["610"] = _pct(is_.net_profit, is_.revenue)
    r["611"] = _pct(is_.operating_profit, is_.revenue)
    r["612"] = _pct(is_.cost_of_sales, is_.revenue)
    r["615"] = _pct(is_.rnd_expense, is_.revenue)
    r["625"] = _pct(is_.finance_cost, is_.revenue)

    int_exp = is_.interest_expense or is_.finance_cost
    if int_exp and int_exp > 0:
        r["627"] = round(is_.operating_profit / int_exp * 100, 2)
    else:
        r["627"] = None

    # ── 안정성 ──
    r["701"] = _pct(bs.total_equity, bs.total_assets)
    r["702"] = _pct(bs.current_assets, bs.current_liabilities)
    r["707"] = _pct(bs.total_liabilities, bs.total_equity)

    if bs.inventories is not None:
        r["703"] = _pct(bs.current_assets - bs.inventories, bs.current_liabilities)
    else:
        r["703"] = None

    total_debt = bs.total_debt
    r["710"] = _pct(total_debt, bs.total_assets) if total_debt is not None else None

    # ── 활동성 ──
    r["801"] = _ratio(is_.revenue, bs.total_assets)
    r["806"] = (
        round(is_.cost_of_sales / bs.inventories, 3)
        if bs.inventories and bs.inventories > 0 and is_.cost_of_sales
        else None
    )
    r["808"] = (
        round(is_.revenue / bs.trade_receivables, 3)
        if bs.trade_receivables and bs.trade_receivables > 0
        else None
    )
    r["809"] = (
        round(is_.cost_of_sales / bs.trade_payables, 3)
        if bs.trade_payables and bs.trade_payables > 0 and is_.cost_of_sales
        else None
    )

    # ── 생산성 (간이 부가가치 추정) ──
    # 부가가치 ≈ 영업이익 + 인건비 + 감가상각비 + 순금융비용 + 세금
    # 인건비 데이터가 없는 경우 SGA의 60%로 추정
    dep = (is_.depreciation or 0) + (is_.amortization or 0)
    labor_est = (is_.sga or 0) * 0.6  # SGA의 약 60%가 인건비로 추정
    value_added = is_.operating_profit + labor_est + dep + (is_.finance_cost or 0)

    if value_added > 0:
        r["9034"] = _pct(value_added, bs.total_assets)
        r["9044"] = _pct(value_added, bs.ppe) if bs.ppe and bs.ppe > 0 else None
        r["9064"] = _pct(value_added, is_.revenue)
        r["9074"] = _pct(labor_est, value_added) if labor_est > 0 else None
    else:
        r.update({"9034": None, "9044": None, "9064": None, "9074": None})

    return r


def get_ratios_by_category(
    ratios: dict[str, float | None],
    category: str,
) -> dict[str, float | None]:
    """특정 카테고리의 비율만 추출."""
    codes = CATEGORY_CODES.get(category, [])
    return {c: ratios.get(c) for c in codes}


# ─────────────────────────────────────────────────────────────
# 내부 헬퍼
# ─────────────────────────────────────────────────────────────

def _pct(num: float | None, den: float | None) -> float | None:
    if num is None or den is None or den == 0:
        return None
    return round(num / den * 100, 2)


def _pct_change(prev: float | None, now: float | None) -> float | None:
    if prev is None or now is None or prev == 0:
        return None
    return round((now - prev) / abs(prev) * 100, 2)


def _ratio(num: float, den: float) -> float | None:
    if den == 0:
        return None
    return round(num / den, 3)
