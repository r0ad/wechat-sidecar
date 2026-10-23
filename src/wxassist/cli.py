"""命令行入口。

用法：
  python run.py                 # 启动悬浮窗（需要微信已登录、窗口不要最小化）
  python run.py --selftest      # 不开 UI，验证截屏 + OCR + 解析
  python run.py --selftest --snapshot snapshots/calib.png
  python run.py --test-llm      # 只测试大模型连接（不启动 UI）
"""

from __future__ import annotations

import argparse
import sys
import time

from . import __version__
from .config import Config
from .providers import PRESETS, get_preset


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wxassist",
        description="微信 AI 回复助手：截屏 + 本地 OCR + 大模型候选回复，永不自动发送",
    )
    parser.add_argument("--version", action="version", version=f"wxassist {__version__}")
    parser.add_argument("--config", help="配置文件路径（默认 ./config.local.json）")
    parser.add_argument(
        "--provider",
        choices=[preset.key for preset in PRESETS],
        help="模型服务商（默认沿用配置文件；界面里也能改）",
    )
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--interval", type=int, help="采集间隔毫秒")
    parser.add_argument("--mode", choices=["copy", "paste"], help="填入方式")
    parser.add_argument("--hotkey", help="显示/隐藏悬浮窗的全局热键，空字符串关闭")
    parser.add_argument("--no-hotkey", action="store_true", help="不注册全局热键")
    parser.add_argument("--selftest", action="store_true", help="无 UI 自检：截屏+OCR+解析")
    parser.add_argument("--set-key", metavar="KEY", help="保存 API Key 到本机加密存储后退出")
    parser.add_argument("--test-llm", action="store_true", help="测试大模型连接后退出")
    parser.add_argument("--snapshot", help="自检时把标注结果保存为图片")
    parser.add_argument("--debug", action="store_true", help="打印更多诊断信息")
    return parser


def apply_overrides(cfg: Config, args: argparse.Namespace) -> None:
    if args.provider:
        cfg.llm.provider = args.provider
        preset = get_preset(args.provider)
        if preset and preset.base_url:
            if not args.base_url:
                cfg.llm.base_url = preset.base_url
            if not args.model:
                cfg.llm.model = preset.model
            if preset.key_env:
                cfg.llm.api_key_env = preset.key_env
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


_LOCK_FILE = None


def ensure_single_instance(lock_dir: str) -> bool:
    """同一时间只允许一个 wxassist 实例（用文件锁，跨进程可靠）。"""
    global _LOCK_FILE
    try:
        import msvcrt
        from pathlib import Path

        path = Path(lock_dir) / "wxassist.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(path, "a+")
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            handle.close()
            return False
        _LOCK_FILE = handle  # 进程存活期间一直持有
        return True
    except Exception:
        return True


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = Config.load(args.config)
    apply_overrides(cfg, args)

    if args.set_key:
        from .secrets_store import save_api_key

        try:
            save_api_key(cfg.data_dir, cfg.llm.provider, args.set_key.strip())
        except Exception as exc:
            print(f"[x] 保存失败：{exc}")
            return 2
        print(f"[ok] 已把 {cfg.llm.provider} 的 Key 保存到本机加密存储")
        return 0

    if args.test_llm:
        return run_llm_test(cfg)

    if args.selftest:
        return run_selftest(cfg, args.snapshot, args.debug)

    if not ensure_single_instance(cfg.data_dir):
        print("[!] wxassist 已经在运行了：请用任务栏/托盘里的悬浮窗，或先退出旧实例")
        return 1

    from .app import AppController

    return AppController(cfg).run()


def run_llm_test(cfg: Config) -> int:
    from .llm import LLMClient

    client = LLMClient(cfg.llm, cfg.data_dir)
    print(f"服务商：{cfg.llm.provider}  Base URL：{cfg.llm.base_url}  模型：{cfg.llm.model}")
    print(f"API Key：{'已配置' if client.has_key() else '未配置'}")
    result = client.test_connection()
    if result.ok:
        print(f"[ok] {result.message} · {result.latency_ms}ms")
        return 0
    print(f"[x] {result.message}")
    return 1


def run_selftest(cfg: Config, snapshot: str | None, debug: bool = False) -> int:
    from .capture import enable_dpi_awareness, find_window, grab_window
    from .debugdraw import annotate, save
    from .layout import BubbleParser, compute_region
    from .ocr import OcrEngine

    enable_dpi_awareness()
    try:
        window = find_window()
    except Exception as exc:
        print(f"[x] {exc}")
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
