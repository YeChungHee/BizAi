"""
종합 등급 계산기.

재무 스코어(60%) + 비재무 스코어(30%) − 페널티(10%) = 최종 점수
→ AAA~D 10단계 등급 매핑.

ARCHITECTURE.md §4.5 & §8 참조.

사용:
    from analysis.grade_calculator import calculate_grade, Grade

    grade = calculate_grade(
        financial_score=fs,
        consultation=ca,
        red_flags=flags,
        mismatches=xv_mismatches,
        audit=stmt.audit,
    )
    print(grade.grade, grade.total_score)
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "schema"))
from models import AuditInfo  # noqa: E402

from .financial_scorer import FinancialScore
from .consultation_analyzer import ConsultationAnalysis
from .red_flag_detector import RedFlag
from .cross_validator import Mismatch


# ─────────────────────────────────────────────────────────────
# 등급 매핑 테이블
# ─────────────────────────────────────────────────────────────

GRADE_TABLE: list[tuple[float, str, str]] = [
    (90, "AAA", "최우량 — 거래 조건 최상위"),
    (85, "AA",  "우량 — 장기 파트너십 가능"),
    (78, "A",   "양호 — 표준 거래 조건"),
    (70, "BBB", "보통상위 — 약간의 조건 조정"),
    (60, "BB",  "보통 — 단계적 거래 확대"),
    (50, "B",   "보통하위 — 담보/보증 검토"),
    (40, "CCC", "주의 — 선급금 조건 필요"),
    (30, "CC",  "경계 — 소액 시범 거래만"),
    (20, "C",   "위험 — 거래 보류 권고"),
    (0,  "D",   "부적격 — 거래 불가"),
]


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class PenaltyDetail:
    source: str       # red_flag | audit | cross_validation
    description: str
    points: float


@dataclass
class Grade:
    # 최종 결과
    grade: str                    # AAA ~ D
    grade_description: str
    total_score: float            # 0~100

    # 구성 요소
    financial_score: float        # 0~100 (원점수)
    financial_weighted: float     # × 0.6
    consultation_score: float     # 0~100 (원점수)
    consultation_weighted: float  # × 0.3
    base_score: float             # financial + consultation (가중)

    # 페널티
    total_penalty: float
    penalties: list[PenaltyDetail] = field(default_factory=list)

    # 추가 정보
    red_flag_count: int = 0
    mismatch_count: int = 0
    audit_opinion: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "grade": self.grade,
            "grade_description": self.grade_description,
            "total_score": self.total_score,
            "financial_score": self.financial_score,
            "financial_weighted": self.financial_weighted,
            "consultation_score": self.consultation_score,
            "consultation_weighted": self.consultation_weighted,
            "base_score": self.base_score,
            "total_penalty": self.total_penalty,
            "penalties": [
                {"source": p.source, "description": p.description, "points": p.points}
                for p in self.penalties
            ],
            "red_flag_count": self.red_flag_count,
            "mismatch_count": self.mismatch_count,
            "audit_opinion": self.audit_opinion,
        }


# ─────────────────────────────────────────────────────────────
# 등급 계산
# ─────────────────────────────────────────────────────────────

def calculate_grade(
    financial_score: FinancialScore,
    consultation: Optional[ConsultationAnalysis] = None,
    red_flags: Optional[list[RedFlag]] = None,
    mismatches: Optional[list[Mismatch]] = None,
    audit: Optional[AuditInfo] = None,
) -> Grade:
    """
    종합 등급 산출.

    산식 (ARCHITECTURE.md §4.5):
        base = financial.overall × 0.6 + consultation.overall × 0.3
        penalty:
            Red Flag: min(30, count × 3)
            감사의견 한정: -10
            감사의견 부적정/의견거절: -30
            교차검증 불일치: -5 per 건
        total = max(0, base - penalty)
        → 10단계 등급

    Args:
        financial_score: 재무 스코어링 결과
        consultation: 상담 분석 결과 (None이면 비재무 50점 적용)
        red_flags: Red Flag 리스트
        mismatches: 교차검증 불일치 리스트
        audit: 감사정보
    """
    flags = red_flags or []
    xv = mismatches or []

    # ── 기본 점수 ──
    fin_score = financial_score.overall
    fin_weighted = round(fin_score * 0.6, 2)

    con_score = consultation.overall if consultation else 50.0
    con_weighted = round(con_score * 0.3, 2)

    base = round(fin_weighted + con_weighted, 2)

    # ── 페널티 ──
    penalties: list[PenaltyDetail] = []

    # Red Flag 페널티 (3점/개, max 30)
    rf_penalty = min(30, len(flags) * 3)
    if rf_penalty > 0:
        # critical은 추가 1점씩
        critical_count = sum(1 for f in flags if f.severity == "critical")
        rf_penalty += critical_count * 2  # critical 추가 가중
        rf_penalty = min(40, rf_penalty)  # 총 최대 40
        penalties.append(PenaltyDetail(
            source="red_flag",
            description=f"Red Flag {len(flags)}건 (critical {critical_count}건)",
            points=rf_penalty,
        ))

    # 감사의견 페널티
    audit_opinion = audit.opinion if audit else None
    if audit_opinion == "한정":
        penalties.append(PenaltyDetail(
            source="audit",
            description="감사의견: 한정",
            points=10,
        ))
    elif audit_opinion in ("부적정", "의견거절"):
        penalties.append(PenaltyDetail(
            source="audit",
            description=f"감사의견: {audit_opinion}",
            points=30,
        ))

    # 교차검증 불일치 페널티
    if xv:
        xv_penalty = sum(m.penalty for m in xv)
        penalties.append(PenaltyDetail(
            source="cross_validation",
            description=f"교차검증 불일치 {len(xv)}건",
            points=xv_penalty,
        ))

    total_penalty = sum(p.points for p in penalties)
    total_score = max(0, round(base - total_penalty, 1))

    # ── 등급 매핑 ──
    grade_str = "D"
    grade_desc = GRADE_TABLE[-1][2]
    for threshold, g, desc in GRADE_TABLE:
        if total_score >= threshold:
            grade_str = g
            grade_desc = desc
            break

    return Grade(
        grade=grade_str,
        grade_description=grade_desc,
        total_score=total_score,
        financial_score=fin_score,
        financial_weighted=fin_weighted,
        consultation_score=con_score,
        consultation_weighted=con_weighted,
        base_score=base,
        total_penalty=total_penalty,
        penalties=penalties,
        red_flag_count=len(flags),
        mismatch_count=len(xv),
        audit_opinion=audit_opinion,
    )


def grade_summary(grade: Grade) -> str:
    """등급 결과 요약 텍스트."""
    lines = [
        "╔══════════════════════════════════════════╗",
        f"║  종합 등급: {grade.grade:>4s}  ({grade.total_score:.1f}점/100)      ║",
        f"║  {grade.grade_description:38s}║",
        "╠══════════════════════════════════════════╣",
        f"║  재무 스코어:  {grade.financial_score:>5.1f} × 0.6 = {grade.financial_weighted:>5.1f}  ║",
        f"║  비재무 스코어: {grade.consultation_score:>5.1f} × 0.3 = {grade.consultation_weighted:>5.1f}  ║",
        f"║  기본 점수:    {grade.base_score:>5.1f}                   ║",
        f"║  페널티:       -{grade.total_penalty:>5.1f}                   ║",
        f"║  최종 점수:    {grade.total_score:>5.1f}                   ║",
        "╚══════════════════════════════════════════╝",
    ]

    if grade.penalties:
        lines.append("\n── 페널티 상세 ──")
        for p in grade.penalties:
            lines.append(f"  -{p.points:.0f}점  [{p.source}] {p.description}")

    return "\n".join(lines)
