"""
DART OpenAPI 클라이언트.

한국 전자공시시스템(DART) API를 통해 기업 정보와 재무데이터를 조회합니다.
PDF 파싱 결과의 보완/검증 또는 PDF 없이 단독으로 재무데이터를 가져오는 용도.

API 문서: https://opendart.fss.or.kr/guide/main.do

사용:
    from ingest.dart_client import DartClient

    client = DartClient(api_key="YOUR_KEY")

    # 기업 검색
    corp = client.search_company("애즈위메이크")
    print(corp)

    # 재무제표 조회
    fs = client.get_financials(corp_code="00123456", bsns_year="2024")
"""

from __future__ import annotations

import sys
import io
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional
from datetime import datetime

import requests

API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "schema"))

from models import (
    FinancialStatement, Statement, Company, Quality,
    BalanceSheet, IncomeStatement, CashFlow,
)


# ─────────────────────────────────────────────────────────────
# DART API 엔드포인트
# ─────────────────────────────────────────────────────────────

BASE_URL = "https://opendart.fss.or.kr/api"

ENDPOINTS = {
    "corp_code":     f"{BASE_URL}/corpCode.xml",       # 기업코드 목록 (ZIP)
    "company":       f"{BASE_URL}/company.json",        # 기업 기본정보
    "fnltt_singl":   f"{BASE_URL}/fnlttSinglAcnt.json", # 단일회사 재무제표
    "fnltt_all":     f"{BASE_URL}/fnlttSinglAcntAll.json", # 단일회사 전체 재무제표
}

# 보고서 코드
REPORT_CODES = {
    "1Q": "11013",  # 1분기
    "2Q": "11012",  # 반기
    "3Q": "11014",  # 3분기
    "annual": "11011",  # 사업보고서
}


# ─────────────────────────────────────────────────────────────
# DART 계정과목 → 스키마 매핑
# ─────────────────────────────────────────────────────────────

DART_BS_MAP: dict[str, str] = {
    "유동자산":           "current_assets",
    "비유동자산":         "non_current_assets",
    "자산총계":           "total_assets",
    "유동부채":           "current_liabilities",
    "비유동부채":         "non_current_liabilities",
    "부채총계":           "total_liabilities",
    "자본총계":           "total_equity",
    "현금및현금성자산":   "cash_and_equivalents",
    "매출채권":           "trade_receivables",
    "재고자산":           "inventories",
    "유형자산":           "ppe",
    "무형자산":           "intangibles",
    "단기차입금":         "short_term_debt",
    "장기차입금":         "long_term_debt",
    "자본금":             "paid_in_capital",
    "이익잉여금":         "retained_earnings",
}

DART_IS_MAP: dict[str, str] = {
    "매출액":             "revenue",
    "수익(매출액)":       "revenue",
    "매출원가":           "cost_of_sales",
    "매출총이익":         "gross_profit",
    "판매비와관리비":     "sga",
    "영업이익":           "operating_profit",
    "영업이익(손실)":     "operating_profit",
    "법인세비용차감전순이익": "profit_before_tax",
    "법인세비용차감전순이익(손실)": "profit_before_tax",
    "당기순이익":         "net_profit",
    "당기순이익(손실)":   "net_profit",
    "이자비용":           "interest_expense",
}


# ─────────────────────────────────────────────────────────────
# 클라이언트
# ─────────────────────────────────────────────────────────────

class DartClient:
    """DART OpenAPI 클라이언트."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._corp_codes: Optional[dict[str, dict]] = None

    # ── 기업코드 목록 로드 ──

    def load_corp_codes(self) -> dict[str, dict]:
        """DART 기업코드 목록 다운로드 및 파싱 (ZIP → XML)."""
        if self._corp_codes:
            return self._corp_codes

        resp = requests.get(ENDPOINTS["corp_code"], params={"crtfc_key": self.api_key})
        resp.raise_for_status()

        # ZIP 안에 CORPCODE.xml
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            xml_name = zf.namelist()[0]
            xml_data = zf.read(xml_name)

        root = ET.fromstring(xml_data)
        codes = {}
        for item in root.findall(".//list"):
            corp_code = item.findtext("corp_code", "")
            corp_name = item.findtext("corp_name", "")
            stock_code = item.findtext("stock_code", "")
            if corp_name:
                codes[corp_name] = {
                    "corp_code": corp_code,
                    "corp_name": corp_name,
                    "stock_code": stock_code.strip() if stock_code else None,
                }

        self._corp_codes = codes
        return codes

    def search_company(self, keyword: str) -> list[dict]:
        """회사명으로 기업코드 검색."""
        codes = self.load_corp_codes()
        results = []
        for name, info in codes.items():
            if keyword in name:
                results.append(info)
        return results

    # ── 기업 기본정보 ──

    def get_company_info(self, corp_code: str) -> dict:
        """기업 기본 정보 조회."""
        resp = requests.get(ENDPOINTS["company"], params={
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
        })
        resp.raise_for_status()
        data = resp.json()
        if data.get("status") != "000":
            raise ValueError(f"DART API 오류: {data.get('message', 'unknown')}")
        return data

    # ── 재무제표 조회 ──

    def get_financials_raw(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "OFS",  # OFS=개별, CFS=연결
    ) -> list[dict]:
        """
        DART 단일회사 재무제표 원본 데이터.

        Args:
            corp_code: DART 고유번호
            bsns_year: 사업연도 (예: "2024")
            reprt_code: 보고서 코드 (11011=사업보고서)
            fs_div: OFS=개별, CFS=연결

        Returns:
            재무제표 항목 리스트
        """
        resp = requests.get(ENDPOINTS["fnltt_singl"], params={
            "crtfc_key": self.api_key,
            "corp_code": corp_code,
            "bsns_year": bsns_year,
            "reprt_code": reprt_code,
            "fs_div": fs_div,
        })
        resp.raise_for_status()
        data = resp.json()

        if data.get("status") == "013":
            # 조회 결과 없음
            return []
        if data.get("status") != "000":
            raise ValueError(f"DART API 오류: {data.get('message', 'unknown')}")

        return data.get("list", [])

    def get_financials(
        self,
        corp_code: str,
        bsns_year: str,
        reprt_code: str = "11011",
        fs_div: str = "OFS",
    ) -> Optional[FinancialStatement]:
        """
        DART 재무제표 → 표준 FinancialStatement 변환.

        Args:
            corp_code: DART 고유번호
            bsns_year: 사업연도
            reprt_code: 보고서 코드
            fs_div: OFS=개별, CFS=연결

        Returns:
            FinancialStatement 또는 None
        """
        items = self.get_financials_raw(corp_code, bsns_year, reprt_code, fs_div)
        if not items:
            return None

        # 기업정보
        corp_name = items[0].get("stock_name", "") or items[0].get("corp_code", "")
        try:
            info = self.get_company_info(corp_code)
            corp_name = info.get("corp_name", corp_name)
        except Exception:
            pass

        # 당기/전기 데이터 분리
        bs_cur, bs_pri = {}, {}
        is_cur, is_pri = {}, {}

        for item in items:
            acct_name = item.get("account_nm", "")
            sj_div = item.get("sj_div", "")  # BS=재무상태표, IS=손익계산서
            thstrm_amount = item.get("thstrm_amount", "")  # 당기
            frmtrm_amount = item.get("frmtrm_amount", "")  # 전기

            # 금액 파싱
            cur_val = _dart_parse_amount(thstrm_amount)
            pri_val = _dart_parse_amount(frmtrm_amount)

            if sj_div == "BS":
                field = DART_BS_MAP.get(acct_name)
                if field:
                    if cur_val is not None:
                        bs_cur[field] = cur_val
                    if pri_val is not None:
                        bs_pri[field] = pri_val
            elif sj_div == "IS":
                field = DART_IS_MAP.get(acct_name)
                if field:
                    if cur_val is not None:
                        is_cur[field] = cur_val
                    if pri_val is not None:
                        is_pri[field] = pri_val

        # FinancialStatement 조립
        year = int(bsns_year)
        statements = []

        if bs_cur or is_cur:
            statements.append(_build_dart_statement(year, bs_cur, is_cur, corp_code))
        if bs_pri or is_pri:
            statements.append(_build_dart_statement(year - 1, bs_pri, is_pri, corp_code))

        if not statements:
            return None

        company = Company(name=corp_name, dart_code=corp_code)

        # 기업정보 보강
        try:
            info = self.get_company_info(corp_code)
            company.ceo = info.get("ceo_nm")
            company.business_no = info.get("bizr_no")
            company.address = info.get("adres")
            company.established = info.get("est_dt")
            company.ksic_code = info.get("induty_code")
            company.ksic_name = info.get("induty_code")
            if info.get("stock_code"):
                company.stock_code = info["stock_code"]
                company.listed = True
        except Exception:
            pass

        return FinancialStatement(
            company=company,
            statements=statements,
            generated_at=datetime.now().isoformat(timespec="seconds"),
        )


# ─────────────────────────────────────────────────────────────
# 유틸
# ─────────────────────────────────────────────────────────────

def _dart_parse_amount(text: str) -> Optional[int]:
    """DART 금액 문자열 → 정수."""
    if not text or text == "-":
        return None
    s = text.replace(",", "").replace(" ", "").strip()
    try:
        return int(s)
    except ValueError:
        return None


def _build_dart_statement(year: int, bs: dict, is_data: dict, corp_code: str) -> Statement:
    """DART 데이터로 Statement 생성."""
    return Statement(
        fiscal_year=year,
        period_end=f"{year}-12-31",
        period_start=f"{year}-01-01",
        report_type="separate",
        accounting_standard="K-GAAP",
        currency="KRW",
        unit="원",
        balance_sheet=BalanceSheet(
            current_assets=bs.get("current_assets", 0),
            non_current_assets=bs.get("non_current_assets", 0),
            total_assets=bs.get("total_assets", 0),
            current_liabilities=bs.get("current_liabilities", 0),
            non_current_liabilities=bs.get("non_current_liabilities", 0),
            total_liabilities=bs.get("total_liabilities", 0),
            total_equity=bs.get("total_equity", 0),
            cash_and_equivalents=bs.get("cash_and_equivalents"),
            trade_receivables=bs.get("trade_receivables"),
            inventories=bs.get("inventories"),
            ppe=bs.get("ppe"),
            intangibles=bs.get("intangibles"),
            short_term_debt=bs.get("short_term_debt"),
            long_term_debt=bs.get("long_term_debt"),
            paid_in_capital=bs.get("paid_in_capital"),
            retained_earnings=bs.get("retained_earnings"),
        ),
        income_statement=IncomeStatement(
            revenue=is_data.get("revenue", 0),
            operating_profit=is_data.get("operating_profit", 0),
            profit_before_tax=is_data.get("profit_before_tax", 0),
            net_profit=is_data.get("net_profit", 0),
            cost_of_sales=is_data.get("cost_of_sales"),
            gross_profit=is_data.get("gross_profit"),
            sga=is_data.get("sga"),
            interest_expense=is_data.get("interest_expense"),
        ),
        cash_flow=CashFlow(operating_cf=0, investing_cf=0, financing_cf=0),
        quality=Quality(
            source="dart",
            source_file=f"DART:{corp_code}:{year}",
            extraction_confidence=0.95,  # 공시 데이터는 신뢰도 높음
            extracted_at=datetime.now().isoformat(timespec="seconds"),
        ),
    )


def dart_summary(client: DartClient, corp_code: str, year: str) -> str:
    """DART 조회 결과 요약."""
    fs = client.get_financials(corp_code, year)
    if not fs:
        return f"DART 조회 결과 없음 (corp_code={corp_code}, year={year})"

    lines = [
        f"═══ DART 조회 결과 ═══",
        f"  기업: {fs.company.name}",
        f"  DART코드: {corp_code}",
    ]
    for stmt in fs.statements:
        bs = stmt.balance_sheet
        inc = stmt.income_statement
        lines.append(f"\n  ── {stmt.fiscal_year}년 ──")
        lines.append(f"  자산총계: {bs.total_assets:>18,.0f}")
        lines.append(f"  매출액:   {inc.revenue:>18,.0f}")
        lines.append(f"  순이익:   {inc.net_profit:>18,.0f}")
    return "\n".join(lines)
