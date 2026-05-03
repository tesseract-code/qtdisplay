# ──────────────────────────────────────────────────────────────────────────────
# Demo content helpers  (no custom stylesheets — inherits palette)
# ──────────────────────────────────────────────────────────────────────────────

from PyQt6.QtCore import (Qt)
from PyQt6.QtGui import (QFont)
from PyQt6.QtWidgets import (
    QWidget, QTextEdit, QTreeWidget, QTreeWidgetItem, QLabel,
    QVBoxLayout, )


def _editor(text: str) -> QWidget:
    w = QWidget()
    ed = QTextEdit()
    ed.setPlainText(text)
    ed.setFontFamily("Cascadia Code, Consolas, monospace")
    vb = QVBoxLayout(w)
    vb.setContentsMargins(0, 0, 0, 0)
    vb.addWidget(ed)
    return w


def _tree(heading: str, items: list) -> QWidget:
    w = QWidget()
    t = QTreeWidget()
    t.setHeaderLabel(heading)
    for parent_text, children in items:
        pi = QTreeWidgetItem([parent_text])
        for c in children:
            pi.addChild(QTreeWidgetItem([c]))
        t.addTopLevelItem(pi)
    t.expandAll()
    vb = QVBoxLayout(w)
    vb.setContentsMargins(4, 4, 4, 4)
    vb.addWidget(t)
    return w


def _placeholder(text: str) -> QWidget:
    w = QWidget()
    lb = QLabel(text)
    lb.setAlignment(Qt.AlignmentFlag.AlignCenter)
    font = QFont()
    font.setPointSize(14)
    font.setBold(True)
    lb.setFont(font)
    vb = QVBoxLayout(w)
    vb.addWidget(lb)
    return w
