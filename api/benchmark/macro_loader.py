"""
한국은행 ECOS API - 거시경제 지표 수집기

마진 자동 조정 엔진의 핵심 재료를 실시간/캐시 방식으로 제공합니다.

수집 지표:
  - 기준금리   (722Y001 / 0101000) — 자금 비용 → 마진율 직접 조정
  - GDP 성장률 (111Y002 / 10111)   — 경기 흐름 → 보수/공격 조정
  - CPI 전년비 (901Y009 / 0)       — 물가 리스크 → 마진 조정
  - BSI 전산업 (512Y006 / FDI20000) — 경기심리 → 신용한도 배수
  - BSI 제조업 (512Y006 / FDI10000) — 제조업 특화 한도 조정
  - 산업생산YoY(301Y013 / IIP_SA00) — 제조업 생산 트렌드 → 한도 조정

사용법:
    loader = MacroLoader()
    snap = loader.cached_snapshot()          # 24h 캐시 우선
    snap = loader.fetch_snapshot()           # 즉시 API 호출

    print(snap.base_rate)                    # 3.5 (%)
    print(snap.bsi_all)                      # 92.0 (P)
    print(snap.is_available)                 # True
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import ssl
import time
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

ECOS_API_KEY = os.getenv("ECOS_API_KEY", "U3YF1DZ1NSS55IX9Y4BT")
BASE_URL = "http://ecos.bok.or.kr/api/StatisticSearch"
_INSECURE_SSL = ssl.create_default_context()
_INSECURE_SSL.check_hostname = False
_INSECURE_SSL.verify_mode = ssl.CERT_NONE

SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "benchmark.db"

CACHE_TTL_HOURS = 24    # 24시간 캐시
MAX_RETRIES = 3
REQUEST_DELAY = 0.3

log = logging.getLogger("macro_loader")


# ─────────────────────────────────────────────────────────────
# 수집 대상 시계열 정의
# ─────────────────────────────────────────────────────────────

# 각 시리즈: (stat_code, item_code_filter, frequency, label)
# item_code_filter: None 이면 전체 수집 후 Python에서 필터
# frequency: "M"=월, "Q"=분기, "A"=연간
MACRO_SERIES: dict[str, dict[str, Any]] = {
    "base_rate": {
        "stat_code": "722Y001",
        "item_code": "0101000",    # 한국은행 기준금리
        "frequency": "M",
        "label": "한국은행 기준금리",
        "unit": "%",
    },
    "gdp_growth": {
        "stat_code": "111Y002",
        "item_code": "10111",      # 실질GDP 전년비
        "frequency": "A",
        "label": "실질GDP 성장률",
        "unit": "%",
    },
    "cpi_yoy": {
        "stat_code": "901Y009",
        "item_code": "0",          # 소비자물가 전년동월비 총지수
        "frequency": "M",
        "label": "소비자물가 전년동월비",
        "unit": "%",
    },
    "bsi_all": {
        "stat_code": "512Y006",
        "item_code": "FDI20000",   # 전산업 BSI 현황
        "frequency": "M",
        "label": "전산업 BSI",
        "unit": "P",
    },
    "bsi_mfg": {
        "stat_code": "512Y006",
        "item_code": "FDI10000",   # 제조업 BSI 현황
        "frequency": "M",
        "label": "제조업 BSI",
        "unit": "P",
    },
    "ip_yoy": {
        "stat_code": "301Y013",
        "item_code": "IIP_SA00",   # 산업생산지수 전년동월비
        "frequency": "M",
        "label": "산업생산지수 전년동월비",
        "unit": "%",
    },
}

# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class MacroSnapshot:
    """최신 거시경제 지표 스냅샷"""
    base_rate: float | None          # 한국은행 기준금리 (%)
    gdp_growth: float | None         # 실질GDP 성장률 (%)
    cpi_yoy: float | None            # 소비자물가 전년동월비 (%)
    bsi_all: float | None            # 전산업 BSI (P, 100=기준)
    bsi_mfg: float | None            # 제조업 BSI (P)
    ip_yoy: float | None             # 산업생산지수 전년동월비 (%)
    reference_date: str              # 기준 기간 (YYYYMM 또는 YYYY)
    fetched_at: str                  # 수집 시각 (ISO 8601)
    source: str = "ECOS"

    @property
    def is_available(self) -> bool:
        """핵심 지표(금리, BSI)가 하나라도 있으면 True"""
        return self.base_rate is not None or self.bsi_all is not None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def default(cls) -> "MacroSnapshot":
        """API 미응답 시 사용하는 중립 기본값"""
        return cls(
            base_rate=3.5,
            gdp_growth=2.0,
            cpi_yoy=2.0,
            bsi_all=95.0,
            bsi_mfg=93.0,
            ip_yoy=0.0,
            reference_date="default",
            fetched_at=datetime.now().isoformat(),
            source="default",
        )


# ─────────────────────────────────────────────────────────────
# ECOS API 호출 (기존 ecos_loader.py 패턴 재활용)
# ─────────────────────────────────────────────────────────────

def _fetch_series(stat_code: str, frequency: str,
                  period_start: str, period_end: str,
                  item_code: str | None = None) -> list[dict]:
    """
    ECOS StatisticSearch 단일 시리즈 호출.
    item_code: URL에 포함할 ITEM_CODE1 값 (없으면 전체)
    """
    parts = [
        BASE_URL, ECOS_API_KEY, "json", "kr",
        "1", "100",
        stat_code,
        frequency,
        period_start,
        period_end,
    ]
    if item_code:
        parts.append(quote(item_code, safe=""))

    url = "/".join(parts) + "/"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": "BizAi-MacroLoader/1.0"})
            ctx = _INSECURE_SSL if url.startswith("https://") else None
            with urlopen(req, timeout=30, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if "StatisticSearch" in data:
                return data["StatisticSearch"].get("row", [])
            elif "RESULT" in data:
                code = data["RESULT"].get("CODE")
                if code == "INFO-200":
                    return []
                log.warning("ECOS error %s for %s: %s",
                            code, stat_code, data["RESULT"].get("MESSAGE"))
                return []
            return []

        except (HTTPError, URLError, TimeoutError) as e:
            log.warning("Attempt %d/%d failed (%s): %s",
                        attempt, MAX_RETRIES, stat_code, e)
            if attempt == MAX_RETRIES:
                return []
            time.sleep(2 * attempt)

    return []


def _latest_value(rows: list[dict], item_code_filter: str | None = None) -> tuple[float | None, str]:
    """
    rows에서 가장 최신 값 반환.
    item_code_filter: None이면 첫 번째 ITEM_CODE1 기준 필터
    Returns (value, period)
    """
    filtered = []
    for row in rows:
        val_raw = row.get("DATA_VALUE")
        if val_raw is None:
            continue
        val_str = str(val_raw).strip()
        if val_str in ("", "-", ".."):
            continue
        try:
            val = float(val_str)
        except ValueError:
            continue

        period = row.get("TIME", "")

        # item_code 필터 (URL에서 넣지 않았을 때 대비)
        if item_code_filter:
            row_code = row.get("ITEM_CODE1", "") or row.get("ITEM_CODE2", "")
            if row_code != item_code_filter:
                continue

        filtered.append((period, val))

    if not filtered:
        return None, ""

    # 가장 최신 기간의 값
    filtered.sort(key=lambda x: x[0], reverse=True)
    period, val = filtered[0]
    return val, period


# ─────────────────────────────────────────────────────────────
# 캐시 (SQLite)
# ─────────────────────────────────────────────────────────────

_CACHE_DDL = """
CREATE TABLE IF NOT EXISTS macro_cache (
    series      TEXT PRIMARY KEY,
    value       REAL,
    period      TEXT,
    fetched_at  TEXT
);
"""

def _get_cached(series: str, max_age_hours: int = CACHE_TTL_HOURS) -> tuple[float | None, str, str]:
    """캐시에서 값 읽기. (value, period, fetched_at) 반환; 만료 시 None 반환."""
    if not DB_PATH.exists():
        return None, "", ""
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_CACHE_DDL)
    row = conn.execute(
        "SELECT value, period, fetched_at FROM macro_cache WHERE series=?",
        (series,)
    ).fetchone()
    conn.close()
    if not row:
        return None, "", ""
    fetched_at = row[2]
    try:
        age = datetime.now() - datetime.fromisoformat(fetched_at)
        if age > timedelta(hours=max_age_hours):
            return None, "", ""
    except ValueError:
        return None, "", ""
    return row[0], row[1], fetched_at


def _set_cached(series: str, value: float, period: str) -> None:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(_CACHE_DDL)
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO macro_cache VALUES (?, ?, ?, ?)",
            (series, value, period, datetime.now().isoformat())
        )
    conn.close()


# ─────────────────────────────────────────────────────────────
# 메인 로더
# ─────────────────────────────────────────────────────────────

class MacroLoader:
    """
    ECOS 거시경제 지표 로더.

    캐시 우선 조회 / 만료 시 API 재수집.
    API 실패 시 MacroSnapshot.default() 반환 (서킷브레이커 패턴).
    """

    def __init__(self, api_key: str = ECOS_API_KEY,
                 cache_ttl_hours: int = CACHE_TTL_HOURS):
        self.api_key = api_key
        self.cache_ttl = cache_ttl_hours

    def fetch_snapshot(self) -> MacroSnapshot:
        """ECOS API 직접 호출 → MacroSnapshot 반환."""
        now = datetime.now()
        year_end = now.year
        year_start = year_end - 2
        period_start_m = f"{year_start}01"
        period_end_m = f"{year_end}12"
        period_start_a = str(year_start)
        period_end_a = str(year_end)

        values: dict[str, tuple[float | None, str]] = {}

        for series_key, cfg in MACRO_SERIES.items():
            try:
                freq = cfg["frequency"]
                pstart = period_start_a if freq == "A" else period_start_m
                pend = period_end_a if freq == "A" else period_end_m
                rows = _fetch_series(
                    stat_code=cfg["stat_code"],
                    frequency=freq,
                    period_start=pstart,
                    period_end=pend,
                    item_code=cfg.get("item_code"),
                )
                val, period = _latest_value(rows, cfg.get("item_code"))
                values[series_key] = (val, period)
                if val is not None:
                    _set_cached(series_key, val, period)
                    log.info("[%s] %s = %.2f (%s)",
                             series_key, cfg["label"], val, period)
                else:
                    log.warning("[%s] 데이터 없음 (stat=%s item=%s)",
                                series_key, cfg["stat_code"], cfg.get("item_code"))
                time.sleep(REQUEST_DELAY)
            except Exception as e:
                log.error("[%s] 수집 실패: %s", series_key, e)
                values[series_key] = (None, "")

        # 기준 기간: 기준금리 기간 우선, 없으면 현재 YYYYMM
        ref_date = (values.get("base_rate", (None, ""))[1]
                    or now.strftime("%Y%m"))

        return MacroSnapshot(
            base_rate=values["base_rate"][0],
            gdp_growth=values["gdp_growth"][0],
            cpi_yoy=values["cpi_yoy"][0],
            bsi_all=values["bsi_all"][0],
            bsi_mfg=values["bsi_mfg"][0],
            ip_yoy=values["ip_yoy"][0],
            reference_date=ref_date,
            fetched_at=now.isoformat(),
        )

    def cached_snapshot(self) -> MacroSnapshot:
        """
        캐시 우선 조회.
        모든 시리즈가 캐시에 있으면 API 호출 없이 반환.
        일부라도 만료/없으면 전체 재수집.
        API 실패 시 캐시 부분 조합 또는 default 반환.
        """
        cached_vals: dict[str, tuple[float | None, str, str]] = {}
        all_fresh = True

        for series_key in MACRO_SERIES:
            val, period, fetched_at = _get_cached(series_key, self.cache_ttl)
            cached_vals[series_key] = (val, period, fetched_at)
            if val is None:
                all_fresh = False

        if all_fresh:
            log.info("거시지표 캐시 사용 (TTL: %dh)", self.cache_ttl)
            ref_date = cached_vals["base_rate"][1] or datetime.now().strftime("%Y%m")
            fetched_at = cached_vals["base_rate"][2] or datetime.now().isoformat()
            return MacroSnapshot(
                base_rate=cached_vals["base_rate"][0],
                gdp_growth=cached_vals["gdp_growth"][0],
                cpi_yoy=cached_vals["cpi_yoy"][0],
                bsi_all=cached_vals["bsi_all"][0],
                bsi_mfg=cached_vals["bsi_mfg"][0],
                ip_yoy=cached_vals["ip_yoy"][0],
                reference_date=ref_date,
                fetched_at=fetched_at,
            )

        log.info("캐시 만료/없음 → ECOS API 재수집")
        try:
            return self.fetch_snapshot()
        except Exception as e:
            log.error("API 수집 실패, 캐시/기본값으로 폴백: %s", e)
            # 캐시에 남은 값이 있으면 최대한 활용
            if any(v[0] is not None for v in cached_vals.values()):
                ref_date = (cached_vals["base_rate"][1]
                            or datetime.now().strftime("%Y%m"))
                return MacroSnapshot(
                    base_rate=cached_vals["base_rate"][0],
                    gdp_growth=cached_vals["gdp_growth"][0],
                    cpi_yoy=cached_vals["cpi_yoy"][0],
                    bsi_all=cached_vals["bsi_all"][0],
                    bsi_mfg=cached_vals["bsi_mfg"][0],
                    ip_yoy=cached_vals["ip_yoy"][0],
                    reference_date=ref_date,
                    fetched_at=datetime.now().isoformat(),
                    source="cache_stale",
                )
            return MacroSnapshot.default()


# ─────────────────────────────────────────────────────────────
# CLI (python macro_loader.py)
# ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")

    ap = argparse.ArgumentParser(description="ECOS 거시경제 지표 수집")
    ap.add_argument("--fresh", action="store_true", help="캐시 무시하고 API 직접 호출")
    args = ap.parse_args()

    loader = MacroLoader()
    snap = loader.fetch_snapshot() if args.fresh else loader.cached_snapshot()

    print("\n═══ 거시경제 스냅샷 ═══")
    print(f"  기준금리:          {snap.base_rate:.2f}%" if snap.base_rate else "  기준금리:          N/A")
    print(f"  GDP 성장률:        {snap.gdp_growth:.1f}%" if snap.gdp_growth else "  GDP 성장률:        N/A")
    print(f"  CPI 전년비:        {snap.cpi_yoy:.1f}%" if snap.cpi_yoy else "  CPI 전년비:        N/A")
    print(f"  BSI 전산업:        {snap.bsi_all:.1f}P" if snap.bsi_all else "  BSI 전산업:        N/A")
    print(f"  BSI 제조업:        {snap.bsi_mfg:.1f}P" if snap.bsi_mfg else "  BSI 제조업:        N/A")
    print(f"  산업생산 YoY:      {snap.ip_yoy:.1f}%" if snap.ip_yoy else "  산업생산 YoY:      N/A")
    print(f"  기준일:            {snap.reference_date}")
    print(f"  데이터 출처:       {snap.source}")
    print(f"  사용 가능:         {'✅' if snap.is_available else '❌ (기본값)'}")
