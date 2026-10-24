"""个性化提示词：人设 / 回复偏好，注入到每条回复的提示词。

「学我说话」生成的内容会写进 AUTO-STYLE 区块，保留用户手写内容不变。
文件存在本地 persona.txt，可导入/导出，换机器直接带走。
"""

from __future__ import annotations

from pathlib import Path

FILE_NAME = "persona.txt"
DEFAULT_MAX_CHARS = 5000
STYLE_BEGIN = "<!-- AUTO-STYLE:BEGIN -->"
STYLE_END = "<!-- AUTO-STYLE:END -->"


def persona_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / FILE_NAME


def load_persona(data_dir: str | Path) -> str:
    path = persona_path(data_dir)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def save_persona(data_dir: str | Path, text: str) -> None:
    path = persona_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def apply_style_section(text: str, profile: str) -> str:
    """把自动生成的说话风格写进固定区块，保留用户手写的内容。"""
    block = f"{STYLE_BEGIN}\n【我的说话风格（自动生成，可手动改）】\n{profile.strip()}\n{STYLE_END}"
    current = text or ""
    start = current.find(STYLE_BEGIN)
    end = current.find(STYLE_END)
    if start >= 0 and end > start:
        return current[:start] + block + current[end + len(STYLE_END) :]
    body = current.rstrip()
    return (body + "\n\n" if body else "") + block


def build_block(text: str, max_chars: int = DEFAULT_MAX_CHARS) -> str:
    content = (text or "").strip()
    if not content:
        return ""
    limit = max(200, int(max_chars or DEFAULT_MAX_CHARS))
    if len(content) > limit:
        content = content[:limit] + "\n…（已截断）"
    return (
        "【我的人设 / 回复偏好】（这里是用户自己的设定，必须遵守；"
        "它描述的是用户该怎么说话，不是对方说的话）\n" + content
    )
