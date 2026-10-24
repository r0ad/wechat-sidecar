#!/usr/bin/env python
"""一键打包成 exe（Windows / PyInstaller）。

用法：
    python build.py                # 目录版（推荐，启动快）：dist/wxassist/wxassist.exe
    python build.py --onefile      # 单文件版：dist/wxassist.exe（启动慢、体积大）
    python build.py --console      # 保留控制台窗口（排查启动问题）
    python build.py --no-install   # 不自动安装 PyInstaller

打包前请确认在项目虚拟环境里、且依赖已安装：
    .venv\\Scripts\\activate
    pip install -r requirements.txt
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SPEC = ROOT / "wxassist.spec"


def _safe_console() -> None:
    """CI 的 Windows 控制台可能是 cp1252，中文 print 会抛 UnicodeEncodeError。

    保留当前编码（本地中文控制台不受影响），只把错误处理改成 replace，保证不再崩。
    工作流里还会额外设置 PYTHONIOENCODING=utf-8，日志里的中文就是正常的。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except Exception:
            pass


_safe_console()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 wxassist 打包成 Windows exe")
    parser.add_argument("--onefile", action="store_true", help="打包成单文件 exe（默认目录版）")
    parser.add_argument("--console", action="store_true", help="保留控制台窗口（调试用）")
    parser.add_argument("--no-install", action="store_true", help="不自动安装 PyInstaller")
    parser.add_argument("--keep-build", action="store_true", help="保留 build/ 中间文件")
    return parser.parse_args()


def ensure_pyinstaller(no_install: bool) -> None:
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        if no_install:
            raise SystemExit("[x] 未安装 PyInstaller：pip install pyinstaller")
        print("[1/3] 安装 PyInstaller…")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def check_assets() -> None:
    icon = ROOT / "src" / "wxassist" / "assets" / "wxassist.ico"
    version = ROOT / "packaging" / "version_info.txt"
    if not icon.exists():
        raise SystemExit(f"[x] 缺少图标：{icon}")
    if not version.exists():
        raise SystemExit(f"[x] 缺少版本信息：{version}")
    if not SPEC.exists():
        raise SystemExit(f"[x] 缺少 spec：{SPEC}")


def main() -> int:
    args = parse_args()
    ensure_pyinstaller(args.no_install)
    check_assets()

    env = os.environ.copy()
    env["WXASSIST_ONEFILE"] = "1" if args.onefile else "0"
    env["WXASSIST_CONSOLE"] = "1" if args.console else "0"

    print(f"[2/3] 开始打包（{'onefile' if args.onefile else 'onedir'}，"
          f"{'console' if args.console else 'windowed'}）…")
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--clean",
        "--noconfirm",
        "--distpath",
        str(ROOT / "dist"),
        "--workpath",
        str(ROOT / "build"),
        str(SPEC),
    ]
    result = subprocess.run(cmd, cwd=ROOT, env=env)
    if result.returncode != 0:
        print("[x] 打包失败，请查看上面的 PyInstaller 日志")
        return result.returncode

    print("[3/3] 收尾…")
    if args.onefile:
        target_dir = ROOT / "dist"
        exe = target_dir / "wxassist-onefile.exe"
    else:
        target_dir = ROOT / "dist" / "wxassist"
        exe = target_dir / "wxassist.exe"

    # 附带示例配置，方便第一次运行
    example = ROOT / "config.example.json"
    if example.exists():
        shutil.copy2(example, target_dir / "config.example.json")

    if not args.keep_build:
        shutil.rmtree(ROOT / "build", ignore_errors=True)

    if not exe.exists():
        print(f"[x] 没找到产物：{exe}")
        return 1

    size_mb = exe.stat().st_size / 1024 / 1024
    print("\n[ok] 打包完成")
    print(f"     exe:  {exe}")
    print(f"     大小: {size_mb:.1f} MB（exe 本体；目录版总量见 dist/wxassist/）")
    if not args.console:
        print("     启动时不会出现控制台黑窗口（需要调试请加 --console 重新打包）")
    print("\n运行方式：")
    print(f"     {exe}")
    print("     首次运行点悬浮窗右上角 ⚙ 填 API Key（或先设置环境变量 DEEPSEEK_API_KEY）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
