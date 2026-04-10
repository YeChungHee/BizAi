"""
Red Flag 탐지 룰 엔진.

10개 규칙 기반으로 재무제표에서 위험 신호를 탐지합니다.
각 Red Flag는 등급 계산 시 페널티로 적용됩니다 (3점/개, max 30).

사용:
    from analysis.red_flag_detector import detect_red_flags

    flags = detect_red_flags(stmt_2024, stmt_2023)
    # → [RedFlag(code="RF01", severity="high", ...), ...]
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "schema"))
from models import Statement  # noqa: E402


# ─────────────────────────────────────────────────────────────
# Red Flag 결과
# ─────────────────────────────────────────────────────────────

@dataclass
class RedFlag:
    code: str          # RF01 ~ RF10
    name: str
    severity: str      # critical | high | medium
    description: str   # 구체적 수치 포함 설명
    metric_value: Optional[float] = None
    threshold: Optional[float] = None


# ─────────────────────────────────────────────────────────────
# 룰 정의 (10개)
# ─────────────────────────────────────────────────────────────

def detect_red_flags(
    current: Statement,
    prior: Optional[Statement] = None,
) -> list[RedFlag]:
    """
    당기(+전기) 재무제표에서 Red Flag를 탐지.

    Returns:
        탐지된 RedFlag 리스트 (중복 없음)
    """
    flags: list[RedFlag] = []
    bs = current.balance_sheet
    is_ = current.income_statement
    cf = current.cash_flow

    # ── RF01: 이자보상비율 < 1 (이자도 못 갚음) ──
    int_exp = is_.interest_expense or is_.finance_cost
    if int_exp and int_exp > 0:
        icr = is_.operating_profit / int_exp
        if icr < 1.0:
            flags.append(RedFlag(
                code="RF01",
                name="이자보상비율 미달",
                severity="critical",
                description=(
                    f"이자보상비율 {icr:.2f}배 (< 1.0): "
                    f"영업이익({is_.operating_profit:,.0f})으로 이자비용({int_exp:,.0f})을 감당 불가"
                ),
                metric_value=icr,
                threshold=1.0,
            ))

    # ── RF02: 매출채권 급증 (전년 대비 30% 이상 증가, 매출 증가율 초과) ──
    if prior and bs.trade_receivables and prior.balance_sheet.trade_receivables:
        ar_growth = (bs.trade_receivables - prior.balance_sheet.trade_receivables) / prior.balance_sheet.trade_receivables * 100
        rev_growth = (is_.revenue - prior.income_statement.revenue) / prior.income_statement.revenue * 100 if prior.income_statement.revenue > 0 else 0
        if ar_growth > 30 and ar_growth > rev_growth * 1.5:
            flags.append(RedFlag(
                code="RF02",
                name="매출채권 급증",
                severity="high",
                description=(
                    f"매출채권 증가율 {ar_growth:.1f}% (매출 증가율 {rev_growth:.1f}%의 1.5배 초과). "
                    f"회수 지연 또는 분식 가능성"
                ),
                metric_value=ar_growth,
                threshold=30.0,
            ))

    # ── RF03: 이익의 질 (영업CF < 순이익) ──
    if is_.net_profit > 0 and cf.operating_cf < is_.net_profit * 0.5:
        quality = cf.operating_cf / is_.net_profit if is_.net_profit != 0 else 0
        flags.append(RedFlag(
            code="RF03",
            name="이익의 질 저하",
            severity="high",
            description=(
                f"영업CF({cf.operating_cf:,.0f}) < 순이익의 50%({is_.net_profit*0.5:,.0f}). "
                f"이익의 질 {quality:.1%} — 현금 창출력 의문"
            ),
            metric_value=quality * 100,
            threshold=50.0,
        ))

    # ── RF04: 부채비율 300% 초과 ──
    if bs.total_equity > 0:
        debt_ratio = bs.total_liabilities / bs.total_equity * 100
        if debt_ratio > 300:
            flags.append(RedFlag(
                code="RF04",
                name="과다 부채",
                severity="high",
                description=(
                    f"부채비율 {debt_ratio:.1f}% (> 300%): "
                    f"부채 {bs.total_liabilities:,.0f} / 자본 {bs.total_equity:,.0f}"
                ),
                metric_value=debt_ratio,
                threshold=300.0,
            ))
    elif bs.total_equity <= 0:
        flags.append(RedFlag(
            code="RF04",
            name="자본잠식",
            severity="critical",
            description=(
                f"자기자본 {bs.total_equity:,.0f} ≤ 0: 완전자본잠식 상태"
            ),
            metric_value=bs.total_equity,
            threshold=0.0,
        ))

    # ── RF05: 유동비율 80% 미만 (단기 지급 능력 부족) ──
    if bs.current_liabilities > 0:
        current_ratio = bs.current_assets / bs.current_liabilities * 100
        if current_ratio < 80:
            flags.append(RedFlag(
                code="RF05",
                name="단기 유동성 부족",
                severity="high",
                description=(
                    f"유동비율 {current_ratio:.1f}% (< 80%): "
                    f"유동자산 {bs.current_assets:,.0f} / 유동부채 {bs.current_liabilities:,.0f}"
                ),
                metric_value=current_ratio,
                threshold=80.0,
            ))

    # ── RF06: 3기 연속 영업이익 감소 ──
    # 이 룰은 3개년 데이터가 필요하므로 현재/전기만으로는 감소 추세만 확인
    if prior and is_.operating_profit < prior.income_statement.operating_profit:
        decline_pct = (
            (is_.operating_profit - prior.income_statement.operating_profit)
            / abs(prior.income_statement.operating_profit) * 100
            if prior.income_statement.operating_profit != 0
            else -100
        )
        if decline_pct < -20:
            flags.append(RedFlag(
                code="RF06",
                name="영업이익 급감",
                severity="medium",
                description=(
                    f"영업이익 전년 대비 {decline_pct:.1f}% 감소. "
                    f"{prior.income_statement.operating_profit:,.0f} → {is_.operating_profit:,.0f}"
                ),
                metric_value=decline_pct,
                threshold=-20.0,
            ))

    # ── RF07: 영업이익 적자 ──
    if is_.operating_profit < 0:
        flags.append(RedFlag(
            code="RF07",
            name="영업적자",
            severity="high",
            description=(
                f"영업이익 {is_.operating_profit:,.0f} (적자). "
                f"본업에서 수익 미창출"
            ),
            metric_value=is_.operating_profit,
            threshold=0.0,
        ))

    # ── RF08: 차입금의존도 50% 초과 ──
    total_debt = bs.total_debt
    if total_debt is not None and bs.total_assets > 0:
        debt_dep = total_debt / bs.total_assets * 100
        if debt_dep > 50:
            flags.append(RedFlag(
                code="RF08",
                name="차입금과다의존",
                severity="medium",
                description=(
                    f"차입금의존도 {debt_dep:.1f}% (> 50%): "
                    f"차입금 {total_debt:,.0f} / 총자산 {bs.total_assets:,.0f}"
                ),
                metric_value=debt_dep,
                threshold=50.0,
            ))

    # ── RF09: 재고자산 급증 (전년 대비 50% 이상, 매출 증가율 초과) ──
    if prior and bs.inventories and prior.balance_sheet.inventories:
        inv_growth = (bs.inventories - prior.balance_sheet.inventories) / prior.balance_sheet.inventories * 100
        rev_growth = (is_.revenue - prior.income_statement.revenue) / prior.income_statement.revenue * 100 if prior.income_statement.revenue > 0 else 0
        if inv_growth > 50 and inv_growth > rev_growth * 2:
            flags.append(RedFlag(
                code="RF09",
                name="재고자산 급증",
                severity="medium",
                description=(
                    f"재고자산 증가율 {inv_growth:.1f}% (매출 증가율 {rev_growth:.1f}%의 2배 초과). "
                    f"과잉재고 또는 판매 부진"
                ),
                metric_value=inv_growth,
                threshold=50.0,
            ))

    # ── RF10: 감사의견 비적정 ──
    if current.audit:
        if current.audit.opinion in ("한정", "부적정", "의견거절"):
            sev = "critical" if current.audit.opinion in ("부적정", "의견거절") else "high"
            flags.append(RedFlag(
                code="RF10",
                name=f"감사의견: {current.audit.opinion}",
                severity=sev,
                description=(
                    f"감사인 '{current.audit.auditor or '미상'}'의 감사의견: {current.audit.opinion}. "
                    f"{'계속기업 불확실성 언급' if current.audit.going_concern_doubt else ''}"
                ),
            ))
        if current.audit.going_concern_doubt:
            flags.append(RedFlag(
                code="RF10b",
                name="계속기업 불확실성",
                severity="critical",
                description="감사보고서에 계속기업 가정에 대한 불확실성이 언급됨",
            ))

    return flags


def red_flag_summary(flags: list[RedFlag]) -> str:
    """Red Flag 요약 텍스트 생성."""
    if not flags:
        return "✅ Red Flag 없음"

    severity_emoji = {"critical": "🔴", "high": "🟠", "medium": "🟡"}
    lines = [f"⚠️  Red Flag {len(flags)}건 탐지\n"]
    for f in sorted(flags, key=lambda x: ("critical", "high", "medium").index(x.severity)):
        emoji = severity_emoji.get(f.severity, "⚪")
        lines.append(f"  {emoji} [{f.code}] {f.name} ({f.severity})")
        lines.append(f"     {f.description}")
    return "\n".join(lines)
