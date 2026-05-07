"""
Tests for FloatingDock
======================

Strategy
--------
PyQt6 requires a running QApplication and (usually) a display.  Rather than
spinning up a headless X server in CI we mock every Qt and project symbol at
the module level, letting us exercise all Python-level logic in plain pytest
without any GUI infrastructure.

Run with:
    pytest test_floating_dock.py -v
"""

from __future__ import annotations

import types
import weakref
from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest


# ---------------------------------------------------------------------------
# Helpers – build a minimal fake PyQt6 surface so the import works
# ---------------------------------------------------------------------------

def _make_qt_mocks():
    """
    Build the region mock and a *real* stub class for DockRegion.
    """
    region = MagicMock(name="region_instance")
    region.count.return_value = 0  # default: no tabs remaining after close

    # A real class so isinstance() accepts it as arg 2.
    class FakeDockRegion:
        def __new__(cls, *args, **kwargs):   # noqa: ARG003
            return region                    # constructor always returns our mock

    # Make isinstance(region, FakeDockRegion) → True
    region.__class__ = FakeDockRegion

    return region, FakeDockRegion


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def mocks():
    """
    Patch every external symbol that FloatingDock touches and yield a
    namespace object with convenient attributes.
    """
    region_inst, DockRegion_cls = _make_qt_mocks()

    # A minimal widget that reports a size
    widget = MagicMock(name="widget")
    widget.width.return_value = 300
    widget.height.return_value = 200

    manager = MagicMock(name="manager")

    # Non-null icon by default
    icon = MagicMock(name="icon")
    icon.isNull.return_value = False

    ns = types.SimpleNamespace(
        widget=widget,
        manager=manager,
        icon=icon,
        region=region_inst,
        DockRegion_cls=DockRegion_cls,
    )

    patches = [
        patch("qtdisplay.dock.floating.QMainWindow.__init__", return_value=None),
        patch("qtdisplay.dock.floating.QMainWindow.setWindowTitle"),
        patch("qtdisplay.dock.floating.QMainWindow.setWindowIcon"),
        patch("qtdisplay.dock.floating.QMainWindow.setCentralWidget"),
        patch("qtdisplay.dock.floating.QMainWindow.centralWidget",
              return_value=region_inst),
        patch("qtdisplay.dock.floating.QMainWindow.resize"),
        patch("qtdisplay.dock.floating.QMainWindow.close"),
        patch("qtdisplay.dock.floating.QMainWindow.closeEvent"),
        patch("qtdisplay.dock.floating.QTimer"),
        patch("qtdisplay.dock.floating.DockRegion", DockRegion_cls),
    ]

    started = [p.start() for p in patches]
    ns._patches = patches
    ns._started = started

    yield ns

    for p in patches:
        p.stop()


def _make_dock(mocks, title="Test", icon=None, use_default_icon=True):
    """Instantiate FloatingDock with mocked dependencies."""
    # Import *after* patches are active
    from qtdisplay.dock.floating import FloatingDock

    chosen_icon = mocks.icon if use_default_icon else icon
    return FloatingDock(mocks.widget, title, chosen_icon, mocks.manager)


# ---------------------------------------------------------------------------
# __init__
# ---------------------------------------------------------------------------

class TestInit:
    def test_window_title_is_set(self, mocks):
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.setWindowTitle") as mock_title:
            FloatingDock(mocks.widget, "My Panel", mocks.icon, mocks.manager)
            mock_title.assert_called_once_with("My Panel")

    def test_non_null_icon_is_applied(self, mocks):
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.setWindowIcon") as mock_icon:
            FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            mock_icon.assert_called_once_with(mocks.icon)

    def test_null_icon_is_not_applied(self, mocks):
        from qtdisplay.dock.floating import FloatingDock
        null_icon = MagicMock()
        null_icon.isNull.return_value = True
        with patch("qtdisplay.dock.floating.QMainWindow.setWindowIcon") as mock_icon:
            FloatingDock(mocks.widget, "T", null_icon, mocks.manager)
            mock_icon.assert_not_called()

    def test_none_icon_is_not_applied(self, mocks):
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.setWindowIcon") as mock_icon:
            FloatingDock(mocks.widget, "T", None, mocks.manager)
            mock_icon.assert_not_called()

    def test_dock_region_created_with_correct_args(self, mocks):
        """
        FakeDockRegion.__new__ always returns our mock, so we can't use
        call-count tracking on the class itself.  Instead verify indirectly:
        add_panel is called on the region mock, which only happens after
        the region is successfully constructed and returned.
        """
        _make_dock(mocks, title="Foo")
        # Construction succeeded → add_panel was called on the region
        assert mocks.region.add_panel.called

    def test_panel_added_to_region(self, mocks):
        _make_dock(mocks, title="Foo")
        mocks.region.add_panel.assert_called_once_with(
            mocks.widget, "Foo", mocks.icon
        )

    def test_region_set_as_central_widget(self, mocks):
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.setCentralWidget") as mock_cw:
            FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            mock_cw.assert_called_once_with(mocks.region)

    def test_became_empty_signal_connected(self, mocks):
        _make_dock(mocks)
        mocks.region.became_empty.connect.assert_called_once()

    def test_resize_uses_widget_dimensions_when_large_enough(self, mocks):
        mocks.widget.width.return_value = 800
        mocks.widget.height.return_value = 600
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.resize") as mock_resize:
            FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            mock_resize.assert_called_once_with(800, 632)  # 600 + 32

    def test_resize_uses_minimum_width_when_widget_too_narrow(self, mocks):
        mocks.widget.width.return_value = 100   # below 400 minimum
        mocks.widget.height.return_value = 600
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.resize") as mock_resize:
            FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            mock_resize.assert_called_once_with(400, 632)

    def test_resize_uses_minimum_height_when_widget_too_short(self, mocks):
        mocks.widget.width.return_value = 500
        mocks.widget.height.return_value = 50   # 50+32=82, below 300 minimum
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QMainWindow.resize") as mock_resize:
            FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            mock_resize.assert_called_once_with(500, 300)

    def test_manager_stored_as_weak_ref(self, mocks):
        dock = _make_dock(mocks)
        # The ref resolves back to the original manager object
        assert dock._manager_ref() is mocks.manager

    def test_became_empty_lambda_schedules_close(self, mocks):
        """
        When became_empty fires, a QTimer.singleShot(0, self.close) must be
        scheduled.
        """
        from qtdisplay.dock.floating import FloatingDock
        with patch("qtdisplay.dock.floating.QTimer") as mock_timer:
            with patch("qtdisplay.dock.floating.QMainWindow.close") as mock_close:
                dock = FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
                # Extract the lambda that was connected
                connect_call = mocks.region.became_empty.connect.call_args
                callback = connect_call[0][0]
                callback()  # simulate signal emission
                mock_timer.singleShot.assert_called_once_with(0, dock.close)


# ---------------------------------------------------------------------------
# manager property
# ---------------------------------------------------------------------------

class TestManagerProperty:
    def test_returns_manager_while_alive(self, mocks):
        dock = _make_dock(mocks)
        assert dock.manager is mocks.manager

    def test_returns_none_after_weakref_cleared(self, mocks):
        dock = _make_dock(mocks)
        # Simulate the manager being garbage-collected
        dock._manager_ref = weakref.ref(MagicMock())
        # Point ref to an object that immediately goes out of scope
        dock._manager_ref = lambda: None  # type: ignore
        assert dock.manager is None

    def test_returns_none_after_cleanup_poisons_ref(self, mocks):
        dock = _make_dock(mocks)
        dock.cleanup()
        assert dock.manager is None


# ---------------------------------------------------------------------------
# cleanup()
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_calls_region_cleanup(self, mocks):
        dock = _make_dock(mocks)
        dock.cleanup()
        mocks.region.cleanup.assert_called_once()

    def test_poisons_manager_weak_ref(self, mocks):
        dock = _make_dock(mocks)
        dock.cleanup()
        assert dock._manager_ref() is None

    def test_skips_region_cleanup_when_central_widget_is_not_dock_region(self, mocks):
        """
        When centralWidget() returns something that is NOT a DockRegion (i.e.
        not an instance of FakeDockRegion / the patched class), cleanup() must
        skip the region teardown without raising.
        """
        from qtdisplay.dock.floating import FloatingDock
        # A plain MagicMock whose __class__ is NOT FakeDockRegion
        plain_widget = MagicMock()
        with patch("qtdisplay.dock.floating.QMainWindow.centralWidget",
                   return_value=plain_widget):
            dock = FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            dock.cleanup()  # must not raise
            mocks.region.cleanup.assert_not_called()

    def test_idempotent_does_not_raise_on_second_call(self, mocks):
        """
        cleanup() has no internal 'already ran' guard, so region.cleanup()
        is called each time centralWidget() still returns a DockRegion.
        The important contract is that the second call does not raise and
        that the weakref remains poisoned.
        """
        dock = _make_dock(mocks)
        dock.cleanup()
        dock.cleanup()  # must not raise
        assert mocks.region.cleanup.call_count == 2  # called once per invocation
        assert dock.manager is None                  # ref still poisoned

    def test_idempotent_weakref_stays_poisoned_on_second_call(self, mocks):
        """
        After the first cleanup() the weakref is poisoned (returns None).
        A second call must leave it poisoned and not raise, regardless of
        how many times region.cleanup() ends up being invoked.
        """
        dock = _make_dock(mocks)
        dock.cleanup()
        assert dock.manager is None
        dock.cleanup()            # second call — must not raise
        assert dock.manager is None


# ---------------------------------------------------------------------------
# closeEvent()
# ---------------------------------------------------------------------------

class TestCloseEvent:
    def _make_event(self):
        event = MagicMock(name="close_event")
        return event

    def test_calls_close_closable_tabs(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 0
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent"):
            dock.closeEvent(event)

        mocks.region.close_closable_tabs.assert_called_once()

    def test_allows_close_when_no_tabs_remain(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 0
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent") as super_close:
            dock.closeEvent(event)

        event.ignore.assert_not_called()
        super_close.assert_called_once_with(event)

    def test_blocks_close_when_non_closable_tabs_remain(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 2  # tabs still present
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent") as super_close:
            dock.closeEvent(event)

        event.ignore.assert_called_once()
        super_close.assert_not_called()

    def test_notifies_manager_on_successful_close(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 0
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent"):
            dock.closeEvent(event)

        mocks.manager.unregister_floating.assert_called_once_with(dock)

    def test_does_not_notify_manager_when_close_blocked(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 1
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent"):
            dock.closeEvent(event)

        mocks.manager.unregister_floating.assert_not_called()

    def test_skips_manager_notification_when_manager_is_gone(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 0
        dock._manager_ref = lambda: None  # type: ignore  # simulate collected ref
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent"):
            dock.closeEvent(event)  # must not raise AttributeError

        mocks.manager.unregister_floating.assert_not_called()

    def test_calls_super_close_event_on_success(self, mocks):
        dock = _make_dock(mocks)
        mocks.region.count.return_value = 0
        event = self._make_event()

        with patch("qtdisplay.dock.floating.QMainWindow.closeEvent") as super_close:
            dock.closeEvent(event)
            super_close.assert_called_once_with(event)

    def test_handles_non_dock_region_central_widget_gracefully(self, mocks):
        """
        When centralWidget() returns a non-DockRegion object, closeEvent()
        must skip close_closable_tabs() and still notify the manager.
        """
        from qtdisplay.dock.floating import FloatingDock
        plain = MagicMock()  # __class__ is plain MagicMock, not FakeDockRegion
        with patch("qtdisplay.dock.floating.QMainWindow.centralWidget",
                   return_value=plain):
            dock = FloatingDock(mocks.widget, "T", mocks.icon, mocks.manager)
            event = self._make_event()
            with patch("qtdisplay.dock.floating.QMainWindow.closeEvent"):
                dock.closeEvent(event)  # must not raise
            mocks.region.close_closable_tabs.assert_not_called()
            mocks.manager.unregister_floating.assert_called_once_with(dock)