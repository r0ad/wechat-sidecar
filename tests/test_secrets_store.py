"""API Key 加密存储与 JSON 解析的单元测试。"""

from __future__ import annotations

import pytest

from wxassist import secrets_store


def test_save_load_and_mask(tmp_path) -> None:
    try:
        secrets_store.save_api_key(tmp_path, "deepseek", "sk-test-1234567890")
    except Exception as exc:  # 非 Windows / 无 DPAPI
        pytest.skip(f"DPAPI 不可用：{exc}")
    assert secrets_store.load_api_key(tmp_path, "deepseek") == "sk-test-1234567890"
    assert secrets_store.has_api_key(tmp_path, "deepseek")
    assert secrets_store.load_api_key(tmp_path, "openai") == ""
    masked = secrets_store.mask_key("sk-test-1234567890")
    assert masked.startswith("sk-t") and masked.endswith("7890")
    assert "123456" not in masked


def test_save_empty_deletes(tmp_path) -> None:
    try:
        secrets_store.save_api_key(tmp_path, "deepseek", "sk-a")
    except Exception as exc:
        pytest.skip(f"DPAPI 不可用：{exc}")
    secrets_store.save_api_key(tmp_path, "deepseek", "")
    assert secrets_store.load_api_key(tmp_path, "deepseek") == ""


def test_encrypted_file_is_not_plaintext(tmp_path) -> None:
    try:
        secrets_store.save_api_key(tmp_path, "deepseek", "sk-super-secret-value")
    except Exception as exc:
        pytest.skip(f"DPAPI 不可用：{exc}")
    blob = (tmp_path / secrets_store.FILE_NAME).read_bytes()
    assert b"sk-super-secret-value" not in blob
