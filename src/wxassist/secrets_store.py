"""API Key 的本地加密存储（Windows DPAPI，当前用户绑定）。

Key 只存在本机、只有当前 Windows 用户能解密；不写入 git，不写日志。
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

try:  # pragma: no cover - 仅 Windows
    import win32crypt
except ImportError:  # pragma: no cover
    win32crypt = None  # type: ignore[assignment]

FILE_NAME = "secret.bin"


def _store_path(data_dir: str | Path) -> Path:
    return Path(data_dir) / FILE_NAME


def _encrypt(plain: bytes) -> bytes:
    if win32crypt is None:
        raise RuntimeError("缺少 pywin32，无法使用 DPAPI 加密")
    return win32crypt.CryptProtectData(plain, "wxassist", None, None, None, 0)


def _decrypt(blob: bytes) -> bytes:
    if win32crypt is None:
        raise RuntimeError("缺少 pywin32，无法使用 DPAPI 解密")
    _description, plain = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return plain


def _load_all(data_dir: str | Path) -> dict[str, str]:
    path = _store_path(data_dir)
    if not path.exists():
        return {}
    try:
        raw = _decrypt(path.read_bytes())
        data = json.loads(raw.decode("utf-8"))
        return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return {}


def _save_all(data_dir: str | Path, data: dict[str, str]) -> None:
    path = _store_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_encrypt(json.dumps(data, ensure_ascii=False).encode("utf-8")))


def save_api_key(data_dir: str | Path, provider: str, api_key: str) -> None:
    data = _load_all(data_dir)
    if api_key:
        data[provider] = api_key
    else:
        data.pop(provider, None)
    if data:
        _save_all(data_dir, data)
    else:
        path = _store_path(data_dir)
        if path.exists():
            path.unlink()


def load_api_key(data_dir: str | Path, provider: str) -> str:
    return _load_all(data_dir).get(provider, "")


def has_api_key(data_dir: str | Path, provider: str) -> bool:
    return bool(load_api_key(data_dir, provider))


def mask_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}…{key[-4:]}"


def encode_for_debug(key: str) -> str:  # pragma: no cover - 仅供调试
    return base64.b64encode(key.encode()).decode()[:8]
