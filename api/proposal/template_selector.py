"""
등급별 제안서 템플릿 선택기.

종합 등급(AAA~D)에 따라 제안서의 톤, 구조, 강조 포인트를 결정합니다.

ARCHITECTURE.md §3.4:
    - A급↑: 경쟁력 가격 / 장기 파트너십
    - BBB~BB: 표준 조건 / 단계 거래
    - B↓: 선급금 / 담보 / 보증

사용:
    from proposal.template_selector import select_template, ProposalTemplate

    template = select_template(grade="A", red_flag_count=0)
    print(template.tone, template.emphasis_sections)
"""

from __future__ import annotations

from dataclasses import dataclass, field


# ─────────────────────────────────────────────────────────────
# 템플릿 결과
# ─────────────────────────────────────────────────────────────

@dataclass
class ProposalTemplate:
    """제안서 템플릿 구성."""
    tier: str                    # premium | standard | cautious | restricted
    tier_label: str              # 표시명
    tone: str                    # 제안서 톤 설명
    greeting_style: str          # 인사말 스타일
    emphasis_sections: list[str] # 강조할 섹션 키워드
    include_risk_section: bool   # 리스크 섹션 포함 여부
    include_collateral_terms: bool  # 담보/보증 조건 포함
    include_advance_payment: bool   # 선급금 조건 포함
    call_to_action: str          # Next Action 유형
    max_proposal_length: str     # short | medium | detailed
    pricing_strategy: str        # 가격 전략 설명

    # 섹션별 가이드
    section_guides: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "tier": self.tier,
            "tier_label": self.tier_label,
            "tone": self.tone,
            "greeting_style": self.greeting_style,
            "emphasis_sections": self.emphasis_sections,
            "include_risk_section": self.include_risk_section,
            "include_collateral_terms": self.include_collateral_terms,
            "include_advance_payment": self.include_advance_payment,
            "call_to_action": self.call_to_action,
            "max_proposal_length": self.max_proposal_length,
            "pricing_strategy": self.pricing_strategy,
            "section_guides": self.section_guides,
        }


# ─────────────────────────────────────────────────────────────
# 등급 → 티어 매핑
# ─────────────────────────────────────────────────────────────

GRADE_TO_TIER: dict[str, str] = {
    "AAA": "premium",
    "AA":  "premium",
    "A":   "premium",
    "BBB": "standard",
    "BB":  "standard",
    "B":   "cautious",
    "CCC": "cautious",
    "CC":  "restricted",
    "C":   "restricted",
    "D":   "restricted",
}


# ─────────────────────────────────────────────────────────────
# 템플릿 정의
# ─────────────────────────────────────────────────────────────

TEMPLATES: dict[str, ProposalTemplate] = {
    "premium": ProposalTemplate(
        tier="premium",
        tier_label="프리미엄 (A급 이상)",
        tone="파트너십 지향적, 적극적, 장기 협력 강조",
        greeting_style="장기 파트너십을 위한 맞춤 제안",
        emphasis_sections=["경쟁력 있는 가격 조건", "장기 파트너십 혜택", "맞춤 솔루션"],
        include_risk_section=False,
        include_collateral_terms=False,
        include_advance_payment=False,
        call_to_action="파트너십 미팅 일정 조율",
        max_proposal_length="detailed",
        pricing_strategy="경쟁력 있는 최소 마진 적용, 볼륨 디스카운트 제안 가능",
        section_guides={
            "현황진단": "긍정적 재무 상태 강조, 성장 잠재력 부각. 상세한 산업 분석과 시장 기회 포함.",
            "맞춤제안": "고객 니즈 기반 솔루션 제시, Trapped Cash 최적화 효과 구체화. 장기 파트너십 프레임워크 포함.",
            "거래조건": "T+30~60일 후불, 넉넉한 신용한도, 볼륨 디스카운트 옵션 명시.",
            "Next Action": "담당 임원 미팅 + 파트너십 계약 논의 일정 제안.",
        },
    ),

    "standard": ProposalTemplate(
        tier="standard",
        tier_label="표준 (BBB~BB급)",
        tone="전문적, 균형잡힌, 신뢰 구축 지향",
        greeting_style="최적의 결제 솔루션 제안",
        emphasis_sections=["안정적 거래 구조", "단계별 확대 계획", "리스크 관리 방안"],
        include_risk_section=True,
        include_collateral_terms=False,
        include_advance_payment=False,
        call_to_action="조건 협의를 위한 실무 미팅",
        max_proposal_length="medium",
        pricing_strategy="리스크 반영 적정 마진, 거래 실적에 따른 조건 개선 가능성 언급",
        section_guides={
            "현황진단": "재무 현황을 균형있게 서술. 강점과 개선점을 함께 언급하되 건설적 톤 유지.",
            "맞춤제안": "표준 솔루션 + 고객 특성 반영. 단계별 거래 확대 로드맵 포함.",
            "거래조건": "T+15~30일 후불, 적정 신용한도. 거래 실적에 따른 조건 완화 경로 제시.",
            "Next Action": "조건 상세 협의 미팅 제안. 필요 서류 안내.",
        },
    ),

    "cautious": ProposalTemplate(
        tier="cautious",
        tier_label="주의 (B~CCC급)",
        tone="신중하고 조건 중심적, 리스크 관리 강조",
        greeting_style="안전한 거래 구조를 위한 제안",
        emphasis_sections=["리스크 관리 방안", "담보/보증 구조", "시범 거래 계획"],
        include_risk_section=True,
        include_collateral_terms=True,
        include_advance_payment=True,
        call_to_action="시범 거래 조건 협의",
        max_proposal_length="medium",
        pricing_strategy="리스크 프리미엄 충분히 반영, 선급금/담보 조건으로 리스크 완화",
        section_guides={
            "현황진단": "재무 리스크 요인을 객관적으로 기술. 개선 가능성도 함께 언급.",
            "맞춤제안": "소규모 시범 거래로 시작하는 단계적 접근법 제시. 리스크 완화 방안 포함.",
            "거래조건": "선급금 30~50% + COD 조건. 담보/보증 요구사항 구체화. 실적 기반 완화 조건 명시.",
            "Next Action": "시범 거래 규모 및 조건 협의. 담보/보증 관련 서류 요청.",
        },
    ),

    "restricted": ProposalTemplate(
        tier="restricted",
        tier_label="제한 (CC~D급)",
        tone="보수적, 조건부 거래 가능성 타진",
        greeting_style="특별 조건 하의 거래 가능성 검토",
        emphasis_sections=["선급금 100%", "거래 조건 제약", "개선 로드맵"],
        include_risk_section=True,
        include_collateral_terms=True,
        include_advance_payment=True,
        call_to_action="내부 심사 후 조건부 거래 검토",
        max_proposal_length="short",
        pricing_strategy="최대 마진 적용, 선급금 100% 필수. D급은 거래 보류 권고.",
        section_guides={
            "현황진단": "주요 리스크 팩터를 명확히 기술. 거래 시 주의사항 안내.",
            "맞춤제안": "선급금 100% 기반의 제한적 거래 구조만 제안. D급은 거래 보류 권고문 작성.",
            "거래조건": "선급금 70~100%. 거래 한도 극소화. 건별 승인 필요.",
            "Next Action": "내부 리스크 심사위원회 검토 후 결과 회신. 필요 시 추가 자료 요청.",
        },
    ),
}


# ─────────────────────────────────────────────────────────────
# 선택 함수
# ─────────────────────────────────────────────────────────────

def select_template(
    grade: str,
    red_flag_count: int = 0,
    override_tier: str | None = None,
) -> ProposalTemplate:
    """
    등급 기반 제안서 템플릿 선택.

    Red Flag 5개 이상이면 한 단계 하향 조정합니다.

    Args:
        grade: 종합 등급 (AAA~D)
        red_flag_count: Red Flag 개수
        override_tier: 수동 티어 지정 (premium/standard/cautious/restricted)

    Returns:
        ProposalTemplate
    """
    if override_tier and override_tier in TEMPLATES:
        return TEMPLATES[override_tier]

    tier = GRADE_TO_TIER.get(grade, "restricted")

    # Red Flag 5개 이상이면 한 단계 하향
    if red_flag_count >= 5:
        downgrade_map = {
            "premium": "standard",
            "standard": "cautious",
            "cautious": "restricted",
        }
        tier = downgrade_map.get(tier, tier)

    return TEMPLATES[tier]


def template_summary(template: ProposalTemplate) -> str:
    """템플릿 선택 결과 요약."""
    lines = [
        f"═══ 제안서 템플릿: {template.tier_label} ═══\n",
        f"  톤: {template.tone}",
        f"  강조점: {', '.join(template.emphasis_sections)}",
        f"  CTA: {template.call_to_action}",
        f"  길이: {template.max_proposal_length}",
        f"  가격전략: {template.pricing_strategy}",
    ]
    if template.include_collateral_terms:
        lines.append("  ⚠️ 담보/보증 조건 포함")
    if template.include_advance_payment:
        lines.append("  ⚠️ 선급금 조건 포함")
    return "\n".join(lines)
