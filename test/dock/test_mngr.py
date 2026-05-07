"""Unit tests for mngr.DockManager.

These tests intentionally use light-weight fakes for PyQt6 and the sibling
qtdisplay.dock modules so the manager logic can be exercised in CI without a
real Qt display server.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest


class Signal:
    def __init__(self):
        self.slots = []

    def connect(self, slot):
        self.slots.append(slot)

    def disconnect(self, slot):
        self.slots = [s for s in self.slots if s is not slot]

    def emit(self, *args, **kwargs):
        for slot in list(self.slots):
            slot(*args, **kwargs)


class FakeQPoint:
    def __init__(self, x=0, y=0):
        self.x = x
        self.y = y

    def __add__(self, other):
        return FakeQPoint(self.x + other.x, self.y + other.y)

    def __sub__(self, other):
        return FakeQPoint(self.x - other.x, self.y - other.y)

    def __eq__(self, other):
        return isinstance(other, FakeQPoint) and (self.x, self.y) == (other.x,
                                                                      other.y)


class FakeQt:
    class Orientation:
        Horizontal = "horizontal"
        Vertical = "vertical"

    class WindowType:
        Widget = "widget-window"

    class CursorShape:
        DragMoveCursor = "drag-move"

    class MouseButton:
        LeftButton = "left-button"

    class Key:
        Key_Escape = "escape"


class FakeQEvent:
    class Type:
        MouseMove = "mouse-move"
        MouseButtonRelease = "mouse-release"
        KeyPress = "key-press"


class FakeQObject:
    pass


class FakeQIcon:
    def __init__(self, null=True):
        self._null = null

    def isNull(self):
        return self._null


class FakeQWidget:
    def __init__(self, *args, **kwargs):
        self._parent = args[0] if args else None
        self.visible = True
        self.deleted = False
        self.cleaned = False
        self._pos = FakeQPoint()
        if hasattr(self._parent, "_children"):
            self._parent._children.append(self)

    def parentWidget(self):
        return self._parent

    def setParent(self, parent):
        old = self._parent
        if hasattr(old, "_widgets") and self in old._widgets:
            old._widgets.remove(self)
        if hasattr(old, "_children") and self in old._children:
            old._children.remove(self)
        self._parent = parent
        if hasattr(parent, "_children") and self not in parent._children:
            parent._children.append(self)

    def hide(self):
        self.visible = False

    def show(self):
        self.visible = True

    def setVisible(self, value):
        self.visible = bool(value)

    def deleteLater(self):
        self.deleted = True

    def grab(self):
        return "snapshot"

    def move(self, point):
        self._pos = point

    def width(self):
        return 200


class FakeQSplitter(FakeQWidget):
    def __init__(self, orientation, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._orientation = orientation
        self._widgets = []
        self._sizes = []

    def orientation(self):
        return self._orientation

    def addWidget(self, widget):
        widget.setParent(self)
        self._widgets.append(widget)
        if len(self._sizes) < len(self._widgets):
            self._sizes.append(200)

    def insertWidget(self, idx, widget):
        widget.setParent(self)
        if widget in self._widgets:
            self._widgets.remove(widget)
        self._widgets.insert(idx, widget)
        if len(self._sizes) < len(self._widgets):
            self._sizes.insert(idx, 200)

    def indexOf(self, widget):
        try:
            return self._widgets.index(widget)
        except ValueError:
            return -1

    def count(self):
        return len(self._widgets)

    def widget(self, idx):
        return self._widgets[idx]

    def sizes(self):
        return list(self._sizes or [200] * len(self._widgets))

    def setSizes(self, sizes):
        self._sizes = list(sizes)

    def setStretchFactor(self, idx, stretch):
        pass


class FakeAction:
    def __init__(self, text, parent=None, checkable=False):
        self.text = text
        self.parent = parent
        self.checkable = checkable
        self.toggled = Signal()


class FakeMenu:
    def __init__(self):
        self.actions = []

    def addAction(self, action):
        self.actions.append(action)


class FakeMenuBar:
    def __init__(self):
        self.menus = []

    def addMenu(self, text):
        menu = FakeMenu()
        menu.text = text
        self.menus.append(menu)
        return menu


class FakeQMainWindow(FakeQWidget):
    def __init__(self):
        super().__init__()
        self._children = []
        self._menu_bar = FakeMenuBar()
        self._central = None
        self.title = None
        self.size = None
        self.flags = None
        self.closed = False

    def setWindowTitle(self, title):
        self.title = title

    def resize(self, *size):
        self.size = size

    def setWindowFlags(self, flags):
        self.flags = flags

    def setCentralWidget(self, widget):
        self._central = widget
        widget.setParent(self)

    def centralWidget(self):
        return self._central

    def menuBar(self):
        return self._menu_bar

    def closeEvent(self, event):
        self.closed = True


class FakeQApplication:
    _instance = None
    widget_at = None
    cursor = None

    def __init__(self):
        self.focusChanged = Signal()
        self.installed_filters = []
        FakeQApplication._instance = self

    @classmethod
    def instance(cls):
        if cls._instance is None:
            cls._instance = FakeQApplication()
        return cls._instance

    def installEventFilter(self, obj):
        self.installed_filters.append(obj)

    def removeEventFilter(self, obj):
        if obj in self.installed_filters:
            self.installed_filters.remove(obj)

    @staticmethod
    def setOverrideCursor(cursor):
        FakeQApplication.cursor = cursor

    @staticmethod
    def restoreOverrideCursor():
        FakeQApplication.cursor = None

    @staticmethod
    def widgetAt(point):
        return FakeQApplication.widget_at


class DragState:
    def __init__(self):
        self.reset()

    def reset(self):
        self.active = False
        self.source = None
        self.tab_index = -1
        self.widget = None
        self.title = None
        self.icon = None


class FakeZone:
    LEFT = 1
    RIGHT = 2
    TOP = 3
    BOTTOM = 4
    CENTER = 5


class FakeDragGhost(FakeQWidget):
    pass


class FakeDropOverlay(FakeQWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.shown_for = None
        self.hovered = None
        self.cleaned = False

    def show_for(self, region):
        self.shown_for = region
        self.visible = True

    def hide_overlay(self):
        self.visible = False
        self.shown_for = None

    def zone_for_global(self, point):
        return FakeZone.CENTER

    def set_hovered(self, zone):
        self.hovered = zone

    def cleanup(self):
        self.cleaned = True


class FakeDockRegion(FakeQWidget):
    def __init__(self, name, manager=None):
        super().__init__(manager)
        self.region_name = name
        self.manager = manager
        self.tabs = []
        self.current = -1
        self.focused = False
        self.cleaned = False

    def add_panel(self, widget, title, icon=None, closable=True):
        self.tabs.append({"widget": widget, "title": title, "icon": icon,
                          "closable": closable})
        widget.setParent(self)
        self.current = len(self.tabs) - 1

    def addTab(self, widget, *args):
        if len(args) == 2:
            icon, title = args
        else:
            icon, title = None, args[0]
        self.add_panel(widget, title, icon)

    def removeTab(self, idx):
        self.tabs.pop(idx)
        self.current = min(self.current, len(self.tabs) - 1)

    def count(self):
        return len(self.tabs)

    def widget(self, idx):
        return self.tabs[idx]["widget"]

    def indexOf(self, widget):
        for i, tab in enumerate(self.tabs):
            if tab["widget"] is widget:
                return i
        return -1

    def tabText(self, idx):
        return self.tabs[idx]["title"]

    def tabIcon(self, idx):
        return self.tabs[idx]["icon"]

    def setCurrentIndex(self, idx):
        self.current = idx

    def currentIndex(self):
        return self.current

    def setCurrentWidget(self, widget):
        self.current = self.indexOf(widget)

    def set_focused(self, value):
        self.focused = bool(value)

    def cleanup(self):
        self.cleaned = True


class FakeFloatingDock(FakeQWidget):
    def __init__(self, widget=None, title=None, icon=None, manager=None):
        super().__init__(manager)
        self._central = FakeDockRegion("floating", manager)
        if widget is not None:
            self._central.add_panel(widget, title, icon)
        self.raised = False
        self.activated = False
        self.cleaned = False
        self.destroyed = Signal()

    def centralWidget(self):
        return self._central

    def raise_(self):
        self.raised = True

    def activateWindow(self):
        self.activated = True

    def cleanup(self):
        self.cleaned = True


def install_stub_modules(monkeypatch):
    app = FakeQApplication()
    drag = DragState()

    pyqt6 = types.ModuleType("PyQt6")
    qtcore = types.ModuleType("PyQt6.QtCore")
    qtcore.Qt = FakeQt
    qtcore.QPoint = FakeQPoint
    qtcore.QEvent = FakeQEvent
    qtcore.QObject = FakeQObject
    qtgui = types.ModuleType("PyQt6.QtGui")
    qtgui.QIcon = FakeQIcon
    qtgui.QAction = FakeAction
    qtwidgets = types.ModuleType("PyQt6.QtWidgets")
    qtwidgets.QApplication = FakeQApplication
    qtwidgets.QMainWindow = FakeQMainWindow
    qtwidgets.QWidget = FakeQWidget
    qtwidgets.QSplitter = FakeQSplitter

    overlay = types.ModuleType("qtdisplay.dock.overlay")
    overlay.DragGhost = FakeDragGhost
    overlay.DropOverlay = FakeDropOverlay
    overlay.Zone = FakeZone
    overlay._drag = drag
    overlay.reset_drag_state = drag.reset

    region = types.ModuleType("qtdisplay.dock.region")
    region.DockRegion = FakeDockRegion
    floating = types.ModuleType("qtdisplay.dock.floating")
    floating.FloatingDock = FakeFloatingDock

    for name, module in {
        "PyQt6": pyqt6,
        "PyQt6.QtCore": qtcore,
        "PyQt6.QtGui": qtgui,
        "PyQt6.QtWidgets": qtwidgets,
        "qtdisplay": types.ModuleType("qtdisplay"),
        "qtdisplay.dock": types.ModuleType("qtdisplay.dock"),
        "qtdisplay.dock.overlay": overlay,
        "qtdisplay.dock.region": region,
        "qtdisplay.dock.floating": floating,
    }.items():
        monkeypatch.setitem(sys.modules, name, module)
    return app, drag


@pytest.fixture
def mngr(monkeypatch):
    install_stub_modules(monkeypatch)
    module_path = (
            Path(__file__).resolve().parents[2]
            / "src"
            / "qtdisplay"
            / "dock"
            / "mngr.py")

    spec = importlib.util.spec_from_file_location("mngr_under_test",
                                                  module_path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def manager(mngr):
    return mngr.DockManager(title="Tests", size=(800, 600))


def test_init_builds_layout_regions_and_view_menu(manager):
    assert manager.title == "Tests"
    assert manager.size == (800, 600)
    assert set(manager._regions) == {"left", "top", "center", "bottom", "right"}
    assert manager._regions["center"].visible is True
    assert manager._regions["left"].visible is False

    view_menu = manager.menuBar().menus[0]
    assert view_menu.text == "&View"
    assert [a.text for a in view_menu.actions] == [
        "Show Left Panel", "Show Top Panel", "Show Bottom Panel",
        "Show Right Panel"
    ]
    view_menu.actions[0].toggled.emit(True)
    assert manager._regions["left"].visible is True


def test_add_panel_adds_to_known_area_and_rejects_unknown(manager):
    widget = FakeQWidget()
    manager.add_panel("center", widget, "Editor", icon="icon", closable=False)

    center = manager._regions["center"]
    assert center.count() == 1
    assert center.tabs[0] == {"widget": widget, "title": "Editor",
                              "icon": "icon", "closable": False}

    with pytest.raises(ValueError, match="Unknown area 'missing'"):
        manager.add_panel("missing", FakeQWidget(), "Bad")


def test_focus_panel_prefers_registered_regions_and_sets_focus(manager):
    widget = FakeQWidget()
    manager.add_panel("center", widget, "Editor")

    manager.focus_panel(widget)

    assert manager._regions["center"].current == 0
    assert manager._focused_region is manager._regions["center"]
    assert manager._regions["center"].focused is True


def test_focus_panel_searches_floating_docks(manager):
    floating = FakeFloatingDock(manager=manager)
    widget = FakeQWidget()
    floating.centralWidget().add_panel(widget, "Float")
    manager._floating.append(floating)

    manager.focus_panel(widget)

    assert floating.centralWidget().current == 0
    assert floating.raised is True
    assert floating.activated is True


def test_focus_tracking_uses_widget_parent_chain(manager):
    region = manager._regions["center"]
    child = FakeQWidget(region)

    manager._on_focus_changed(None, child)
    assert manager._focused_region is region
    assert region.focused is True

    manager._on_focus_changed(child, FakeQWidget())
    assert manager._focused_region is None
    assert region.focused is False


def test_begin_drag_sets_global_drag_state_and_ghost(manager, mngr):
    src = manager._regions["center"]
    widget = FakeQWidget()
    icon = FakeQIcon(null=False)
    src.add_panel(widget, "Editor", icon)

    manager.begin_drag(src, 0, FakeQPoint(10, 20))

    assert mngr._drag.active is True
    assert mngr._drag.source is src
    assert mngr._drag.widget is widget
    assert mngr._drag.title == "Editor"
    assert manager._ghost.visible is True
    assert manager._ghost._pos == FakeQPoint(24, 34)
    assert manager in FakeQApplication.instance().installed_filters
    assert FakeQApplication.cursor == FakeQt.CursorShape.DragMoveCursor


def test_drop_to_region_moves_dragged_tab_and_cleans_source(manager, mngr):
    src = manager._regions["left"]
    dest = manager._regions["center"]
    widget = FakeQWidget()
    icon = FakeQIcon(null=False)
    src.add_panel(widget, "Moved", icon)
    mngr._drag.active = True
    mngr._drag.source = src
    mngr._drag.widget = widget
    mngr._drag.title = "Moved"
    mngr._drag.icon = icon

    manager._drop_to_region(dest)

    assert src.count() == 0
    assert src.visible is False
    assert dest.indexOf(widget) == 0
    assert dest.current == 0


def test_create_floating_window_removes_tab_and_registers_window(manager, mngr):
    src = manager._regions["center"]
    widget = FakeQWidget()
    src.add_panel(widget, "Detached")
    mngr._drag.source = src
    mngr._drag.widget = widget
    mngr._drag.title = "Detached"
    mngr._drag.icon = None

    manager._create_floating_window(FakeQPoint(300, 40))

    assert src.count() == 0
    assert len(manager._floating) == 1
    floating = manager._floating[0]
    assert floating.centralWidget().indexOf(widget) == 0
    assert floating.visible is True


def test_split_region_with_current_tab_creates_new_registered_region(manager):
    region = manager._regions["center"]
    first, second = FakeQWidget(), FakeQWidget()
    region.add_panel(first, "One")
    region.add_panel(second, "Two")
    region.setCurrentIndex(1)

    manager.split_region_with_current_tab(region, "right")

    split_names = [name for name in manager._regions if
                   name.startswith("split_")]
    assert len(split_names) == 1
    new_region = manager._regions[split_names[0]]
    assert new_region.indexOf(second) == 0
    assert region.indexOf(first) == 0


def test_cleanup_empty_non_core_region_removes_from_registry(manager):
    split = manager._new_split_region()
    parent = FakeQSplitter(FakeQt.Orientation.Horizontal)
    parent.addWidget(split)
    manager._regions[split.region_name] = split

    manager._cleanup_empty_region(split)

    assert split.deleted is True
    assert split.region_name not in manager._regions
    assert split.parentWidget() is None


def test_cleanup_is_idempotent_and_cleans_owned_objects(manager, mngr):
    floating = FakeFloatingDock(manager=manager)
    manager._floating.append(floating)
    manager._ghost = FakeDragGhost()
    mngr._drag.active = True

    manager.cleanup()
    manager.cleanup()

    assert manager._floating == []
    assert manager._regions == {}
    assert manager._focused_region is None
    assert manager._current_target is None
    assert manager._drop_overlay.cleaned is True
    assert floating.cleaned is True
    assert mngr._drag.active is False
