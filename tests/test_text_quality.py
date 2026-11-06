"""OCR 乱码判断测试：图片里的文字不能被当成聊天内容。

样本都来自真实截图/真实运行记录（图片消息、表情、桌面误拍）。
"""

from __future__ import annotations

import pytest

from wxassist.text_quality import (
    cjk_ratio,
    is_machine_token,
    looks_like_file_card,
    looks_like_garbled,
    looks_like_non_dialogue,
    non_dialogue_reason,
    quote_echo_content,
)


# 图片/表情里被硬读出来的乱码（都带 OCR 置信度）
GARBLED = [
    ("TE088n m877a, e38i8nM\nHEERA8\nORFRA.", 0.55),
    ("Fe088nm8TTa, e3aiBnM\nBEAIRHATB9", 0.55),
    ("CGNAZHHINTTS\nNANTWRERSTNHUA.\n5784", 0.60),
    ("HEERA8", 0.56),
    ("ORFRA.", 0.60),
    ("IPJH*", 0.55),
]

# 真实聊天文本：哪怕 OCR 置信度不高也不能丢
REAL = [
    ("才把数据导入到excel里，现在还在处理", 0.99),
    ("合并相似的问题", 0.99),
    ("分析的怎么样了 你的聊天记录", 0.99),
    ("有个生成标书的，用了rag", 0.99),
    ("先用1$试试", 0.99),
    ("OK", 0.99),
    ("PDF 发我", 0.99),
    ("5pm 见", 0.99),
    ("Send me the PDF at 5pm", 0.99),
    ("iPhone15 开箱", 0.99),
    ("gpt-4o 模型", 0.99),
    ("win11 usb3", 0.99),
    ("https://r0ad.host/", 0.99),
    ("r0ad@example.com", 0.99),
    ("好的", 0.55),  # 中文短消息，置信度低也保留
    ("在吗", 0.60),
]


@pytest.mark.parametrize(("text", "score"), GARBLED)
def test_garbled_image_text_is_rejected(text: str, score: float) -> None:
    assert looks_like_garbled(text, score) is True


@pytest.mark.parametrize(("text", "score"), REAL)
def test_real_chat_text_is_kept(text: str, score: float) -> None:
    assert looks_like_garbled(text, score) is False


def test_multiline_garbled_rejected_without_score() -> None:
    """启动时清理历史乱码没有置信度可用，靠硬证据也要能认出来。"""
    assert looks_like_garbled("TE088n m877a, e38i8nM\nHEERA8\nORFRA.")
    assert looks_like_garbled("Fe088nm8TTa, e3aiBnM\nBEAIRHATB9")
    assert not looks_like_garbled("合并相似的问题")
    assert not looks_like_garbled("https://r0ad.host/")


def test_short_or_symbol_text_is_left_alone() -> None:
    """太短、只有数字或只有符号的文本不判乱码，避免误杀（如「0」「88」）。"""
    for text in ("0", "24", "24000", "88", "*", "……", ""):
        assert looks_like_garbled(text, 0.4) is False


def test_machine_token_rules() -> None:
    assert is_machine_token("TE088n")
    assert is_machine_token("m877a")
    assert is_machine_token("e38i8nM")
    assert not is_machine_token("HEERA8")  # 字母在前、数字在尾：像产品型号，靠置信度兜底
    assert not is_machine_token("win11")
    assert not is_machine_token("usb3")
    assert not is_machine_token("iPhone15")
    assert not is_machine_token("5pm")
    assert not is_machine_token("OK")


def test_cjk_ratio() -> None:
    assert cjk_ratio("合并相似的问题") > 0.9
    assert cjk_ratio("TE088n m877a") == 0.0
    assert cjk_ratio("") == 0.0


# ---------- 非对话内容：语音时长 / 占位符 / 文件卡 / 文章卡 / 空引用头 ----------
NON_DIALOGUE = [
    '3"',
    '10"',
    "12''",
    "8秒",
    " 6” ",
    "[图片]",
    "【语音】",
    '[语音] 15"',
    '语音 6"',
    "微信聊天监控与AI回复助手-\n调研报告.md\nMD\n29.2K",
    "调研报告.pdf\n2.1M",
    "芯和半导体Xpeedic\n公众号名片",
    "视频号：芯和半导体Xpeedic\n215篇原创内容\n1个朋友关注",
    "抖音团队效率低下：是编导摆烂，\n还是管理失职？\n阅读4720 赞 24",
    "原创storyJavaScript2026年10月28日09:10湖北64人",
    "卢恒：",
]

REAL_CHAT = [
    "24000",
    "15",
    "3",
    "好的",
    "会议纪要.docx 已经发你了",
    "可以多关注下我们公司的公众号",
    "我们那家店开了几十年了",
    "注意：明天带伞",
    "冯智：数字游民吗？\n服药时最好不要吃柚子",
    "60",
    "6月30日",
]


@pytest.mark.parametrize("text", NON_DIALOGUE)
def test_non_dialogue_content_is_rejected(text: str) -> None:
    assert looks_like_non_dialogue(text) is True
    assert non_dialogue_reason(text)


@pytest.mark.parametrize("text", REAL_CHAT)
def test_real_chat_text_is_not_non_dialogue(text: str) -> None:
    assert looks_like_non_dialogue(text) is False


def test_voice_duration_needs_quote_or_seconds() -> None:
    """光秃秃的数字是正常消息（价格、数量），不能当语音时长。"""
    assert looks_like_non_dialogue("8") is False
    assert looks_like_non_dialogue("88") is False


def test_file_card_needs_size_and_extension() -> None:
    assert looks_like_file_card("微信聊天监控与AI回复助手-\n调研报告.md\nMD\n29.2K")
    assert looks_like_file_card("方案.pdf\n2.1M")
    assert not looks_like_file_card("好的\n20K")


def test_quote_echo_content() -> None:
    assert quote_echo_content("随遇而安：经济师是怎么个说法呢") == "经济师是怎么个说法呢"
    assert quote_echo_content("冯智：数字游民吗？\n服药时最好不要吃柚子") == "数字游民吗？"
    assert quote_echo_content("好的") == ""
    assert quote_echo_content("https://r0ad.host/") == ""
    assert quote_echo_content("4月20日14:47") == ""
    assert quote_echo_content("14:47") == ""


def test_non_dialogue_line_filter_is_narrow() -> None:
    """逐行过滤只丢明确的占位符/时长/空引用头，卡片/文件卡交给整条判断。"""
    from wxassist.text_quality import looks_like_non_dialogue_line

    assert looks_like_non_dialogue_line('10"')
    assert looks_like_non_dialogue_line("[图片]")
    assert looks_like_non_dialogue_line("卢恒：")
    assert not looks_like_non_dialogue_line("公众号名片")
    assert not looks_like_non_dialogue_line("调研报告.md")


def test_long_colon_heads_are_not_treated_as_empty_quotes() -> None:
    """「我打个比方：」这类 5 字以上的正文开头不能被当成空引用头删掉。"""
    from wxassist.text_quality import looks_like_non_dialogue_line

    assert not looks_like_non_dialogue_line("我打个比方：")
    assert not looks_like_non_dialogue_line("注意：")
    assert not looks_like_non_dialogue_line("提醒：")


def test_card_rules_do_not_match_normal_sentences() -> None:
    """名片/日期人数/阅读赞只有卡片独占一行时才算，正文里出现不能误判。"""
    from wxassist.text_quality import looks_like_card

    assert not looks_like_card("这是我的个人名片")
    assert not looks_like_card("2026年5月1日 10:30 我们3人出发")
    assert not looks_like_card("记得阅读 3 赞 5 下")
    assert looks_like_card("芯和半导体Xpeedic\n公众号名片")
    assert looks_like_card("阅读4720 赞 24")
