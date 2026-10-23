# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置。

由 build.py 调用，通过环境变量控制：
  WXASSIST_ONEFILE=1   打包成单文件 exe（启动慢、体积大）
  WXASSIST_CONSOLE=1   保留控制台窗口（排查问题用）
"""

import os
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_dynamic_libs, collect_submodules

ROOT = Path(globals().get("SPECPATH", os.getcwd()))
ONEFILE = os.environ.get("WXASSIST_ONEFILE") == "1"
CONSOLE = os.environ.get("WXASSIST_CONSOLE") == "1"

# 图标、OCR 模型等数据文件
datas = [(str(ROOT / "src" / "wxassist" / "assets"), "wxassist/assets")]

hiddenimports = [
    "keyboard._winkeyboard",
    "win32api",
    "win32con",
    "win32gui",
    "win32process",
    "win32clipboard",
    "win32crypt",
    "win32timezone",
    "windows_capture",
]

binaries = []

# RapidOCR 自带 ONNX 模型，必须整包带上
for package in ("rapidocr_onnxruntime",):
    pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hidden

try:
    binaries += collect_dynamic_libs("windows_capture")
except Exception:
    pass
hiddenimports += collect_submodules("windows_capture")

# 明显用不到的大模块，减小体积
excludes = [
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtQuick",
    "PySide6.QtQml",
    "PySide6.Qt3DCore",
    "PySide6.QtCharts",
    "PySide6.QtMultimedia",
    "PySide6.QtDataVisualization",
    "tkinter",
    "pytest",
]

a = Analysis(
    [str(ROOT / "run.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)

ICON = str(ROOT / "src" / "wxassist" / "assets" / "wxassist.ico")
VERSION_FILE = str(ROOT / "packaging" / "version_info.txt")

if ONEFILE:
    exe = EXE(
        pyz,
        a.scripts,
        a.binaries,
        a.datas,
        [],
        name="wxassist",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        upx_exclude=[],
        runtime_tmpdir=None,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
        version=VERSION_FILE,
    )
else:
    exe = EXE(
        pyz,
        a.scripts,
        [],
        exclude_binaries=True,
        name="wxassist",
        debug=False,
        bootloader_ignore_signals=False,
        strip=False,
        upx=False,
        console=CONSOLE,
        disable_windowed_traceback=False,
        argv_emulation=False,
        target_arch=None,
        codesign_identity=None,
        entitlements_file=None,
        icon=ICON,
        version=VERSION_FILE,
    )
    coll = COLLECT(
        exe,
        a.binaries,
        a.datas,
        strip=False,
        upx=False,
        upx_exclude=[],
        name="wxassist",
    )
