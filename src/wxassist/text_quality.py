"""OCR 文本可信度：把图片/表情里硬读出来的乱码挡在消息之外。

微信的图片消息在屏幕上只是一个缩略图，里面的字往往只有几个像素高。
RapidOCR 去识别这种区域时会输出一串随机拉丁字母/数字，例如：

    TE088n m877a, e38i8nM
    HEERA8
    ORFRA.

这些串没有语义、也不是对方的原话；一旦进上下文，会显示在「对方最新消息」里，
还会被发给模型，导致候选回复跑偏（「最后那条乱码重发下」就是这么来的）。

原则：宁可漏掉可疑的拉丁乱码，也不误杀真实消息。所以判断很保守：
- 中文占比像样（>= 15%）就当正常聊天文本，一律放行；
- URL / 邮箱 / 盘符路径放行；
- 纯拉丁/数字文本必须满足硬条件才判乱码：
  1. 「机器串」>= 2 个（字母数字混排、且不像 5pm/12px 这种数字+单位）；
  2. 或整段没有任何元音（>= 5 个字母时），英文里不可能出现；
  3. 或 OCR 置信度偏低时，单个机器串 / 全大写短串 / 元音极少也算。
"""

from __future__ import annotations

import re

CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
# URL、邮箱、Windows 路径：不是聊天乱码，直接放行
SAFE_RE = re.compile(
    r"(?:https?://|www\.|://|[A-Za-z]:\\|\\\\|[\w.+-]+@[\w-]+\.[A-Za-z]{2,})"
)
# 5pm / 12px / 3d / 1080p 这类「数字 + 短单位」，是正常人话，不算机器串
UNIT_RE = re.compile(r"\d{1,4}[A-Za-z]{1,2}")
VOWELS = frozenset("aeiouAEIOU")

MIN_CJK_RATIO = 0.15  # 中文占比达到这个值就认为是正常聊天文本
LOW_SCORE = 0.85  # 低于这个置信度，才允许用「弱证据」判乱码
MIN_VOWEL_RATIO = 0.2  # 英文单词的元音比例不会这么低


def cjk_ratio(text: str) -> float:
    """中文（含扩展 A/兼容区）字符占可见字符的比例。"""
    counts = _counts(text or "")
    visible = counts["cjk"] + counts["letters"] + counts["digits"]
    if visible <= 0:
        return 0.0
    return counts["cjk"] / visible


def looks_like_garbled(text: str, score: float | None = None) -> bool:
    """判断一段 OCR 文本是否是不可用的乱码（多半来自图片/表情里的文字）。

    score 是 OCR 置信度；传了才启用「弱证据」规则，传 None 时只认可硬证据，
    以便安全地清理历史记录（没有置信度可用）。
    """
    raw = (text or "").strip()
    if not raw:
        return False
    counts = _counts(raw)
    letters, digits, visible = counts["letters"], counts["digits"], counts["visible"]
    if visible <= 0:
        return False
    # 中文文本：OCR 认错字是另一类问题，这里不处理，避免误杀真实消息
    if counts["cjk"] / visible >= MIN_CJK_RATIO:
        return False
    if SAFE_RE.search(raw):
        return False
    if letters + digits < 3:  # 太短，无法判断
        return False

    tokens = [token for token in TOKEN_RE.findall(raw) if len(token) >= 2]
    if not tokens:
        return False
    machine = [token for token in tokens if is_machine_token(token)]

    # 硬证据 1：多个机器串（"TE088n m877a" 这种一眼假）
    if len(machine) >= 2:
        return True
    # 硬证据 2：一个元音都没有的拉丁串（英文/拼音里不存在，>=5 个字母才判）
    if letters >= 5 and not any(ch in VOWELS for ch in raw if ch.isascii() and ch.isalpha()):
        return True

    # 弱证据：只在置信度不高时启用
    if score is not None and score < LOW_SCORE:
        if machine:
            return True
        if len(tokens) == 1 and _shouty(tokens[0]) and len(tokens[0]) >= 4:
            return True
        if letters >= 4 and _vowel_ratio(raw) < MIN_VOWEL_RATIO:
            return True
        # 整段大写、还很长：正常聊天不会这样写（大写缩写都很短）
        if letters >= 8 and not any(ch.islower() for ch in raw):
            return True
    return False


def is_machine_token(token: str) -> bool:
    """字母数字随机混排的「机器串」（TE088n / m877a / e38i8nM）。

    只认字母和数字互相穿插的写法；win11、usb3、iPhone15、5pm 这类
    「单词 + 数字」不判，免得误伤正常消息。
    """
    core = token.strip("._-")
    if len(core) < 3:
        return False
    has_alpha = any(ch.isascii() and ch.isalpha() for ch in core)
    has_digit = any(ch.isdigit() for ch in core)
    if not (has_alpha and has_digit):
        return False
    if UNIT_RE.fullmatch(core):
        return False
    # 末尾紧贴数字的字母（TE088n / m877a）
    if core[-1].isalpha() and core[-2].isdigit():
        return True
    # 字母被数字夹住（e38i8nM），或数字被字母夹住（a1b id 之类）
    for index in range(1, len(core) - 1):
        prev, cur, nxt = core[index - 1], core[index], core[index + 1]
        if cur.isdigit() and prev.isalpha() and nxt.isalpha():
            return True
        if cur.isalpha() and prev.isdigit() and nxt.isdigit():
            return True
    return False


def _shouty(token: str) -> bool:
    """全大写且没有任何小写字母。"""
    letters = [ch for ch in token if ch.isascii() and ch.isalpha()]
    return bool(letters) and all(ch.isupper() for ch in letters)


def _vowel_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isascii() and ch.isalpha()]
    if not letters:
        return 1.0
    return sum(1 for ch in letters if ch in VOWELS) / len(letters)


def _counts(text: str) -> dict[str, int]:
    cjk = letters = digits = 0
    for ch in text:
        if CJK_RE.match(ch):
            cjk += 1
        elif ch.isascii() and ch.isalpha():
            letters += 1
        elif ch.isdigit() and ch.isascii():
            digits += 1
    return {"cjk": cjk, "letters": letters, "digits": digits, "visible": cjk + letters + digits}
