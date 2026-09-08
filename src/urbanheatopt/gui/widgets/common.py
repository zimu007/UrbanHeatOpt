"""Small shared widgets: cards, section headers, badges, status LED, field rows."""
from __future__ import annotations

from PySide6.QtCore import QEasingCurve, QVariantAnimation
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QVBoxLayout, QWidget

from urbanheatopt.gui.theme import C


def repolish(widget: QWidget) -> None:
    """Re-apply the application stylesheet after a dynamic property change."""
    style = widget.style()
    style.unpolish(widget)
    style.polish(widget)
    widget.update()


class Card(QFrame):
    """Flat rounded card used by every functional region."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")


def section_header(title: str, hint: str = "") -> QWidget:
    """Card header: bold section title plus an optional muted hint line."""
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)
    title_label = QLabel(title)
    title_label.setObjectName("SectionTitle")
    layout.addWidget(title_label)
    if hint:
        hint_label = QLabel(hint)
        hint_label.setObjectName("SectionHint")
        hint_label.setWordWrap(True)
        layout.addWidget(hint_label)
    return box


def make_badge(text: str, level: str = "info") -> QLabel:
    """Pill-shaped status badge; level is pass / warn / error / info."""
    badge = QLabel(text)
    badge.setProperty("level", level)
    return badge


def set_badge(badge: QLabel, text: str, level: str) -> None:
    badge.setText(text)
    badge.setProperty("level", level)
    repolish(badge)


class StatusLED(QLabel):
    """8px round state light; 'running' pulses between primary and accent."""

    _COLORS = {
        "idle": C["text_disabled"],
        "running": C["primary"],
        "ok": C["good"],
        "error": C["critical"],
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("StatusLed")
        self._animation: QVariantAnimation | None = None
        self.set_state("idle")

    def set_state(self, state: str) -> None:
        if self._animation is not None:
            self._animation.stop()
            self._animation.deleteLater()
            self._animation = None
        if state == "running":
            self._animation = QVariantAnimation(self)
            self._animation.setStartValue(QColor(C["primary"]))
            self._animation.setEndValue(QColor(C["accent"]))
            self._animation.setDuration(900)
            self._animation.setLoopCount(-1)
            self._animation.setEasingCurve(QEasingCurve.Type.InOutSine)
            self._animation.valueChanged.connect(self._apply_color)
            self._animation.start()
        else:
            self._apply_color(QColor(self._COLORS[state]))

    def _apply_color(self, color: QColor) -> None:
        self.setStyleSheet(
            f"#StatusLed {{ background: {color.name()}; border-radius: 5px; }}"
        )


def field_row(label_text: str, widget: QWidget) -> QWidget:
    """Labeled input row used inside the side-panel cards."""
    row = QWidget()
    layout = QHBoxLayout(row)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(8)
    label = QLabel(label_text)
    label.setObjectName("FieldLabel")
    label.setMinimumWidth(60)
    layout.addWidget(label)
    layout.addWidget(widget, 1)
    return row


__all__ = [
    "Card",
    "section_header",
    "make_badge",
    "set_badge",
    "StatusLED",
    "field_row",
    "repolish",
]
