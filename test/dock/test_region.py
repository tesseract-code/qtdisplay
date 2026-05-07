"""
Tests for DockRegion
====================

Strategy
--------
All PyQt6 and project dependencies are mocked before import so the suite runs
without a display server or a QApplication.

The two ``isinstance`` checks in production code —
``isinstance(bar, DockTabBar)`` in ``cleanup()`` and
``close_closable_tabs()`` — require real stub classes (not MagicMocks) as the
second argument.  The same ``__new__``-returns-mock / ``__class__``-override
trick used in test_floating_dock.py is applied to ``DockTabBar`` here.

Run with:
    pytest test_dock_region.py -v
"""

from __future__ import annotations

import types
import weakref
from unittest.mock import MagicMock, call, patch, sentinel

import pytest


# ---------------------------------------------------------------------------
# Real stub for DockTabBar
# ---------------------------------------------------------------------------

def _make_tab_bar_stub():
    """
    Return (bar_instance_mock, FakeDockTabBar).

    FakeDockTabBar is a real type so ``isinstance(bar, DockTabBar)`` works
    in production code.  Constructing it always returns the same mock so
    DockRegion.__init__'s ``DockTabBar()`` call hands back the mock we spy on.
    """
    bar = MagicMock(name="tab_bar")

    # Wire the signals the constructor connects to as plain mocks.
    bar.drag_initiated = MagicMock()
    bar.drag_initiated.connect = MagicMock()
    bar.tabCloseRequested = MagicMock()
    bar.tabCloseRequested.connect = MagicMock()
    bar.split_requested = MagicMock()
    bar.split_requested.connect = MagicMock()

    class FakeDockTabBar:
        def __new__(cls, *args, **kwargs):   # noqa: ARG003
            return bar

    bar.__class__ = FakeDockTabBar
    return bar, FakeDockTabBar


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mocks():
    """
    Patch every external symbol touched by DockRegion and yield a namespace.

    All QTabWidget methods are patched on the class so they are visible to
    instances created during the test.  DockTabBar is replaced with the real
    stub class so isinstance passes correctly.
    """
    bar_inst, FakeDockTabBar = _make_tab_bar_stub()
    manager = MagicMock(name="manager")

    # currentChanged signal stub used in __init__
    current_changed = MagicMock()
    current_changed.connect = MagicMock()

    ns = types.SimpleNamespace(
        bar=bar_inst,
        FakeDockTabBar=FakeDockTabBar,
        manager=manager,
    )

    patches = [
        patch("qtdisplay.dock.region.QTabWidget.__init__", return_value=None),
        patch("qtdisplay.dock.region.QTabWidget.setTabBar"),
        patch("qtdisplay.dock.region.QTabWidget.setDocumentMode"),
        patch("qtdisplay.dock.region.QTabWidget.setTabsClosable"),
        patch("qtdisplay.dock.region.QTabWidget.setMovable"),
        patch("qtdisplay.dock.region.QTabWidget.setMinimumSize"),
        patch("qtdisplay.dock.region.QTabWidget.setSizePolicy"),
        patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=bar_inst),
        patch("qtdisplay.dock.region.QTabWidget.count", return_value=1),
        patch("qtdisplay.dock.region.QTabWidget.widget", return_value=MagicMock()),
        patch("qtdisplay.dock.region.QTabWidget.removeTab"),
        patch("qtdisplay.dock.region.QTabWidget.addTab"),
        patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget"),
        patch("qtdisplay.dock.region.QTabWidget.tabPosition",
              return_value=MagicMock()),
        patch("qtdisplay.dock.region.QTabWidget.rect",
              return_value=MagicMock()),
        patch("qtdisplay.dock.region.QTabWidget.update"),
        patch("qtdisplay.dock.region.QTabWidget.hide"),
        patch("qtdisplay.dock.region.QTabWidget.paintEvent"),
        # currentChanged is accessed as self.currentChanged; patch via property
        patch("qtdisplay.dock.region.QTabWidget.currentChanged",
              new_callable=lambda: property(lambda self: current_changed),
              create=True),
        patch("qtdisplay.dock.region.DockTabBar", FakeDockTabBar),
        patch("qtdisplay.dock.region.QPainter"),
        patch("qtdisplay.dock.region.QPen"),
        patch("qtdisplay.dock.region.QColor"),
        patch("qtdisplay.dock.region.QSizePolicy"),
    ]

    for p in patches:
        p.start()

    ns._patches = patches
    ns._current_changed = current_changed

    yield ns

    for p in patches:
        p.stop()


def _make_region(mocks, name="main"):
    from qtdisplay.dock.region import DockRegion
    return DockRegion(name, mocks.manager)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_region_name_stored(self, mocks):
        r = _make_region(mocks, name="sidebar")
        assert r.region_name == "sidebar"

    def test_manager_stored_as_weakref(self, mocks):
        r = _make_region(mocks)
        assert r._manager_ref() is mocks.manager

    def test_focused_starts_false(self, mocks):
        r = _make_region(mocks)
        assert r._focused is False

    def test_tab_bar_constructed_and_set(self, mocks):
        from qtdisplay.dock.region import DockRegion
        with patch("qtdisplay.dock.region.QTabWidget.setTabBar") as mock_set:
            DockRegion("x", mocks.manager)
            mock_set.assert_called_once_with(mocks.bar)

    def test_drag_initiated_signal_connected(self, mocks):
        _make_region(mocks)
        mocks.bar.drag_initiated.connect.assert_called_once()

    def test_tab_close_requested_connected_to_close_tab(self, mocks):
        _make_region(mocks)
        mocks.bar.tabCloseRequested.connect.assert_called_once()

    def test_split_requested_connected(self, mocks):
        _make_region(mocks)
        mocks.bar.split_requested.connect.assert_called_once()

    def test_document_mode_enabled(self, mocks):
        from qtdisplay.dock.region import DockRegion
        with patch("qtdisplay.dock.region.QTabWidget.setDocumentMode") as m:
            DockRegion("x", mocks.manager)
            m.assert_called_once_with(True)

    def test_tabs_closable_disabled(self, mocks):
        from qtdisplay.dock.region import DockRegion
        with patch("qtdisplay.dock.region.QTabWidget.setTabsClosable") as m:
            DockRegion("x", mocks.manager)
            m.assert_called_once_with(False)

    def test_movable_disabled(self, mocks):
        from qtdisplay.dock.region import DockRegion
        with patch("qtdisplay.dock.region.QTabWidget.setMovable") as m:
            DockRegion("x", mocks.manager)
            m.assert_called_once_with(False)

    def test_minimum_size_set(self, mocks):
        from qtdisplay.dock.region import DockRegion
        with patch("qtdisplay.dock.region.QTabWidget.setMinimumSize") as m:
            DockRegion("x", mocks.manager)
            m.assert_called_once_with(60, 50)

    def test_current_changed_syncs_close_buttons(self, mocks):
        _make_region(mocks)
        # currentChanged.connect must have been called with bar._sync_close_buttons
        mocks._current_changed.connect.assert_called_once_with(
            mocks.bar._sync_close_buttons
        )

    def test_drag_lambda_calls_manager_begin_drag(self, mocks):
        _make_region(mocks)
        callback = mocks.bar.drag_initiated.connect.call_args[0][0]
        fake_point = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=mocks.bar):
            # We need the region instance for the lambda closure
            from qtdisplay.dock.region import DockRegion
            region = DockRegion("y", mocks.manager)
            # Retrieve the lambda connected during this specific construction
            cb = mocks.bar.drag_initiated.connect.call_args[0][0]
            cb(3, fake_point)
            mocks.manager.begin_drag.assert_called_with(region, 3, fake_point)

    def test_split_lambda_calls_on_split_requested(self, mocks):
        from qtdisplay.dock.region import DockRegion
        region = DockRegion("z", mocks.manager)
        cb = mocks.bar.split_requested.connect.call_args[0][0]
        cb("horizontal")
        mocks.manager.split_region_with_current_tab.assert_called_once_with(
            region, "horizontal"
        )


# ---------------------------------------------------------------------------
# manager property
# ---------------------------------------------------------------------------

class TestManagerProperty:
    def test_returns_live_manager(self, mocks):
        r = _make_region(mocks)
        assert r.manager is mocks.manager

    def test_returns_none_when_poisoned(self, mocks):
        r = _make_region(mocks)
        r._manager_ref = lambda: None  # type: ignore
        assert r.manager is None


# ---------------------------------------------------------------------------
# _on_split_requested
# ---------------------------------------------------------------------------

class TestOnSplitRequested:
    def test_delegates_to_manager(self, mocks):
        r = _make_region(mocks)
        r._on_split_requested("vertical")
        mocks.manager.split_region_with_current_tab.assert_called_once_with(
            r, "vertical"
        )

    def test_does_nothing_when_manager_gone(self, mocks):
        r = _make_region(mocks)
        r._manager_ref = lambda: None  # type: ignore
        r._on_split_requested("horizontal")  # must not raise
        mocks.manager.split_region_with_current_tab.assert_not_called()


# ---------------------------------------------------------------------------
# set_focused
# ---------------------------------------------------------------------------

class TestSetFocused:
    def test_sets_focused_true(self, mocks):
        r = _make_region(mocks)
        r._focused = False
        with patch("qtdisplay.dock.region.QTabWidget.update") as mock_update:
            r.set_focused(True)
        assert r._focused is True
        mock_update.assert_called_once()

    def test_sets_focused_false(self, mocks):
        r = _make_region(mocks)
        r._focused = True
        with patch("qtdisplay.dock.region.QTabWidget.update") as mock_update:
            r.set_focused(False)
        assert r._focused is False
        mock_update.assert_called_once()

    def test_no_update_when_value_unchanged_true(self, mocks):
        r = _make_region(mocks)
        r._focused = True
        with patch("qtdisplay.dock.region.QTabWidget.update") as mock_update:
            r.set_focused(True)
        mock_update.assert_not_called()

    def test_no_update_when_value_unchanged_false(self, mocks):
        r = _make_region(mocks)
        r._focused = False
        with patch("qtdisplay.dock.region.QTabWidget.update") as mock_update:
            r.set_focused(False)
        mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# _content_rect
# ---------------------------------------------------------------------------

class TestContentRect:
    """
    _content_rect adjusts the full widget rect based on tab bar position.
    We verify the correct adjusted() call is made for each TabPosition value.
    """

    def _rect_for_position(self, mocks, position):
        from qtdisplay.dock.region import DockRegion, QTabWidget

        bar = mocks.bar
        bar.height.return_value = 30
        bar.width.return_value = 80
        full_rect = MagicMock(name="rect")
        full_rect.adjusted = MagicMock(return_value=sentinel.adjusted_rect)

        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=bar), \
             patch("qtdisplay.dock.region.QTabWidget.rect", return_value=full_rect), \
             patch("qtdisplay.dock.region.QTabWidget.tabPosition",
                   return_value=position):
            result = r._content_rect()

        return full_rect, result

    def test_north_excludes_tab_bar_from_top(self, mocks):
        from qtdisplay.dock.region import QTabWidget
        full_rect, result = self._rect_for_position(
            mocks, QTabWidget.TabPosition.North
        )
        full_rect.adjusted.assert_called_once_with(0, 30, 0, 0)
        assert result is sentinel.adjusted_rect

    def test_south_excludes_tab_bar_from_bottom(self, mocks):
        from qtdisplay.dock.region import QTabWidget
        full_rect, result = self._rect_for_position(
            mocks, QTabWidget.TabPosition.South
        )
        full_rect.adjusted.assert_called_once_with(0, 0, 0, -30)

    def test_west_excludes_tab_bar_from_left(self, mocks):
        from qtdisplay.dock.region import QTabWidget
        full_rect, result = self._rect_for_position(
            mocks, QTabWidget.TabPosition.West
        )
        full_rect.adjusted.assert_called_once_with(80, 0, 0, 0)

    def test_east_excludes_tab_bar_from_right(self, mocks):
        from qtdisplay.dock.region import QTabWidget
        full_rect, result = self._rect_for_position(
            mocks, QTabWidget.TabPosition.East
        )
        full_rect.adjusted.assert_called_once_with(0, 0, -80, 0)

    def test_unknown_position_returns_full_rect(self, mocks):
        from qtdisplay.dock.region import DockRegion
        bar = mocks.bar
        full_rect = MagicMock(name="rect")

        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=bar), \
             patch("qtdisplay.dock.region.QTabWidget.rect", return_value=full_rect), \
             patch("qtdisplay.dock.region.QTabWidget.tabPosition",
                   return_value=sentinel.unknown_position):
            result = r._content_rect()

        full_rect.adjusted.assert_not_called()
        assert result is full_rect


# ---------------------------------------------------------------------------
# paintEvent
# ---------------------------------------------------------------------------

class TestPaintEvent:
    def test_skips_painting_when_not_focused(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("p", mocks.manager)
        r._focused = False
        ev = MagicMock()
        with patch("qtdisplay.dock.region.QPainter") as mock_painter, \
             patch("qtdisplay.dock.region.QTabWidget.paintEvent"):
            r.paintEvent(ev)
            mock_painter.assert_not_called()

    def test_paints_border_when_focused(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("p", mocks.manager)
        r._focused = True
        ev = MagicMock()
        with patch("qtdisplay.dock.region.QPainter") as mock_painter, \
             patch("qtdisplay.dock.region.QPen") as mock_pen, \
             patch("qtdisplay.dock.region.QTabWidget.paintEvent"), \
             patch.object(r, "_content_rect", return_value=MagicMock()):
            r.paintEvent(ev)
            mock_painter.assert_called_once_with(r)

    def test_painter_end_called_in_finally(self, mocks):
        """painter.end() must be called even if drawing raises."""
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("p", mocks.manager)
        r._focused = True
        ev = MagicMock()
        painter_inst = MagicMock()
        painter_inst.setPen.side_effect = RuntimeError("boom")
        with patch("qtdisplay.dock.region.QPainter", return_value=painter_inst), \
             patch("qtdisplay.dock.region.QTabWidget.paintEvent"), \
             patch.object(r, "_content_rect", return_value=MagicMock()):
            with pytest.raises(RuntimeError):
                r.paintEvent(ev)
            painter_inst.end.assert_called_once()

    def test_super_paint_event_always_called(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("p", mocks.manager)
        r._focused = False
        ev = MagicMock()
        with patch("qtdisplay.dock.region.QPainter"), \
             patch("qtdisplay.dock.region.QTabWidget.paintEvent") as super_pe:
            r.paintEvent(ev)
            super_pe.assert_called_once_with(ev)


# ---------------------------------------------------------------------------
# removeTab
# ---------------------------------------------------------------------------

class TestRemoveTab:
    def test_calls_super_remove_tab(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.removeTab") as super_rm, \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=1):
            r.removeTab(2)
            super_rm.assert_called_once_with(2)

    def test_emits_became_empty_when_count_reaches_zero(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=0):
            r.removeTab(0)
            r.became_empty.emit.assert_called_once()

    def test_does_not_emit_became_empty_when_tabs_remain(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=2):
            r.removeTab(0)
            r.became_empty.emit.assert_not_called()


# ---------------------------------------------------------------------------
# _close_tab
# ---------------------------------------------------------------------------

class TestCloseTab:
    def test_removes_tab_at_index(self, mocks):
        from qtdisplay.dock.region import DockRegion
        widget = MagicMock(name="tab_widget")
        r = DockRegion("r", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.widget", return_value=widget), \
             patch("qtdisplay.dock.region.QTabWidget.removeTab") as mock_rm, \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=1):
            r._close_tab(1)
            mock_rm.assert_called_once_with(1)

    def test_calls_delete_later_on_widget(self, mocks):
        from qtdisplay.dock.region import DockRegion
        widget = MagicMock(name="tab_widget")
        r = DockRegion("r", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.widget", return_value=widget), \
             patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=1):
            r._close_tab(0)
            widget.deleteLater.assert_called_once()

    def test_hides_non_center_region_when_empty(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("sidebar", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.widget", return_value=MagicMock()), \
             patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=0), \
             patch("qtdisplay.dock.region.QTabWidget.hide") as mock_hide:
            r._close_tab(0)
            mock_hide.assert_called_once()

    def test_does_not_hide_center_region_when_empty(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("center", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.widget", return_value=MagicMock()), \
             patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=0), \
             patch("qtdisplay.dock.region.QTabWidget.hide") as mock_hide:
            r._close_tab(0)
            mock_hide.assert_not_called()

    def test_does_not_hide_when_tabs_remain(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("sidebar", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.widget", return_value=MagicMock()), \
             patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=2), \
             patch("qtdisplay.dock.region.QTabWidget.hide") as mock_hide:
            r._close_tab(0)
            mock_hide.assert_not_called()

    def test_handles_none_widget_gracefully(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        r.became_empty = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.widget", return_value=None), \
             patch("qtdisplay.dock.region.QTabWidget.removeTab"), \
             patch("qtdisplay.dock.region.QTabWidget.count", return_value=1):
            r._close_tab(0)  # must not raise AttributeError on None.deleteLater


# ---------------------------------------------------------------------------
# close_closable_tabs
# ---------------------------------------------------------------------------

class TestCloseClosableTabs:
    def test_delegates_to_tab_bar_close_all(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=mocks.bar):
            r.close_closable_tabs()
        mocks.bar._close_all.assert_called_once_with(r)

    def test_skips_when_tab_bar_is_not_dock_tab_bar(self, mocks):
        from qtdisplay.dock.region import DockRegion
        plain_bar = MagicMock()  # __class__ is plain MagicMock, not FakeDockTabBar
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=plain_bar):
            r.close_closable_tabs()  # must not raise
        plain_bar._close_all.assert_not_called()
        mocks.bar._close_all.assert_not_called()


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_calls_bar_cleanup(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=mocks.bar):
            r.cleanup()
        mocks.bar.cleanup.assert_called_once()

    def test_poisons_manager_weakref(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=mocks.bar):
            r.cleanup()
        assert r.manager is None

    def test_skips_bar_cleanup_when_bar_is_not_dock_tab_bar(self, mocks):
        from qtdisplay.dock.region import DockRegion
        plain_bar = MagicMock()
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=plain_bar):
            r.cleanup()  # must not raise
        plain_bar.cleanup.assert_not_called()
        mocks.bar.cleanup.assert_not_called()

    def test_is_safe_to_call_twice(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=mocks.bar):
            r.cleanup()
            r.cleanup()  # must not raise
        assert r.manager is None

    def test_weakref_stays_poisoned_on_second_call(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        with patch("qtdisplay.dock.region.QTabWidget.tabBar", return_value=mocks.bar):
            r.cleanup()
            assert r.manager is None
            r.cleanup()
            assert r.manager is None


# ---------------------------------------------------------------------------
# add_panel
# ---------------------------------------------------------------------------

class TestAddPanel:
    def test_adds_tab_with_title(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        widget = MagicMock()
        icon = MagicMock()
        icon.isNull.return_value = True  # no icon path
        with patch("qtdisplay.dock.region.QTabWidget.addTab") as mock_add, \
             patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget"):
            r.add_panel(widget, "My Tab", icon)
            mock_add.assert_called_once_with(widget, "My Tab")

    def test_adds_tab_with_icon_when_non_null(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        widget = MagicMock()
        icon = MagicMock()
        icon.isNull.return_value = False
        with patch("qtdisplay.dock.region.QTabWidget.addTab") as mock_add, \
             patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget"):
            r.add_panel(widget, "Icon Tab", icon)
            mock_add.assert_called_once_with(widget, icon, "Icon Tab")

    def test_adds_tab_without_icon_when_none(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        widget = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.addTab") as mock_add, \
             patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget"):
            r.add_panel(widget, "No Icon", None)
            mock_add.assert_called_once_with(widget, "No Icon")

    def test_sets_current_widget_after_add(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        widget = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.addTab"), \
             patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget") as mock_cur:
            r.add_panel(widget, "T")
            mock_cur.assert_called_once_with(widget)

    def test_non_closable_sets_property(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        widget = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.addTab"), \
             patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget"):
            r.add_panel(widget, "T", closable=False)
        widget.setProperty.assert_called_once_with("_dock_closable", False)

    def test_closable_tab_does_not_set_property(self, mocks):
        from qtdisplay.dock.region import DockRegion
        r = DockRegion("r", mocks.manager)
        widget = MagicMock()
        with patch("qtdisplay.dock.region.QTabWidget.addTab"), \
             patch("qtdisplay.dock.region.QTabWidget.setCurrentWidget"):
            r.add_panel(widget, "T", closable=True)
        widget.setProperty.assert_not_called()


# ---------------------------------------------------------------------------
# _focus_color classmethod
# ---------------------------------------------------------------------------

class TestFocusColor:
    def test_returns_qcolor(self, mocks):
        from qtdisplay.dock.region import DockRegion
        DockRegion._FOCUS_COLOR = None  # reset cached value
        with patch("qtdisplay.dock.region.QColor") as mock_color:
            mock_color.return_value = sentinel.color
            result = DockRegion._focus_color()
            mock_color.assert_called_once_with(70, 110, 230)
            assert result is sentinel.color

    def test_caches_color_on_second_call(self, mocks):
        from qtdisplay.dock.region import DockRegion
        DockRegion._FOCUS_COLOR = None
        with patch("qtdisplay.dock.region.QColor") as mock_color:
            mock_color.return_value = sentinel.color
            DockRegion._focus_color()
            DockRegion._focus_color()
            mock_color.assert_called_once()  # only constructed once