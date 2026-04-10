"""
재무제표 표준 JSON 검증기 + 비율 자동계산기.

역할:
  1. JSON Schema 기본 검증 (jsonschema 없이 경량 구현 - dataclass로 구조 검증)
  2. 내부 일관성 검증 (예: total_assets = current + non_current)
  3. ECOS 벤치마크 지표 코드와 동일한 규약으로 재무비율 계산

사용:
    python validator.py examples/sample_c26.json
    python validator.py examples/sample_c26.json --ratios 2024
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

# 상대 임포트를 피하고 같은 디렉토리 기반으로 동작
sys.path.insert(0, str(Path(__file__).parent))
from models import (  # type: ignore  # noqa: E402
    FinancialStatement,
    Statement,
)

TOLERANCE_PCT = 1.0  # 합산 검증 허용 오차 (%)

# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    ok: bool
    errors: list[str]
    warnings: list[str]

    def report(self) -> str:
        lines = [f"{'✅ VALID' if self.ok else '❌ INVALID'}"]
        if self.errors:
            lines.append("\n[Errors]")
            lines.extend(f"  ✗ {e}" for e in self.errors)
        if self.warnings:
            lines.append("\n[Warnings]")
            lines.extend(f"  ⚠ {w}" for w in self.warnings)
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# 구조 검증
# ─────────────────────────────────────────────────────────────

def validate(fs: FinancialStatement) -> ValidationResult:
    errors: list[str] = []
    warnings: list[str] = []

    if not fs.company.name:
        errors.append("company.name is required")
    if not fs.statements:
        errors.append("at least 1 statement required")

    for stmt in fs.statements:
        y = stmt.fiscal_year
        bs = stmt.balance_sheet
        is_ = stmt.income_statement

        # 1. 재무상태표 합산 일치성
        sum_assets = bs.current_assets + bs.non_current_assets
        if not _approx_equal(sum_assets, bs.total_assets):
            errors.append(
                f"[{y}] BS: current + non_current ({sum_assets:,.0f}) "
                f"≠ total_assets ({bs.total_assets:,.0f})"
            )

        sum_liab = bs.current_liabilities + bs.non_current_liabilities
        if not _approx_equal(sum_liab, bs.total_liabilities):
            errors.append(
                f"[{y}] BS: current_liab + non_current_liab "
                f"({sum_liab:,.0f}) ≠ total_liab ({bs.total_liabilities:,.0f})"
            )

        # 2. 자산 = 부채 + 자본
        lhs = bs.total_assets
        rhs = bs.total_liabilities + bs.total_equity
        if not _approx_equal(lhs, rhs):
            errors.append(
                f"[{y}] BS: assets ({lhs:,.0f}) ≠ liab+equity ({rhs:,.0f})"
            )

        # 3. 손익계산서 일관성
        if is_.revenue <= 0:
            warnings.append(f"[{y}] IS: revenue ≤ 0")
        if is_.gross_profit is not None and is_.cost_of_sales is not None:
            calc_gp = is_.revenue - is_.cost_of_sales
            if not _approx_equal(calc_gp, is_.gross_profit):
                errors.append(
                    f"[{y}] IS: revenue - COGS ({calc_gp:,.0f}) "
                    f"≠ gross_profit ({is_.gross_profit:,.0f})"
                )

        # 4. 현금흐름 일관성 경고
        cf = stmt.cash_flow
        if cf.net_cf is not None:
            calc_net = cf.operating_cf + cf.investing_cf + cf.financing_cf
            if (cf.fx_effect or 0) + calc_net != cf.net_cf:
                if not _approx_equal(calc_net + (cf.fx_effect or 0), cf.net_cf):
                    warnings.append(
                        f"[{y}] CF: sum ≠ net_cf (허용오차 밖)"
                    )

        # 5. 품질 플래그
        if stmt.quality.source == "pdf":
            conf = stmt.quality.extraction_confidence or 0
            if conf < 0.85:
                warnings.append(
                    f"[{y}] PDF 신뢰도 낮음: {conf:.2f} (< 0.85)"
                )
        if stmt.audit and stmt.audit.opinion == "한정":
            warnings.append(f"[{y}] 감사의견: 한정 (-1등급 요인)")
        if stmt.audit and stmt.audit.opinion in ("부적정", "의견거절"):
            warnings.append(f"[{y}] 감사의견: {stmt.audit.opinion} (-3등급 요인)")

    return ValidationResult(ok=not errors, errors=errors, warnings=warnings)


def _approx_equal(a: float, b: float) -> bool:
    if a == b:
        return True
    base = max(abs(a), abs(b), 1)
    return abs(a - b) / base * 100 < TOLERANCE_PCT


# ─────────────────────────────────────────────────────────────
# 재무비율 자동계산 (ECOS 지표 코드와 동일)
# ─────────────────────────────────────────────────────────────

def calculate_ratios(
    current: Statement, prior: Statement | None = None
) -> dict[str, float | None]:
    """
    ECOS 지표 코드 기준으로 회사의 재무비율 계산.
    prior가 주어지면 성장성 지표(501,502,505,506) 계산 가능.

    반환 키는 benchmark.lookup 의 indicator_code 와 일치:
      성장성: 501,502,505,506
      수익성: 602,606,610,611,612,615,625,627
      안정성: 701,702,703,707,710
      활동성: 801,806,808,809
    """
    bs = current.balance_sheet
    is_ = current.income_statement
    r: dict[str, float | None] = {}

    # ───── 성장성 ─────
    if prior is not None:
        p_bs, p_is = prior.balance_sheet, prior.income_statement
        r["501"] = _pct_change(p_bs.total_assets, bs.total_assets)          # 총자산증가율
        r["502"] = _pct_change(p_bs.ppe, bs.ppe)                            # 유형자산증가율
        r["505"] = _pct_change(p_bs.total_equity, bs.total_equity)          # 자기자본증가율
        r["506"] = _pct_change(p_is.revenue, is_.revenue)                   # 매출액증가율
    else:
        r.update({"501": None, "502": None, "505": None, "506": None})

    # ───── 수익성 ─────
    r["602"] = _pct(is_.net_profit, bs.total_assets)                        # ROA
    r["606"] = _pct(is_.net_profit, bs.total_equity)                        # ROE
    r["610"] = _pct(is_.net_profit, is_.revenue)                            # 매출액순이익률
    r["611"] = _pct(is_.operating_profit, is_.revenue)                      # 매출액영업이익률
    r["612"] = _pct(is_.cost_of_sales, is_.revenue)                         # 매출원가대매출액
    r["615"] = _pct(is_.rnd_expense, is_.revenue)                           # 연구개발비대매출액
    r["625"] = _pct(is_.finance_cost, is_.revenue)                          # 금융비용대매출액

    # 이자보상비율: 영업이익 / 이자비용 × 100
    int_exp = is_.interest_expense or is_.finance_cost
    if int_exp and int_exp > 0:
        r["627"] = is_.operating_profit / int_exp * 100
    else:
        r["627"] = None

    # ───── 안정성 ─────
    r["701"] = _pct(bs.total_equity, bs.total_assets)                       # 자기자본비율
    r["702"] = _pct(bs.current_assets, bs.current_liabilities)              # 유동비율
    r["707"] = _pct(bs.total_liabilities, bs.total_equity)                  # 부채비율

    # 당좌비율 = (유동자산 - 재고) / 유동부채
    if bs.inventories is not None:
        r["703"] = _pct(bs.current_assets - bs.inventories, bs.current_liabilities)
    else:
        r["703"] = None

    # 차입금의존도 = 총차입금 / 총자산
    total_debt = bs.total_debt
    r["710"] = _pct(total_debt, bs.total_assets) if total_debt is not None else None

    # ───── 활동성 (회전율) ─────
    r["801"] = _ratio(is_.revenue, bs.total_assets)                         # 총자산회전율
    if bs.inventories is not None and bs.inventories > 0 and is_.cost_of_sales:
        r["806"] = is_.cost_of_sales / bs.inventories                       # 재고자산회전율
    else:
        r["806"] = None
    if bs.trade_receivables is not None and bs.trade_receivables > 0:
        r["808"] = is_.revenue / bs.trade_receivables                       # 매출채권회전율
    else:
        r["808"] = None
    if bs.trade_payables is not None and bs.trade_payables > 0 and is_.cost_of_sales:
        r["809"] = is_.cost_of_sales / bs.trade_payables                    # 매입채무회전율
    else:
        r["809"] = None

    return r


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


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("json_path", type=Path)
    ap.add_argument("--ratios", type=int, metavar="YEAR",
                    help="해당 연도의 재무비율 출력 (전년도 있으면 성장성 포함)")
    args = ap.parse_args()

    data = json.loads(args.json_path.read_text(encoding="utf-8"))
    try:
        fs = FinancialStatement.from_dict(data)
    except Exception as e:
        print(f"❌ 파싱 실패: {e}")
        return 2

    print(f"Company: {fs.company.name}")
    print(f"Years  : {fs.years()}")
    print()

    result = validate(fs)
    print(result.report())

    if args.ratios:
        stmt = fs.get_statement(args.ratios)
        if not stmt:
            print(f"\n[{args.ratios}] 연도 데이터 없음")
            return 1
        prior = fs.get_statement(args.ratios - 1)
        ratios = calculate_ratios(stmt, prior)

        print(f"\n═══ {args.ratios}년 재무비율 ═══")
        LABELS = {
            "501": ("성장성", "총자산증가율", "%"),
            "502": ("성장성", "유형자산증가율", "%"),
            "505": ("성장성", "자기자본증가율", "%"),
            "506": ("성장성", "매출액증가율", "%"),
            "602": ("수익성", "ROA", "%"),
            "606": ("수익성", "ROE", "%"),
            "610": ("수익성", "매출액순이익률", "%"),
            "611": ("수익성", "영업이익률", "%"),
            "612": ("수익성", "매출원가율", "%"),
            "615": ("수익성", "R&D/매출", "%"),
            "625": ("수익성", "금융비용/매출", "%"),
            "627": ("수익성", "이자보상비율", "%"),
            "701": ("안정성", "자기자본비율", "%"),
            "702": ("안정성", "유동비율", "%"),
            "703": ("안정성", "당좌비율", "%"),
            "707": ("안정성", "부채비율", "%"),
            "710": ("안정성", "차입금의존도", "%"),
            "801": ("활동성", "총자산회전율", "회"),
            "806": ("활동성", "재고자산회전율", "회"),
            "808": ("활동성", "매출채권회전율", "회"),
            "809": ("활동성", "매입채무회전율", "회"),
        }
        cur_cat = None
        for code, (cat, name, unit) in LABELS.items():
            if cat != cur_cat:
                print(f"\n[{cat}]")
                cur_cat = cat
            val = ratios.get(code)
            val_str = f"{val:>10.2f} {unit}" if val is not None else "        N/A"
            print(f"  {code} {name:<15s} {val_str}")

    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
