from __future__ import annotations

from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, QPoint, QRect
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import QApplication, QWidget, QLabel

if TYPE_CHECKING:
    from qtdisplay.dock.region import DockRegion


# ──────────────────────────────────────────────────────────────────────────────
# Icon helper
# ──────────────────────────────────────────────────────────────────────────────

def _std_icon(sp) -> QIcon:
    return QApplication.style().standardIcon(sp)


# ──────────────────────────────────────────────────────────────────────────────
# Drop-zone constants
# ──────────────────────────────────────────────────────────────────────────────

class Zone:
    NONE   = -1
    CENTER =  0
    TOP    =  1
    BOTTOM =  2
    LEFT   =  3
    RIGHT  =  4


# ──────────────────────────────────────────────────────────────────────────────
# Module-level drag state
# ──────────────────────────────────────────────────────────────────────────────

class _Drag:
    def __init__(self) -> None:
        self.active:    bool              = False
        self.source:    DockRegion | None = None
        self.tab_index: int               = -1
        self.widget:    QWidget | None    = None
        self.title:     str               = ""
        self.icon:      QIcon | None      = None

    def reset(self) -> None:
        self.__init__()


_drag = _Drag()


def reset_drag_state() -> None:
    """
    Reset the module-level drag singleton to its null state.

    Call this from the manager's cleanup routine (or from ``DockManager``\'s
    ``__del__`` / ``cleanup``) to ensure that ``_drag`` holds no references
    to widgets or regions that are being destroyed.  This is especially
    important when a drag is interrupted by a programmatic teardown rather
    than a normal mouse-release.
    """
    _drag.reset()


# ──────────────────────────────────────────────────────────────────────────────
# Drop Overlay  —  one instance, shared across all regions
# ──────────────────────────────────────────────────────────────────────────────

class DropOverlay(QWidget):
    """
    Semi-transparent overlay covering the target region during a tab drag.

    No buttons — zones are determined entirely by cursor position:

    ┌──────────────────────────┐
    │          TOP  25%        │
    ├──────┬────────────┬──────┤
    │ LEFT │   CENTER   │RIGHT │  <- each side strip is 25% of the dimension
    │  25% │  (merge)   │ 25%  │
    ├──────┴────────────┴──────┤
    │         BOTTOM 25%       │
    └──────────────────────────┘

    The active zone is shown by brightening that strip so the user can
    see exactly what will happen on release without any button clutter.
    """

    # Fraction of width/height that counts as an edge (split) zone.
    EDGE = 0.25

    # Colours — kept as class constants so they are easy to theme.
    _BASE_FILL   = QColor(100, 150, 255,  60)
    _BASE_BORDER = QColor( 70, 110, 230, 180)
    _ZONE_FILL   = QColor(100, 150, 255, 130)
    _ZONE_BORDER = QColor( 70, 110, 230, 220)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._region: DockRegion | None = None
        self.hovered: int = Zone.NONE

        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.hide()

    # ── public interface ──────────────────────────────────────────────────────

    def show_for(self, region: DockRegion) -> None:
        self._region = region
        self._snap()
        self.hovered = Zone.NONE
        if not self.isVisible():
            self.show()
        else:
            self.update()

    def hide_overlay(self) -> None:
        self._region = None
        self.hovered = Zone.NONE
        self.hide()

    def cleanup(self) -> None:
        """
        Release all references held by the overlay.

        Hides the widget, clears the region pointer, and disconnects any
        signals so the overlay does not keep a live reference to a region
        that may already be mid-teardown.  Safe to call more than once.
        """
        self.hide_overlay()  # sets _region = None, hides the widget

    def _effective_tabs(self) -> int:
        """Number of tabs the region will have after the current drag completes."""
        if self._region is None:
            return 0
        count = self._region.count()
        # If we are dragging a tab from this same region, subtract it.
        if self._region is _drag.source and _drag.active:
            count -= 1
        return count

    def zone_for_global(self, gpos: QPoint) -> int:
        """
        Return the zone the cursor is in, based purely on its position
        within the overlay (no buttons involved).
        """
        # If there is nothing to split, only allow the centre (merge/cancel) action.
        if self._effective_tabs() < 1:
            return Zone.CENTER

        local = self.mapFromGlobal(gpos)
        w, h = self.width(), self.height()
        x, y = local.x(), local.y()

        # Clamp to at least 40 px so tiny regions still have usable zones.
        edge_w = max(int(w * self.EDGE), 40)
        edge_h = max(int(h * self.EDGE), 40)

        if x < edge_w:
            return Zone.LEFT
        if x > w - edge_w:
            return Zone.RIGHT
        if y < edge_h:
            return Zone.TOP
        if y > h - edge_h:
            return Zone.BOTTOM
        return Zone.CENTER

    def set_hovered(self, zone: int) -> None:
        if zone != self.hovered:
            self.hovered = zone
            self.update()

    # ── internal helpers ──────────────────────────────────────────────────────

    def _snap(self) -> None:
        if self._region is None:
            return
        tl = self._region.mapToGlobal(QPoint(0, 0))
        self.setGeometry(tl.x(), tl.y(),
                         self._region.width(), self._region.height())

    def _zone_rect(self, zone: int) -> QRect | None:
        """Return the rectangle for *zone* in overlay-local coordinates."""
        w, h = self.width(), self.height()
        edge_w = max(int(w * self.EDGE), 40)
        edge_h = max(int(h * self.EDGE), 40)

        if zone == Zone.LEFT:
            return QRect(0, 0, edge_w, h)
        if zone == Zone.RIGHT:
            return QRect(w - edge_w, 0, edge_w, h)
        if zone == Zone.TOP:
            return QRect(0, 0, w, edge_h)
        if zone == Zone.BOTTOM:
            return QRect(0, h - edge_h, w, edge_h)
        # CENTER: now the **entire** region, so the merge highlight fills everything.
        if zone == Zone.CENTER:
            return self.rect()
        return None

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, _ev) -> None:
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 1. Base semi-transparent fill over the whole region.
        p.fillRect(self.rect(), self._BASE_FILL)

        # 2. Brighter fill + stronger border on the active zone strip.
        zone_rect = self._zone_rect(self.hovered)
        if zone_rect is not None:
            p.fillRect(zone_rect, self._ZONE_FILL)
            p.setPen(QPen(self._ZONE_BORDER, 2))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(zone_rect.adjusted(1, 1, -1, -1))

        # 3. Overall border so the drop target is clearly outlined.
        p.setPen(QPen(self._BASE_BORDER, 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.rect().adjusted(1, 1, -1, -1))

        p.end()


# ──────────────────────────────────────────────────────────────────────────────
# Drag ghost  —  live pixmap snapshot of the dragged widget
# ──────────────────────────────────────────────────────────────────────────────

class DragGhost(QLabel):
    """
    Translucent floating thumbnail that follows the cursor during a drag.

    Pass ``widget.grab()`` at the moment the drag begins so the ghost
    shows an exact snapshot of what is being moved.  The pixmap is scaled
    down to at most ``_MAX_W x _MAX_H`` pixels to stay unobtrusive.

    Callers must call ``move(cursor_global_pos)`` from their
    ``mouseMoveEvent`` handler to track the cursor.
    """

    _MAX_W = 320
    _MAX_H = 200

    def __init__(self, pixmap: QPixmap) -> None:
        super().__init__()
        self.setWindowFlags(
            Qt.WindowType.ToolTip |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowTransparentForInput   # ← never intercept mouse events
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        if pixmap.width() > self._MAX_W or pixmap.height() > self._MAX_H:
            pixmap = pixmap.scaled(
                self._MAX_W, self._MAX_H,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )

        self.setPixmap(pixmap)
        self.setFixedSize(pixmap.size())
        self.setWindowOpacity(0.72)