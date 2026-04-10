"""
ECOS (Bank of Korea) 기업경영분석 벤치마크 데이터 로더

한국은행 ECOS API에서 업종별·규모별 재무지표를 수집하여
SQLite + JSON으로 저장합니다.

사용법:
    python ecos_loader.py --year-start 2020 --year-end 2024
    python ecos_loader.py --full      # 전체 업종 수집
    python ecos_loader.py --test      # 주요 업종 샘플만 수집

데이터 소스:
    - 501Y005 성장성 지표
    - 501Y006 손익(수익성) 지표
    - 501Y007 자산/자본(안정성) 지표
    - 501Y008 회전율(활동성) 지표
    - 501Y009 생산성 지표
"""

import argparse
import json
import logging
import os
import ssl
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

# ─────────────────────────────────────────────────────────────
# 설정
# ─────────────────────────────────────────────────────────────

ECOS_API_KEY = os.getenv("ECOS_API_KEY", "U3YF1DZ1NSS55IX9Y4BT")
# ECOS는 HTTP/HTTPS 모두 제공. 로컬 SSL 체인 이슈 회피를 위해 HTTP 기본.
BASE_URL = "http://ecos.bok.or.kr/api/StatisticSearch"
# HTTPS 강제 시 SSL 검증 완화용 컨텍스트 (corporate proxy / self-signed chain 대응)
_INSECURE_SSL = ssl.create_default_context()
_INSECURE_SSL.check_hostname = False
_INSECURE_SSL.verify_mode = ssl.CERT_NONE
SCRIPT_DIR = Path(__file__).parent
DB_PATH = SCRIPT_DIR / "benchmark.db"
JSON_PATH = SCRIPT_DIR / "benchmark.json"

# 요청 간 대기 (초) - ECOS API 부하 방지
REQUEST_DELAY = 0.25
MAX_RETRIES = 3

# 대상 통계표 (STAT_CODE: 설명)
STAT_TABLES = {
    "501Y005": "성장성",
    "501Y006": "수익성",
    "501Y007": "안정성",
    "501Y008": "활동성",
    "501Y009": "생산성",
}

# 수집 대상 핵심 지표 (ITEM_CODE3 기준 - ECOS 실제 코드)
# 재무분석 평가에 꼭 필요한 핵심 지표로 선별
CORE_INDICATORS = {
    # 성장성 (501Y005)
    "501": "총자산증가율",
    "502": "유형자산증가율",
    "505": "자기자본증가율",
    "506": "매출액증가율",
    # 수익성 (501Y006)
    "602": "총자산순이익률",          # ROA
    "606": "자기자본순이익률",        # ROE
    "610": "매출액순이익률",
    "611": "매출액영업이익률",
    "612": "매출원가대매출액",
    "615": "연구개발비대매출액",
    "625": "금융비용대매출액",
    "627": "이자보상비율",
    # 안정성 (501Y007)
    "701": "자기자본비율",
    "702": "유동비율",
    "703": "당좌비율",
    "707": "부채비율",
    "710": "차입금의존도",
    # 활동성 (501Y008)
    "801": "총자산회전율",
    "806": "재고자산회전율",
    "808": "매출채권회전율",
    "809": "매입채무회전율",
    # 생산성 (501Y009)
    "9034": "총자본투자효율",
    "9044": "설비투자효율",
    "9064": "부가가치율",
    "9074": "노동소득분배율",
}

# 기업 규모 코드
SIZE_CODES = {
    "A": "종합",
    "L": "대기업",
    "J": "대기업_중견",
    "D": "중기업",
    "M": "중소기업",
    "S": "소기업",
}

# 주요 업종 코드 (테스트용 - --test 모드에서 사용)
SAMPLE_INDUSTRIES = [
    ("ZZZ00", "전산업"),
    ("C", "제조업"),
    ("C10", "식료품"),
    ("C20", "화학"),
    ("C21", "의약품"),
    ("C26", "전자부품·컴퓨터·영상·음향·통신장비"),
    ("C28", "전기장비"),
    ("C29", "기계"),
    ("C30", "자동차"),
    ("F", "건설업"),
    ("G", "도소매"),
    ("H", "운수창고"),
    ("I", "숙박음식"),
    ("J", "정보통신"),
    ("L", "부동산"),
    ("M", "전문과학기술"),
    ("ZZZ80", "비제조업"),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("ecos")


# ─────────────────────────────────────────────────────────────
# 데이터 모델
# ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class BenchmarkRow:
    stat_code: str       # 501Y006 등
    category: str        # 성장성/수익성/...
    year: int            # 2024
    industry_code: str   # C26
    industry_name: str
    size_code: str       # L, M, S...
    size_name: str
    indicator_code: str  # 707
    indicator_name: str  # 부채비율
    value: float
    unit: str            # %, 배, ...


# ─────────────────────────────────────────────────────────────
# API 호출
# ─────────────────────────────────────────────────────────────

def fetch_ecos(stat_code: str, year_start: int, year_end: int,
               industry_code: str = "") -> list[dict]:
    """
    ECOS StatisticSearch API 호출.
    industry_code가 주어지면 해당 업종만, 아니면 전체 반환.
    최대 10000행까지 안전하게 수집.
    """
    parts = [
        BASE_URL,
        ECOS_API_KEY,
        "json",
        "kr",
        "1",         # start
        "10000",     # end (최대)
        stat_code,
        "A",         # Annual
        str(year_start),
        str(year_end),
    ]
    if industry_code:
        parts.append(quote(industry_code, safe=""))

    url = "/".join(parts) + "/"

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            req = Request(url, headers={"User-Agent": "BizAi-Benchmark/1.0"})
            # HTTP 기본, HTTPS일 경우 검증 완화 컨텍스트 사용
            ctx = _INSECURE_SSL if url.startswith("https://") else None
            with urlopen(req, timeout=30, context=ctx) as resp:
                data = json.loads(resp.read().decode("utf-8"))

            if "StatisticSearch" in data:
                return data["StatisticSearch"].get("row", [])
            elif "RESULT" in data:
                code = data["RESULT"].get("CODE")
                msg = data["RESULT"].get("MESSAGE", "")
                if code == "INFO-200":  # no data
                    return []
                raise RuntimeError(f"ECOS error {code}: {msg}")
            else:
                log.warning("Unexpected response for %s/%s: %s",
                            stat_code, industry_code, list(data.keys()))
                return []

        except (HTTPError, URLError, TimeoutError) as e:
            log.warning("Attempt %d/%d failed for %s/%s: %s",
                        attempt, MAX_RETRIES, stat_code, industry_code, e)
            if attempt == MAX_RETRIES:
                raise
            time.sleep(2 * attempt)

    return []


# ─────────────────────────────────────────────────────────────
# 파싱
# ─────────────────────────────────────────────────────────────

def parse_rows(stat_code: str, raw_rows: list[dict]) -> list[BenchmarkRow]:
    """ECOS 원본 row → BenchmarkRow로 변환. 관심 지표만 필터."""
    category = STAT_TABLES.get(stat_code, "기타")
    parsed: list[BenchmarkRow] = []

    for row in raw_rows:
        indicator_code = row.get("ITEM_CODE3", "")
        if indicator_code not in CORE_INDICATORS:
            continue

        size_code = row.get("ITEM_CODE2", "")
        if size_code not in SIZE_CODES:
            continue

        value_raw = row.get("DATA_VALUE")
        if value_raw is None:
            continue
        value_str = str(value_raw).strip()
        if not value_str or value_str in ("-", "..", ""):
            continue

        try:
            value = float(value_str)
        except ValueError:
            continue

        try:
            year = int(row.get("TIME", "0")[:4])
        except ValueError:
            continue

        parsed.append(BenchmarkRow(
            stat_code=stat_code,
            category=category,
            year=year,
            industry_code=row.get("ITEM_CODE1", ""),
            industry_name=(row.get("ITEM_NAME1") or "").strip(),
            size_code=size_code,
            size_name=SIZE_CODES[size_code],
            indicator_code=indicator_code,
            indicator_name=CORE_INDICATORS[indicator_code],
            value=value,
            unit=(row.get("UNIT_NAME") or "").strip(),
        ))

    return parsed


# ─────────────────────────────────────────────────────────────
# 저장 (SQLite + JSON)
# ─────────────────────────────────────────────────────────────

DDL = """
CREATE TABLE IF NOT EXISTS benchmark (
    stat_code       TEXT NOT NULL,
    category        TEXT NOT NULL,
    year            INTEGER NOT NULL,
    industry_code   TEXT NOT NULL,
    industry_name   TEXT,
    size_code       TEXT NOT NULL,
    size_name       TEXT,
    indicator_code  TEXT NOT NULL,
    indicator_name  TEXT,
    value           REAL,
    unit            TEXT,
    PRIMARY KEY (year, industry_code, size_code, indicator_code)
);
CREATE INDEX IF NOT EXISTS idx_lookup
    ON benchmark(industry_code, size_code, year);
CREATE INDEX IF NOT EXISTS idx_indicator
    ON benchmark(indicator_code, year);
"""


def save_sqlite(rows: Iterable[BenchmarkRow]) -> int:
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(DDL)
    count = 0
    with conn:
        for r in rows:
            conn.execute(
                """
                INSERT OR REPLACE INTO benchmark VALUES
                (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (r.stat_code, r.category, r.year, r.industry_code,
                 r.industry_name, r.size_code, r.size_name,
                 r.indicator_code, r.indicator_name, r.value, r.unit),
            )
            count += 1
    conn.close()
    return count


def dump_json_from_db() -> dict:
    """SQLite → 계층적 JSON ({industry: {size: {year: {indicator: value}}}})."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.execute("""
        SELECT industry_code, industry_name, size_code, size_name,
               year, indicator_code, indicator_name, value, unit, category
        FROM benchmark
        ORDER BY industry_code, size_code, year, indicator_code
    """)

    out: dict = {"_meta": {
        "source": "Bank of Korea ECOS (501Y005-009)",
        "indicators": CORE_INDICATORS,
        "sizes": SIZE_CODES,
        "stat_tables": STAT_TABLES,
    }, "data": {}}

    for row in cur:
        ind = row["industry_code"]
        size = row["size_code"]
        year = str(row["year"])
        ind_code = row["indicator_code"]

        data = out["data"]
        data.setdefault(ind, {"name": row["industry_name"], "sizes": {}})
        data[ind]["sizes"].setdefault(size, {"name": row["size_name"], "years": {}})
        data[ind]["sizes"][size]["years"].setdefault(year, {})
        data[ind]["sizes"][size]["years"][year][ind_code] = {
            "name": row["indicator_name"],
            "value": row["value"],
            "unit": row["unit"],
            "category": row["category"],
        }

    conn.close()
    with open(JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    return out


# ─────────────────────────────────────────────────────────────
# 메인 수집 루프
# ─────────────────────────────────────────────────────────────

def collect(year_start: int, year_end: int,
            industries: list[tuple[str, str]] | None = None) -> int:
    """
    통계표 × (업종|연도) 조합으로 수집.
    ECOS 단일 호출 당 10000행 제한이 있으므로:
      - industries 주어지면: 통계표 × 업종별 호출 (업종 수 × 통계표 수)
      - industries=None  : 통계표 × 연도별 호출 (연도 수 × 통계표 수)
    """
    total = 0
    for stat_code, label in STAT_TABLES.items():
        log.info("═══ %s (%s) ═══", stat_code, label)

        if industries is None:
            # 연도별 루프 → 각 호출이 안전하게 10000행 미만
            for year in range(year_start, year_end + 1):
                raw = fetch_ecos(stat_code, year, year)
                parsed = parse_rows(stat_code, raw)
                saved = save_sqlite(parsed)
                log.info("  %d년 전체업종: %d rows → %d saved",
                         year, len(raw), saved)
                total += saved
                time.sleep(REQUEST_DELAY)
        else:
            for ind_code, ind_name in industries:
                raw = fetch_ecos(stat_code, year_start, year_end, ind_code)
                parsed = parse_rows(stat_code, raw)
                saved = save_sqlite(parsed)
                log.info("  %s (%s): %d rows → %d saved",
                         ind_code, ind_name, len(raw), saved)
                total += saved
                time.sleep(REQUEST_DELAY)
    return total


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--year-start", type=int, default=2020)
    ap.add_argument("--year-end", type=int, default=2024)
    ap.add_argument("--test", action="store_true",
                    help="샘플 17개 업종만 수집")
    ap.add_argument("--full", action="store_true",
                    help="업종 필터 없이 전체 수집 (권장 - 1회 호출로 끝남)")
    args = ap.parse_args()

    industries = SAMPLE_INDUSTRIES if args.test else None
    if not args.full and not args.test:
        log.info("모드 미지정 → --full (전체 수집) 기본 적용")

    log.info("저장 경로: DB=%s JSON=%s", DB_PATH, JSON_PATH)
    log.info("기간: %d~%d / 지표: %d개 / 규모: %d단계",
             args.year_start, args.year_end,
             len(CORE_INDICATORS), len(SIZE_CODES))

    start = time.time()
    total = collect(args.year_start, args.year_end, industries)
    elapsed = time.time() - start

    log.info("═══ 수집 완료 ═══")
    log.info("저장된 행: %d / 소요: %.1fs", total, elapsed)

    log.info("JSON 익스포트 중...")
    out = dump_json_from_db()
    n_ind = len(out["data"])
    log.info("업종 수: %d / JSON 저장: %s", n_ind, JSON_PATH)


if __name__ == "__main__":
    main()
