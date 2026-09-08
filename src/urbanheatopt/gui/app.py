"""QApplication bootstrap: font, stylesheet, window, demo/screenshot modes."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="run_gui", description="UrbanHeatOpt 桌面前端")
    parser.add_argument("--demo", action="store_true", help="启动即载入演示数据（软著截图态）")
    parser.add_argument(
        "--screenshot", type=Path, default=None, metavar="DIR",
        help="离屏渲染空闲态与演示态截图到目录后退出（不弹窗）",
    )
    args = parser.parse_args(argv)

    # Qt's Windows offscreen plugin does not load the system CJK glyphs in the
    # bundled environment.  Keep the native Windows platform for screenshots;
    # headless Linux/macOS runs still use the offscreen plugin.
    if args.screenshot is not None and sys.platform != "win32":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

    from PySide6.QtGui import QFont
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication(sys.argv[:1])
    app.setApplicationName("UrbanHeatOpt")

    from urbanheatopt.gui.main_window import MainWindow
    from urbanheatopt.gui.theme import FONT_UI, build_qss

    app.setFont(QFont(FONT_UI, 10))
    app.setStyleSheet(build_qss())
    window = MainWindow()

    if args.screenshot is not None:
        args.screenshot.mkdir(parents=True, exist_ok=True)
        window.show()
        app.processEvents()
        window.grab().save(str(args.screenshot / "window_idle.png"))
        window.load_demo()
        app.processEvents()
        window.grab().save(str(args.screenshot / "window_demo.png"))
        return 0

    if args.demo:
        window.load_demo()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
