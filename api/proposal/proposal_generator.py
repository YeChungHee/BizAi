"""
제안서 자동 생성기.

분석 결과 + 마진 시뮬레이션 + 템플릿을 결합하여
4섹션 구조의 제안서를 생성합니다.

ARCHITECTURE.md §3.4:
    1. 현황진단 (재무/비재무 요약)
    2. 맞춤 제안 (상담 니즈 반영)
    3. 조건 (가격/납기/결제)
    4. Next Action

사용:
    from proposal.proposal_generator import generate_proposal, Proposal

    proposal = generate_proposal(
        company_name="삼성전자 부품사",
        grade=grade,
        financial_score=fin_result,
        consultation=consultation,
        red_flags=flags,
        margin_sim=sim,
        risk_premium=rp,
        template=template,
    )
    print(proposal.to_text())
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

# 경로 설정 (프로젝트 내 다른 모듈과 동일 패턴)
API_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(API_DIR))
sys.path.insert(0, str(API_DIR / "schema"))

from analysis.grade_calculator import Grade
from analysis.financial_scorer import FinancialScore
from analysis.consultation_analyzer import ConsultationAnalysis
from analysis.red_flag_detector import RedFlag
from simulation.risk_premium import RiskPremium
from simulation.margin_simulator import MarginSimulation
from .template_selector import ProposalTemplate


# ─────────────────────────────────────────────────────────────
# 결과 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class ProposalSection:
    """제안서 개별 섹션."""
    title: str
    content: str
    subsections: list[dict[str, str]] = field(default_factory=list)


@dataclass
class Proposal:
    """완성된 제안서."""
    company_name: str
    proposal_date: str
    grade: str
    grade_description: str
    template_tier: str

    # 4개 섹션
    diagnosis: ProposalSection      # 1. 현황진단
    recommendation: ProposalSection # 2. 맞춤제안
    terms: ProposalSection          # 3. 거래조건
    next_action: ProposalSection    # 4. Next Action

    # 메타
    summary_line: str               # 한 줄 요약
    total_score: float
    key_metrics: dict[str, str] = field(default_factory=dict)

    def sections(self) -> list[ProposalSection]:
        return [self.diagnosis, self.recommendation, self.terms, self.next_action]

    def to_text(self) -> str:
        """제안서 전체 텍스트 (이메일 본문 형식)."""
        lines = [
            f"{'═' * 60}",
            f"  {self.company_name} — FlowPay 맞춤 제안서",
            f"  작성일: {self.proposal_date}",
            f"  종합등급: {self.grade} ({self.total_score:.1f}점)",
            f"{'═' * 60}",
            "",
        ]

        for i, section in enumerate(self.sections(), 1):
            lines.append(f"{'─' * 60}")
            lines.append(f"  {i}. {section.title}")
            lines.append(f"{'─' * 60}")
            lines.append(section.content)
            for sub in section.subsections:
                lines.append(f"\n  ▸ {sub['title']}")
                lines.append(f"    {sub['content']}")
            lines.append("")

        lines.append(f"{'═' * 60}")
        lines.append(f"  {self.summary_line}")
        lines.append(f"{'═' * 60}")

        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "company_name": self.company_name,
            "proposal_date": self.proposal_date,
            "grade": self.grade,
            "grade_description": self.grade_description,
            "template_tier": self.template_tier,
            "total_score": self.total_score,
            "summary_line": self.summary_line,
            "key_metrics": self.key_metrics,
            "sections": [
                {
                    "title": s.title,
                    "content": s.content,
                    "subsections": s.subsections,
                }
                for s in self.sections()
            ],
        }


# ─────────────────────────────────────────────────────────────
# 섹션 생성 함수
# ─────────────────────────────────────────────────────────────

def _build_diagnosis(
    company_name: str,
    grade: Grade,
    financial_score: FinancialScore,
    consultation: Optional[ConsultationAnalysis],
    red_flags: list[RedFlag],
    template: ProposalTemplate,
) -> ProposalSection:
    """1. 현황진단 섹션 생성."""

    # 재무 요약 — FinancialScore.categories: list[CategoryScore]
    fin_parts = []
    for cat in financial_score.categories:
        fin_parts.append(f"  · {cat.category}: {cat.score:.1f}점")
    fin_summary = "\n".join(fin_parts) if fin_parts else "  (재무 상세 데이터 없음)"

    # 등급 설명
    grade_text = (
        f"  {company_name}의 종합 평가 등급은 {grade.grade} ({grade.total_score:.1f}점)입니다.\n"
        f"  {grade.grade_description}\n\n"
        f"  재무 스코어: {grade.financial_score:.1f}점 (가중: {grade.financial_weighted:.1f})\n"
        f"  비재무 스코어: {grade.consultation_score:.1f}점 (가중: {grade.consultation_weighted:.1f})\n"
    )

    # Red Flag 요약
    rf_text = ""
    if red_flags:
        rf_items = [f"  · [{f.severity.upper()}] {f.name}: {f.description}" for f in red_flags[:5]]
        rf_text = f"\n  주요 리스크 요인 ({len(red_flags)}건):\n" + "\n".join(rf_items)

    content = f"{grade_text}\n  [영역별 재무 스코어]\n{fin_summary}"
    if rf_text and template.include_risk_section:
        content += rf_text

    subsections = []
    if consultation:
        top_cats = sorted(consultation.categories, key=lambda c: c.score, reverse=True)[:3]
        sub_text = ", ".join([f"{c.category}({c.score:.0f}점)" for c in top_cats])
        subsections.append({
            "title": "비재무 강점",
            "content": sub_text,
        })

    return ProposalSection(
        title="현황진단",
        content=content,
        subsections=subsections,
    )


def _build_recommendation(
    company_name: str,
    grade: Grade,
    consultation: Optional[ConsultationAnalysis],
    margin_sim: MarginSimulation,
    template: ProposalTemplate,
) -> ProposalSection:
    """2. 맞춤 제안 섹션 생성."""

    # 상담 니즈 반영
    needs_text = ""
    if consultation and consultation.key_quotes:
        quotes = consultation.key_quotes[:3]
        needs_items = [f'  · "{q}"' for q in quotes]
        needs_text = "  상담에서 확인된 귀사의 주요 관심사:\n" + "\n".join(needs_items) + "\n\n"

    # 솔루션 제안
    tier = template.tier
    if tier == "premium":
        solution = (
            f"  FlowPay는 {company_name}의 우수한 재무 건전성을 기반으로\n"
            f"  장기 파트너십 프레임워크를 제안드립니다.\n\n"
            f"  귀사의 사업 확장에 맞춘 유연한 결제 솔루션으로\n"
            f"  Trapped Cash를 최적화하여 운영 효율을 극대화할 수 있습니다."
        )
    elif tier == "standard":
        solution = (
            f"  FlowPay는 {company_name}의 안정적 사업 기반을 고려하여\n"
            f"  단계적으로 확대 가능한 거래 구조를 제안드립니다.\n\n"
            f"  초기 거래 실적을 기반으로 조건을 점진적으로 개선하여\n"
            f"  양사 모두에게 이로운 관계를 구축할 수 있습니다."
        )
    elif tier == "cautious":
        solution = (
            f"  FlowPay는 {company_name}의 현재 상황을 고려하여\n"
            f"  리스크를 관리하면서도 거래 기회를 모색하는\n"
            f"  시범 거래 중심의 접근법을 제안드립니다.\n\n"
            f"  시범 거래 성공 시 단계적 조건 완화가 가능합니다."
        )
    else:
        solution = (
            f"  FlowPay는 {company_name}의 현재 재무 상황에 대한\n"
            f"  면밀한 검토를 진행하였습니다.\n\n"
            f"  현 시점에서는 특별 조건 하의 제한적 거래만 가능하며,\n"
            f"  재무 상태 개선 시 조건 재검토를 약속드립니다."
        )

    content = needs_text + solution

    subsections = [
        {"title": point, "content": template.section_guides.get("맞춤제안", "")}
        for point in template.emphasis_sections[:2]
    ]

    return ProposalSection(
        title="맞춤 제안",
        content=content,
        subsections=subsections,
    )


def _build_terms(
    margin_sim: MarginSimulation,
    risk_premium: RiskPremium,
    template: ProposalTemplate,
) -> ProposalSection:
    """3. 거래조건 섹션 생성."""

    rec = margin_sim.recommended
    rec_scenario = {
        "min": margin_sim.min_scenario,
        "likely": margin_sim.likely,
        "max": margin_sim.max_scenario,
    }[rec]

    terms_lines = [
        f"  [권장 거래 조건 — {rec_scenario.label}]",
        f"",
        f"  · 적용 마진율: {rec_scenario.margin_rate:.2f}%",
        f"    (기본 {rec_scenario.base_margin:.1f}% + 리스크프리미엄 {rec_scenario.risk_premium:.1f}%p)",
        f"  · 결제 조건: {risk_premium.recommended_payment_terms}",
        f"  · 신용 한도: {risk_premium.recommended_credit_limit}",
    ]

    if rec_scenario.expected_profit is not None:
        terms_lines.append(
            f"  · 예상 거래이익: {rec_scenario.expected_profit:,.0f}원"
        )

    if template.include_collateral_terms:
        terms_lines.append(f"\n  [담보/보증 요구사항]")
        terms_lines.append(f"  · 담보 필요: {'예' if risk_premium.requires_collateral else '아니오'}")
        terms_lines.append(f"  · 선급금 필요: {'예' if risk_premium.requires_advance_payment else '아니오'}")

    # 3가지 시나리오 비교
    terms_lines.append(f"\n  [시나리오 비교]")
    terms_lines.append(f"  {'시나리오':16s} {'마진율':>8s} {'예상이익':>14s}")
    terms_lines.append(f"  {'─' * 42}")
    for s in margin_sim.scenarios():
        marker = " ★" if s.label == rec_scenario.label else "  "
        profit = f"{s.expected_profit:>12,.0f}원" if s.expected_profit else "      -"
        terms_lines.append(f" {marker} {s.label:16s} {s.margin_rate:>7.2f}% {profit}")

    content = "\n".join(terms_lines)

    subsections = []
    if template.include_advance_payment:
        subsections.append({
            "title": "선급금 안내",
            "content": "리스크 등급에 따라 선급금이 요구됩니다. 거래 실적 누적 시 선급금 비율 조정 가능.",
        })

    return ProposalSection(
        title="거래 조건",
        content=content,
        subsections=subsections,
    )


def _build_next_action(
    company_name: str,
    template: ProposalTemplate,
    grade: Grade,
) -> ProposalSection:
    """4. Next Action 섹션 생성."""

    tier = template.tier
    if tier == "premium":
        steps = [
            "1. 파트너십 제안 검토 (본 제안서)",
            "2. 임원급 미팅 일정 조율 (1주 내)",
            "3. 세부 조건 협의 및 계약 체결",
            "4. 첫 거래 시작 + 전담팀 배정",
        ]
    elif tier == "standard":
        steps = [
            "1. 제안서 검토 및 내부 논의",
            "2. 실무 담당자 미팅 (2주 내)",
            "3. 시범 거래 조건 확정",
            "4. 시범 거래 진행 → 정식 계약 전환",
        ]
    elif tier == "cautious":
        steps = [
            "1. 제안서 검토 및 추가 자료 제출",
            "2. 담보/보증 관련 서류 협의",
            "3. 소규모 시범 거래 (1건)",
            "4. 결과 검토 후 거래 규모 조정",
        ]
    else:
        steps = [
            "1. 내부 리스크 심사위원회 검토 (2주 소요)",
            "2. 추가 재무 자료 요청 가능",
            "3. 심사 결과에 따른 조건부 거래 검토",
            "4. 재무 상태 개선 시 재평가 (6개월 후)",
        ]

    content = (
        f"  {template.call_to_action}\n\n"
        + "\n".join(f"  {s}" for s in steps)
    )

    subsections = [{
        "title": "담당자 연락처",
        "content": "FlowPay 영업팀 (담당: [영업 담당자명]) | [이메일] | [연락처]",
    }]

    return ProposalSection(
        title="Next Action",
        content=content,
        subsections=subsections,
    )


# ─────────────────────────────────────────────────────────────
# 메인 생성 함수
# ─────────────────────────────────────────────────────────────

def generate_proposal(
    company_name: str,
    grade: Grade,
    financial_score: FinancialScore,
    margin_sim: MarginSimulation,
    risk_premium: RiskPremium,
    template: ProposalTemplate,
    consultation: Optional[ConsultationAnalysis] = None,
    red_flags: Optional[list[RedFlag]] = None,
    proposal_date: Optional[str] = None,
) -> Proposal:
    """
    전체 4섹션 제안서 생성.

    Args:
        company_name: 기업명
        grade: 종합 등급
        financial_score: 재무 스코어
        margin_sim: 마진 시뮬레이션 결과
        risk_premium: 리스크 프리미엄
        template: 선택된 제안서 템플릿
        consultation: 상담 분석 (선택)
        red_flags: Red Flag 리스트 (선택)
        proposal_date: 제안일 (없으면 오늘)

    Returns:
        Proposal
    """
    flags = red_flags or []
    p_date = proposal_date or date.today().isoformat()

    rec_scenario = {
        "min": margin_sim.min_scenario,
        "likely": margin_sim.likely,
        "max": margin_sim.max_scenario,
    }[margin_sim.recommended]

    summary = (
        f"{company_name} ({grade.grade}등급) → "
        f"권장 마진 {rec_scenario.margin_rate:.2f}%, "
        f"결제조건: {risk_premium.recommended_payment_terms}"
    )

    key_metrics = {
        "종합등급": grade.grade,
        "종합점수": f"{grade.total_score:.1f}",
        "재무스코어": f"{grade.financial_score:.1f}",
        "비재무스코어": f"{grade.consultation_score:.1f}",
        "RedFlag": f"{len(flags)}건",
        "리스크프리미엄": f"+{risk_premium.total_premium:.1f}%p",
        "권장마진": f"{rec_scenario.margin_rate:.2f}%",
        "결제조건": risk_premium.recommended_payment_terms,
    }

    diagnosis = _build_diagnosis(
        company_name, grade, financial_score, consultation, flags, template,
    )
    recommendation = _build_recommendation(
        company_name, grade, consultation, margin_sim, template,
    )
    terms = _build_terms(margin_sim, risk_premium, template)
    next_action = _build_next_action(company_name, template, grade)

    return Proposal(
        company_name=company_name,
        proposal_date=p_date,
        grade=grade.grade,
        grade_description=grade.grade_description,
        template_tier=template.tier,
        diagnosis=diagnosis,
        recommendation=recommendation,
        terms=terms,
        next_action=next_action,
        summary_line=summary,
        total_score=grade.total_score,
        key_metrics=key_metrics,
    )


def proposal_summary(proposal: Proposal) -> str:
    """제안서 요약 (콘솔 출력용)."""
    lines = [
        f"═══ 제안서 생성 완료 ═══",
        f"  기업: {proposal.company_name}",
        f"  등급: {proposal.grade} ({proposal.total_score:.1f}점)",
        f"  템플릿: {proposal.template_tier}",
        f"  일자: {proposal.proposal_date}",
        f"  요약: {proposal.summary_line}",
        f"",
        f"  섹션 구성:",
    ]
    for i, s in enumerate(proposal.sections(), 1):
        lines.append(f"    {i}. {s.title} ({len(s.content)}자)")
    return "\n".join(lines)
