"""Repository-local GUI entry; mirrors run.py's src bootstrap.

用法:
    python run_gui.py                 # 启动界面（空闲态）
    python run_gui.py --demo          # 启动即载入演示结果（软著截图态）
    python run_gui.py --screenshot DIR  # 离屏生成空闲/演示两张截图后退出
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from urbanheatopt.gui.app import main

if __name__ == "__main__":
    raise SystemExit(main())
