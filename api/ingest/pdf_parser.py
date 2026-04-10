"""
재무제표 PDF 파서.

pdfplumber를 사용하여 한국 재무제표 PDF에서
재무상태표·손익계산서를 추출하고 표준 FinancialStatement로 변환합니다.

핵심 전략:
    이 PDF의 테이블은 5열 구조:
        셀[0] = 계정과목 (40줄, 합계+상세 혼합)
        셀[1] = 당기 상세 금액 (하위 계정만, 약 24줄)
        셀[2] = 당기 합계 금액 (상위 합계만, 약 13줄)
        셀[3] = 전기 상세 금액
        셀[4] = 전기 합계 금액

    계정과목의 레벨(합계/상세)에 따라 다른 금액 열과 매핑해야 함.
    합계 행(Ⅰ. Ⅱ. (1) 접두사 또는 '총계' 포함) → 셀[2]/셀[4]
    상세 행(개별 계정) → 셀[1]/셀[3]

사용:
    from ingest.pdf_parser import parse_financial_pdf

    fs = parse_financial_pdf("/path/to/재무제표.pdf")
    stmt = fs.get_statement(2024)
    print(stmt.income_statement.revenue)
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

import pdfplumber

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "schema"))

from models import (
    FinancialStatement, Statement, Company, Quality,
    BalanceSheet, IncomeStatement, CashFlow, AuditInfo,
)


# ─────────────────────────────────────────────────────────────
# 계정과목 → 스키마 필드 매핑
# ─────────────────────────────────────────────────────────────

BALANCE_SHEET_MAP: dict[str, str] = {
    # Tier 1 (필수)
    "유동자산":         "current_assets",
    "비유동자산":       "non_current_assets",
    "자산총계":         "total_assets",
    "유동부채":         "current_liabilities",
    "비유동부채":       "non_current_liabilities",
    "부채총계":         "total_liabilities",
    "자본총계":         "total_equity",
    "부채및자본총계":   "_bs_check",  # 검증용
    # Tier 2 — 자산
    "현금및현금성자산": "cash_and_equivalents",
    "단기금융상품":     "short_term_investments",
    "매출채권":         "trade_receivables",
    "미수금":           "other_receivables",
    "재고자산":         "inventories",
    "선급비용":         "prepaid_expenses",
    "유형자산":         "ppe",
    "무형자산":         "intangibles",
    "투자자산":         "investments",
    "매도가능증권":     "investments",
    "지분법적용투자주식": "investments",
    # Tier 2 — 부채
    "단기차입금":       "short_term_debt",
    "유동성장기부채":   "current_portion_of_ltd",
    "장기차입금":       "long_term_debt",
    "매입채무":         "trade_payables",
    # Tier 2 — 자본
    "자본금":           "paid_in_capital",
    "자본잉여금":       "capital_surplus",
    "주식발행초과금":   "capital_surplus",
    "이익잉여금":       "retained_earnings",
    "결손금":           "retained_earnings",
    "미처리결손금":     "retained_earnings",
}

INCOME_STATEMENT_MAP: dict[str, str] = {
    "매출액":           "revenue",
    "영업이익":         "operating_profit",
    "법인세차감전이익": "profit_before_tax",
    "법인세차감전순이익": "profit_before_tax",
    "당기순이익":       "net_profit",
    "당기순손실":       "net_profit",
    # Tier 2
    "매출원가":         "cost_of_sales",
    "매출총이익":       "gross_profit",
    "판매비와관리비":   "sga",
    "영업외수익":       "other_income",
    "영업외비용":       "other_expense",
    "이자수익":         "finance_income",
    "이자비용":         "interest_expense",
    "법인세등":         "income_tax",
    "법인세비용":       "income_tax",
    "감가상각비":       "depreciation",
    "무형고정자산상각": "amortization",
    "연구개발비":       "rnd_expense",
}


# ─────────────────────────────────────────────────────────────
# 텍스트 정규화
# ─────────────────────────────────────────────────────────────

def _normalize(text: str) -> str:
    """계정과목명 정규화: 공백·접두사 제거."""
    if not text:
        return ""
    s = re.sub(r'\s+', '', text)
    s = re.sub(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]+\.?', '', s)
    s = re.sub(r'^\(\d+\)', '', s)
    s = re.sub(r'^<|>$', '', s)
    return s.strip()


def _parse_amount(text: str) -> Optional[int]:
    """금액 문자열 → 정수."""
    if not text or not text.strip():
        return None
    s = text.strip()
    negative = s.startswith('(') and s.endswith(')')
    if negative:
        s = s[1:-1]
    s = re.sub(r'[,\s]', '', s)
    if not re.match(r'^-?\d+$', s):
        return None
    val = int(s)
    return -val if negative else val


def _classify_row(raw_account: str) -> str:
    """
    계정과목 행 분류.
    Returns:
        "header"  — 대제목 (자산/부채/자본). 금액 열 소모 안 함.
        "summary" — 합계 행 (Ⅰ./Ⅱ./(1)/(2)/총계). 합계 금액열.
        "detail"  — 개별 계정. 상세 금액열.
    """
    s = raw_account.strip()
    norm = _normalize(s)

    # 대제목: 금액 없는 섹션 헤더
    if norm in ('자산', '부채', '자본'):
        return "header"

    # 합계: Ⅰ. Ⅱ. 접두사
    if re.match(r'^[ⅠⅡⅢⅣⅤⅥⅦⅧⅨⅩ]', s):
        return "summary"
    # 합계: (1) (2) 접두사
    if re.match(r'^\(\d+\)', s):
        return "summary"
    # 합계: '총계' 포함
    if '총계' in norm:
        return "summary"

    return "detail"


# ─────────────────────────────────────────────────────────────
# 페이지 유형 감지
# ─────────────────────────────────────────────────────────────

def _detect_report_type(page_text: str) -> str:
    norm = re.sub(r'\s+', '', page_text[:300])
    if "재무상태표" in norm:
        return "balance_sheet"
    if "손익계산서" in norm or "포괄손익" in norm:
        return "income_statement"
    if "현금흐름표" in norm:
        return "cash_flow"
    if "결손금처리" in norm or "이익잉여금처분" in norm:
        return "disposition"
    if "시산표" in norm:
        return "trial_balance"
    return "unknown"


def _detect_unit(page_text: str) -> tuple[str, int]:
    norm = re.sub(r'\s+', '', page_text[:500])
    if "백만원" in norm:
        return "백만원", 1_000_000
    if "천원" in norm:
        return "천원", 1_000
    return "원", 1


def _extract_company_name(text: str) -> str:
    m = re.search(r'회\s*사\s*명\s*:\s*(.+?)(?:\(단위|\n)', text)
    if m:
        return re.sub(r'\s+', '', m.group(1).strip())
    return "미확인기업"


def _extract_fiscal_years(text: str) -> list[dict]:
    years = []
    # 패턴 1: "제 6기 2024년 12월 31일 현재" (재무상태표)
    for m in re.finditer(r'제\s*(\d+)\s*기\s*(\d{4})\s*년\s*(\d{1,2})\s*월\s*(\d{1,2})\s*일', text):
        years.append({
            "fiscal_year": int(m.group(2)),
            "period_end": f"{m.group(2)}-{int(m.group(3)):02d}-{int(m.group(4)):02d}",
        })
    # 패턴 2: "제6(당)기" 축약형
    for m in re.finditer(r'제\s*(\d+)\s*\(\s*당\s*\)\s*기\s*(\d{4})', text):
        years.append({"fiscal_year": int(m.group(2)), "period_end": f"{m.group(2)}-12-31"})
    for m in re.finditer(r'제\s*(\d+)\s*\(\s*전\s*\)\s*기\s*(\d{4})', text):
        years.append({"fiscal_year": int(m.group(2)), "period_end": f"{m.group(2)}-12-31"})
    # 중복 제거, 최신순 정렬
    seen = set()
    unique = []
    for y in years:
        if y["fiscal_year"] not in seen:
            seen.add(y["fiscal_year"])
            unique.append(y)
    return sorted(unique, key=lambda x: x["fiscal_year"], reverse=True)


# ─────────────────────────────────────────────────────────────
# 5열 병합 테이블 파서 (핵심)
# ─────────────────────────────────────────────────────────────

def _parse_5col_table(
    table_rows: list[list],
    field_map: dict[str, str],
    multiplier: int = 1,
) -> tuple[dict[str, int], dict[str, int]]:
    """
    5~6열 병합 테이블에서 당기/전기 데이터 추출.

    전략:
    1. 모든 데이터 행에서 계정과목/금액 셀을 열 위치 기반으로 수집
    2. 열 위치로 당기상세/당기합계/전기상세/전기합계 구분
    3. 수집된 리스트를 header/summary/detail 분류로 매핑
    """
    # Phase 1: 전체 행에서 계정과목과 금액을 열 위치별로 수집
    all_accounts: list[str] = []
    # 열 인덱스별 금액 수집 (열 위치가 핵심)
    col_amounts: dict[int, list[str]] = {}

    for row in table_rows:
        if not row:
            continue
        cells = [c or "" for c in row]
        if len(cells) < 2:
            continue

        # 계정과목 셀 (한글 많은 셀)
        acct_idx = -1
        for i, c in enumerate(cells):
            if not c.strip():
                continue
            hangul = len(re.findall(r'[가-힣]', c))
            if hangul > 5:
                acct_idx = i
                all_accounts.extend(c.split('\n'))
                break

        # 금액 셀들 (열 위치 보존)
        for i, c in enumerate(cells):
            if i == acct_idx or not c or not c.strip():
                continue
            if re.search(r'\d', c):
                if i not in col_amounts:
                    col_amounts[i] = []
                col_amounts[i].extend(c.split('\n'))

    if not all_accounts:
        return {}, {}

    # Phase 2: 열 위치 → 당기/전기 × 상세/합계 매핑
    # 정렬된 열 인덱스
    sorted_cols = sorted(col_amounts.keys())

    # 일반적 구조: 4열 = [당기상세, 당기합계, 전기상세, 전기합계]
    # 또는 2열 = [당기합계, 전기합계]
    # 핵심: 앞쪽 절반이 당기, 뒤쪽이 전기
    cur_detail: list[str] = []
    cur_total: list[str] = []
    pri_detail: list[str] = []
    pri_total: list[str] = []

    if len(sorted_cols) >= 4:
        cur_detail = col_amounts[sorted_cols[0]]
        cur_total = col_amounts[sorted_cols[1]]
        pri_detail = col_amounts[sorted_cols[2]]
        pri_total = col_amounts[sorted_cols[3]]
    elif len(sorted_cols) == 3:
        cur_detail = col_amounts[sorted_cols[0]]
        cur_total = col_amounts[sorted_cols[1]]
        pri_total = col_amounts[sorted_cols[2]]
    elif len(sorted_cols) == 2:
        # 2열: 각각이 상세+합계 혼합일 수 있음
        # 줄 수가 많은 쪽이 당기
        c0 = col_amounts[sorted_cols[0]]
        c1 = col_amounts[sorted_cols[1]]
        cur_total = c0
        pri_total = c1
    elif len(sorted_cols) == 1:
        cur_total = col_amounts[sorted_cols[0]]

    # Phase 3: 계정과목 분류 → 금액 매핑
    current_data: dict[str, int] = {}
    prior_data: dict[str, int] = {}

    di_cur = 0
    ti_cur = 0
    di_pri = 0
    ti_pri = 0

    for acct_raw in all_accounts:
        norm = _normalize(acct_raw)
        if not norm:
            continue

        row_type = _classify_row(acct_raw)
        if row_type == "header":
            continue

        field = field_map.get(norm)

        # 당기
        cur_amt = None
        if row_type == "summary":
            if ti_cur < len(cur_total):
                cur_amt = _parse_amount(cur_total[ti_cur])
                ti_cur += 1
        else:
            if di_cur < len(cur_detail):
                cur_amt = _parse_amount(cur_detail[di_cur])
                di_cur += 1

        # 전기
        pri_amt = None
        if row_type == "summary":
            if ti_pri < len(pri_total):
                pri_amt = _parse_amount(pri_total[ti_pri])
                ti_pri += 1
        else:
            if di_pri < len(pri_detail):
                pri_amt = _parse_amount(pri_detail[di_pri])
                di_pri += 1

        if not field or field.startswith('_'):
            continue

        if norm in ("결손금", "미처리결손금", "당기순손실"):
            if cur_amt is not None and cur_amt > 0:
                cur_amt = -cur_amt
            if pri_amt is not None and pri_amt > 0:
                pri_amt = -pri_amt

        if cur_amt is not None:
            current_data[field] = cur_amt * multiplier
        if pri_amt is not None:
            prior_data[field] = pri_amt * multiplier

    return current_data, prior_data


# ─────────────────────────────────────────────────────────────
# 빌더 유틸
# ─────────────────────────────────────────────────────────────

def _build_bs(data: dict) -> BalanceSheet:
    return BalanceSheet(
        current_assets=data.get("current_assets", 0),
        non_current_assets=data.get("non_current_assets", 0),
        total_assets=data.get("total_assets", 0),
        current_liabilities=data.get("current_liabilities", 0),
        non_current_liabilities=data.get("non_current_liabilities", 0),
        total_liabilities=data.get("total_liabilities", 0),
        total_equity=data.get("total_equity", 0),
        cash_and_equivalents=data.get("cash_and_equivalents"),
        short_term_investments=data.get("short_term_investments"),
        trade_receivables=data.get("trade_receivables"),
        other_receivables=data.get("other_receivables"),
        inventories=data.get("inventories"),
        prepaid_expenses=data.get("prepaid_expenses"),
        ppe=data.get("ppe"),
        intangibles=data.get("intangibles"),
        investments=data.get("investments"),
        short_term_debt=data.get("short_term_debt"),
        current_portion_of_ltd=data.get("current_portion_of_ltd"),
        long_term_debt=data.get("long_term_debt"),
        trade_payables=data.get("trade_payables"),
        paid_in_capital=data.get("paid_in_capital"),
        capital_surplus=data.get("capital_surplus"),
        retained_earnings=data.get("retained_earnings"),
        treasury_stock=data.get("treasury_stock"),
    )


def _build_is(data: dict) -> IncomeStatement:
    return IncomeStatement(
        revenue=data.get("revenue", 0),
        operating_profit=data.get("operating_profit", 0),
        profit_before_tax=data.get("profit_before_tax", 0),
        net_profit=data.get("net_profit", 0),
        cost_of_sales=data.get("cost_of_sales"),
        gross_profit=data.get("gross_profit"),
        sga=data.get("sga"),
        other_income=data.get("other_income"),
        other_expense=data.get("other_expense"),
        finance_income=data.get("finance_income"),
        interest_expense=data.get("interest_expense"),
        income_tax=data.get("income_tax"),
        depreciation=data.get("depreciation"),
        amortization=data.get("amortization"),
        rnd_expense=data.get("rnd_expense"),
    )


# ─────────────────────────────────────────────────────────────
# 메인 파서
# ─────────────────────────────────────────────────────────────

def parse_financial_pdf(pdf_path: str | Path) -> FinancialStatement:
    """
    재무제표 PDF → 표준 FinancialStatement 객체.

    Args:
        pdf_path: PDF 파일 경로

    Returns:
        FinancialStatement (당기 + 전기 Statement 포함)
    """
    pdf_path = Path(pdf_path)
    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pdfplumber.open(str(pdf_path)) as pdf:
        pages_info = []
        full_text = ""
        for page in pdf.pages:
            text = page.extract_text() or ""
            tables = page.extract_tables()
            rtype = _detect_report_type(text)
            pages_info.append({"text": text, "tables": tables, "type": rtype})
            full_text += text + "\n"

    # 기업정보
    company_name = _extract_company_name(full_text)
    fiscal_years = _extract_fiscal_years(full_text)
    if not fiscal_years:
        raise ValueError("회계연도를 추출할 수 없습니다.")

    cur_year = fiscal_years[0]
    pri_year = fiscal_years[1] if len(fiscal_years) > 1 else None
    unit_label, multiplier = _detect_unit(full_text)

    # 재무상태표 파싱
    bs_cur, bs_pri = {}, {}
    for pi in pages_info:
        if pi["type"] == "balance_sheet":
            for tbl in pi["tables"]:
                c, p = _parse_5col_table(tbl, BALANCE_SHEET_MAP, multiplier)
                bs_cur.update(c)
                bs_pri.update(p)

    # 손익계산서 파싱
    is_cur, is_pri = {}, {}
    for pi in pages_info:
        if pi["type"] == "income_statement":
            for tbl in pi["tables"]:
                c, p = _parse_5col_table(tbl, INCOME_STATEMENT_MAP, multiplier)
                is_cur.update(c)
                is_pri.update(p)

    # 현금흐름표 유무
    has_cf = any(pi["type"] == "cash_flow" for pi in pages_info)

    # Quality
    missing = []
    warnings = []
    bs_req = ["current_assets", "non_current_assets", "total_assets",
              "current_liabilities", "non_current_liabilities",
              "total_liabilities", "total_equity"]
    is_req = ["revenue", "operating_profit", "profit_before_tax", "net_profit"]

    for f in bs_req:
        if f not in bs_cur:
            missing.append(f"balance_sheet.{f}")
    for f in is_req:
        if f not in is_cur:
            missing.append(f"income_statement.{f}")
    if not has_cf:
        missing.append("cash_flow")
        warnings.append("현금흐름표 미포함")

    total_req = len(bs_req) + len(is_req)
    found = sum(1 for f in bs_req if f in bs_cur) + sum(1 for f in is_req if f in is_cur)
    confidence = round(found / total_req, 2) if total_req else 0.0

    # Statement 조립
    def _make_stmt(year_info, bs_data, is_data, is_primary):
        return Statement(
            fiscal_year=year_info["fiscal_year"],
            period_end=year_info["period_end"],
            period_start=f"{year_info['fiscal_year']}-01-01",
            report_type="separate",
            accounting_standard="K-GAAP",
            currency="KRW",
            unit="원",
            balance_sheet=_build_bs(bs_data),
            income_statement=_build_is(is_data),
            cash_flow=CashFlow(operating_cf=0, investing_cf=0, financing_cf=0),
            audit=None,
            quality=Quality(
                source="pdf",
                source_file=pdf_path.name,
                extraction_confidence=confidence,
                missing_fields=missing if is_primary else [],
                warnings=warnings if is_primary else [],
                extracted_at=datetime.now().isoformat(timespec="seconds"),
            ),
        )

    statements = [_make_stmt(cur_year, bs_cur, is_cur, True)]
    if pri_year and (bs_pri or is_pri):
        statements.append(_make_stmt(pri_year, bs_pri, is_pri, False))

    return FinancialStatement(
        company=Company(name=company_name),
        statements=statements,
        generated_at=datetime.now().isoformat(timespec="seconds"),
    )


# ─────────────────────────────────────────────────────────────
# 요약
# ─────────────────────────────────────────────────────────────

def parse_summary(fs: FinancialStatement) -> str:
    lines = [
        f"═══ PDF 파싱 결과 ═══",
        f"  기업: {fs.company.name}",
        f"  연도: {fs.years()}",
    ]
    for stmt in fs.statements:
        bs = stmt.balance_sheet
        inc = stmt.income_statement
        q = stmt.quality
        lines.append(f"\n  ── {stmt.fiscal_year}년 ──")
        lines.append(f"  자산총계:     {bs.total_assets:>18,.0f}")
        lines.append(f"  부채총계:     {bs.total_liabilities:>18,.0f}")
        lines.append(f"  자본총계:     {bs.total_equity:>18,.0f}")
        lines.append(f"  매출액:       {inc.revenue:>18,.0f}")
        lines.append(f"  영업이익:     {inc.operating_profit:>18,.0f}")
        lines.append(f"  당기순이익:   {inc.net_profit:>18,.0f}")
        lines.append(f"  신뢰도:       {q.extraction_confidence:.0%}")
        if q.missing_fields:
            lines.append(f"  누락:         {', '.join(q.missing_fields)}")
        if q.warnings:
            lines.append(f"  경고:         {'; '.join(q.warnings)}")
    return "\n".join(lines)
