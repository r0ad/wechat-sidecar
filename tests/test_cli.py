"""单实例锁测试。"""

from __future__ import annotations

from wxassist import cli


def test_single_instance_lock(tmp_path) -> None:
    assert cli.ensure_single_instance(str(tmp_path))
    try:
        assert not cli.ensure_single_instance(str(tmp_path))  # 第二次应被拒绝
    finally:
        assert cli._LOCK_FILE is not None
        cli._LOCK_FILE.close()
    assert cli.ensure_single_instance(str(tmp_path))  # 释放后可再次获取
    assert cli._LOCK_FILE is not None
    cli._LOCK_FILE.close()
