"""
표준 재무제표 Python dataclass 모델.

JSON Schema (financial_statement.schema.json) 와 1:1 대응.
from_dict / to_dict 로 파이썬 객체 ↔ JSON 변환 가능.

사용:
    from schema.models import FinancialStatement

    with open("company.json") as f:
        data = json.load(f)
    fs = FinancialStatement.from_dict(data)

    stmt = fs.get_statement(year=2024)
    print(stmt.balance_sheet.total_assets)
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict, fields
from datetime import datetime
from typing import Any, Optional

SCHEMA_VERSION = "1.0"


# ─────────────────────────────────────────────────────────────
# 내부 유틸
# ─────────────────────────────────────────────────────────────

def _filter_kwargs(cls: type, data: dict) -> dict:
    """dataclass 필드만 남기고 나머지 무시 (forward compatibility)."""
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in data.items() if k in valid}


def _drop_none(d: dict) -> dict:
    """None 필드를 제거한 dict (to_dict에서 사용)."""
    return {k: v for k, v in d.items() if v is not None and v != []}


# ─────────────────────────────────────────────────────────────
# Company
# ─────────────────────────────────────────────────────────────

@dataclass
class Company:
    name: str
    business_no: Optional[str] = None
    corporate_no: Optional[str] = None
    stock_code: Optional[str] = None
    dart_code: Optional[str] = None
    listed: bool = False

    ksic_code: Optional[str] = None
    ksic_name: Optional[str] = None
    size_code: Optional[str] = None
    size_label: Optional[str] = None

    ceo: Optional[str] = None
    established: Optional[str] = None
    employees: Optional[int] = None
    address: Optional[str] = None
    website: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Company":
        return cls(**_filter_kwargs(cls, d))


# ─────────────────────────────────────────────────────────────
# BalanceSheet
# ─────────────────────────────────────────────────────────────

@dataclass
class BalanceSheet:
    # Tier 1 (필수)
    current_assets: float
    non_current_assets: float
    total_assets: float
    current_liabilities: float
    non_current_liabilities: float
    total_liabilities: float
    total_equity: float

    # Tier 2 (권장)
    cash_and_equivalents: Optional[float] = None
    short_term_investments: Optional[float] = None
    trade_receivables: Optional[float] = None
    other_receivables: Optional[float] = None
    inventories: Optional[float] = None
    prepaid_expenses: Optional[float] = None

    ppe: Optional[float] = None
    intangibles: Optional[float] = None
    investments: Optional[float] = None

    short_term_debt: Optional[float] = None
    current_portion_of_ltd: Optional[float] = None
    long_term_debt: Optional[float] = None
    trade_payables: Optional[float] = None
    lease_liabilities: Optional[float] = None

    paid_in_capital: Optional[float] = None
    capital_surplus: Optional[float] = None
    retained_earnings: Optional[float] = None
    treasury_stock: Optional[float] = None
    non_controlling_interest: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "BalanceSheet":
        return cls(**_filter_kwargs(cls, d))

    @property
    def total_debt(self) -> Optional[float]:
        """단기+유동성장기+장기 차입금 합계. 하나라도 있으면 나머지는 0으로 간주."""
        parts = [self.short_term_debt, self.current_portion_of_ltd,
                 self.long_term_debt]
        if all(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)


# ─────────────────────────────────────────────────────────────
# IncomeStatement
# ─────────────────────────────────────────────────────────────

@dataclass
class IncomeStatement:
    # Tier 1
    revenue: float
    operating_profit: float
    profit_before_tax: float
    net_profit: float

    # Tier 2
    cost_of_sales: Optional[float] = None
    gross_profit: Optional[float] = None
    sga: Optional[float] = None

    other_income: Optional[float] = None
    other_expense: Optional[float] = None
    finance_income: Optional[float] = None
    finance_cost: Optional[float] = None
    interest_expense: Optional[float] = None

    income_tax: Optional[float] = None

    depreciation: Optional[float] = None
    amortization: Optional[float] = None

    rnd_expense: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "IncomeStatement":
        return cls(**_filter_kwargs(cls, d))

    @property
    def ebitda(self) -> Optional[float]:
        """영업이익 + 감가 + 상각."""
        if self.depreciation is None and self.amortization is None:
            return None
        dep = self.depreciation or 0
        amt = self.amortization or 0
        return self.operating_profit + dep + amt


# ─────────────────────────────────────────────────────────────
# CashFlow
# ─────────────────────────────────────────────────────────────

@dataclass
class CashFlow:
    operating_cf: float
    investing_cf: float
    financing_cf: float

    net_cf: Optional[float] = None
    fx_effect: Optional[float] = None

    capex: Optional[float] = None
    depreciation_cf: Optional[float] = None

    interest_paid: Optional[float] = None
    dividends_paid: Optional[float] = None
    tax_paid: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "CashFlow":
        return cls(**_filter_kwargs(cls, d))

    @property
    def free_cash_flow(self) -> Optional[float]:
        """영업CF - CAPEX."""
        if self.capex is None:
            return None
        return self.operating_cf - abs(self.capex)


# ─────────────────────────────────────────────────────────────
# Audit + Quality
# ─────────────────────────────────────────────────────────────

@dataclass
class AuditInfo:
    opinion: Optional[str] = None
    auditor: Optional[str] = None
    report_date: Optional[str] = None
    going_concern_doubt: Optional[bool] = None

    @classmethod
    def from_dict(cls, d: dict) -> "AuditInfo":
        return cls(**_filter_kwargs(cls, d or {}))


@dataclass
class Quality:
    source: str  # pdf | dart | manual | excel
    source_file: Optional[str] = None
    extraction_confidence: Optional[float] = None
    missing_fields: list[str] = field(default_factory=list)
    low_confidence_fields: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    extracted_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Quality":
        return cls(**_filter_kwargs(cls, d))


# ─────────────────────────────────────────────────────────────
# Statement (단일 회계기간)
# ─────────────────────────────────────────────────────────────

@dataclass
class Statement:
    fiscal_year: int
    period_end: str
    report_type: str           # consolidated | separate | unknown
    accounting_standard: str   # K-IFRS | K-GAAP | unknown
    currency: str
    unit: str
    balance_sheet: BalanceSheet
    income_statement: IncomeStatement
    cash_flow: CashFlow
    quality: Quality

    period_start: Optional[str] = None
    audit: Optional[AuditInfo] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Statement":
        return cls(
            fiscal_year=d["fiscal_year"],
            period_end=d["period_end"],
            period_start=d.get("period_start"),
            report_type=d.get("report_type", "unknown"),
            accounting_standard=d.get("accounting_standard", "unknown"),
            currency=d.get("currency", "KRW"),
            unit=d.get("unit", "원"),
            balance_sheet=BalanceSheet.from_dict(d["balance_sheet"]),
            income_statement=IncomeStatement.from_dict(d["income_statement"]),
            cash_flow=CashFlow.from_dict(d["cash_flow"]),
            audit=AuditInfo.from_dict(d["audit"]) if d.get("audit") else None,
            quality=Quality.from_dict(d["quality"]),
        )

    def to_dict(self) -> dict:
        return _drop_none({
            "fiscal_year": self.fiscal_year,
            "period_end": self.period_end,
            "period_start": self.period_start,
            "report_type": self.report_type,
            "accounting_standard": self.accounting_standard,
            "currency": self.currency,
            "unit": self.unit,
            "audit": _drop_none(asdict(self.audit)) if self.audit else None,
            "balance_sheet": _drop_none(asdict(self.balance_sheet)),
            "income_statement": _drop_none(asdict(self.income_statement)),
            "cash_flow": _drop_none(asdict(self.cash_flow)),
            "quality": _drop_none(asdict(self.quality)),
        })


# ─────────────────────────────────────────────────────────────
# FinancialStatement (최상위 envelope)
# ─────────────────────────────────────────────────────────────

@dataclass
class FinancialStatement:
    company: Company
    statements: list[Statement]
    schema_version: str = SCHEMA_VERSION
    generated_at: Optional[str] = None

    @classmethod
    def from_dict(cls, d: dict) -> "FinancialStatement":
        if d.get("schema_version") != SCHEMA_VERSION:
            raise ValueError(
                f"Unsupported schema_version: {d.get('schema_version')}, "
                f"expected {SCHEMA_VERSION}"
            )
        return cls(
            schema_version=d["schema_version"],
            generated_at=d.get("generated_at"),
            company=Company.from_dict(d["company"]),
            statements=[Statement.from_dict(s) for s in d["statements"]],
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "generated_at": self.generated_at or datetime.now().isoformat(timespec="seconds"),
            "company": _drop_none(asdict(self.company)),
            "statements": [s.to_dict() for s in self.statements],
        }

    # ─── 편의 메서드 ─────────────────────────────────

    def get_statement(self, year: int) -> Optional[Statement]:
        for s in self.statements:
            if s.fiscal_year == year:
                return s
        return None

    def years(self) -> list[int]:
        return sorted({s.fiscal_year for s in self.statements})

    def latest(self) -> Statement:
        return max(self.statements, key=lambda s: s.fiscal_year)
