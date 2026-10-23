"""单实例锁测试。"""

from __future__ import annotations

from wxassist.cli import _LOCK_FILE, ensure_single_instance


def test_single_instance_lock(tmp_path) -> None:
    assert ensure_single_instance(str(tmp_path))
    assert not ensure_single_instance(str(tmp_path))  # 第二次应被拒绝
    _LOCK_FILE.close()
    assert ensure_single_instance(str(tmp_path))  # 释放后可再次获取
    _LOCK_FILE.close()
