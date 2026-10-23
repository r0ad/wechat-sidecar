"""开发/运行入口：把 src 加入路径后调用 CLI。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

# 控制台按 UTF-8 输出，避免中文在 cmd 里乱码
if sys.platform == "win32" and sys.stdout is not None:
    try:
        import ctypes

        ctypes.windll.kernel32.SetConsoleOutputCP(65001)
        ctypes.windll.kernel32.SetConsoleCP(65001)
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        if sys.stderr is not None:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 打包成 windowed exe 后没有控制台，print 不能写到 None；落到本地日志里方便排查
if getattr(sys, "frozen", False) and (sys.stdout is None or sys.stderr is None):
    import os

    log_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "wxassist"
    log_dir.mkdir(parents=True, exist_ok=True)
    stream = open(log_dir / "console.log", "a", encoding="utf-8", buffering=1)
    sys.stdout = stream
    sys.stderr = stream

from wxassist.cli import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
