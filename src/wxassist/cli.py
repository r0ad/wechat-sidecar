"""命令行入口。

用法：
  python run.py                 # 启动悬浮窗（需要微信已登录、窗口不要最小化）
  python run.py --selftest      # 不开 UI，验证截屏 + OCR + 解析
  python run.py --selftest --snapshot snapshots/calib.png
  python run.py --demo          # 合成对话演示（Mock 模型，不需要 API Key）
  python run.py --screenshot docs/overlay.png
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time

from . import __version__
from .config import Config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wxassist",
        description="微信 AI 回复助手：截屏 + 本地 OCR + 候选回复，永不自动发送",
    )
    parser.add_argument("--version", action="version", version=f"wxassist {__version__}")
    parser.add_argument("--config", help="配置文件路径（默认 ./config.local.json）")
    parser.add_argument("--provider", choices=["deepseek", "openai", "ollama", "mock"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--interval", type=int, help="采集间隔毫秒")
    parser.add_argument("--mode", choices=["copy", "paste"], help="填入方式")
    parser.add_argument("--hotkey", help="显示/隐藏悬浮窗的全局热键，空字符串关闭")
    parser.add_argument("--no-hotkey", action="store_true", help="不注册全局热键")
    parser.add_argument("--demo", action="store_true", help="合成对话演示模式")
    parser.add_argument("--selftest", action="store_true", help="无 UI 自检：截屏+OCR+解析")
    parser.add_argument("--snapshot", help="自检时把标注结果保存为图片")
    parser.add_argument("--screenshot", help="用演示数据渲染悬浮窗并截图后退出")
    parser.add_argument("--debug", action="store_true", help="打印更多诊断信息")
    return parser


def apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.provider:
        cfg.llm.provider = args.provider
    if args.model:
        cfg.llm.model = args.model
    if args.base_url:
        cfg.llm.base_url = args.base_url
    if args.interval:
        cfg.capture.interval_ms = max(250, args.interval)
    if args.mode:
        cfg.filler.mode = args.mode
    if args.no_hotkey:
        cfg.ui.hotkey = ""
    elif args.hotkey is not None:
        cfg.ui.hotkey = args.hotkey


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    apply_overrides(cfg, args)

    if args.selftest:
        return run_selftest(cfg, args.snapshot, args.debug)

    # 演示/截图模式不落到真实数据库里
    if args.demo or args.screenshot:
        cfg.data_dir = tempfile.mkdtemp(prefix="wxassist-demo-")

    from .app import AppController, DemoController, run_screenshot_mode

    if args.screenshot:
        return run_screenshot_mode(cfg, args.screenshot)
    if args.demo:
        return DemoController(cfg).run()
    return AppController(cfg).run()


def run_selftest(cfg: Config, snapshot: str | None, debug: bool = False) -> int:
    from .capture import enable_dpi_awareness, find_window, grab_window
    from .debugdraw import annotate, save
    from .layout import BubbleParser, compute_region
    from .ocr import OcrEngine

    enable_dpi_awareness()
    try:
        window = find_window()
    except Exception as exc:
        print(f"[x] 找不到微信窗口：{exc}")
        return 2

    print(
        f"[1/4] 窗口：{window.title} ({window.exe or '未知进程'}) "
        f"客户区 {window.client_size()} DPI×{window.dpi_scale():.2f}"
    )
    try:
        image, mode = grab_window(window)
    except Exception as exc:
        print(f"[x] 截屏失败：{exc}")
        return 2
    print(f"[2/4] 截屏：{image.shape[1]}x{image.shape[0]}（{mode}）")

    engine = OcrEngine()
    if not engine.warmup():
        print("[x] OCR 初始化失败，请确认已安装 rapidocr-onnxruntime")
        return 3

    height, width = image.shape[:2]
    region = compute_region(width, height, window.dpi_scale(), cfg.capture)
    started = time.perf_counter()
    lines = engine.recognize(image, bbox=(region.chat_left, 0, width, region.input_top))
    ocr_ms = (time.perf_counter() - started) * 1000
    print(
        f"[3/4] OCR：{len(lines)} 行，耗时 {ocr_ms:.0f}ms，"
        f"面板 x≥{region.chat_left} y∈[{region.header_bottom},{region.input_top}]"
    )

    result = BubbleParser(cfg.capture).parse(lines, image, region)
    print(f"[4/4] 解析：会话={result.session!r}，气泡={len(result.bubbles)}")
    for bubble in result.bubbles[-10:]:
        who = "我" if bubble.is_me else "对方"
        print(f"      [{who}] {bubble.text[:60]!r}")

    if debug:
        print("- 前 10 行 OCR：")
        for line in lines[:10]:
            print(f"      ({line.x0},{line.y0})-({line.x1},{line.y1}) {line.text!r}")

    if snapshot:
        save(snapshot, annotate(image, result.bubbles, header=region.input_top))
        print(f"[ok] 标注图已保存：{snapshot}")

    if not result.bubbles:
        print("[!] 没有解析出气泡：确认微信停在聊天页、窗口未被遮挡")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
