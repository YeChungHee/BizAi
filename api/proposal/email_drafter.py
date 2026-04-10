"""
이메일 제안서 초안 생성기.

Proposal 객체를 이메일 형식으로 변환합니다.
향후 Gmail MCP 연동 시 draft 자동 생성이 가능합니다.

ARCHITECTURE.md §3.4:
    Gmail MCP로 draft 생성 (담당자 검토 후 발송)

사용:
    from proposal.email_drafter import draft_email, EmailDraft

    draft = draft_email(
        proposal=proposal,
        recipient_name="김영업",
        recipient_email="kim@company.com",
        sender_name="박담당",
    )
    print(draft.subject)
    print(draft.body)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from proposal.proposal_generator import Proposal


# ─────────────────────────────────────────────────────────────
# 이메일 초안 객체
# ─────────────────────────────────────────────────────────────

@dataclass
class EmailDraft:
    """이메일 제안서 초안."""
    subject: str
    body: str
    recipient_name: str
    recipient_email: str
    sender_name: str
    sender_email: str
    cc: list[str] = field(default_factory=list)

    # 메타
    company_name: str = ""
    grade: str = ""
    template_tier: str = ""

    def to_dict(self) -> dict:
        return {
            "subject": self.subject,
            "body": self.body,
            "recipient_name": self.recipient_name,
            "recipient_email": self.recipient_email,
            "sender_name": self.sender_name,
            "sender_email": self.sender_email,
            "cc": self.cc,
            "company_name": self.company_name,
            "grade": self.grade,
            "template_tier": self.template_tier,
        }

    def to_gmail_payload(self) -> dict:
        """Gmail API 호환 payload (draft 생성용)."""
        return {
            "to": f"{self.recipient_name} <{self.recipient_email}>",
            "cc": ", ".join(self.cc) if self.cc else None,
            "subject": self.subject,
            "body": self.body,
        }


# ─────────────────────────────────────────────────────────────
# 제목 생성
# ─────────────────────────────────────────────────────────────

SUBJECT_TEMPLATES: dict[str, str] = {
    "premium":    "[FlowPay] {company} — 장기 파트너십 맞춤 제안",
    "standard":   "[FlowPay] {company} — 결제 솔루션 제안",
    "cautious":   "[FlowPay] {company} — 거래 조건 제안",
    "restricted": "[FlowPay] {company} — 거래 검토 결과 안내",
}


# ─────────────────────────────────────────────────────────────
# 이메일 본문 생성
# ─────────────────────────────────────────────────────────────

def _build_email_body(
    proposal: Proposal,
    recipient_name: str,
    sender_name: str,
) -> str:
    """이메일 본문을 제안서 기반으로 생성."""

    tier = proposal.template_tier

    # 인사말
    if tier == "premium":
        greeting = (
            f"{recipient_name} 담당자님께,\n\n"
            f"안녕하세요. FlowPay {sender_name}입니다.\n"
            f"귀사와의 장기적 파트너십을 위해 맞춤 제안을 드리게 되어 기쁩니다.\n"
        )
    elif tier == "standard":
        greeting = (
            f"{recipient_name} 담당자님께,\n\n"
            f"안녕하세요. FlowPay {sender_name}입니다.\n"
            f"귀사에 적합한 결제 솔루션을 제안드리고자 연락드립니다.\n"
        )
    elif tier == "cautious":
        greeting = (
            f"{recipient_name} 담당자님께,\n\n"
            f"안녕하세요. FlowPay {sender_name}입니다.\n"
            f"귀사와의 거래 가능성을 검토하여 아래와 같이 제안드립니다.\n"
        )
    else:
        greeting = (
            f"{recipient_name} 담당자님께,\n\n"
            f"안녕하세요. FlowPay {sender_name}입니다.\n"
            f"귀사에 대한 검토 결과를 안내드립니다.\n"
        )

    # 본문 — 핵심 요약
    summary = f"\n{'─' * 50}\n"
    summary += f"■ 요약\n\n"
    for key, val in proposal.key_metrics.items():
        summary += f"  · {key}: {val}\n"
    summary += f"\n{'─' * 50}\n"

    # 본문 — 4개 섹션 요약 (이메일용 축약)
    sections_text = ""
    for i, section in enumerate(proposal.sections(), 1):
        sections_text += f"\n■ {i}. {section.title}\n\n"
        # 이메일에서는 내용을 요약하여 표시
        content_lines = section.content.strip().split("\n")
        # 최대 10줄만 표시
        display_lines = content_lines[:10]
        for line in display_lines:
            sections_text += f"{line}\n"
        if len(content_lines) > 10:
            sections_text += f"  ... (상세 내용은 첨부 제안서 참조)\n"

    # 마무리
    if tier == "premium":
        closing = (
            f"\n{'─' * 50}\n"
            f"상세한 내용 논의를 위해 편하신 시간에 미팅을 요청드립니다.\n"
            f"귀사의 성장에 FlowPay가 함께할 수 있기를 기대합니다.\n\n"
            f"감사합니다.\n"
            f"FlowPay {sender_name} 드림"
        )
    elif tier == "standard":
        closing = (
            f"\n{'─' * 50}\n"
            f"궁금하신 사항이 있으시면 언제든 연락 부탁드립니다.\n"
            f"좋은 협력 관계를 기대합니다.\n\n"
            f"감사합니다.\n"
            f"FlowPay {sender_name} 드림"
        )
    else:
        closing = (
            f"\n{'─' * 50}\n"
            f"추가 문의사항이 있으시면 연락 부탁드립니다.\n\n"
            f"감사합니다.\n"
            f"FlowPay {sender_name} 드림"
        )

    return greeting + summary + sections_text + closing


# ─────────────────────────────────────────────────────────────
# 메인 함수
# ─────────────────────────────────────────────────────────────

def draft_email(
    proposal: Proposal,
    recipient_name: str,
    recipient_email: str,
    sender_name: str = "FlowPay 영업팀",
    sender_email: str = "sales@flowpay.co.kr",
    cc: Optional[list[str]] = None,
) -> EmailDraft:
    """
    Proposal로부터 이메일 초안 생성.

    Args:
        proposal: 완성된 제안서
        recipient_name: 수신자명
        recipient_email: 수신자 이메일
        sender_name: 발신자명
        sender_email: 발신자 이메일
        cc: 참조 이메일 리스트

    Returns:
        EmailDraft
    """
    tier = proposal.template_tier
    subject_tpl = SUBJECT_TEMPLATES.get(tier, SUBJECT_TEMPLATES["standard"])
    subject = subject_tpl.format(company=proposal.company_name)

    body = _build_email_body(proposal, recipient_name, sender_name)

    return EmailDraft(
        subject=subject,
        body=body,
        recipient_name=recipient_name,
        recipient_email=recipient_email,
        sender_name=sender_name,
        sender_email=sender_email,
        cc=cc or [],
        company_name=proposal.company_name,
        grade=proposal.grade,
        template_tier=tier,
    )


def email_summary(draft: EmailDraft) -> str:
    """이메일 초안 요약."""
    lines = [
        f"═══ 이메일 초안 ═══",
        f"  To: {draft.recipient_name} <{draft.recipient_email}>",
        f"  From: {draft.sender_name} <{draft.sender_email}>",
    ]
    if draft.cc:
        lines.append(f"  CC: {', '.join(draft.cc)}")
    lines.extend([
        f"  Subject: {draft.subject}",
        f"  기업: {draft.company_name} ({draft.grade}등급)",
        f"  본문: {len(draft.body)}자",
    ])
    return "\n".join(lines)
