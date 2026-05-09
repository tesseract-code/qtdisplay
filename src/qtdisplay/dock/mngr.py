"""
dock/manager.py
---------------
Top-level dock manager built on QMainWindow.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Callable, Iterable
from uuid import uuid4

from PyQt6.QtCore import Qt, QPoint, QEvent, QObject, pyqtSignal
from PyQt6.QtGui import QIcon, QAction
from PyQt6.QtWidgets import (
    QApplication, QFileDialog, QMainWindow, QMessageBox, QWidget, QSplitter,
)

from qtdisplay.dock.overlay import (
    DragGhost, DropOverlay, Zone, _drag, reset_drag_state,
)
from qtdisplay.dock.region import DockRegion
from qtdisplay.dock.tab_bar import DOCK_CLOSABLE_PROPERTY
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
        self._panel_provider: Callable[[str], tuple[QWidget, str, QIcon | None, bool] | None] | None = None

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

        vm.addSeparator()

        save_act = QAction("Save Layout…", self)
        save_act.setShortcut("Ctrl+Shift+S")
        save_act.triggered.connect(self._menu_save_layout)
        vm.addAction(save_act)

        restore_act = QAction("Restore Layout…", self)
        restore_act.setShortcut("Ctrl+Shift+R")
        restore_act.triggered.connect(self._menu_restore_layout)
        vm.addAction(restore_act)

    # ── layout save / restore ─────────────────────────────────────────────────

    def register_panel_provider(
        self,
        provider: Callable[[str], tuple[QWidget, str, QIcon | None, bool] | None],
    ) -> None:
        """
        Register the factory used by :meth:`restore_layout` and the
        *Restore Layout…* menu action.

        The callable receives a ``panel_id`` string (the value stored in the
        ``DOCK_PANEL_ID_PROPERTY`` dynamic property when the panel was added)
        and must return either:

        * ``(widget, title, icon, closable)`` — the widget to re-add, its tab
          label, an optional :class:`QIcon`, and a closability flag; or
        * ``None`` — to silently skip that panel (e.g. it has been removed).

        The provider is called once per panel during restore, in tab order,
        region by region, with floating windows last.
        """
        self._panel_provider = provider

    # ── serialisation ─────────────────────────────────────────────────────────

    def save_layout(self) -> dict:
        """
        Serialise the current dock layout to a JSON-compatible dict.

        The returned dict is version-tagged and contains:

        * ``"geometry"`` — main-window position and size as ``[x, y, w, h]``.
        * ``"layout"``   — recursive splitter / region tree rooted at the
          horizontal central splitter.
        * ``"floating"`` — list of floating-window entries, each with its
          own geometry, title, and tab list.

        Tab entries carry ``"id"``, ``"title"``, and ``"closable"`` so that
        :meth:`restore_layout` can rebuild the exact panel arrangement even
        after the application has been restarted.
        """
        geo = self.geometry()
        return {
            "version": 1,
            "geometry": [geo.x(), geo.y(), geo.width(), geo.height()],
            "layout": self._serialise_node(self._h),
            "floating": [self._serialise_floating(f) for f in self._floating],
        }

    def save_layout_to_file(self, path: str | Path) -> None:
        """Write :meth:`save_layout` output to *path* as pretty-printed JSON."""
        Path(path).write_text(
            json.dumps(self.save_layout(), indent=2), encoding="utf-8"
        )

    def _serialise_node(self, widget: QWidget) -> dict:
        """Recursively serialise a splitter/region subtree."""
        if isinstance(widget, QSplitter):
            return {
                "type": "splitter",
                "orientation": (
                    "H" if widget.orientation() == Qt.Orientation.Horizontal else "V"
                ),
                "sizes": widget.sizes(),
                "children": [
                    self._serialise_node(widget.widget(i))
                    for i in range(widget.count())
                ],
            }
        if isinstance(widget, DockRegion):
            tabs: list[dict] = []
            for i in range(widget.count()):
                w = widget.widget(i)
                pid = (
                    w.property(DOCK_PANEL_ID_PROPERTY)
                    if callable(getattr(w, "property", None))
                    else None
                ) or ""
                closable = (
                    w.property(DOCK_CLOSABLE_PROPERTY)
                    if callable(getattr(w, "property", None))
                    else True
                )
                tabs.append({
                    "id": str(pid),
                    "title": widget.tabText(i),
                    "closable": bool(closable) if closable is not None else True,
                })
            return {
                "type": "region",
                "name": widget.region_name,
                "tabs": tabs,
                "current": widget.currentIndex(),
                "visible": widget.isVisible(),
            }
        # Unrecognised widget type — record as a no-op placeholder.
        return {"type": "unknown"}

    def _serialise_floating(self, win: FloatingDock) -> dict:
        """Serialise one floating-dock window."""
        geo = win.geometry()
        region = win.region
        tabs: list[dict] = []
        if region is not None:
            for i in range(region.count()):
                w = region.widget(i)
                pid = (
                    w.property(DOCK_PANEL_ID_PROPERTY)
                    if callable(getattr(w, "property", None))
                    else None
                ) or ""
                closable = (
                    w.property(DOCK_CLOSABLE_PROPERTY)
                    if callable(getattr(w, "property", None))
                    else True
                )
                tabs.append({
                    "id": str(pid),
                    "title": region.tabText(i),
                    "closable": bool(closable) if closable is not None else True,
                })
        return {
            "geometry": [geo.x(), geo.y(), geo.width(), geo.height()],
            "title": win.windowTitle(),
            "tabs": tabs,
            "current": region.currentIndex() if region is not None else 0,
        }

    # ── deserialisation ───────────────────────────────────────────────────────

    def restore_layout(
        self,
        state: dict,
        panel_provider: Callable[
            [str], tuple[QWidget, str, QIcon | None, bool] | None
        ] | None = None,
    ) -> None:
        """
        Rebuild the dock layout from a dict previously returned by
        :meth:`save_layout`.

        Parameters
        ----------
        state:
            The saved layout dict.  Must carry ``"version": 1``.
        panel_provider:
            Callable ``(panel_id) -> (widget, title, icon, closable) | None``.
            Falls back to the provider registered with
            :meth:`register_panel_provider` when ``None``.  If neither is
            available a :exc:`RuntimeError` is raised.

        Restore sequence
        ----------------
        1. All floating windows are closed and their widgets released
           (``setParent(None)``).
        2. Every tab is removed from every docked region; widgets are
           released so the provider can re-adopt them.
        3. Non-core dynamic regions are deleted; the five core regions
           (``left``, ``top``, ``center``, ``bottom``, ``right``) are kept.
        4. The splitter tree is reconstructed from the saved node tree,
           reusing core regions in-place and creating fresh
           :class:`DockRegion` objects for dynamically-created regions.
        5. Each region is re-populated by calling *panel_provider* for every
           saved tab entry.  Unknown IDs (provider returns ``None``) are
           silently skipped.
        6. Floating windows are recreated and positioned.
        7. The main-window geometry is restored.
        """
        provider = panel_provider or self._panel_provider
        if provider is None:
            raise RuntimeError(
                "restore_layout() requires a panel_provider.  "
                "Pass one directly or call register_panel_provider() first."
            )

        version = state.get("version")
        if version != 1:
            raise ValueError(f"Unsupported layout version: {version!r}")

        layout_node = state.get("layout", {})

        # 1. Tear down floating windows, releasing their widgets.
        for floating in list(self._floating):
            region = floating.region
            if region is not None:
                while region.count():
                    w = region.widget(0)
                    region.removeTab(0)
                    w.setParent(None)  # type: ignore[arg-type]
            floating.cleanup()
            floating.close()
            floating.deleteLater()
        self._floating.clear()

        # 2. Release all panels from docked regions.
        for region in list(self._regions.values()):
            while region.count():
                w = region.widget(0)
                region.removeTab(0)
                w.setParent(None)  # type: ignore[arg-type]

        # 3. Delete dynamic (non-core) regions from registry + layout.
        _CORE = {"left", "top", "center", "bottom", "right"}
        for name in list(self._regions.keys()):
            if name not in _CORE:
                r = self._regions.pop(name)
                r.setParent(None)  # type: ignore[arg-type]
                r.deleteLater()

        # 4. Rebuild the splitter tree.
        orphaned_splitters = self._strip_splitter(self._h)
        self._restore_splitter(self._h, layout_node)
        for sp in orphaned_splitters:
            sp.deleteLater()

        # Keep _v pointing at the first nested splitter inside _h (if any).
        for i in range(self._h.count()):
            child = self._h.widget(i)
            if isinstance(child, QSplitter):
                self._v = child
                break

        # 5. Populate regions with panels.
        self._populate_from_node(layout_node, provider)

        # 6. Recreate floating windows.
        for fstate in state.get("floating", []):
            tabs = fstate.get("tabs", [])
            if not tabs:
                continue

            first = provider(tabs[0]["id"])
            if first is None:
                continue

            w0, t0, ic0, cl0 = first
            win = FloatingDock(w0, t0, ic0, self)
            fregion = win.region
            if fregion is not None:
                for tab_data in tabs[1:]:
                    result = provider(tab_data["id"])
                    if result is None:
                        continue
                    wn, tn, icn, cln = result
                    if callable(getattr(wn, "setProperty", None)):
                        wn.setProperty(DOCK_PANEL_ID_PROPERTY, tab_data["id"])
                    fregion.add_panel(wn, tn, icn, closable=cln)

                cur = fstate.get("current", 0)
                if 0 <= cur < fregion.count():
                    fregion.setCurrentIndex(cur)

            fgeo = fstate.get("geometry", [])
            if len(fgeo) == 4:
                win.setGeometry(*fgeo)
            win.show()
            self._floating.append(win)
            win.destroyed.connect(lambda w=win: self.unregister_floating(w))

        # 7. Restore main-window geometry.
        geo = state.get("geometry", [])
        if len(geo) == 4:
            self.setGeometry(*geo)

    def restore_layout_from_file(
        self,
        path: str | Path,
        panel_provider: Callable[
            [str], tuple[QWidget, str, QIcon | None, bool] | None
        ] | None = None,
    ) -> None:
        """Load a JSON file written by :meth:`save_layout_to_file` and restore it."""
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.restore_layout(data, panel_provider)

    # ── restore helpers ───────────────────────────────────────────────────────

    def _strip_splitter(self, splitter: QSplitter) -> list[QSplitter]:
        """
        Recursively detach all children of *splitter* and return every
        intermediate :class:`QSplitter` found, so callers can
        ``deleteLater`` them once the new tree has been built.

        :class:`DockRegion` children are merely detached — they stay alive
        in ``self._regions`` and will be re-adopted by the new tree.
        """
        orphans: list[QSplitter] = []
        while splitter.count():
            child = splitter.widget(0)
            # Recurse into nested splitters *before* detaching so their
            # children are stripped while they are still accessible.
            if isinstance(child, QSplitter):
                orphans.extend(self._strip_splitter(child))
                orphans.append(child)
            child.setParent(None)  # type: ignore[arg-type]
        return orphans

    def _restore_splitter(self, splitter: QSplitter, node: dict) -> None:
        """Reconstruct the children of *splitter* in-place from *node*."""
        if node.get("type") != "splitter":
            return

        orient = (
            Qt.Orientation.Horizontal
            if node.get("orientation") == "H"
            else Qt.Orientation.Vertical
        )
        splitter.setOrientation(orient)

        for child_node in node.get("children", []):
            child_widget = self._node_to_widget(child_node)
            if child_widget is not None:
                splitter.addWidget(child_widget)

        sizes = node.get("sizes", [])
        if sizes:
            splitter.setSizes(sizes)

    def _node_to_widget(self, node: dict) -> QWidget | None:
        """
        Convert one saved node to a live widget, recursing into nested
        splitters.  Returns ``None`` for unknown node types.
        """
        kind = node.get("type")

        if kind == "splitter":
            orient = (
                Qt.Orientation.Horizontal
                if node.get("orientation") == "H"
                else Qt.Orientation.Vertical
            )
            sp = QSplitter(orient)
            for child_node in node.get("children", []):
                child = self._node_to_widget(child_node)
                if child is not None:
                    sp.addWidget(child)
            sizes = node.get("sizes", [])
            if sizes:
                sp.setSizes(sizes)
            return sp

        if kind == "region":
            name = node.get("name", "")
            if name in self._regions:
                return self._regions[name]
            # Dynamic region not yet in registry — create it fresh.
            region = DockRegion(name, self)
            self._regions[name] = region
            return region

        return None

    def _populate_from_node(
        self,
        node: dict,
        provider: Callable[[str], tuple[QWidget, str, QIcon | None, bool] | None],
    ) -> None:
        """Walk *node* recursively and fill every region with its saved panels."""
        kind = node.get("type")

        if kind == "region":
            name = node.get("name", "")
            region = self._regions.get(name)
            if region is None:
                return

            for tab_data in node.get("tabs", []):
                result = provider(tab_data["id"])
                if result is None:
                    continue
                widget, title, icon, closable = result
                # Re-stamp the original ID so the next save round-trips cleanly.
                if callable(getattr(widget, "setProperty", None)):
                    widget.setProperty(DOCK_PANEL_ID_PROPERTY, tab_data["id"])
                region.add_panel(widget, title, icon, closable=closable)

            cur = node.get("current", 0)
            if 0 <= cur < region.count():
                region.setCurrentIndex(cur)

            # Core side panels are hidden by default; restore their visibility.
            visible = node.get("visible", True)
            if name != "center":
                region.setVisible(bool(visible))
            elif not region.isVisible():
                region.show()

        elif kind == "splitter":
            for child_node in node.get("children", []):
                self._populate_from_node(child_node, provider)

    # ── menu-triggered save / restore ─────────────────────────────────────────

    def _menu_save_layout(self) -> None:
        """Prompt for a file path and write the current layout as JSON."""
        path, _ = QFileDialog.getSaveFileName(
            self,
            "Save Layout",
            "layout.json",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self.save_layout_to_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))

    def _menu_restore_layout(self) -> None:
        """Prompt for a layout file and restore it using the registered provider."""
        if self._panel_provider is None:
            QMessageBox.information(
                self,
                "No Panel Provider",
                "Register a panel provider first by calling\n"
                "manager.register_panel_provider(fn)\nbefore restoring a layout.",
            )
            return

        path, _ = QFileDialog.getOpenFileName(
            self,
            "Restore Layout",
            "",
            "JSON Files (*.json);;All Files (*)",
        )
        if not path:
            return
        try:
            self.restore_layout_from_file(path)
        except Exception as exc:
            QMessageBox.critical(self, "Restore Failed", str(exc))

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