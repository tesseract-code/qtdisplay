"""
dock/tabbar.py
--------------
Custom QTabBar for DockRegion.

Emits ``drag_initiated(tab_index, global_pos)`` **only** when a tab
is dragged outside the bar area.  Dragging within the bar reorders
tabs with a gap placeholder and a floating ghost label, committing
the move only on mouse release.

Emits ``split_requested(direction)`` when the user chooses a split
action from the context menu.  ``direction`` is one of:
    "left" | "right" | "top" | "bottom"
"""

from __future__ import annotations

from typing import runtime_checkable, Protocol

from PyQt6.QtCore import Qt, QPoint, QRect, QSize, pyqtSignal
from PyQt6.QtGui import QAction, QColor, QIcon, QPainter, QPen, QPalette
from PyQt6.QtWidgets import QMenu, QPushButton, QStyle, QStyleOptionTab, QTabBar, QWidget


DOCK_CLOSABLE_PROPERTY = "_dock_closable"


# ──────────────────────────────────────────────────────────────────────────────
# Reorder ghost
# ──────────────────────────────────────────────────────────────────────────────

class _ReorderGhost(QWidget):
    """
    Floating rounded-rectangle label shown while a tab is being reordered
    within the bar.  Displays the tab's icon and text so the user always
    knows what they are moving.
    """

    _H_PAD    = 12
    _V_PAD    = 5
    _ICON_W   = 16

    def __init__(self, text: str, icon: QIcon | None) -> None:
        super().__init__(None)
        self._text = text
        self._icon = icon if (icon and not icon.isNull()) else None

        self.setWindowFlags(
            Qt.WindowType.ToolTip |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)

        fm    = self.fontMetrics()
        icon_w = (self._ICON_W + 6) if self._icon else 0
        w     = self._H_PAD + icon_w + fm.horizontalAdvance(text) + self._H_PAD
        h     = max(self._ICON_W, fm.height()) + self._V_PAD * 2
        self.setFixedSize(w, h)

    def paintEvent(self, _ev) -> None:
        p   = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        pal = self.palette()

        bg = pal.color(QPalette.ColorRole.Base)
        bg.setAlpha(230)
        p.setPen(QPen(pal.color(QPalette.ColorRole.Highlight), 1.5))
        p.setBrush(bg)
        p.drawRoundedRect(self.rect().adjusted(1, 1, -1, -1), 6, 6)

        x = self._H_PAD
        if self._icon:
            pix = self._icon.pixmap(self._ICON_W, self._ICON_W)
            p.drawPixmap(x, (self.height() - self._ICON_W) // 2, pix)
            x += self._ICON_W + 6

        p.setPen(pal.color(QPalette.ColorRole.Text))
        p.drawText(
            x, 0, self.width() - x - self._H_PAD, self.height(),
            Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
            self._text,
        )
        p.end()


# ──────────────────────────────────────────────────────────────────────────────
# Tab bar
# ──────────────────────────────────────────────────────────────────────────────

@runtime_checkable
class CleanupTab(Protocol):
    """
    Duck-type protocol for tab widget that need explicit teardown when closed.

    Any widget class that defines a ``cleanup(self) -> None`` method
    automatically satisfies this protocol — no inheritance required.
    ``DockTabBar`` will call it before emitting ``tabCloseRequested``.
    """

    def cleanup(self) -> None:
        """Release resources, cancel async tasks, disconnect signals, etc."""
        ...

class DockTabBar(QTabBar):
    """
    Tab bar supporting in-bar reorder **and** tear-off drag.

    Reorder behaviour
    -----------------
    While the cursor stays inside the bar a gap placeholder (blue dashed
    rounded rect) tracks where the tab will land.  ``moveTab`` is called
    exactly once on mouse release; the Qt layout is never mutated mid-drag.
    A floating ``_ReorderGhost`` follows the cursor showing the tab's icon
    and name.

    Close-button rules
    ------------------
    One button per tab, installed by ``tabInserted``.  Visible only on the
    currently selected tab (``_sync_close_buttons``).  All buttons are
    hidden during a reorder drag and restored on release.

    Split signal
    ------------
    ``split_requested(direction)`` is emitted when the user picks a split
    action from the right-click context menu.  ``direction`` is one of
    ``"left"``, ``"right"``, ``"top"``, ``"bottom"``.  The owning
    ``DockRegion`` forwards this to ``DockManager.split_region_with_current_tab``.
    """

    drag_initiated  = pyqtSignal(int, QPoint)
    split_requested = pyqtSignal(str)          # "left" | "right" | "top" | "bottom"

    THRESHOLD = 8
    _H_PAD    = 30

    def __init__(self) -> None:
        super().__init__()
        self._press_pos: QPoint | None = None
        self._press_tab: int = -1
        self._dragging: bool = False

        # reorder state — only valid while _dragging is True
        self._drag_from: int = -1
        self._drop_at: int = -1
        self._reorder_ghost: _ReorderGhost | None = None

        self.setMovable(True)
        self.setTabsClosable(True)
        self.setExpanding(True)  # ← changed
        self.setElideMode(Qt.TextElideMode.ElideNone)
        self.setUsesScrollButtons(True)

    # ── close-button lifecycle ────────────────────────────────────────────────

    def tabInserted(self, index: int) -> None:
        """Called by Qt after every tab insertion — install the close button and tooltip."""
        super().tabInserted(index)
        self._install_close_button(index)
        self.sync_close_buttons(self.currentIndex())
        self.setTabToolTip(index, self.tabText(index))  # ← added

    def setTabText(self, index: int, text: str) -> None:
        """Override to keep the tooltip in sync with the tab label."""
        super().setTabText(index, text)
        self.setTabToolTip(index, text)  # ← added

    def tabRemoved(self, index: int) -> None:
        """Re-sync after removal so the surviving current tab shows its button."""
        super().tabRemoved(index)
        self.sync_close_buttons(self.currentIndex())

    def request_close(self, index: int) -> None:
        """Public close-request entry point for the owning DockRegion/manager."""
        self._request_close(index)

    def close_all_tabs(self, tw) -> None:
        """Public entry point to close every closable tab in *tw*."""
        self._close_all(tw)

    def close_other_tabs(self, tw, keep_idx: int) -> None:
        """Public entry point to close every closable tab except *keep_idx*."""
        self._close_others(tw, keep_idx)

    def sync_close_buttons(self, current_idx: int | None = None) -> None:
        """Public entry point to refresh close-button visibility."""
        self._sync_close_buttons(self.currentIndex() if current_idx is None else current_idx)

    @staticmethod
    def _tab_is_closable(tw, index: int) -> bool:
        """
        Return ``False`` only when the widget at *index* has been explicitly
        marked non-closable via the ``"_dock_closable"`` Qt dynamic property.

        Absent property (i.e. never set) is treated as ``True`` so that
        widgets added without a closability specification default to closable.
        This is the single source of truth consulted by every close path:
        button installation, ``_request_close``, ``_close_all``,
        ``_close_others``, and the context menu.
        """
        if tw is None:
            return True
        widget = tw.widget(index)
        if widget is None:
            return True
        # property() returns None when the property has never been set.
        # Only an explicit False value means non-closable.
        return widget.property(DOCK_CLOSABLE_PROPERTY) is not False

    def _install_close_button(self, index: int) -> None:
        # Non-closable tabs never get a close button — skip entirely so there
        # is no button object to show/hide/re-sync later.
        if not self._tab_is_closable(self.parentWidget(), index):
            return

        if self.tabButton(index, QTabBar.ButtonPosition.RightSide) is not None:
            return

        btn = QPushButton()
        btn.setFlat(True)
        btn.setFixedSize(16, 16)

        # ── Create a fixed‑color close icon from the standard one ────────
        standard_icon = self.style().standardIcon(
            QStyle.StandardPixmap.SP_TitleBarCloseButton
        )
        pixmap = standard_icon.pixmap(16, 16)  # size we need
        # Tint it to a concrete color (e.g. a dark grey, adjust to taste)
        painter = QPainter(pixmap)
        painter.setCompositionMode(
            QPainter.CompositionMode.CompositionMode_SourceIn
        )
        painter.fillRect(pixmap.rect(), QColor("#555555"))  # fixed color
        painter.end()
        btn.setIcon(QIcon(pixmap))
        # ─────────────────────────────────────────────────────────────────

        # Per‑button styling (borderless, transparent, hover effects)
        btn.setStyleSheet("""
            QPushButton {
                border: none;
                background: transparent;
                padding: 0px;
                margin: 0px;
                /* Icon color is now baked into the pixmap, so no 'color' needed */
            }
            QPushButton:hover {
                background: rgba(128, 128, 128, 40);
                border-radius: 2px;
            }
            QPushButton:pressed {
                background: rgba(128, 128, 128, 80);
            }
        """)

        btn.setVisible(False)
        btn.clicked.connect(lambda: self._close_button_clicked(btn))
        self.setTabButton(index, QTabBar.ButtonPosition.RightSide, btn)

    def _request_close(self, index: int) -> None:
        """
        Single close path used by every code site that wants to close a tab.

        1. Non-closable tabs are silently ignored — this acts as the last
           line of defence even if a caller bypasses the UI-level guards.
        2. If the tab's widget satisfies :class:`CleanupTab` (duck-typed),
           ``cleanup()`` is called *before* the signal fires so the widget
           can release resources while it is still fully alive.
        3. ``tabCloseRequested`` is emitted; the owner (DockRegion) actually
           removes the tab.
        """
        tw = self.parentWidget()          # DockRegion / QTabWidget
        if not self._tab_is_closable(tw, index):
            return                        # non-closable — ignore silently
        if tw is not None:
            widget = tw.widget(index)
            if isinstance(widget, CleanupTab):   # duck-type check — no subclass needed
                widget.cleanup()
        self.tabCloseRequested.emit(index)

    def _close_button_clicked(self, btn: QPushButton) -> None:
        """Find which tab owns *btn* right now and request close."""
        for i in range(self.count()):
            if self.tabButton(i, QTabBar.ButtonPosition.RightSide) is btn:
                self._request_close(i)   # ← was: self.tabCloseRequested.emit(i)
                return

    def _close_all(self, tw) -> None:
        for i in range(tw.count() - 1, -1, -1):
            if self._tab_is_closable(tw, i):
                self._request_close(i)

    def _close_others(self, tw, keep_idx: int) -> None:
        widget_to_keep = tw.widget(keep_idx)
        for i in range(tw.count() - 1, -1, -1):
            if tw.widget(i) is not widget_to_keep and self._tab_is_closable(tw, i):
                self._request_close(i)

    def _sync_close_buttons(self, current_idx: int) -> None:
        """
        Show the close button only on *current_idx*; hide it on every other tab.

        Called by ``tabInserted``, ``tabRemoved``, and — via DockRegion —
        ``currentChanged``.
        """
        for i in range(self.count()):
            btn = self.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if btn is not None:
                btn.setVisible(i == current_idx)

    def _set_all_close_buttons_visible(self, visible: bool) -> None:
        for i in range(self.count()):
            btn = self.tabButton(i, QTabBar.ButtonPosition.RightSide)
            if btn is not None:
                btn.setVisible(visible)

    # ── size hints ────────────────────────────────────────────────────────────

    def _needed_width(self, idx: int) -> int:
        fm = self.fontMetrics()
        txt = self.tabText(idx)
        ico = self.tabIcon(idx)

        # base text width with padding
        w = self._H_PAD + fm.horizontalAdvance(txt) + self._H_PAD

        # icon width – use the bar’s actual icon size if set, else fallback
        icon_size = self.iconSize()
        if not ico.isNull():
            if icon_size.isValid() and icon_size.width() > 0:
                w += icon_size.width() + 4  # icon + spacing
            else:
                w += 20 + 4  # fallback

        # close button – add its width plus extra margin (style often adds padding)
        btn = self.tabButton(idx, QTabBar.ButtonPosition.RightSide)
        if btn is not None:
            w += btn.width() + 8  # generous margin to avoid overlap

        # (optional) add the style’s default inter‑tab horizontal padding
        style_hspace = self.style().pixelMetric(
            QStyle.PixelMetric.PM_TabBarTabHSpace, None, self
        )
        w += style_hspace

        return w

    def tabSizeHint(self, index: int) -> QSize:
        base = super().tabSizeHint(index)
        return QSize(max(base.width(), self._needed_width(index)), base.height())

    def minimumTabSizeHint(self, index: int) -> QSize:
        base = super().minimumTabSizeHint(index)
        return QSize(max(base.width(), self._needed_width(index)), base.height())

    # ── mouse events ─────────────────────────────────────────────────────────

    def mousePressEvent(self, ev) -> None:
        if ev.button() == Qt.MouseButton.LeftButton:
            self._press_pos = ev.pos()
            self._press_tab = self.tabAt(ev.pos())
        super().mousePressEvent(ev)

    def mouseMoveEvent(self, ev) -> None:
        if (
            not self._dragging
            and self._press_pos is not None
            and self._press_tab >= 0
            and ev.buttons() & Qt.MouseButton.LeftButton
            and (ev.pos() - self._press_pos).manhattanLength() >= self.THRESHOLD
        ):
            self._dragging  = True
            self._drag_from = self._press_tab
            self._drop_at   = self._press_tab
            self._set_all_close_buttons_visible(False)
            self._reorder_ghost = _ReorderGhost(
                self.tabText(self._drag_from),
                self.tabIcon(self._drag_from),
            )

        if not self.rect().contains(ev.pos()):
            # Cursor left the bar → tear-off; abandon reorder cleanly.
            source = self._drag_from if self._drag_from >= 0 else self._press_tab
            self._cleanup_reorder(commit=False)
            self.drag_initiated.emit(source, ev.globalPosition().toPoint())
            self._press_pos = None
            self._press_tab = -1
            self._dragging  = False
            return

        # Still inside bar — update drop index and ghost position.
        new_drop = self._compute_drop_at(ev.pos().x())
        if new_drop != self._drop_at:
            self._drop_at = new_drop
            self.update()

        if self._reorder_ghost:
            gp = ev.globalPosition().toPoint()
            self._reorder_ghost.move(gp + QPoint(12, 8))
            if not self._reorder_ghost.isVisible():
                self._reorder_ghost.show()

    def mouseReleaseEvent(self, ev) -> None:
        if self._dragging and self._drag_from >= 0:
            self._cleanup_reorder(commit=True)
        self._press_pos = None
        self._press_tab = -1
        self._dragging  = False
        super().mouseReleaseEvent(ev)

    # ── reorder helpers ───────────────────────────────────────────────────────

    def _compute_drop_at(self, cursor_x: int) -> int:
        """
        Insertion index into the non-dragged tab sequence for a cursor at *cursor_x*.

        Uses the tabs' natural (unmoved) rects for the midpoint comparison,
        which is correct because ``moveTab`` is never called mid-drag.
        """
        for seq, tab_idx in enumerate(
                i for i in range(self.count()) if i != self._drag_from
        ):
            if cursor_x < self.tabRect(tab_idx).center().x():
                return seq
        return self.count() - 1   # after all non-dragged tabs

    def _cleanup_reorder(self, commit: bool) -> None:
        """
        Commit or discard the reorder and restore close-button visibility.

        ``moveTab(drag_from, drop_at)`` is the correct call because Qt's
        moveTab semantics (remove-then-insert) happen to map 1-to-1 onto
        ``_drop_at`` as derived from ``_compute_drop_at``.
        """
        if commit and self._drag_from >= 0 and self._drop_at != self._drag_from:
            self.moveTab(self._drag_from, self._drop_at)

        if self._reorder_ghost is not None:
            self._reorder_ghost.hide()
            self._reorder_ghost.deleteLater()
            self._reorder_ghost = None

        self._drag_from = -1
        self._drop_at   = -1
        self.sync_close_buttons(self.currentIndex())
        self.update()

    # ── paint ─────────────────────────────────────────────────────────────────

    def paintEvent(self, ev) -> None:
        """
        During a reorder drag render a custom layout:

        * Non-dragged tabs are drawn at shifted positions that make room for
          the gap at ``_drop_at``.
        * The dragged tab itself is omitted — its space is occupied by the gap.
        * The gap is a blue-tinted dashed rounded rect, same width as the
          dragged tab, showing exactly where it will land on release.

        ``QStyleOptionTab.rect`` is overridden with each tab's computed
        position so the system style draws at the right place.
        ``QStyleOptionTab.position`` is corrected for the new sequence order
        so tab borders render cleanly.
        """
        if not self._dragging or self._drag_from < 0:
            super().paintEvent(ev)
            return

        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)

        dragged_natural = self.tabRect(self._drag_from)
        gap_w = dragged_natural.width()
        tab_h = dragged_natural.height()
        tab_y = dragged_natural.y()

        non_dragged = [i for i in range(self.count()) if i != self._drag_from]

        # Build a list of (tab_index, draw_rect) and locate the gap rect.
        # Positions are computed from scratch so they correctly reflect the
        # gap being inserted at _drop_at — the Qt layout (tabRect) is not used
        # for positions here, only for per-tab widths.
        start_x = self.tabRect(0).x() if self.count() > 0 else 0
        x = start_x
        draw_items: list[tuple[int, QRect]] = []
        gap_rect: QRect | None = None

        for seq, tab_idx in enumerate(non_dragged):
            if seq == self._drop_at:
                gap_rect = QRect(x, tab_y, gap_w, tab_h)
                x += gap_w
            w = self.tabRect(tab_idx).width()
            draw_items.append((tab_idx, QRect(x, tab_y, w, tab_h)))
            x += w

        if self._drop_at >= len(non_dragged):
            gap_rect = QRect(x, tab_y, gap_w, tab_h)

        # Draw each non-dragged tab at its computed position.
        n = len(draw_items)
        for draw_seq, (tab_idx, rect) in enumerate(draw_items):
            opt = QStyleOptionTab()
            self.initStyleOption(opt, tab_idx)
            opt.rect = rect

            # Correct the sequence-position flag so borders render properly.
            if n == 1:
                opt.position = QStyleOptionTab.TabPosition.OnlyOneTab
            elif draw_seq == 0:
                opt.position = QStyleOptionTab.TabPosition.Beginning
            elif draw_seq == n - 1:
                opt.position = QStyleOptionTab.TabPosition.End
            else:
                opt.position = QStyleOptionTab.TabPosition.Middle

            self.style().drawControl(
                QStyle.ControlElement.CE_TabBarTab, opt, p, self
            )

        # Draw the gap: a dashed blue rounded rect the same size as the dragged tab.
        if gap_rect is not None:
            pal = self.palette()
            fill = pal.color(QPalette.ColorRole.Highlight)
            fill.setAlpha(45)
            border = pal.color(QPalette.ColorRole.Highlight)
            border.setAlpha(180)
            p.setPen(QPen(border, 1.5, Qt.PenStyle.DashLine))
            p.setBrush(fill)
            p.drawRoundedRect(gap_rect.adjusted(2, 3, -2, -3), 4, 4)

        p.end()

    # ── context menu ──────────────────────────────────────────────────────────

    # ── teardown ──────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """
        Fully tear down the tab bar.

        Call order
        ----------
        1. Abort any in-progress reorder drag so the ghost widget is
           destroyed and its ``deleteLater`` is not left dangling.
        2. Walk every tab widget owned by the parent ``QTabWidget`` and call
           ``cleanup()`` on those that satisfy :class:`CleanupTab`.  This is
           the same duck-type check used by ``_request_close``; doing it here
           ensures widgets are torn down even when the whole dock is being
           destroyed rather than closed tab-by-tab.
        3. Disconnect all signals emitted by this bar so that any lambdas
           captured in connected slots (which may hold ``self`` references)
           are released.
        4. Reset every piece of internal drag/reorder state to its null value
           so there are no lingering references to ``QPoint``, ``QWidget``,
           or other Qt objects.

        This method is idempotent — calling it more than once is safe.
        """
        # 1. Abort any live reorder drag (destroys the ghost label).
        if self._dragging or self._reorder_ghost is not None:
            self._cleanup_reorder(commit=False)

        # 2. Call cleanup() on every tab widget that supports it.
        tw = self.parentWidget()  # DockRegion / QTabWidget
        if tw is not None:
            for i in range(tw.count()):
                widget = tw.widget(i)
                if isinstance(widget, CleanupTab):
                    try:
                        widget.cleanup()
                    except Exception:
                        pass  # never let a misbehaving widget abort the chain

        # 3. Disconnect all signals to break any captured reference cycles.
        for sig in (self.drag_initiated, self.split_requested, self.tabCloseRequested):
            try:
                sig.disconnect()
            except RuntimeError:
                pass  # already had no connections — safe to ignore

        # 4. Zero out all internal state so nothing is kept alive by this object.
        self._press_pos  = None
        self._press_tab  = -1
        self._dragging   = False
        self._drag_from  = -1
        self._drop_at    = -1
        # _reorder_ghost was already cleared by _cleanup_reorder above.

    # ── context menu ──────────────────────────────────────────────────────────

    def contextMenuEvent(self, ev) -> None:
        right_clicked_idx = self.tabAt(ev.pos())
        tw = self.parentWidget()   # the DockRegion (QTabWidget)

        menu = QMenu(self)

        # ── Tab selection sub-menu ─────────────────────────────────────────
        select_menu = menu.addMenu("Select Tab")
        for i in range(tw.count()):
            act = QAction(tw.tabText(i), self)
            act.setCheckable(True)
            act.setChecked(i == tw.currentIndex())
            if not tw.tabIcon(i).isNull():
                act.setIcon(tw.tabIcon(i))
            act.triggered.connect(lambda _, idx=i: tw.setCurrentIndex(idx))
            select_menu.addAction(act)

        menu.addSeparator()

        # ── Close actions ──────────────────────────────────────────────────
        # "Close <tab>" — only shown when the right-clicked tab is closable.
        if right_clicked_idx >= 0 and self._tab_is_closable(tw, right_clicked_idx):
            label = tw.tabText(right_clicked_idx)
            close_this = QAction(f'Close "{label}"', self)
            close_this.triggered.connect(
                lambda: self._request_close(right_clicked_idx)
            )
            menu.addAction(close_this)

        # "Close All" — only shown when at least one tab can be closed.
        closable_indices = [i for i in range(tw.count()) if self._tab_is_closable(tw, i)]
        if closable_indices:
            close_all = QAction("Close All Tabs", self)
            close_all.triggered.connect(lambda: self._close_all(tw))
            menu.addAction(close_all)

        # "Close Others" — only shown when at least one *other* tab is closable.
        if right_clicked_idx >= 0:
            other_closable = [
                i for i in range(tw.count())
                if i != right_clicked_idx and self._tab_is_closable(tw, i)
            ]
            if other_closable:
                close_others = QAction("Close Other Tabs", self)
                close_others.triggered.connect(
                    lambda: self._close_others(tw, right_clicked_idx)
                )
                menu.addAction(close_others)

        # ── Split actions ──────────────────────────────────────────────────
        # Only shown when there are at least two tabs so splitting is meaningful.
        if tw.count() >= 2:
            menu.addSeparator()

            split_menu = menu.addMenu("Split Current Tab")

            act_left = QAction("◀  Split Left", self)
            act_left.triggered.connect(lambda: self.split_requested.emit("left"))
            split_menu.addAction(act_left)

            act_right = QAction("▶  Split Right", self)
            act_right.triggered.connect(lambda: self.split_requested.emit("right"))
            split_menu.addAction(act_right)

            act_top = QAction("▲  Split Above", self)
            act_top.triggered.connect(lambda: self.split_requested.emit("top"))
            split_menu.addAction(act_top)

            act_bottom = QAction("▼  Split Below", self)
            act_bottom.triggered.connect(lambda: self.split_requested.emit("bottom"))
            split_menu.addAction(act_bottom)

        menu.exec(ev.globalPos())