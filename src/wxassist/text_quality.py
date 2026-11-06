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

import difflib
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


# ---------- 非对话内容：语音时长 / 占位符 / 文件卡 / 文章卡 / 引用头 ----------
#
# 微信聊天面板里除了「双方说的话」，还会渲染很多不是消息的东西；它们都是可读的
# 文字，乱码过滤挡不住，必须按「消息类型」识别后丢掉：
# - 语音消息：气泡里只显示时长（6" / 6'' / 12″）；
# - 系统占位符：[图片] [语音] [表情] 这类（消息列表预览、引用块的占位文字）；
# - 文件消息：文件名 + 类型 + 大小（调研报告.md / MD / 29.2K）；
# - 公众号/视频号/文章卡片：公众号名片、N 篇原创内容、阅读 N 赞 N 等；
# - 引用块只剩一个「名字：」头（内容没识别出来）。
#
# 原则和乱码过滤一致：只认非常具体的结构，宁可漏掉，也不要误杀真实聊天。
VOICE_DURATION_RE = re.compile(r"^\s*\d{1,3}\s*(?:\"|″|”|''|′′|′|秒)\s*$")
PLACEHOLDER_WORDS = (
    "图片|表情|动画表情|语音|视频|文件|链接|位置|名片|转账|红包|小程序|聊天记录|收藏|"
    "音乐|视频号|公众号|直播|接龙|卡券|游戏|投票|合并转发|订单|视频通话"
)
# 必须带方括号/圆括号，或「语音 15"」这类带时长的写法；不认孤零零一个「图片」二字
PLACEHOLDER_RE = re.compile(
    r"^(?:[\[【(（]\s*(?:" + PLACEHOLDER_WORDS + r")\s*[\]】)）]\s*(?:\d{1,3}\s*[\"″”秒]?)?"
    r"|(?:语音|视频通话)\s*\d{1,3}\s*[\"″”秒''′]?)\s*$"
)
FILE_EXT_LINE_RE = re.compile(
    r"^(?:PDF|DOC|DOCX|XLS|XLSX|PPT|PPTX|ZIP|RAR|7Z|TAR|GZ|TXT|MD|CSV|EXE|MSI|APK|"
    r"MP3|MP4|AVI|MOV|PNG|JPG|JPEG|GIF|WEBP|ISO|DMG|JSON|XML|HTML|PY|JS|TS)$",
    re.I,
)
FILE_SIZE_RE = re.compile(
    r"^\d+(?:\.\d+)?\s*(?:[KMG]B?|B|字节)\s*(?:未下载|已下载|已过期)?$", re.I
)
# 红包卡：微信固定祝福语 + 卡片元信息。单看「恭喜发财，大吉大利」可能是真祝福，
# 必须和「微信红包 / 已领取 / 已存入零钱」同框才算卡片。
RED_PACKET_WISH = "恭喜发财，大吉大利"
RED_PACKET_MARKS = ("微信红包", "已领取", "已存入零钱", "查看红包", "领取红包", "红包已过期")
CARD_RES = (
    re.compile(r"公众号名片|视频号名片"),
    # 卡片元信息都独占一行；用行首/行尾锚定，避免「记得阅读 3 赞 5 下」这类正文误中
    re.compile(r"(?m)^\s*\d+\s*篇原创内容\s*$"),
    re.compile(r"(?m)^\s*\d+\s*个朋友关注\s*$"),
    re.compile(r"(?m)^\s*阅读\s*\d+\s*赞\s*\d+\s*$"),
    # 公众号文章页元信息：原创 2026年10月28日09:10 湖北 64人
    re.compile(r"(?m)^原创.*\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}.*\d+\s*人\s*$"),
    re.compile(r"(?m)^\s*视频号[:：]"),
)
QUOTE_HEADER_RE = re.compile(r"^([^\s：:]{1,12})[：:]\s*(\S.*)$")
URL_LINE_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9+.-]*://\S*$")
# 空引用头：只有「名字：」；名字限 4 字以内，且排除「注意：」这类常见正文开头
EMPTY_QUOTE_RE = re.compile(r"^([^\s：:]{1,4})[：:]\s*$")
EMPTY_QUOTE_STOPWORDS = (
    "注意", "提醒", "建议", "另外", "还有", "顺便", "其实", "所以", "但是",
    "因为", "如果", "然后", "最后", "首先", "其次", "总之", "对了", "结论",
)


def looks_like_voice_duration(text: str) -> bool:
    """语音气泡里只有时长，例如 6" / 6'' / 12″ / 8秒。"""
    return bool(VOICE_DURATION_RE.match((text or "").strip()))


def looks_like_placeholder(text: str) -> bool:
    """整条只是一个消息类型占位符，例如 [图片] / 【语音】 15"。"""
    return bool(PLACEHOLDER_RE.match((text or "").strip()))


def looks_like_file_card(text: str) -> bool:
    """文件消息卡片：文件名（含扩展名或独立扩展名行）+ 文件大小。

    微信卡片里大小可能不是最后一行（下面还有「微信电脑版」之类的平台行），
    所以只要大小出现在末尾几行内、且上面有扩展名/文件名，就按文件卡判掉。
    """
    lines = [line.strip() for line in (text or "").splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    size_positions = [index for index, line in enumerate(lines) if FILE_SIZE_RE.match(line)]
    if not size_positions or size_positions[-1] < len(lines) - 4:
        return False
    head = "\n".join(lines)
    if any(FILE_EXT_LINE_RE.match(line) for line in lines):
        return True
    return bool(re.search(r"\.[A-Za-z0-9]{2,5}\b", head))


def looks_like_card(text: str) -> bool:
    """公众号/视频号/文章/红包卡片：带只有卡片才会出现的元信息。"""
    raw = text or ""
    if any(pattern.search(raw) for pattern in CARD_RES):
        return True
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    # 「微信红包」独占一行只会出现在红包卡上；「已领取」单独一行却可能是真人回复，
    # 所以必须再带上固定祝福语才算卡片。
    if "微信红包" in lines:
        return True
    return RED_PACKET_WISH in lines and any(mark in lines for mark in RED_PACKET_MARKS)


def quote_echo_content(text: str) -> str:
    """引用块首行 `名字：被引用内容` 里的被引用内容；不是引用头返回空串。

    微信引用消息的第一行一定是「发言人：原消息开头」；单行引用块和
    「引用 + 回复」两行结构都能取出被引用的内容，交给上层和历史消息比对。
    """
    first = (text or "").splitlines()[0].strip() if (text or "").strip() else ""
    # 首行本身就是链接（没有「名字：」前缀）时不是引用头；
    # 但「某人：https://…」是被引用的链接消息，必须照样识别。
    if URL_LINE_RE.match(first):
        return ""
    match = QUOTE_HEADER_RE.match(first)
    if not match:
        return ""
    name = match.group(1)
    # 时间分隔符（14:47 / 4月20日14:47）不是引用头
    if re.fullmatch(r"[\d\s:/]+", name) or "月" in name or "日" in name:
        return ""
    return match.group(2).strip()


def looks_like_empty_quote_head(text: str) -> bool:
    """只有「名字：」的空引用头；排除「注意：」这类正常正文开头。"""
    match = EMPTY_QUOTE_RE.match((text or "").strip())
    return bool(match and match.group(1) not in EMPTY_QUOTE_STOPWORDS)


def looks_like_non_dialogue_line(text: str) -> bool:
    """逐行过滤用：只认不会和正文混淆的整行占位符/语音时长/空引用头。"""
    raw = (text or "").strip()
    if not raw:
        return True
    if looks_like_voice_duration(raw) or looks_like_placeholder(raw):
        return True
    return looks_like_empty_quote_head(raw)


def non_dialogue_reason(text: str) -> str:
    """返回这条文本「不是对话」的原因；空串表示像正常聊天文本。"""
    raw = (text or "").strip()
    if not raw:
        return ""
    if looks_like_voice_duration(raw):
        return "voice"
    if looks_like_placeholder(raw):
        return "placeholder"
    if looks_like_file_card(raw):
        return "file"
    if looks_like_card(raw):
        return "card"
    if looks_like_empty_quote_head(raw):
        return "quote"
    return ""


def looks_like_non_dialogue(text: str) -> bool:
    """整条文本是不是语音/占位符/文件卡/文章卡/空引用头这类非对话内容。"""
    return bool(non_dialogue_reason(text))


def normalize_for_match(text: str) -> str:
    """去掉所有空白并转小写：OCR 换行/空格差异不应该影响引用比对。"""
    return "".join((text or "").split()).lower()


def matches_known_quote(content: str, candidates: list[str]) -> bool:
    """引用内容是否和已知消息高度重合（引用回声）；太短的不判，避免误杀。"""
    if len(content) < 6:
        return False
    for candidate in candidates:
        if not candidate or len(candidate) < 6:
            continue
        if candidate == content:
            return True
        shorter, longer = (
            (candidate, content) if len(candidate) <= len(content) else (content, candidate)
        )
        if len(shorter) >= 6 and longer.startswith(shorter):
            return True
        if abs(len(candidate) - len(content)) <= max(4, int(len(content) * 0.3)):
            if difflib.SequenceMatcher(None, candidate, content).ratio() >= 0.9:
                return True
    return False


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
