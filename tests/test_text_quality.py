"""OCR 乱码判断测试：图片里的文字不能被当成聊天内容。

样本都来自真实截图/真实运行记录（图片消息、表情、桌面误拍）。
"""

from __future__ import annotations

import pytest

from wxassist.text_quality import cjk_ratio, is_machine_token, looks_like_garbled


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
