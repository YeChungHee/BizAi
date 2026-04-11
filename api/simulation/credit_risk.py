"""
신용 리스크 평가 모듈.

등급 + 재무비율 + 정성 지표를 종합하여
기업의 부도확률(PD)·손실률(LGD)·Altman Z' Score를 산출합니다.

파이프라인 위치:
    grade_calculator → ★credit_risk★ → payment_pricer
                                      → margin_simulator

사용:
    # 방법1: Statement 객체로부터 (기존 파이프라인)
    profile = assess_from_statements(grade, stmt_2025, stmt_2024, red_flags)

    # 방법2: 수동 입력 (PDF 리포트 등)
    profile = assess_credit_risk(
        grade="BB-",
        total_assets=1_908_325, current_assets=1_096_004, ...
    )

    print(profile.adjusted_pd_pct)    # 19.0%
    print(profile.lgd_pct)            # 70.0%
    print(profile.z_prime_score)      # 1.024
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from schema.models import Statement
    from analysis.grade_calculator import Grade
    from analysis.red_flag_detector import RedFlag


# ─────────────────────────────────────────────────────────────
# 등급 → 기본 부도율 테이블 (1년, %)
# 국내 신평사 통계 기반 근사값
# ─────────────────────────────────────────────────────────────

GRADE_BASE_PD: dict[str, float] = {
    "AAA": 0.03, "AA": 0.10, "A": 0.30,
    "BBB": 1.00, "BB": 3.00, "BB-": 4.50,
    "B+": 5.00,  "B": 7.00,  "B-": 9.00,
    "CCC": 12.0, "CC": 18.0, "C": 25.0, "D": 50.0,
}


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class PDFactor:
    """부도확률 조정 인자 하나."""
    name: str
    delta_pct: float   # 조정량 (%p, 양수=위험 증가)
    reason: str


@dataclass
class CreditRiskProfile:
    """신용 리스크 프로필 — PD/LGD/Z-score + 주요 지표 일괄 보유."""

    # ── 등급 ──
    grade: str
    grade_score: float          # 0~100 (grade_calculator 점수)

    # ── 부도 확률 (PD) ──
    base_pd_pct: float          # 등급 기본 PD (%)
    adjusted_pd_pct: float      # 조정 후 최종 PD (%)
    pd_factors: list[PDFactor] = field(default_factory=list)

    # ── Altman Z' Score ──
    z_prime_score: float | None = None
    z_zone: str = "n/a"        # "safe" | "grey" | "distress" | "n/a"

    # ── LGD ──
    lgd_pct: float = 60.0      # Loss Given Default (%)
    lgd_basis: str = ""

    # ── 기대손실 ──
    @property
    def annual_el_pct(self) -> float:
        """연간 기대손실 = PD × LGD."""
        return round(self.adjusted_pd_pct * self.lgd_pct / 100, 2)

    # ── 주요 재무지표 (quick reference) ──
    current_ratio: float | None = None
    quick_ratio: float | None = None
    debt_ratio_pct: float | None = None
    cash_ratio_pct: float | None = None       # 현금/유동부채 (%)
    loan_dependency_pct: float | None = None  # 차입금의존도 (%)
    ar_days: float | None = None              # 매출채권 회전일
    op_margin_pct: float | None = None        # 영업이익률 (%)
    revenue_growth_pct: float | None = None   # 매출 성장률 (%)

    # ── 컨텍스트 ──
    cash_available: float | None = None       # 즉시 가용 현금 (원)
    annual_revenue: float | None = None       # 연매출 (원)
    red_flag_count: int = 0

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "grade_score": self.grade_score,
            "base_pd_pct": self.base_pd_pct,
            "adjusted_pd_pct": self.adjusted_pd_pct,
            "annual_el_pct": self.annual_el_pct,
            "z_prime_score": self.z_prime_score,
            "z_zone": self.z_zone,
            "lgd_pct": self.lgd_pct,
            "lgd_basis": self.lgd_basis,
            "pd_factors": [
                {"name": f.name, "delta": f.delta_pct, "reason": f.reason}
                for f in self.pd_factors
            ],
            "metrics": {
                "current_ratio": self.current_ratio,
                "debt_ratio_pct": self.debt_ratio_pct,
                "cash_ratio_pct": self.cash_ratio_pct,
                "loan_dependency_pct": self.loan_dependency_pct,
                "ar_days": self.ar_days,
                "op_margin_pct": self.op_margin_pct,
                "revenue_growth_pct": self.revenue_growth_pct,
            },
        }

    def summary(self) -> str:
        lines = [
            f"═══ 신용 리스크 프로필 ({self.grade}) ═══",
            f"  PD: {self.adjusted_pd_pct:.1f}% (기본 {self.base_pd_pct:.1f}%)",
            f"  LGD: {self.lgd_pct:.0f}% ({self.lgd_basis})",
            f"  연간 EL: {self.annual_el_pct:.2f}%",
        ]
        if self.z_prime_score is not None:
            lines.append(f"  Altman Z': {self.z_prime_score:.3f} ({self.z_zone})")
        if self.pd_factors:
            lines.append("  ── PD 조정 인자 ──")
            for f in self.pd_factors:
                sign = "+" if f.delta_pct > 0 else ""
                lines.append(f"  {sign}{f.delta_pct:.1f}%p  {f.name}")
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────
# Altman Z' Score (비상장 기업 수정 모형)
# ─────────────────────────────────────────────────────────────

def _altman_z_prime(
    current_assets: float,
    current_liabilities: float,
    total_assets: float,
    retained_earnings: float,
    operating_profit: float,
    total_equity: float,
    total_liabilities: float,
    revenue: float,
) -> tuple[float, str]:
    """
    Altman Z'-Score (비상장 수정 모형) 계산.

    Z' = 0.717·X1 + 0.847·X2 + 3.107·X3 + 0.420·X4 + 0.998·X5

    Returns: (score, zone)
        zone: "safe" (>2.9) | "grey" (1.23~2.9) | "distress" (<1.23)
    """
    if total_assets <= 0 or total_liabilities <= 0:
        return 0.0, "distress"

    X1 = (current_assets - current_liabilities) / total_assets
    X2 = retained_earnings / total_assets
    X3 = operating_profit / total_assets
    X4 = total_equity / total_liabilities
    X5 = revenue / total_assets

    z = 0.717*X1 + 0.847*X2 + 3.107*X3 + 0.420*X4 + 0.998*X5

    if z > 2.90:
        zone = "safe"
    elif z > 1.23:
        zone = "grey"
    else:
        zone = "distress"

    return round(z, 3), zone


# ─────────────────────────────────────────────────────────────
# PD 조정 인자 엔진
# ─────────────────────────────────────────────────────────────

def _compute_pd_factors(
    z_score: float | None,
    z_zone: str,
    current_ratio: float | None,
    debt_ratio_pct: float | None,
    cash_ratio_pct: float | None,
    loan_dependency_pct: float | None,
    op_margin_pct: float | None,
    revenue_growth_pct: float | None,
    ar_days: float | None,
    retained_positive: bool,
    cash_flow_grade: str | None,
    company_age_years: float | None,
    employee_count: int | None,
    consecutive_profit_years: int,
    consecutive_loss_years: int,
    has_advance_payment: bool,
    st_debt_to_cl_pct: float | None,
) -> list[PDFactor]:
    """재무·정성 지표 기반 PD 조정 인자를 생성."""
    factors: list[PDFactor] = []

    def _add(name: str, delta: float, reason: str):
        if delta != 0:
            factors.append(PDFactor(name, delta, reason))

    # ── 위험 증가 (양수) ─────────────────────────────────
    # 현금흐름등급
    cf_map = {"매우불량": 5.0, "불량": 3.0, "보통": 0.0, "양호": -1.0, "우량": -2.0}
    if cash_flow_grade and cash_flow_grade in cf_map:
        d = cf_map[cash_flow_grade]
        if d != 0:
            _add(f"현금흐름등급 '{cash_flow_grade}'", d,
                 f"현금흐름 등급에 따른 지급능력 {'악화' if d > 0 else '양호'}")

    # Z-Score
    if z_zone == "distress":
        _add("Altman Z' 위험지대", 4.0,
             f"Z'={z_score:.2f} < 1.23: 부실 예측 모형 위험 판정")
    elif z_zone == "grey":
        _add("Altman Z' 회색지대", 1.0,
             f"Z'={z_score:.2f}: 불확실 구간")

    # 유동비율
    if current_ratio is not None and current_ratio < 1.0:
        _add("유동비율 1.0 미달", 2.0,
             f"유동비율 {current_ratio:.2f}배: 단기 부채 상환 능력 부족")

    # 부채비율
    if debt_ratio_pct is not None and debt_ratio_pct > 200:
        delta = 2.0 if debt_ratio_pct > 300 else 1.0
        _add(f"부채비율 {debt_ratio_pct:.0f}%", delta,
             "과도한 레버리지")

    # 차입금의존도
    if loan_dependency_pct is not None and loan_dependency_pct > 50:
        _add(f"차입금의존도 {loan_dependency_pct:.0f}%", 2.0,
             "총자산 대비 차입금 과다")

    # 현금비율
    if cash_ratio_pct is not None and cash_ratio_pct < 10:
        _add(f"현금/유동부채 {cash_ratio_pct:.1f}%", 2.0,
             "즉시 현금 지급력 극히 낮음")

    # 단기차입금 집중
    if st_debt_to_cl_pct is not None and st_debt_to_cl_pct > 80:
        _add(f"단기차입금 유동부채 대비 {st_debt_to_cl_pct:.0f}%", 2.0,
             "차입금 만기 집중 리스크")

    # 연속 영업적자
    if consecutive_loss_years >= 3:
        _add(f"영업적자 {consecutive_loss_years}년 연속", 3.0,
             "본업 수익 창출 불능 지속")
    elif consecutive_loss_years >= 2:
        _add(f"영업적자 {consecutive_loss_years}년 연속", 2.0,
             "영업 수익성 회복 미흡")

    # 업력
    if company_age_years is not None and company_age_years < 3:
        _add(f"업력 {company_age_years:.0f}년", 1.0,
             "짧은 업력, 검증 부족")

    # 소규모
    if employee_count is not None and employee_count < 10:
        _add(f"직원 {employee_count}명", 1.0,
             "Key-man 리스크, 조직 안정성 취약")

    # 매출채권 회전 느림
    if ar_days is not None and ar_days > 90:
        _add(f"매출채권 회전일 {ar_days:.0f}일", 1.0,
             "채권 회수 속도 느림 (>90일)")

    # 선납금 없음
    if not has_advance_payment:
        _add("선납금 없음", 2.0,
             "위험 경감 수단 부재, 전액 신용 노출")

    # ── 위험 감소 (음수) ────────────────────────────────
    # 연속 흑자
    if consecutive_profit_years >= 3:
        _add(f"순이익 {consecutive_profit_years}년 연속 흑자", -3.0,
             "지속적 이익 창출 검증됨")
    elif consecutive_profit_years >= 2:
        _add(f"순이익 {consecutive_profit_years}년 연속 흑자", -1.0,
             "이익 창출 추세")

    # 매출 성장
    if revenue_growth_pct is not None:
        if revenue_growth_pct > 100:
            _add(f"매출 성장률 {revenue_growth_pct:.0f}%", -2.0,
                 "폭발적 성장세")
        elif revenue_growth_pct > 20:
            _add(f"매출 성장률 {revenue_growth_pct:.0f}%", -1.0,
                 "양호한 성장세")

    # 영업이익률
    if op_margin_pct is not None and op_margin_pct > 5:
        _add(f"영업이익률 {op_margin_pct:.1f}%", -1.0,
             "양호한 본업 수익성")

    # 이익잉여금
    if retained_positive:
        _add("이익잉여금 양수", -1.0,
             "순수 누적 이익으로 자본 축적")

    return factors


# ─────────────────────────────────────────────────────────────
# LGD 추정
# ─────────────────────────────────────────────────────────────

def _estimate_lgd(
    has_advance_payment: bool,
    advance_payment_pct: float,
    has_collateral: bool,
    retained_positive: bool,
    op_margin_positive: bool,
    cash_flow_grade: str | None,
    debt_ratio_pct: float | None,
) -> tuple[float, str]:
    """LGD(%) 추정. Returns (lgd_pct, basis_description)."""
    lgd = 60.0  # 무담보 거래채권 기본 LGD
    parts = ["기본 60%"]

    # 선납금 효과
    if has_advance_payment and advance_payment_pct > 0:
        reduction = advance_payment_pct * 0.8  # 선납금의 80% 효과
        lgd -= reduction
        parts.append(f"선납금 {advance_payment_pct:.0f}% → -{reduction:.0f}%p")

    # 담보
    if has_collateral:
        lgd -= 20.0
        parts.append("담보 → -20%p")

    # 이익잉여금 + 영업이익 양수 → 자산 회수 가능성 높음
    if retained_positive and op_margin_positive:
        lgd -= 10.0
        parts.append("이익잉여금+영업흑자 → -10%p")

    # 현금흐름 매우불량 → 회수 어려움
    if cash_flow_grade == "매우불량":
        lgd += 10.0
        parts.append("현금흐름 매우불량 → +10%p")

    # 과다 부채 → 잔여 자산 분배에서 후순위
    if debt_ratio_pct is not None and debt_ratio_pct > 300:
        lgd += 10.0
        parts.append(f"부채비율 {debt_ratio_pct:.0f}% → +10%p")

    lgd = max(20.0, min(90.0, lgd))
    return round(lgd, 1), " | ".join(parts)


# ─────────────────────────────────────────────────────────────
# 메인 평가 함수 (수동 입력용)
# ─────────────────────────────────────────────────────────────

def assess_credit_risk(
    grade: str,
    grade_score: float = 50.0,
    # ── BS ──
    total_assets: float | None = None,
    current_assets: float | None = None,
    current_liabilities: float | None = None,
    total_liabilities: float | None = None,
    total_equity: float | None = None,
    cash_and_equivalents: float | None = None,
    short_term_deposits: float | None = None,
    trade_receivables: float | None = None,
    trade_receivables_prior: float | None = None,
    retained_earnings: float | None = None,
    short_term_debt: float | None = None,
    long_term_debt: float | None = None,
    inventories: float | None = None,
    # ── IS ──
    revenue: float | None = None,
    revenue_prior: float | None = None,
    operating_profit: float | None = None,
    net_profit: float | None = None,
    interest_expense: float | None = None,
    # ── 정성 ──
    cash_flow_grade: str | None = None,
    company_age_years: float | None = None,
    employee_count: int | None = None,
    red_flag_count: int = 0,
    consecutive_profit_years: int = 0,
    consecutive_loss_years: int = 0,
    # ── 거래 조건 ──
    has_advance_payment: bool = False,
    advance_payment_pct: float = 0.0,
    has_collateral: bool = False,
) -> CreditRiskProfile:
    """
    재무 데이터 + 정성 지표 → 신용 리스크 프로필.

    모든 금액은 동일 통화·단위 (천원 등). 내부 비율 계산은 상대값이므로
    단위 자체는 중요하지 않되 일관성이 필요합니다.
    """
    # ── 비율 계산 ─────────────────────────────────────────
    current_ratio = None
    if current_assets and current_liabilities and current_liabilities > 0:
        current_ratio = round(current_assets / current_liabilities, 4)

    quick_ratio = None
    if current_assets and inventories is not None and current_liabilities and current_liabilities > 0:
        quick_ratio = round((current_assets - inventories) / current_liabilities, 4)

    debt_ratio_pct = None
    if total_liabilities and total_equity and total_equity > 0:
        debt_ratio_pct = round(total_liabilities / total_equity * 100, 1)

    cash_ratio_pct = None
    cash_avail = (cash_and_equivalents or 0) + (short_term_deposits or 0)
    if cash_avail > 0 and current_liabilities and current_liabilities > 0:
        cash_ratio_pct = round(cash_avail / current_liabilities * 100, 1)

    total_debt = (short_term_debt or 0) + (long_term_debt or 0)
    loan_dep_pct = None
    if total_debt > 0 and total_assets and total_assets > 0:
        loan_dep_pct = round(total_debt / total_assets * 100, 1)

    ar_days = None
    if trade_receivables and revenue and revenue > 0:
        if trade_receivables_prior:
            avg_ar = (trade_receivables + trade_receivables_prior) / 2
        else:
            avg_ar = trade_receivables
        ar_days = round(365 / (revenue / avg_ar), 0)

    op_margin_pct = None
    if operating_profit is not None and revenue and revenue > 0:
        op_margin_pct = round(operating_profit / revenue * 100, 1)

    rev_growth_pct = None
    if revenue and revenue_prior and revenue_prior > 0:
        rev_growth_pct = round((revenue - revenue_prior) / revenue_prior * 100, 1)

    st_debt_to_cl_pct = None
    if short_term_debt and current_liabilities and current_liabilities > 0:
        st_debt_to_cl_pct = round(short_term_debt / current_liabilities * 100, 1)

    retained_positive = (retained_earnings is not None and retained_earnings > 0)

    # ── Altman Z' Score ───────────────────────────────────
    z_score, z_zone = None, "n/a"
    if all(v is not None for v in [
        current_assets, current_liabilities, total_assets,
        total_liabilities, total_equity, revenue, operating_profit,
    ]) and retained_earnings is not None:
        z_score, z_zone = _altman_z_prime(
            current_assets=current_assets,
            current_liabilities=current_liabilities,
            total_assets=total_assets,
            retained_earnings=retained_earnings,
            operating_profit=operating_profit,
            total_equity=total_equity,
            total_liabilities=total_liabilities,
            revenue=revenue,
        )

    # ── Base PD ───────────────────────────────────────────
    base_pd = GRADE_BASE_PD.get(grade, 10.0)

    # ── PD Factors ────────────────────────────────────────
    pd_factors = _compute_pd_factors(
        z_score=z_score, z_zone=z_zone,
        current_ratio=current_ratio,
        debt_ratio_pct=debt_ratio_pct,
        cash_ratio_pct=cash_ratio_pct,
        loan_dependency_pct=loan_dep_pct,
        op_margin_pct=op_margin_pct,
        revenue_growth_pct=rev_growth_pct,
        ar_days=ar_days,
        retained_positive=retained_positive,
        cash_flow_grade=cash_flow_grade,
        company_age_years=company_age_years,
        employee_count=employee_count,
        consecutive_profit_years=consecutive_profit_years,
        consecutive_loss_years=consecutive_loss_years,
        has_advance_payment=has_advance_payment,
        st_debt_to_cl_pct=st_debt_to_cl_pct,
    )

    total_adj = sum(f.delta_pct for f in pd_factors)
    adjusted_pd = max(0.5, min(60.0, round(base_pd + total_adj, 1)))

    # ── LGD ───────────────────────────────────────────────
    lgd, lgd_basis = _estimate_lgd(
        has_advance_payment=has_advance_payment,
        advance_payment_pct=advance_payment_pct,
        has_collateral=has_collateral,
        retained_positive=retained_positive,
        op_margin_positive=(op_margin_pct is not None and op_margin_pct > 0),
        cash_flow_grade=cash_flow_grade,
        debt_ratio_pct=debt_ratio_pct,
    )

    return CreditRiskProfile(
        grade=grade,
        grade_score=grade_score,
        base_pd_pct=base_pd,
        adjusted_pd_pct=adjusted_pd,
        pd_factors=pd_factors,
        z_prime_score=z_score,
        z_zone=z_zone,
        lgd_pct=lgd,
        lgd_basis=lgd_basis,
        current_ratio=current_ratio,
        quick_ratio=quick_ratio,
        debt_ratio_pct=debt_ratio_pct,
        cash_ratio_pct=cash_ratio_pct,
        loan_dependency_pct=loan_dep_pct,
        ar_days=ar_days,
        op_margin_pct=op_margin_pct,
        revenue_growth_pct=rev_growth_pct,
        cash_available=cash_avail if cash_avail > 0 else None,
        annual_revenue=revenue,
        red_flag_count=red_flag_count,
    )


# ─────────────────────────────────────────────────────────────
# Statement 객체 기반 래퍼 (기존 파이프라인 연동)
# ─────────────────────────────────────────────────────────────

def assess_from_statements(
    grade: "Grade",
    current: "Statement",
    prior: "Statement | None" = None,
    red_flags: "list[RedFlag] | None" = None,
    cash_flow_grade: str | None = None,
    company_age_years: float | None = None,
    employee_count: int | None = None,
    consecutive_profit_years: int = 0,
    consecutive_loss_years: int = 0,
    has_advance_payment: bool = False,
    advance_payment_pct: float = 0.0,
    has_collateral: bool = False,
) -> CreditRiskProfile:
    """
    기존 파이프라인의 Grade + Statement → CreditRiskProfile.

    grade_calculator, red_flag_detector 결과를 그대로 받아 처리합니다.
    """
    bs = current.balance_sheet
    is_ = current.income_statement
    p_bs = prior.balance_sheet if prior else None

    return assess_credit_risk(
        grade=grade.grade,
        grade_score=grade.total_score,
        total_assets=bs.total_assets,
        current_assets=bs.current_assets,
        current_liabilities=bs.current_liabilities,
        total_liabilities=bs.total_liabilities,
        total_equity=bs.total_equity,
        cash_and_equivalents=bs.cash_and_equivalents,
        short_term_deposits=bs.short_term_investments,
        trade_receivables=bs.trade_receivables,
        trade_receivables_prior=p_bs.trade_receivables if p_bs else None,
        retained_earnings=bs.retained_earnings,
        short_term_debt=bs.short_term_debt,
        long_term_debt=bs.long_term_debt,
        inventories=bs.inventories,
        revenue=is_.revenue,
        revenue_prior=prior.income_statement.revenue if prior else None,
        operating_profit=is_.operating_profit,
        net_profit=is_.net_profit,
        interest_expense=is_.interest_expense or is_.finance_cost,
        cash_flow_grade=cash_flow_grade,
        company_age_years=company_age_years,
        employee_count=employee_count,
        red_flag_count=len(red_flags) if red_flags else 0,
        consecutive_profit_years=consecutive_profit_years,
        consecutive_loss_years=consecutive_loss_years,
        has_advance_payment=has_advance_payment,
        advance_payment_pct=advance_payment_pct,
        has_collateral=has_collateral,
    )
