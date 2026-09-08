"""Desktop GUI scaffold for UrbanHeatOpt (软著 front-end).

Phase 2: main-window skeleton + modern QSS styling + demo data.
The GUI never imports the optimization stack; it talks to the backend only
through :mod:`urbanheatopt.gui.contracts` (see the Phase-1 blueprint:
docs/design/GUI_PHASE1_BLUEPRINT_CN.md).
"""
from __future__ import annotations

from urbanheatopt.gui.contracts import CONTRACT_VERSION as GUI_CONTRACT_VERSION

GUI_VERSION = "1.0.0"
SYSTEM_NAME = "UrbanHeatOpt · 城市供热多目标优化求解平台"

__all__ = ["GUI_VERSION", "GUI_CONTRACT_VERSION", "SYSTEM_NAME"]
