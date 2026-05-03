"""
dock/region.py
--------------
A QTabWidget subregion managed by DockManager.

Focus highlight is drawn on the *content* (stacked-widget) area only,
not across the full widget including the tab bar strip.
"""

from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QColor, QIcon, QPainter, QPen
from PyQt6.QtWidgets import (
    QSizePolicy,
    QTabBar,
    QTabWidget,
    QWidget,
)

from qtdisplay.dock.tab_bar import DockTabBar

if TYPE_CHECKING:
    from qtdisplay.dock.mngr import DockManager


class DockRegion(QTabWidget):
    """
    A named tab-widget region managed by :class:`DockManager`.

    Close-button ownership
    ----------------------
    ``DockTabBar`` installs and manages close buttons entirely via
    ``tabInserted`` / ``tabRemoved``.  ``DockRegion`` must not call
    ``setTabButton`` — doing so would create a second button and break
    the tab bar's index-lookup logic.

    Focus is indicated by a coloured border drawn around the content pane.

    Split support
    -------------
    The tab bar emits ``split_requested(direction)`` when the user picks a
    split action from the context menu.  This region forwards the signal to
    ``DockManager.split_region_with_current_tab`` so the manager can handle
    all layout mutations in one place.
    """

    FOCUS_WIDTH = 2

    became_empty = pyqtSignal()

    _FOCUS_COLOR: QColor | None = None

    @classmethod
    def _focus_color(cls) -> QColor:
        if cls._FOCUS_COLOR is None:
            cls._FOCUS_COLOR = QColor(70, 110, 230)
        return cls._FOCUS_COLOR

    def __init__(self, name: str, manager: DockManager) -> None:
        super().__init__()
        self.region_name = name
        self._manager_ref = weakref.ref(manager)
        self._focused = False

        bar = DockTabBar()
        bar.drag_initiated.connect(lambda i, p: manager.begin_drag(self, i, p))
        bar.tabCloseRequested.connect(self._close_tab)

        # Forward split requests to the manager, which owns all layout logic.
        bar.split_requested.connect(
            lambda direction: self._on_split_requested(direction)
        )

        self.setTabBar(bar)

        # Keep the close button in sync with whichever tab is active.
        # DockTabBar.tabInserted handles newly added tabs; this signal covers
        # the user clicking a different tab after insertion.
        self.currentChanged.connect(bar._sync_close_buttons)

        self.setDocumentMode(True)
        self.setTabsClosable(False)   # buttons installed by DockTabBar
        self.setMovable(False)
        self.setMinimumSize(60, 50)
        self.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )

    @property
    def manager(self) -> DockManager | None:
        return self._manager_ref()

    # ── split forwarding ──────────────────────────────────────────────────────

    def _on_split_requested(self, direction: str) -> None:
        """
        Delegate the split to the manager.

        The manager is the single source of truth for layout mutations; it
        knows where this region lives in the splitter hierarchy and can
        safely restructure it.
        """
        mgr = self.manager
        if mgr is not None:
            mgr.split_region_with_current_tab(self, direction)

    # ── focus highlight ────────────────────────────────────────────────────────

    def set_focused(self, v: bool) -> None:
        if v != self._focused:
            self._focused = v
            self.update()

    def _content_rect(self):
        bar = self.tabBar()
        r = self.rect()
        match self.tabPosition():
            case QTabWidget.TabPosition.North:
                return r.adjusted(0, bar.height(), 0, 0)
            case QTabWidget.TabPosition.South:
                return r.adjusted(0, 0, 0, -bar.height())
            case QTabWidget.TabPosition.West:
                return r.adjusted(bar.width(), 0, 0, 0)
            case QTabWidget.TabPosition.East:
                return r.adjusted(0, 0, -bar.width(), 0)
            case _:
                return r

    def paintEvent(self, ev) -> None:
        super().paintEvent(ev)
        if not self._focused:
            return
        p = QPainter(self)
        try:
            p.setPen(QPen(self._focus_color(), self.FOCUS_WIDTH))
            p.setBrush(Qt.BrushStyle.NoBrush)
            half = self.FOCUS_WIDTH // 2
            p.drawRect(self._content_rect().adjusted(half, half, -half, -half))
        finally:
            p.end()

    # ── tab management ────────────────────────────────────────────────────────

    def removeTab(self, index: int) -> None:
        """Override to emit `became_empty` when the last tab is removed."""
        super().removeTab(index)
        if self.count() == 0:
            self.became_empty.emit()

    def _close_tab(self, idx: int) -> None:
        w = self.widget(idx)
        self.removeTab(idx)
        if w:
            w.deleteLater()
        if self.count() == 0 and self.region_name != "center":
            self.hide()

    def close_closable_tabs(self) -> None:
        """
        Request close for every tab that is not marked non-closable.

        Used by :class:`FloatingDock` when the user presses the window's
        title-bar close button.  Closable tabs go through the normal
        ``_request_close`` path (so :class:`CleanupTab` widgets are torn
        down correctly); non-closable tabs are left untouched.

        After this call the caller should check ``self.count() > 0`` to
        decide whether to block the window-close event.
        """
        bar = self.tabBar()
        if isinstance(bar, DockTabBar):
            bar._close_all(self)

    # ── teardown ──────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """
        Fully tear down this region and everything it contains.

        Call order
        ----------
        1. Delegate to :meth:`DockTabBar.cleanup`, which calls ``cleanup()``
           on every tab widget satisfying :class:`CleanupTab` and destroys
           any live drag ghost.
        2. Disconnect signals owned by this region — ``became_empty`` and
           ``currentChanged`` — so lambda slots that captured ``self`` or
           the manager are released.
        3. Poison the manager weak reference so any post-cleanup code that
           calls ``self.manager`` receives ``None`` rather than a stale
           manager that may itself be partially torn down.

        This method is idempotent — calling it more than once is safe.
        """
        # 1. Tear down the tab bar (and every CleanupTab widget inside it).
        bar = self.tabBar()
        if isinstance(bar, DockTabBar):
            bar.cleanup()

        # 3. Drop the manager reference so there is no accidental retain cycle.
        self._manager_ref = lambda: None  # type: ignore[assignment]

    def add_panel(
            self,
            widget: QWidget,
            title: str,
            icon: QIcon | None = None,
            closable: bool = True,
    ) -> None:
        """
        Add *widget* as a new tab.

        Parameters
        ----------
        widget:
            The content widget to embed.
        title:
            Tab label.
        icon:
            Optional tab icon.
        closable:
            When ``False``, the close button is permanently suppressed for
            this tab and all close actions skip it.  The state is stored as
            the Qt dynamic property ``"_dock_closable"`` on *widget* itself
            so it travels with the widget if it is later moved to a different
            region via drag or split.

        The close button is installed automatically by DockTabBar.tabInserted;
        no extra call is needed here.
        """
        if not closable:
            widget.setProperty("_dock_closable", False)

        if icon and not icon.isNull():
            self.addTab(widget, icon, title)
        else:
            self.addTab(widget, title)
        self.setCurrentWidget(widget)