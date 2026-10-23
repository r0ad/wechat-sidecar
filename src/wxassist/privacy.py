"""隐私：发送给大模型前脱敏；敏感话题识别。"""

from __future__ import annotations

import re

from .models import Message

PHONE = re.compile(r"(?<!\d)(1[3-9]\d{9})(?!\d)")
ID_CARD = re.compile(r"(?<!\d)(\d{17}[\dXx])(?!\d)")
LONG_NUMBER = re.compile(r"(?<!\d)(\d{16,19})(?!\d)")
EMAIL = re.compile(r"\b([A-Za-z0-9._%+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
LANDLINE = re.compile(r"(?<!\d)(0\d{2,3}-?\d{7,8})(?!\d)")


def redact(text: str) -> str:
    """手机号 / 身份证 / 银行卡 / 邮箱 / 座机 → 打码。"""
    text = PHONE.sub(lambda m: f"{m.group(1)[:3]}****{m.group(1)[-4:]}", text)
    text = ID_CARD.sub(lambda m: f"{m.group(1)[:6]}********{m.group(1)[-4:]}", text)
    text = LONG_NUMBER.sub(lambda m: f"****{m.group(1)[-4:]}", text)
    text = EMAIL.sub(lambda m: f"{m.group(1)[:1]}***@{m.group(2)}", text)
    text = LANDLINE.sub("*******", text)
    return text


def redact_messages(messages: list[Message]) -> list[Message]:
    return [
        Message(
            session=m.session,
            text=redact(m.text),
            is_me=m.is_me,
            ts=m.ts,
            sender=m.sender,
        )
        for m in messages
    ]


SENSITIVE_TOPICS: dict[str, tuple[str, ...]] = {
    "金钱": ("借钱", "转账", "打款", "汇款", "借我", "还钱", "银行卡", "红包"),
    "验证码/密码": ("验证码", "密码", "登录码", "支付密码"),
    "身份信息": ("身份证", "护照", "户口本", "住址"),
    "法律/合同": ("合同", "签字", "律师", "起诉", "报警", "派出所"),
    "医疗": ("医院", "手术", "怀孕", "确诊", "吃药", "病情"),
    "人身安全": ("自杀", "轻生", "不想活", "跳楼"),
}


def sensitive_topics(text: str) -> list[str]:
    hits: list[str] = []
    for label, words in SENSITIVE_TOPICS.items():
        if any(word in text for word in words):
            hits.append(label)
    return hits
