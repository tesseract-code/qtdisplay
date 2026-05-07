"""
dock/manager.py
---------------
Top-level dock manager built on QMainWindow.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Iterable
from uuid import uuid4

from PyQt6.QtCore import Qt, QPoint, QEvent, QObject, pyqtSignal
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QSplitter,
)

from qtdisplay.dock.overlay import (
    DragGhost, DropOverlay, Zone, _drag, reset_drag_state,
)
from qtdisplay.dock.region import DockRegion
from qtdisplay.dock.floating import FloatingDock


# ──────────────────────────────────────────────────────────────────────────────
# Public API types
# ──────────────────────────────────────────────────────────────────────────────

DOCK_PANEL_ID_PROPERTY = "_dock_panel_id"

class DockArea(str, Enum):
    """Named, built-in regions in the dock workspace."""

    LEFT = "left"
    TOP = "top"
    CENTER = "center"
    BOTTOM = "bottom"
    RIGHT = "right"

    @classmethod
    def coerce(cls, value: "DockArea | str") -> "DockArea":
        try:
            return value if isinstance(value, cls) else cls(str(value))
        except ValueError as exc:
            raise ValueError(
                f"Unknown dock area {value!r}. "
                f"Expected one of: {[area.value for area in cls]}"
            ) from exc


class DockSide(str, Enum):
    """Side used when splitting a panel or region."""

    LEFT = "left"
    RIGHT = "right"
    TOP = "top"
    BOTTOM = "bottom"

    @classmethod
    def coerce(cls, value: "DockSide | str") -> "DockSide":
        try:
            return value if isinstance(value, cls) else cls(str(value))
        except ValueError as exc:
            raise ValueError(
                f"Unknown dock side {value!r}. "
                f"Expected one of: {[side.value for side in cls]}"
            ) from exc


@dataclass(frozen=True, slots=True)
class PanelHandle:
    """Stable handle returned when a panel is added to the dock."""

    id: str
    widget: QWidget
    title: str

# ──────────────────────────────────────────────────────────────────────────────
# Dock manager
# ──────────────────────────────────────────────────────────────────────────────

class DockManager(QMainWindow):
    panel_added = pyqtSignal(QWidget)
    panel_removed = pyqtSignal(QWidget)
    panel_focused = pyqtSignal(QWidget)
    panel_floated = pyqtSignal(QWidget)

    def __init__(
            self,
            title: str = "Dock Manager",
            size: tuple[int, int] = (1400, 860),
            embedded: bool = True,
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        self.resize(*size)
        if embedded:
            self.setWindowFlags(Qt.WindowType.Widget)

        self._ghost: DragGhost | None = None
        self._regions: dict[str, DockRegion] = {}
        self._floating: list[FloatingDock] = []
        self._focused_region: DockRegion | None = None

        # Single shared drop overlay (compass with split/merge zones).
        self._drop_overlay = DropOverlay(self)
        self._current_target: DockRegion | None = None

        self._build_layout()
        self._build_view_menu()
        QApplication.instance().focusChanged.connect(self._on_focus_changed)

    # ── public API ────────────────────────────────────────────────────────────

    def add_panel(
            self,
            area: DockArea | str,
            widget: QWidget,
            title: str,
            icon: QIcon | None = None,
            closable: bool = True,
            panel_id: str | None = None,
    ) -> PanelHandle:
        """
        Add *widget* to the named dock *area*.

        The existing call shape remains valid::

            manager.add_panel("center", widget, "Title")

        ``area`` may be either a :class:`DockArea` or its string value.  The
        returned :class:`PanelHandle` gives application code a stable, reusable
        reference for later focus/move/close operations.
        """
        area = DockArea.coerce(area)
        region = self._regions.get(area.value)
        if region is None:
            raise ValueError(
                f"Unknown area {area.value!r}.  "
                f"Must be one of: {list(self._regions)}"
            )

        get_prop = getattr(widget, "property", lambda _name: None)
        set_prop = getattr(widget, "setProperty", lambda _name, _value: None)
        pid = panel_id or get_prop(DOCK_PANEL_ID_PROPERTY) or uuid4().hex
        set_prop(DOCK_PANEL_ID_PROPERTY, pid)

        region.add_panel(widget, title, icon, closable=closable)
        region.show()

        handle = PanelHandle(str(pid), widget, title)
        self.panel_added.emit(widget)
        return handle

    def focus_panel(self, panel: QWidget | PanelHandle) -> bool:
        """
        Bring *panel* to the front and highlight its region.

        Accepts either the original ``QWidget`` or the :class:`PanelHandle`
        returned by :meth:`add_panel`.  Returns ``True`` when the panel is
        currently hosted by this manager.
        """
        widget = panel.widget if isinstance(panel, PanelHandle) else panel

        for region in self._regions.values():
            idx = region.indexOf(widget)
            if idx >= 0:
                region.setCurrentIndex(idx)
                region.show()
                self._set_focused_region(region)
                self.panel_focused.emit(widget)
                return True

        for floating in self._floating:
            inner = floating.centralWidget()
            if isinstance(inner, DockRegion):
                idx = inner.indexOf(widget)
                if idx >= 0:
                    inner.setCurrentIndex(idx)
                    floating.raise_()
                    floating.activateWindow()
                    self.panel_focused.emit(widget)
                    return True

        return False

    def panels(self) -> list[QWidget]:
        """Return every panel widget currently owned by this manager."""
        return [widget for region in self._iter_regions() for widget in self._widgets_in_region(region)]

    def regions(self) -> dict[str, DockRegion]:
        """Return a shallow copy of the registered dock regions by name."""
        return dict(self._regions)

    def remove_panel(self, panel: QWidget | PanelHandle) -> bool:
        """Remove *panel* from its region without deleting the widget."""
        located = self._locate_panel(panel)
        if located is None:
            return False

        region, idx = located
        widget = region.widget(idx)
        region.removeTab(idx)
        widget.setParent(None)
        self._cleanup_empty_region(region)
        self.panel_removed.emit(widget)
        return True

    def close_panel(self, panel: QWidget | PanelHandle) -> bool:
        """Close *panel*, honoring the same cleanup path as tab close buttons."""
        located = self._locate_panel(panel)
        if located is None:
            return False

        region, idx = located
        widget = region.widget(idx)
        bar = region.tabBar()
        if hasattr(bar, "request_close"):
            bar.request_close(idx)
        else:
            region.removeTab(idx)
            widget.deleteLater()
        self._cleanup_empty_region(region)
        self.panel_removed.emit(widget)
        return True

    def split_panel(
            self,
            panel: QWidget | PanelHandle,
            direction: DockSide | str,
    ) -> bool:
        """Split the panel's current region and move *panel* to the new side."""
        side = DockSide.coerce(direction)
        located = self._locate_panel(panel)
        if located is None:
            return False

        region, idx = located
        region.setCurrentIndex(idx)
        return self.split_region_with_current_tab(region, side)

    def float_panel(
            self,
            panel: QWidget | PanelHandle,
            gpos: QPoint | None = None,
    ) -> bool:
        """Detach *panel* into a floating dock window."""
        located = self._locate_panel(panel)
        if located is None:
            return False

        region, idx = located
        widget = region.widget(idx)
        self._float_panel_from_region(region, idx, gpos)
        self.panel_floated.emit(widget)
        return True

    def _iter_regions(self) -> Iterable[DockRegion]:
        for region in self._regions.values():
            yield region
        for floating in self._floating:
            inner = floating.centralWidget()
            if isinstance(inner, DockRegion):
                yield inner

    @staticmethod
    def _widgets_in_region(region: DockRegion) -> list[QWidget]:
        return [region.widget(i) for i in range(region.count())]

    def _locate_panel(self, panel: QWidget | PanelHandle) -> tuple[DockRegion, int] | None:
        widget = panel.widget if isinstance(panel, PanelHandle) else panel
        for region in self._iter_regions():
            idx = region.indexOf(widget)
            if idx >= 0:
                return region, idx
        return None

    # ── layout ────────────────────────────────────────────────────────────────

    def _region(self, name: str) -> DockRegion:
        r = DockRegion(name, self)
        self._regions[name] = r
        return r

    def _build_layout(self) -> None:
        self._h = QSplitter(Qt.Orientation.Horizontal)
        self._left = self._region("left")

        self._v = QSplitter(Qt.Orientation.Vertical)
        self._top = self._region("top")
        self._center = self._region("center")
        self._bottom = self._region("bottom")
        for region, stretch in [
            (self._top, 1),
            (self._center, 5),
            (self._bottom, 1),
        ]:
            self._v.addWidget(region)
            self._v.setStretchFactor(self._v.indexOf(region), stretch)

        self._right = self._region("right")
        for region, stretch in [
            (self._left, 1),
            (self._v, 5),
            (self._right, 1),
        ]:
            self._h.addWidget(region)
            self._h.setStretchFactor(self._h.indexOf(region), stretch)

        self.setCentralWidget(self._h)
        self._left.hide()
        self._top.hide()
        self._bottom.hide()
        self._right.hide()

    def _build_view_menu(self) -> None:
        vm = self.menuBar().addMenu("&View")
        for name in (DockArea.LEFT.value, DockArea.TOP.value, DockArea.BOTTOM.value, DockArea.RIGHT.value):
            act = QAction(f"Show {name.title()} Panel", self, checkable=True)
            r = self._regions[name]
            act.toggled.connect(lambda v, rr=r: rr.setVisible(v))
            vm.addAction(act)

    # ── focus tracking ────────────────────────────────────────────────────────

    def _set_focused_region(self, region: 'DockRegion | None') -> None:
        """
        Update the focused-region highlight.

        Shared by ``_on_focus_changed`` (driven by Qt's global focus signal)
        and ``focus_panel`` (driven by explicit API calls) so both paths
        produce identical visual results.
        """
        if region is self._focused_region:
            return
        if self._focused_region is not None:
            self._focused_region.set_focused(False)
        self._focused_region = region
        if self._focused_region is not None:
            self._focused_region.set_focused(True)

    def _on_focus_changed(self, _old: QWidget, new: 'QWidget | None') -> None:
        next_region: DockRegion | None = None
        if new is not None:
            for region in self._regions.values():
                if self._widget_in_region(new, region):
                    next_region = region
                    break
        self._set_focused_region(next_region)

    @staticmethod
    def _widget_in_region(widget: QWidget, region: DockRegion) -> bool:
        w: QWidget | None = widget
        while w is not None:
            if w is region:
                return True
            w = w.parentWidget()
        return False

    # ── drag coordination ─────────────────────────────────────────────────────

    def begin_drag(self, source: DockRegion, tab_idx: int,
                   gpos: QPoint) -> None:
        if tab_idx < 0 or tab_idx >= source.count():
            return

        _drag.active    = True
        _drag.source    = source
        _drag.tab_index = tab_idx
        _drag.widget    = source.widget(tab_idx)
        _drag.title     = source.tabText(tab_idx)
        _drag.icon      = source.tabIcon(tab_idx)

        snapshot = _drag.widget.grab()
        self._ghost = DragGhost(snapshot)
        self._ghost.move(gpos + QPoint(14, 14))
        self._ghost.show()

        QApplication.instance().installEventFilter(self)
        QApplication.setOverrideCursor(Qt.CursorShape.DragMoveCursor)

    def eventFilter(self, obj: QObject, ev: QEvent) -> bool:
        if not _drag.active:
            return False

        t = ev.type()

        if t == QEvent.Type.MouseMove:
            gp = ev.globalPosition().toPoint()
            if self._ghost:
                self._ghost.move(gp + QPoint(14, 14))

            # Find target region under cursor (no longer excludes the source).
            target = self._find_region_at_global(gp)

            if target is not self._current_target:
                if target is not None:
                    self._drop_overlay.show_for(target)
                else:
                    self._drop_overlay.hide_overlay()
                self._current_target = target

            # Keep the compass zone highlight live.
            if target is not None:
                zone = self._drop_overlay.zone_for_global(gp)
                self._drop_overlay.set_hovered(zone)

            return True

        if (
                t == QEvent.Type.MouseButtonRelease
                and ev.button() == Qt.MouseButton.LeftButton
        ):
            gp = ev.globalPosition().toPoint()
            target = self._find_region_at_global(gp)

            if target is not None:
                zone = self._drop_overlay.zone_for_global(gp)

                if target is _drag.source:
                    # Dropped on the source region itself
                    if zone == Zone.CENTER:
                        # Cancel the drag – just clean up, no movement
                        self._end_drag()
                        return True
                    else:
                        # Split the source region and place the tab in the new half
                        self._split_and_drop(target, zone)
                else:
                    # Different region – split or merge as before
                    if zone in (Zone.LEFT, Zone.RIGHT, Zone.TOP, Zone.BOTTOM):
                        self._split_and_drop(target, zone)
                    else:
                        self._drop_to_region(target)
            else:
                # Dropped on empty desktop → detach to floating window.
                self._create_floating_window(gp)

            self._end_drag()
            return True

        if t == QEvent.Type.KeyPress and ev.key() == Qt.Key.Key_Escape:
            self._end_drag()
            return True

        return False

    def _find_region_at_global(self, gpos: QPoint) -> DockRegion | None:
        """Return the DockRegion under the global cursor (if any)."""
        widget = QApplication.widgetAt(gpos)
        while widget is not None:
            if isinstance(widget, DockRegion):
                return widget
            widget = widget.parentWidget()
        return None

    def _drop_to_region(self, dest: DockRegion) -> None:
        """Move the dragged tab into the target region as a new tab."""
        src, widget, title, icon = (
            _drag.source, _drag.widget, _drag.title, _drag.icon,
        )
        idx = src.indexOf(widget)
        if idx >= 0:
            src.removeTab(idx)

        if icon and not icon.isNull():
            dest.addTab(widget, icon, title)
        else:
            dest.addTab(widget, title)

        dest.setCurrentWidget(widget)
        dest.show()

        self._cleanup_empty_region(src)

    def _create_floating_window(self, gpos: QPoint) -> None:
        if _drag.widget is None:
            return

        src = _drag.source
        idx = src.indexOf(_drag.widget)
        if idx >= 0:
            src.removeTab(idx)

        win = FloatingDock(_drag.widget, _drag.title, _drag.icon, self)
        win.move(gpos - QPoint(win.width() // 2, 16))
        win.show()
        self._floating.append(win)

        # Ensure floating window is removed from list on close
        win.destroyed.connect(lambda w=win: self.unregister_floating(w))

        self._cleanup_empty_region(src)

    def _float_panel_from_region(
            self,
            region: DockRegion,
            idx: int,
            gpos: QPoint | None = None,
    ) -> FloatingDock:
        """Move a tab from *region*/*idx* into a new floating dock."""
        widget = region.widget(idx)
        title = region.tabText(idx)
        icon = region.tabIcon(idx)
        region.removeTab(idx)

        win = FloatingDock(widget, title, icon, self)
        if gpos is not None:
            win.move(gpos - QPoint(win.width() // 2, 16))
        win.show()
        self._floating.append(win)
        win.destroyed.connect(lambda w=win: self.unregister_floating(w))

        self._cleanup_empty_region(region)
        return win

    def _end_drag(self) -> None:
        QApplication.instance().removeEventFilter(self)
        QApplication.restoreOverrideCursor()

        self._drop_overlay.hide_overlay()
        self._current_target = None

        if self._ghost:
            self._ghost.hide()
            self._ghost.deleteLater()
            self._ghost = None

        _drag.reset()

    # ── split logic ───────────────────────────────────────────────────────────

    def _split_and_drop(self, region: DockRegion, zone: int) -> None:
        """
        Split *region* in the direction indicated by *zone* and place the
        currently dragged tab in the newly created half.
        """
        _dir_map = {
            Zone.LEFT:   (Qt.Orientation.Horizontal, "before"),
            Zone.RIGHT:  (Qt.Orientation.Horizontal, "after"),
            Zone.TOP:    (Qt.Orientation.Vertical,   "before"),
            Zone.BOTTOM: (Qt.Orientation.Vertical,   "after"),
        }
        orientation, position = _dir_map[zone]

        src    = _drag.source
        widget = _drag.widget
        title  = _drag.title
        icon   = _drag.icon

        # Detach the tab from its current region.
        idx = src.indexOf(widget)
        if idx >= 0:
            src.removeTab(idx)

        # Cleanup empty source region immediately
        self._cleanup_empty_region(src)

        # Create a fresh region and populate it.
        new_region = self._new_split_region()
        new_region.add_panel(widget, title, icon)

        self._insert_region_split(region, new_region, orientation, position)
        new_region.show()

    def split_region_with_current_tab(
            self,
            region: DockRegion,
            direction: DockSide | str,
    ) -> bool:
        """
        Public entry-point used by the tab-bar context menu.

        Moves the region's *currently active* tab into a new side-by-side
        region created by splitting *region* in the requested direction.
        Does nothing if the region has only one tab (splitting would leave
        an empty region).
        """
        if region.count() < 1:
            return False

        idx    = region.currentIndex()
        widget = region.widget(idx)
        title  = region.tabText(idx)
        icon   = region.tabIcon(idx)

        region.removeTab(idx)

        side = DockSide.coerce(direction)
        _orient_map = {
            DockSide.LEFT:   (Qt.Orientation.Horizontal, "before"),
            DockSide.RIGHT:  (Qt.Orientation.Horizontal, "after"),
            DockSide.TOP:    (Qt.Orientation.Vertical,   "before"),
            DockSide.BOTTOM: (Qt.Orientation.Vertical,   "after"),
        }
        orientation, position = _orient_map[side]

        new_region = self._new_split_region()
        new_region.add_panel(widget, title, icon)

        self._insert_region_split(region, new_region, orientation, position)
        new_region.show()

        # Cleanup empty source region.
        self._cleanup_empty_region(region)
        return True

    def _new_split_region(self) -> DockRegion:
        """Create a new, uniquely-named DockRegion and register it."""
        name = f"split_{len(self._regions)}"
        # Guard against name collisions when many splits have occurred.
        while name in self._regions:
            name = f"split_{len(self._regions)}_{id(object())}"
        r = DockRegion(name, self)
        self._regions[name] = r
        return r

    def _insert_region_split(
            self,
            existing: DockRegion,
            new_region: DockRegion,
            orientation: Qt.Orientation,
            position: str,           # "before" | "after"
    ) -> None:
        """
        Insert *new_region* next to *existing* along *orientation*.

        Two cases:

        **Same orientation** — the parent splitter already runs in the
        requested direction, so *new_region* is simply inserted adjacent
        to *existing* and the occupied slot's size is split evenly.

        **Different orientation** — a new nested QSplitter is created,
        *existing* is moved into it alongside *new_region*, and the new
        splitter takes *existing*'s former slot in the parent.
        """
        parent = existing.parentWidget()

        if isinstance(parent, QSplitter) and parent.orientation() == orientation:
            # ── Fast path: just insert into the existing splitter ──────────
            idx    = parent.indexOf(existing)
            sizes  = parent.sizes()

            insert_at = idx if position == "before" else idx + 1
            parent.insertWidget(insert_at, new_region)

            # Split the vacated slot's pixels evenly between the two halves.
            old  = sizes[idx] if idx < len(sizes) else 200
            half = max(old // 2, 80)
            new_sizes = sizes[:idx] + [half, half] + sizes[idx + 1:]
            parent.setSizes(new_sizes)

        elif isinstance(parent, QSplitter):
            # ── Slow path: wrap existing in a new nested splitter ──────────
            idx        = parent.indexOf(existing)
            outer_sizes = parent.sizes()

            splitter = QSplitter(orientation)

            # addWidget() re-parents existing, removing it from parent.
            if position == "before":
                splitter.addWidget(new_region)
                splitter.addWidget(existing)
            else:
                splitter.addWidget(existing)
                splitter.addWidget(new_region)

            # Insert the new splitter where existing used to live.
            parent.insertWidget(idx, splitter)
            parent.setSizes(outer_sizes)   # N-1 removed + 1 inserted = same N

            old  = outer_sizes[idx] if idx < len(outer_sizes) else 400
            half = max(old // 2, 80)
            splitter.setSizes([half, half])

        # else: floating / unmanaged parent — silently do nothing.

    # ── region lifecycle (CRITICAL FIX) ───────────────────────────────────────

    def _cleanup_empty_region(self, region: DockRegion) -> None:
        """
        If *region* is empty and not one of the core permanent regions,
        remove it from the layout, destroy it, and collapse any
        now-single-child splitter.
        """
        # If still has tabs, nothing to do.
        if region.count() != 0:
            return

        # Core regions are never removed; they can be hidden.
        core = {"left", "right", "top", "bottom", "center"}
        if region.region_name in core:
            if region.region_name != "center":
                region.hide()
            return

        # Non-core region: remove from layout and registry.
        parent = region.parentWidget()
        region.setParent(None)          # detach from layout

        # Delete region widget (scheduled for later)
        region.deleteLater()

        # Remove from registry
        if region.region_name in self._regions:
            del self._regions[region.region_name]

        # Collapse parent splitter if it now has only one child.
        if isinstance(parent, QSplitter):
            self._collapse_splitter_if_single(parent)

    def _collapse_splitter_if_single(self, splitter: QSplitter) -> None:
        """If *splitter* has exactly one child, replace it with that child."""
        if splitter.count() != 1:
            return

        sole = splitter.widget(0)
        grand_parent = splitter.parentWidget()

        # Only collapse if grand_parent is a QSplitter (avoid removing the top-level splitter)
        if isinstance(grand_parent, QSplitter):
            idx = grand_parent.indexOf(splitter)
            if idx >= 0:
                splitter.setParent(None)             # detach the splitter itself
                grand_parent.insertWidget(idx, sole)
                splitter.deleteLater()               # schedule removal of the now-empty splitter
        # else: keep the splitter (it might be the central widget or some other container)

    # ── floating window cleanup ───────────────────────────────────────────────

    def unregister_floating(self, window: FloatingDock) -> None:
        if window in self._floating:
            self._floating.remove(window)

    # ── manager teardown ──────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """
        Fully tear down the dock manager and every object it owns.

        This is the root of the cleanup chain.  It must be called before
        the manager is closed or deleted so that all resources are released
        while every object is still fully alive.  ``closeEvent`` calls it
        automatically; call it explicitly only if you need to destroy the
        manager without closing its window.

        Call order
        ----------
        1. **focusChanged** — disconnect the application-level signal first
           so the handler cannot fire against partially-destroyed regions
           during the rest of teardown.
        2. **Active drag** — if a drag is in progress, remove the global
           event filter, restore the cursor, destroy the ghost widget, and
           reset the module-level ``_drag`` singleton.  Doing this before
           touching regions ensures no dangling ``_drag.source`` reference.
        3. **Drop overlay** — hide and release its region pointer.
        4. **Floating docks** — call each window's ``cleanup()`` (which
           cascades to its ``DockRegion``, tab bar, and tab widgets), then
           schedule deletion.  The list is cleared afterwards so
           ``_cleanup_floating`` cannot access freed objects.
        5. **Registered regions** — call ``cleanup()`` on every region still
           in ``self._regions``.  Each call cascades to ``DockTabBar`` and
           every ``CleanupTab`` widget it contains.
        6. **Null out references** — drop ``_focused_region`` and
           ``_current_target`` so the GC is not blocked by any lingering
           cycles through this object.

        This method is idempotent — calling it more than once is safe.
        """
        # 1. Stop reacting to focus changes during teardown.
        try:
            QApplication.instance().focusChanged.disconnect(self._on_focus_changed)
        except (RuntimeError, TypeError):
            pass  # already disconnected or application is shutting down

        # 2. Abort any in-progress drag cleanly.
        if _drag.active:
            QApplication.instance().removeEventFilter(self)
            QApplication.restoreOverrideCursor()
        if self._ghost is not None:
            self._ghost.hide()
            self._ghost.deleteLater()
            self._ghost = None
        reset_drag_state()  # zeros _drag singleton; safe even when not active

        # 3. Tear down the shared drop overlay.
        self._drop_overlay.cleanup()
        self._current_target = None

        # 4. Clean up every floating dock window.
        for floating in list(self._floating):
            floating.cleanup()   # → DockRegion.cleanup → DockTabBar.cleanup → CleanupTab widgets
            floating.deleteLater()
        self._floating.clear()   # prevent _cleanup_floating from touching freed objects

        # 5. Clean up every registered region.
        for region in list(self._regions.values()):
            region.cleanup()     # → DockTabBar.cleanup → CleanupTab widgets
        self._regions.clear()

        # 6. Drop all remaining reference-holding attributes.
        self._focused_region = None

    def closeEvent(self, event) -> None:
        """Ensure full teardown whenever the window is closed."""
        self.cleanup()
        super().closeEvent(event)