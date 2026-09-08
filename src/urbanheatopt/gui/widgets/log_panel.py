"""Right-side log panel: 校验日志 / 求解日志 / QA 摘要 tabs."""
from __future__ import annotations

from typing import Iterable

from PySide6.QtGui import QFont, QTextCursor
from PySide6.QtWidgets import QPlainTextEdit, QTabWidget, QVBoxLayout, QWidget

from urbanheatopt.gui import mock_data
from urbanheatopt.gui.theme import FONT_MONO

_TABS = (("validation", "校验日志"), ("solver", "求解日志"), ("qa", "QA 摘要"))


class LogPanel(QWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setMinimumWidth(240)
        self.setMaximumWidth(340)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget()
        self._panes: dict[str, QPlainTextEdit] = {}
        for key, title in _TABS:
            pane = QPlainTextEdit()
            pane.setReadOnly(True)
            pane.setFont(QFont(FONT_MONO, 9))
            pane.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
            self._panes[key] = pane
            self.tabs.addTab(pane, title)
        layout.addWidget(self.tabs)

    def append(self, key: str, text: str) -> None:
        pane = self._panes[key]
        pane.appendPlainText(text)
        pane.moveCursor(QTextCursor.MoveOperation.End)

    def append_lines(self, key: str, lines: Iterable[str]) -> None:
        for line in lines:
            self.append(key, line)

    def clear_all(self) -> None:
        for pane in self._panes.values():
            pane.clear()

    def load_demo(self) -> None:
        self.clear_all()
        self.append_lines("validation", mock_data.DEMO_VALIDATION_LOG)
        self.append_lines("solver", mock_data.DEMO_SOLVER_LOG)
        self.append_lines("qa", mock_data.DEMO_QA_LOG)


__all__ = ["LogPanel"]
