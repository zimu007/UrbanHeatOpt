"""Scientific dark-blue theme tokens and the single QSS stylesheet.

Palette policy:
- chrome hues (backgrounds, primary, accent) belong to the product theme;
- status steps are the dataviz skill's fixed, dark-surface-validated palette
  (good #0CA30C / warning #FAB219 / serious #EC835A / critical #D03B3B) and are
  never reused as decorative series colors;
- chart series hues are the validated all-pairs trio on the card surface
  #14233C (validator: scripts/validate_palette.js --mode dark --surface 14233C).
"""
from __future__ import annotations

from typing import Final

C = {
    # surfaces
    "bg_window": "#0C1524",
    "bg_panel": "#101D31",
    "bg_card": "#14233C",
    "bg_input": "#0F1B2E",
    "bg_hover": "#1A2C4A",
    # borders
    "border": "#22355A",
    "border_strong": "#2E4A7A",
    # chrome
    "primary": "#3D7EFF",
    "primary_hover": "#5A92FF",
    "primary_press": "#2F63CC",
    "accent": "#2FD4E8",
    # ink
    "text": "#E7EEF9",
    "text_muted": "#8DA3C4",
    "text_disabled": "#4A5E80",
    # status (fixed palette - never themed, never series colors)
    "good": "#0CA30C",
    "warning": "#FAB219",
    "serious": "#EC835A",
    "critical": "#D03B3B",
    "info": "#3D7EFF",
    # chart series (validated all-pairs on bg_card)
    "series_blue": "#3987E5",  # ε interior points
    "series_aqua": "#199E70",  # cost endpoint
    "series_orange": "#D95926",  # carbon endpoint
    "grid": "#22355A",
}

# The non-UI face renders CJK reliably in Qt's offscreen/minimal platform
# plugins as well as the ordinary Windows platform plugin.
FONT_UI: Final = "Microsoft YaHei"
FONT_MONO: Final = "Consolas"

_T = C  # short alias inside the template


def build_qss() -> str:
    """Build the application stylesheet from the token table."""
    return f"""
* {{
    font-family: "{FONT_UI}";
    color: {_T["text"]};
    font-size: 12px;
}}
QMainWindow, #AppRoot {{
    background: {_T["bg_window"]};
}}

/* ---------- header ---------- */
#HeaderBar {{
    background: {_T["bg_panel"]};
    border-bottom: 1px solid {_T["border"]};
}}
#AppName {{
    font-size: 16px;
    font-weight: 600;
    color: {_T["text"]};
}}
#VersionBadge {{
    color: {_T["text_muted"]};
    border: 1px solid {_T["border_strong"]};
    border-radius: 10px;
    padding: 2px 10px;
    font-size: 11px;
}}
#HeaderStatus {{
    color: {_T["text_muted"]};
    font-size: 12px;
}}

/* ---------- cards & titles ---------- */
QFrame#Card {{
    background: {_T["bg_card"]};
    border: 1px solid {_T["border"]};
    border-radius: 8px;
}}
#SectionTitle {{
    font-size: 13px;
    font-weight: 600;
    color: {_T["text"]};
}}
#SectionHint {{
    color: {_T["text_muted"]};
    font-size: 11px;
}}
#FieldLabel {{
    color: {_T["text_muted"]};
    font-size: 11px;
}}
#MonoValue {{
    font-family: "{FONT_MONO}";
    color: {_T["text"]};
}}

/* ---------- buttons ---------- */
QPushButton {{
    background: {_T["bg_hover"]};
    border: 1px solid {_T["border_strong"]};
    border-radius: 6px;
    padding: 6px 14px;
    color: {_T["text"]};
    min-height: 30px;
}}
QPushButton:hover {{
    background: #203A63;
    border-color: {_T["primary"]};
}}
QPushButton:pressed {{
    background: {_T["bg_panel"]};
}}
QPushButton:disabled {{
    color: {_T["text_disabled"]};
    background: {_T["bg_panel"]};
    border-color: {_T["border"]};
}}
QPushButton[variant="primary"] {{
    background: {_T["primary"]};
    border: none;
    color: #FFFFFF;
    font-weight: 600;
}}
QPushButton[variant="primary"]:hover {{
    background: {_T["primary_hover"]};
}}
QPushButton[variant="primary"]:pressed {{
    background: {_T["primary_press"]};
}}
QPushButton[variant="primary"]:disabled {{
    background: {_T["border"]};
    color: {_T["text_disabled"]};
}}
QPushButton[variant="danger"] {{
    background: transparent;
    border: 1px solid {_T["critical"]};
    color: {_T["critical"]};
}}
QPushButton[variant="danger"]:hover {{
    background: rgba(208, 59, 59, 0.14);
}}
QPushButton[variant="danger"]:disabled {{
    color: {_T["text_disabled"]};
    border-color: {_T["border"]};
    background: transparent;
}}

/* ---------- inputs ---------- */
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
    background: {_T["bg_input"]};
    border: 1px solid {_T["border"]};
    border-radius: 6px;
    padding: 4px 8px;
    color: {_T["text"]};
    min-height: 28px;
    selection-background-color: {_T["primary"]};
}}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{
    border-color: {_T["primary"]};
}}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {{
    color: {_T["text_disabled"]};
    background: {_T["bg_panel"]};
}}
QComboBox::drop-down {{
    border: none;
    width: 22px;
}}
QComboBox::down-arrow {{
    image: none;
    border-left: 4px solid transparent;
    border-right: 4px solid transparent;
    border-top: 6px solid {_T["text_muted"]};
    margin-right: 8px;
}}
QComboBox QAbstractItemView {{
    background: {_T["bg_card"]};
    border: 1px solid {_T["border_strong"]};
    selection-background-color: {_T["bg_hover"]};
    color: {_T["text"]};
    outline: none;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    border: none;
    width: 16px;
    background: {_T["bg_input"]};
}}
QCheckBox {{
    color: {_T["text"]};
    spacing: 8px;
}}
QCheckBox::indicator {{
    width: 16px;
    height: 16px;
    border: 1px solid {_T["border_strong"]};
    border-radius: 4px;
    background: {_T["bg_input"]};
}}
QCheckBox::indicator:checked {{
    background: {_T["primary"]};
    border-color: {_T["primary"]};
}}

/* ---------- badges & status LED ---------- */
QLabel[level="pass"] {{
    color: {_T["good"]};
    border: 1px solid rgba(12, 163, 12, 0.55);
    background: rgba(12, 163, 12, 0.12);
    border-radius: 10px;
    padding: 3px 10px;
}}
QLabel[level="warn"] {{
    color: {_T["warning"]};
    border: 1px solid rgba(250, 178, 25, 0.55);
    background: rgba(250, 178, 25, 0.12);
    border-radius: 10px;
    padding: 3px 10px;
}}
QLabel[level="error"] {{
    color: {_T["critical"]};
    border: 1px solid rgba(208, 59, 59, 0.55);
    background: rgba(208, 59, 59, 0.12);
    border-radius: 10px;
    padding: 3px 10px;
}}
QLabel[level="info"] {{
    color: {_T["info"]};
    border: 1px solid rgba(61, 126, 255, 0.55);
    background: rgba(61, 126, 255, 0.12);
    border-radius: 10px;
    padding: 3px 10px;
}}
#StatusLed {{
    border-radius: 5px;
    min-width: 10px;
    max-width: 10px;
    min-height: 10px;
    max-height: 10px;
}}

/* ---------- workflow stepper ---------- */
#StepCircle {{
    border-radius: 13px;
    min-width: 26px;
    max-width: 26px;
    min-height: 26px;
    max-height: 26px;
    font-size: 12px;
    font-weight: 600;
}}
#StepCircle[stepState="pending"] {{
    color: {_T["text_muted"]};
    background: transparent;
    border: 1px solid {_T["border_strong"]};
}}
#StepCircle[stepState="active"] {{
    color: #FFFFFF;
    background: {_T["primary"]};
    border: 1px solid {_T["primary"]};
}}
#StepCircle[stepState="done"] {{
    color: #062B06;
    background: {_T["good"]};
    border: 1px solid {_T["good"]};
}}
#StepCircle[stepState="failed"] {{
    color: #FFFFFF;
    background: {_T["critical"]};
    border: 1px solid {_T["critical"]};
}}
#StepTitle {{
    font-size: 13px;
    font-weight: 600;
}}
#StepTitle[stepState="pending"] {{ color: {_T["text_muted"]}; }}
#StepTitle[stepState="active"] {{ color: {_T["text"]}; }}
#StepTitle[stepState="done"] {{ color: {_T["text"]}; }}
#StepTitle[stepState="failed"] {{ color: {_T["critical"]}; }}
#StepSub {{
    font-size: 11px;
    color: {_T["text_muted"]};
}}
#StepConnector {{
    max-height: 2px;
    min-height: 2px;
    border: none;
    background: {_T["border"]};
}}
#StepConnector[connState="done"] {{ background: {_T["good"]}; }}

/* ---------- KPI cards ---------- */
QFrame#KpiCard {{
    background: {_T["bg_panel"]};
    border: 1px solid {_T["border"]};
    border-radius: 8px;
}}
#KpiTitle {{
    color: {_T["text_muted"]};
    font-size: 12px;
}}
#KpiValue {{
    font-family: "{FONT_MONO}";
    font-size: 22px;
    font-weight: 600;
    color: {_T["text"]};
}}
#KpiUnit {{
    color: {_T["text_muted"]};
    font-size: 11px;
}}
#KpiSub {{
    color: {_T["text_muted"]};
    font-size: 11px;
}}

/* ---------- progress bar ---------- */
QProgressBar {{
    background: {_T["bg_input"]};
    border: 1px solid {_T["border"]};
    border-radius: 3px;
    min-height: 6px;
    max-height: 6px;
    text-align: center;
    font-size: 0px;
}}
QProgressBar::chunk {{
    background: {_T["primary"]};
    border-radius: 3px;
}}

/* ---------- log panel ---------- */
QTabWidget::pane {{
    border: 1px solid {_T["border"]};
    border-radius: 6px;
    top: -1px;
    background: {_T["bg_input"]};
}}
QTabBar::tab {{
    background: transparent;
    color: {_T["text_muted"]};
    padding: 6px 14px;
    border-bottom: 2px solid transparent;
}}
QTabBar::tab:selected {{
    color: {_T["text"]};
    border-bottom: 2px solid {_T["primary"]};
}}
QTabBar::tab:hover {{ color: {_T["text"]}; }}
QPlainTextEdit {{
    background: {_T["bg_input"]};
    border: none;
    color: #C9D6E8;
    font-family: "{FONT_MONO}";
    font-size: 11px;
}}

/* ---------- table ---------- */
QTableView {{
    background: {_T["bg_input"]};
    alternate-background-color: {_T["bg_panel"]};
    gridline-color: {_T["border"]};
    border: 1px solid {_T["border"]};
    border-radius: 6px;
    selection-background-color: {_T["bg_hover"]};
    selection-color: {_T["text"]};
}}
QHeaderView::section {{
    background: {_T["bg_panel"]};
    color: {_T["text_muted"]};
    border: none;
    border-bottom: 1px solid {_T["border"]};
    padding: 6px;
    font-weight: 600;
}}

/* ---------- menus & status bar ---------- */
QMenuBar {{
    background: {_T["bg_panel"]};
    border-bottom: 1px solid {_T["border"]};
}}
QMenuBar::item {{
    padding: 5px 12px;
    background: transparent;
}}
QMenuBar::item:selected {{ background: {_T["bg_card"]}; }}
QMenu {{
    background: {_T["bg_card"]};
    border: 1px solid {_T["border_strong"]};
}}
QMenu::item {{ padding: 6px 24px; }}
QMenu::item:selected {{ background: {_T["bg_hover"]}; }}
QMenu::separator {{ height: 1px; background: {_T["border"]}; margin: 4px 8px; }}
QStatusBar {{
    background: {_T["bg_panel"]};
    border-top: 1px solid {_T["border"]};
    color: {_T["text_muted"]};
}}
QStatusBar::item {{ border: none; }}

/* ---------- misc ---------- */
QToolTip {{
    background: {_T["bg_card"]};
    color: {_T["text"]};
    border: 1px solid {_T["border_strong"]};
    padding: 4px 8px;
}}
QScrollBar:vertical {{
    background: {_T["bg_panel"]};
    width: 10px;
    margin: 0;
}}
QScrollBar::handle:vertical {{
    background: {_T["border_strong"]};
    border-radius: 5px;
    min-height: 24px;
}}
QScrollBar::handle:vertical:hover {{ background: {_T["primary"]}; }}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
QScrollBar:horizontal {{
    background: {_T["bg_panel"]};
    height: 10px;
    margin: 0;
}}
QScrollBar::handle:horizontal {{
    background: {_T["border_strong"]};
    border-radius: 5px;
    min-width: 24px;
}}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {{ width: 0; }}
QSplitter::handle {{ background: {_T["border"]}; }}
QSplitter::handle:hover {{ background: {_T["border_strong"]}; }}
QSplitter::handle:horizontal {{ width: 1px; }}
QSplitter::handle:vertical {{ height: 1px; }}
"""


__all__ = ["C", "FONT_UI", "FONT_MONO", "build_qss"]
