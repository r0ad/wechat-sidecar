"""容错的 JSON 解析：模型经常带 ```json 围栏或多余解释。"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def extract_json(text: str) -> Any:
    """从模型输出里抠出第一个合法 JSON 值；失败返回 None。"""
    if not text:
        return None
    candidates: list[str] = []
    fenced = _FENCE.findall(text)
    candidates.extend(fenced)
    candidates.append(text)
    for candidate in candidates:
        candidate = candidate.strip()
        if not candidate:
            continue
        parsed = _try_parse(candidate)
        if parsed is not None:
            return parsed
        for opener, closer in (("{", "}"), ("[", "]")):
            start = candidate.find(opener)
            if start < 0:
                continue
            end = _matching_bracket(candidate, start, opener, closer)
            if end > start:
                parsed = _try_parse(candidate[start : end + 1])
                if parsed is not None:
                    return parsed
    return None


def _try_parse(raw: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return None


def _matching_bracket(text: str, start: int, opener: str, closer: str) -> int:
    depth = 0
    in_string = False
    escaped = False
    for idx in range(start, len(text)):
        char = text[idx]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == opener:
            depth += 1
        elif char == closer:
            depth -= 1
            if depth == 0:
                return idx
    return -1
