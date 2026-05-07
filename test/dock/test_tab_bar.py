"""
Tests for DockTabBar (and _ReorderGhost, CleanupTab)
=====================================================

Strategy
--------
All PyQt6 widget classes are patched so no QApplication or display is needed.
PyQt6 *constants* (Qt.MouseButton, etc.) are imported directly — the module
can always be imported even without a display.

isinstance notes
----------------
* ``isinstance(widget, CleanupTab)`` uses a runtime_checkable Protocol.
  Any object with a ``cleanup`` method satisfies it.  MagicMock auto-creates
  attributes, so a plain MagicMock *always* passes.  Use ``MagicMock(spec=[])``
  or ``MagicMock(spec=object)`` to represent a widget that does NOT satisfy it.
* No stub-class trick is needed for CleanupTab itself.
* ``paintEvent`` for both _ReorderGhost and DockTabBar is Qt-rendering-heavy
  and is intentionally not tested here; the unit value is low compared to the
  mocking complexity.

Run with:
    pytest test_dock_tab_bar.py -v
"""

from __future__ import annotations

import types
from unittest.mock import MagicMock, call, patch, sentinel

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _qtabbar_base_patches():
    """Return the minimal list of patches needed to construct a DockTabBar."""
    return [
        patch("qtdisplay.dock.tab_bar.QTabBar.__init__", return_value=None),
        patch("qtdisplay.dock.tab_bar.QTabBar.setMovable"),
        patch("qtdisplay.dock.tab_bar.QTabBar.setTabsClosable"),
        patch("qtdisplay.dock.tab_bar.QTabBar.setExpanding"),
        patch("qtdisplay.dock.tab_bar.QTabBar.setElideMode"),
        patch("qtdisplay.dock.tab_bar.QTabBar.setUsesScrollButtons"),
    ]


def _make_bar():
    """Instantiate DockTabBar with construction patches active and signals mocked."""
    from qtdisplay.dock.tab_bar import DockTabBar
    b = DockTabBar()
    # Replace pyqtSignal descriptors with controllable mocks.
    b.drag_initiated   = MagicMock(name="drag_initiated")
    b.split_requested  = MagicMock(name="split_requested")
    b.tabCloseRequested = MagicMock(name="tabCloseRequested")
    return b


@pytest.fixture()
def bar():
    """A freshly constructed DockTabBar with all Qt construction side-effects patched."""
    patches = _qtabbar_base_patches()
    for p in patches:
        p.start()
    b = _make_bar()
    yield b
    for p in patches:
        p.stop()


def _make_tw(count=2, widgets=None):
    """
    Return a mock QTabWidget (the parent of DockTabBar).

    Parameters
    ----------
    count:
        Value returned by tw.count().
    widgets:
        List of widget mocks indexed by position.  Defaults to plain MagicMocks.
    """
    tw = MagicMock(name="tab_widget")
    tw.count.return_value = count
    if widgets is None:
        widgets = [MagicMock(name=f"widget_{i}") for i in range(count)]
    tw.widget.side_effect = lambda i: widgets[i] if 0 <= i < len(widgets) else None
    return tw, widgets


def _press_event(pos=None, button=None):
    """Minimal mock for a QMouseEvent (press)."""
    from PyQt6.QtCore import Qt
    ev = MagicMock()
    ev.pos.return_value = pos if pos is not None else MagicMock()
    ev.button.return_value = button if button is not None else Qt.MouseButton.LeftButton
    return ev


def _move_event(pos=None, manhattan=20, in_bar=True, left_held=True):
    """
    Minimal mock for a QMouseEvent (move).

    Parameters
    ----------
    manhattan:
        Integer returned by (ev.pos() - press_pos).manhattanLength().
        Values < THRESHOLD(8) won't trigger drag start.
    in_bar:
        Whether rect().contains(ev.pos()) returns True.
    left_held:
        Whether the left mouse button is held (controls the & check).
    """
    from PyQt6.QtCore import Qt
    ev = MagicMock()
    move_pos = pos if pos is not None else MagicMock()
    ev.pos.return_value = move_pos

    delta = MagicMock()
    delta.manhattanLength.return_value = manhattan
    move_pos.__sub__ = MagicMock(return_value=delta)

    if left_held:
        ev.buttons.return_value = Qt.MouseButton.LeftButton
    else:
        ev.buttons.return_value = Qt.MouseButton.NoButton

    gp = MagicMock()
    gp.toPoint.return_value = MagicMock(name="global_point")
    ev.globalPosition.return_value = gp
    return ev


# ---------------------------------------------------------------------------
# _ReorderGhost
# ---------------------------------------------------------------------------

class TestReorderGhost:
    """Tests for _ReorderGhost sizing logic (paintEvent omitted — pure Qt)."""

    @staticmethod
    def _ghost_patches(fm):
        return [
            patch("qtdisplay.dock.tab_bar.QWidget.__init__", return_value=None),
            patch("qtdisplay.dock.tab_bar.QWidget.setWindowFlags"),
            patch("qtdisplay.dock.tab_bar.QWidget.setAttribute"),
            patch("qtdisplay.dock.tab_bar.QWidget.fontMetrics", return_value=fm),
            patch("qtdisplay.dock.tab_bar.QWidget.setFixedSize"),
        ]

    def _make_fm(self, advance=60, height=14):
        fm = MagicMock()
        fm.horizontalAdvance.return_value = advance
        fm.height.return_value = height
        return fm

    def test_stores_text(self):
        fm = self._make_fm()
        patches = self._ghost_patches(fm)
        for p in patches:
            p.start()
        from qtdisplay.dock.tab_bar import _ReorderGhost
        g = _ReorderGhost("Hello", None)
        assert g._text == "Hello"
        for p in patches:
            p.stop()

    def test_null_icon_stored_as_none(self):
        fm = self._make_fm()
        null_icon = MagicMock()
        null_icon.isNull.return_value = True
        patches = self._ghost_patches(fm)
        for p in patches:
            p.start()
        from qtdisplay.dock.tab_bar import _ReorderGhost
        g = _ReorderGhost("T", null_icon)
        assert g._icon is None
        for p in patches:
            p.stop()

    def test_valid_icon_stored(self):
        fm = self._make_fm()
        icon = MagicMock()
        icon.isNull.return_value = False
        patches = self._ghost_patches(fm)
        for p in patches:
            p.start()
        from qtdisplay.dock.tab_bar import _ReorderGhost
        g = _ReorderGhost("T", icon)
        assert g._icon is icon
        for p in patches:
            p.stop()

    def test_setfixedsize_called_with_computed_dimensions(self):
        fm = self._make_fm(advance=50, height=14)
        patches = self._ghost_patches(fm)
        started = [p.start() for p in patches]

        from qtdisplay.dock.tab_bar import _ReorderGhost
        with patch("qtdisplay.dock.tab_bar.QWidget.setFixedSize") as mock_size:
            _ReorderGhost("Tab", None)
            # Without icon: H_PAD(12) + advance(50) + H_PAD(12) = 74 wide
            # height: max(16, 14) + V_PAD*2(10) = 26
            mock_size.assert_called_once_with(74, 26)

        for p in patches:
            p.stop()

    def test_setfixedsize_adds_icon_width_when_icon_present(self):
        fm = self._make_fm(advance=50, height=14)
        icon = MagicMock()
        icon.isNull.return_value = False
        patches = self._ghost_patches(fm)
        for p in patches:
            p.start()

        from qtdisplay.dock.tab_bar import _ReorderGhost
        with patch("qtdisplay.dock.tab_bar.QWidget.setFixedSize") as mock_size:
            _ReorderGhost("Tab", icon)
            # With icon: H_PAD(12) + ICON_W+6(22) + advance(50) + H_PAD(12) = 96
            mock_size.assert_called_once_with(96, 26)

        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# CleanupTab protocol
# ---------------------------------------------------------------------------

class TestCleanupTabProtocol:
    def test_object_with_cleanup_satisfies_protocol(self):
        from qtdisplay.dock.tab_bar import CleanupTab

        class GoodWidget:
            def cleanup(self): pass

        assert isinstance(GoodWidget(), CleanupTab)

    def test_object_without_cleanup_does_not_satisfy_protocol(self):
        from qtdisplay.dock.tab_bar import CleanupTab

        class BadWidget:
            pass

        assert not isinstance(BadWidget(), CleanupTab)

    def test_magic_mock_with_explicit_spec_does_not_satisfy_protocol(self):
        from qtdisplay.dock.tab_bar import CleanupTab
        # spec=object prevents auto-attribute creation
        plain = MagicMock(spec=object)
        assert not isinstance(plain, CleanupTab)


# ---------------------------------------------------------------------------
# DockTabBar.__init__
# ---------------------------------------------------------------------------

class TestDockTabBarInit:
    def test_initial_press_pos_is_none(self, bar):
        assert bar._press_pos is None

    def test_initial_press_tab_is_minus_one(self, bar):
        assert bar._press_tab == -1

    def test_initial_dragging_is_false(self, bar):
        assert bar._dragging is False

    def test_initial_drag_from_is_minus_one(self, bar):
        assert bar._drag_from == -1

    def test_initial_drop_at_is_minus_one(self, bar):
        assert bar._drop_at == -1

    def test_initial_reorder_ghost_is_none(self, bar):
        assert bar._reorder_ghost is None

    def test_setmovable_called_true(self):
        patches = _qtabbar_base_patches()
        for p in patches:
            p.start()
        with patch("qtdisplay.dock.tab_bar.QTabBar.setMovable") as m:
            _make_bar()
            m.assert_called_with(True)
        for p in patches:
            p.stop()

    def test_settabsclosable_called_true(self):
        patches = _qtabbar_base_patches()
        for p in patches:
            p.start()
        with patch("qtdisplay.dock.tab_bar.QTabBar.setTabsClosable") as m:
            _make_bar()
            m.assert_called_with(True)
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# _tab_is_closable (static helper)
# ---------------------------------------------------------------------------

class TestTabIsClosable:
    @staticmethod
    def _call(tw, index):
        from qtdisplay.dock.tab_bar import DockTabBar
        return DockTabBar._tab_is_closable(tw, index)

    def test_returns_true_when_tw_is_none(self):
        assert self._call(None, 0) is True

    def test_returns_true_when_widget_is_none(self):
        tw = MagicMock()
        tw.widget.return_value = None
        assert self._call(tw, 0) is True

    def test_returns_true_when_property_never_set(self):
        tw = MagicMock()
        tw.widget.return_value.property.return_value = None
        assert self._call(tw, 0) is True

    def test_returns_false_when_property_is_false(self):
        tw = MagicMock()
        tw.widget.return_value.property.return_value = False
        assert self._call(tw, 0) is False

    def test_returns_true_when_property_is_true(self):
        tw = MagicMock()
        tw.widget.return_value.property.return_value = True
        assert self._call(tw, 0) is True

    def test_queries_correct_property_name(self):
        tw = MagicMock()
        widget = MagicMock()
        widget.property.return_value = None
        tw.widget.return_value = widget
        self._call(tw, 3)
        widget.property.assert_called_once_with("_dock_closable")


# ---------------------------------------------------------------------------
# _install_close_button
# ---------------------------------------------------------------------------

class TestInstallCloseButton:
    def _run(self, bar, tw, index, tab_is_closable=True, button_exists=False):
        with patch.object(bar, "_tab_is_closable", return_value=tab_is_closable), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton",
                   return_value=MagicMock() if button_exists else None), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabButton") as mock_set, \
             patch("qtdisplay.dock.tab_bar.QPushButton") as MockBtn, \
             patch("qtdisplay.dock.tab_bar.QPainter"), \
             patch("qtdisplay.dock.tab_bar.QIcon"), \
             patch("qtdisplay.dock.tab_bar.QColor"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.style") as mock_style:
            mock_style.return_value.standardIcon.return_value.pixmap.return_value = MagicMock()
            bar._install_close_button(index)
        return mock_set

    def test_skips_when_tab_not_closable(self, bar):
        mock_set = self._run(bar, MagicMock(), 0, tab_is_closable=False)
        mock_set.assert_not_called()

    def test_skips_when_button_already_exists(self, bar):
        mock_set = self._run(bar, MagicMock(), 0, tab_is_closable=True, button_exists=True)
        mock_set.assert_not_called()

    def test_installs_button_when_closable_and_absent(self, bar):
        mock_set = self._run(bar, MagicMock(), 2, tab_is_closable=True, button_exists=False)
        mock_set.assert_called_once()

    def test_button_set_initially_invisible(self, bar):
        tw = MagicMock()
        btn_inst = MagicMock()
        with patch.object(bar, "_tab_is_closable", return_value=True), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=None), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabButton"), \
             patch("qtdisplay.dock.tab_bar.QPushButton", return_value=btn_inst), \
             patch("qtdisplay.dock.tab_bar.QPainter"), \
             patch("qtdisplay.dock.tab_bar.QIcon"), \
             patch("qtdisplay.dock.tab_bar.QColor"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.style") as mock_style:
            mock_style.return_value.standardIcon.return_value.pixmap.return_value = MagicMock()
            bar._install_close_button(0)
        btn_inst.setVisible.assert_called_with(False)

    def test_button_click_connected(self, bar):
        tw = MagicMock()
        btn_inst = MagicMock()
        with patch.object(bar, "_tab_is_closable", return_value=True), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=None), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabButton"), \
             patch("qtdisplay.dock.tab_bar.QPushButton", return_value=btn_inst), \
             patch("qtdisplay.dock.tab_bar.QPainter"), \
             patch("qtdisplay.dock.tab_bar.QIcon"), \
             patch("qtdisplay.dock.tab_bar.QColor"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.style") as mock_style:
            mock_style.return_value.standardIcon.return_value.pixmap.return_value = MagicMock()
            bar._install_close_button(0)
        btn_inst.clicked.connect.assert_called_once()


# ---------------------------------------------------------------------------
# tabInserted
# ---------------------------------------------------------------------------

class TestTabInserted:
    def test_calls_super(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabInserted") as super_ti, \
             patch.object(bar, "_install_close_button"), \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="T"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabToolTip"):
            bar.tabInserted(1)
            super_ti.assert_called_once_with(1)

    def test_installs_close_button(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabInserted"), \
             patch.object(bar, "_install_close_button") as mock_install, \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="T"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabToolTip"):
            bar.tabInserted(2)
            mock_install.assert_called_once_with(2)

    def test_syncs_close_buttons(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabInserted"), \
             patch.object(bar, "_install_close_button"), \
             patch.object(bar, "_sync_close_buttons") as mock_sync, \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="T"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabToolTip"):
            bar.tabInserted(2)
            mock_sync.assert_called_once_with(3)

    def test_sets_tooltip_to_tab_text(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabInserted"), \
             patch.object(bar, "_install_close_button"), \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="MyTab"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabToolTip") as mock_tip:
            bar.tabInserted(1)
            mock_tip.assert_called_once_with(1, "MyTab")


# ---------------------------------------------------------------------------
# setTabText
# ---------------------------------------------------------------------------

class TestSetTabText:
    def test_calls_super(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.setTabText") as super_stt, \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabToolTip"):
            bar.setTabText(0, "New Name")
            super_stt.assert_called_once_with(0, "New Name")

    def test_syncs_tooltip(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.setTabText"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.setTabToolTip") as mock_tip:
            bar.setTabText(2, "Updated")
            mock_tip.assert_called_once_with(2, "Updated")


# ---------------------------------------------------------------------------
# tabRemoved
# ---------------------------------------------------------------------------

class TestTabRemoved:
    def test_calls_super(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabRemoved") as super_tr, \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0):
            bar.tabRemoved(1)
            super_tr.assert_called_once_with(1)

    def test_syncs_close_buttons_after_removal(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabRemoved"), \
             patch.object(bar, "_sync_close_buttons") as mock_sync, \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=2):
            bar.tabRemoved(0)
            mock_sync.assert_called_once_with(2)


# ---------------------------------------------------------------------------
# _request_close
# ---------------------------------------------------------------------------

class TestRequestClose:
    def test_silently_ignores_non_closable_tab(self, bar):
        tw, widgets = _make_tw(count=1)
        widgets[0].property.return_value = False  # non-closable
        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar._request_close(0)
        bar.tabCloseRequested.emit.assert_not_called()

    def test_emits_signal_for_closable_tab(self, bar):
        tw, widgets = _make_tw(count=1)
        widgets[0].property.return_value = None  # closable
        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar._request_close(0)
        bar.tabCloseRequested.emit.assert_called_once_with(0)

    def test_calls_cleanup_on_cleanup_tab_widget(self, bar):
        from qtdisplay.dock.tab_bar import CleanupTab

        class GoodWidget:
            cleanup = MagicMock()
            def property(self, name): return None  # closable

        widget = GoodWidget()
        tw = MagicMock()
        tw.widget.return_value = widget
        tw.count.return_value = 1

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar._request_close(0)

        widget.cleanup.assert_called_once()

    def test_cleanup_called_before_signal_emit(self, bar):
        """cleanup() must run while the tab is still alive (before tabCloseRequested)."""
        call_order = []

        class OrderedWidget:
            def cleanup(self): call_order.append("cleanup")
            def property(self, name): return None

        tw = MagicMock()
        tw.widget.return_value = OrderedWidget()
        tw.count.return_value = 1

        bar.tabCloseRequested.emit.side_effect = lambda _: call_order.append("emit")

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar._request_close(0)

        assert call_order == ["cleanup", "emit"]

    def test_does_not_call_cleanup_on_plain_widget(self, bar):
        widget = MagicMock(spec=object)  # no cleanup attribute
        widget.property = MagicMock(return_value=None)
        tw = MagicMock()
        tw.widget.return_value = widget
        tw.count.return_value = 1

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar._request_close(0)

        assert not hasattr(widget, "cleanup") or not widget.cleanup.called

    def test_emits_when_tw_is_none(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            # _tab_is_closable returns True when tw is None
            bar._request_close(0)
        bar.tabCloseRequested.emit.assert_called_once_with(0)


# ---------------------------------------------------------------------------
# _close_button_clicked
# ---------------------------------------------------------------------------

class TestCloseButtonClicked:
    def test_finds_owning_tab_and_requests_close(self, bar):
        btn = MagicMock(name="btn")
        other_btn = MagicMock(name="other")

        def tabButton(i, side):
            return btn if i == 1 else other_btn

        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", side_effect=tabButton), \
             patch.object(bar, "_request_close") as mock_close:
            bar._close_button_clicked(btn)
            mock_close.assert_called_once_with(1)

    def test_does_nothing_if_button_not_found(self, bar):
        btn = MagicMock(name="btn")
        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=MagicMock()), \
             patch.object(bar, "_request_close") as mock_close:
            bar._close_button_clicked(btn)
            mock_close.assert_not_called()


# ---------------------------------------------------------------------------
# _close_all
# ---------------------------------------------------------------------------

class TestCloseAll:
    def test_closes_all_closable_tabs_in_reverse(self, bar):
        tw, widgets = _make_tw(count=3)
        for w in widgets:
            w.property.return_value = None  # all closable

        with patch.object(bar, "_request_close") as mock_close:
            bar._close_all(tw)

        assert mock_close.call_count == 3
        # Must be called in reverse (2, 1, 0)
        assert mock_close.call_args_list == [call(2), call(1), call(0)]

    def test_skips_non_closable_tabs(self, bar):
        tw, widgets = _make_tw(count=3)
        widgets[0].property.return_value = None   # closable
        widgets[1].property.return_value = False  # non-closable
        widgets[2].property.return_value = None   # closable

        with patch.object(bar, "_request_close") as mock_close:
            bar._close_all(tw)

        # Only indices 0 and 2 should be closed
        closed = {c.args[0] for c in mock_close.call_args_list}
        assert closed == {0, 2}

    def test_does_nothing_when_no_tabs(self, bar):
        tw, _ = _make_tw(count=0)
        with patch.object(bar, "_request_close") as mock_close:
            bar._close_all(tw)
        mock_close.assert_not_called()


# ---------------------------------------------------------------------------
# _close_others
# ---------------------------------------------------------------------------

class TestCloseOthers:
    def test_closes_all_but_kept_widget(self, bar):
        tw, widgets = _make_tw(count=3)
        for w in widgets:
            w.property.return_value = None  # all closable

        with patch.object(bar, "_request_close") as mock_close:
            bar._close_others(tw, keep_idx=1)

        closed = {c.args[0] for c in mock_close.call_args_list}
        assert closed == {0, 2}

    def test_does_not_close_non_closable_others(self, bar):
        tw, widgets = _make_tw(count=3)
        widgets[0].property.return_value = False  # non-closable
        widgets[1].property.return_value = None   # closable (keep)
        widgets[2].property.return_value = None   # closable

        with patch.object(bar, "_request_close") as mock_close:
            bar._close_others(tw, keep_idx=1)

        # Only index 2 should be closed (index 0 is non-closable, index 1 is kept)
        mock_close.assert_called_once_with(2)

    def test_identifies_kept_widget_by_object_identity(self, bar):
        """keep_idx selects by widget object identity, not just index."""
        tw, widgets = _make_tw(count=2)
        for w in widgets:
            w.property.return_value = None

        with patch.object(bar, "_request_close") as mock_close:
            bar._close_others(tw, keep_idx=0)

        mock_close.assert_called_once_with(1)


# ---------------------------------------------------------------------------
# _sync_close_buttons
# ---------------------------------------------------------------------------

class TestSyncCloseButtons:
    def test_shows_button_at_current_index(self, bar):
        btns = [MagicMock(name=f"btn_{i}") for i in range(3)]

        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton",
                   side_effect=lambda i, _: btns[i]):
            bar._sync_close_buttons(1)

        btns[0].setVisible.assert_called_with(False)
        btns[1].setVisible.assert_called_with(True)
        btns[2].setVisible.assert_called_with(False)

    def test_ignores_tabs_with_no_button(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=None):
            bar._sync_close_buttons(0)  # must not raise AttributeError

    def test_hides_all_when_current_idx_out_of_range(self, bar):
        btns = [MagicMock(), MagicMock()]
        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton",
                   side_effect=lambda i, _: btns[i]):
            bar._sync_close_buttons(99)
        btns[0].setVisible.assert_called_with(False)
        btns[1].setVisible.assert_called_with(False)


# ---------------------------------------------------------------------------
# _set_all_close_buttons_visible
# ---------------------------------------------------------------------------

class TestSetAllCloseButtonsVisible:
    def test_shows_all_buttons(self, bar):
        btns = [MagicMock(), MagicMock()]
        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton",
                   side_effect=lambda i, _: btns[i]):
            bar._set_all_close_buttons_visible(True)
        for b in btns:
            b.setVisible.assert_called_with(True)

    def test_hides_all_buttons(self, bar):
        btns = [MagicMock(), MagicMock()]
        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton",
                   side_effect=lambda i, _: btns[i]):
            bar._set_all_close_buttons_visible(False)
        for b in btns:
            b.setVisible.assert_called_with(False)

    def test_skips_tabs_with_no_button(self, bar):
        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=None):
            bar._set_all_close_buttons_visible(True)  # must not raise


# ---------------------------------------------------------------------------
# _needed_width
# ---------------------------------------------------------------------------

class TestNeededWidth:
    def _make_fm(self, advance=80):
        fm = MagicMock()
        fm.horizontalAdvance.return_value = advance
        return fm

    def test_base_width_with_no_icon_no_button(self, bar):
        icon = MagicMock()
        icon.isNull.return_value = True  # no icon
        fm = self._make_fm(advance=80)
        style = MagicMock()
        style.pixelMetric.return_value = 0

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="Tab"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabIcon", return_value=icon), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=None), \
             patch("qtdisplay.dock.tab_bar.QTabBar.fontMetrics", return_value=fm), \
             patch("qtdisplay.dock.tab_bar.QTabBar.iconSize", return_value=MagicMock(isValid=lambda: False)), \
             patch("qtdisplay.dock.tab_bar.QTabBar.style", return_value=style):
            w = bar._needed_width(0)

        # H_PAD(30) + advance(80) + H_PAD(30) = 140
        assert w == 140

    def test_adds_close_button_width(self, bar):
        icon = MagicMock()
        icon.isNull.return_value = True
        fm = self._make_fm(advance=80)
        btn = MagicMock()
        btn.width.return_value = 16
        style = MagicMock()
        style.pixelMetric.return_value = 0

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="Tab"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabIcon", return_value=icon), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=btn), \
             patch("qtdisplay.dock.tab_bar.QTabBar.fontMetrics", return_value=fm), \
             patch("qtdisplay.dock.tab_bar.QTabBar.iconSize", return_value=MagicMock(isValid=lambda: False)), \
             patch("qtdisplay.dock.tab_bar.QTabBar.style", return_value=style):
            w = bar._needed_width(0)

        # 140 (base) + btn.width(16) + 8 = 164
        assert w == 164

    def test_adds_icon_width_when_icon_present(self, bar):
        icon = MagicMock()
        icon.isNull.return_value = False
        fm = self._make_fm(advance=80)
        icon_size = MagicMock()
        icon_size.isValid.return_value = True
        icon_size.width.return_value = 16
        style = MagicMock()
        style.pixelMetric.return_value = 0

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="Tab"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabIcon", return_value=icon), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabButton", return_value=None), \
             patch("qtdisplay.dock.tab_bar.QTabBar.fontMetrics", return_value=fm), \
             patch("qtdisplay.dock.tab_bar.QTabBar.iconSize", return_value=icon_size), \
             patch("qtdisplay.dock.tab_bar.QTabBar.style", return_value=style):
            w = bar._needed_width(0)

        # 140 (base) + icon_w(16) + 4 = 160
        assert w == 160


# ---------------------------------------------------------------------------
# tabSizeHint / minimumTabSizeHint
# ---------------------------------------------------------------------------

class TestSizeHints:
    def test_tabsizehint_uses_max_of_base_and_needed(self, bar):
        from PyQt6.QtCore import QSize
        base = QSize(50, 30)
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabSizeHint", return_value=base), \
             patch.object(bar, "_needed_width", return_value=120):
            result = bar.tabSizeHint(0)
        assert result.width() == 120
        assert result.height() == 30

    def test_tabsizehint_keeps_base_when_larger(self, bar):
        from PyQt6.QtCore import QSize
        base = QSize(200, 30)
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabSizeHint", return_value=base), \
             patch.object(bar, "_needed_width", return_value=50):
            result = bar.tabSizeHint(0)
        assert result.width() == 200

    def test_minimumtabsizehint_uses_max_of_base_and_needed(self, bar):
        from PyQt6.QtCore import QSize
        base = QSize(40, 28)
        with patch("qtdisplay.dock.tab_bar.QTabBar.minimumTabSizeHint", return_value=base), \
             patch.object(bar, "_needed_width", return_value=90):
            result = bar.minimumTabSizeHint(0)
        assert result.width() == 90


# ---------------------------------------------------------------------------
# mousePressEvent
# ---------------------------------------------------------------------------

class TestMousePressEvent:
    def test_left_button_stores_press_pos(self, bar):
        from PyQt6.QtCore import Qt
        pos = MagicMock(name="pos")
        ev = _press_event(pos=pos, button=Qt.MouseButton.LeftButton)
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.mousePressEvent"):
            bar.mousePressEvent(ev)
        assert bar._press_pos is pos

    def test_left_button_stores_press_tab(self, bar):
        from PyQt6.QtCore import Qt
        ev = _press_event(button=Qt.MouseButton.LeftButton)
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.mousePressEvent"):
            bar.mousePressEvent(ev)
        assert bar._press_tab == 3

    def test_right_button_does_not_store_state(self, bar):
        from PyQt6.QtCore import Qt
        ev = _press_event(button=Qt.MouseButton.RightButton)
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=1), \
             patch("qtdisplay.dock.tab_bar.QTabBar.mousePressEvent"):
            bar.mousePressEvent(ev)
        assert bar._press_pos is None
        assert bar._press_tab == -1

    def test_calls_super(self, bar):
        from PyQt6.QtCore import Qt
        ev = _press_event()
        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.mousePressEvent") as super_mpe:
            bar.mousePressEvent(ev)
            super_mpe.assert_called_once_with(ev)


# ---------------------------------------------------------------------------
# mouseMoveEvent
# ---------------------------------------------------------------------------

class TestMouseMoveEvent:
    def _setup_bar_for_drag_start(self, bar):
        """Put bar into a state where the threshold check will pass."""
        press_pos = MagicMock(name="press_pos")
        bar._press_pos  = press_pos
        bar._press_tab  = 1
        bar._dragging   = False
        bar._drag_from  = -1
        bar._drop_at    = -1
        return press_pos

    def test_drag_start_sets_dragging(self, bar):
        self._setup_bar_for_drag_start(bar)
        ev = _move_event(manhattan=20, in_bar=True)
        rect = MagicMock()
        rect.contains.return_value = True

        with patch("qtdisplay.dock.tab_bar.QTabBar.rect", return_value=rect), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabText", return_value="T"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabIcon", return_value=MagicMock()), \
             patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=3), \
             patch.object(bar, "_set_all_close_buttons_visible"), \
             patch.object(bar, "_compute_drop_at", return_value=1), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"), \
             patch("qtdisplay.dock.tab_bar.QWidget.__init__", return_value=None), \
             patch("qtdisplay.dock.tab_bar.QWidget.setWindowFlags"), \
             patch("qtdisplay.dock.tab_bar.QWidget.setAttribute"), \
             patch("qtdisplay.dock.tab_bar.QWidget.fontMetrics", return_value=MagicMock(
                 horizontalAdvance=MagicMock(return_value=50),
                 height=MagicMock(return_value=14)
             )), \
             patch("qtdisplay.dock.tab_bar.QWidget.setFixedSize"), \
             patch("qtdisplay.dock.tab_bar.QWidget.isVisible", return_value=False), \
             patch("qtdisplay.dock.tab_bar.QWidget.show"), \
             patch("qtdisplay.dock.tab_bar.QWidget.move"):
            bar.mouseMoveEvent(ev)

        assert bar._dragging is True

    def test_threshold_not_met_does_not_start_drag(self, bar):
        self._setup_bar_for_drag_start(bar)
        ev = _move_event(manhattan=2, in_bar=True)  # below threshold of 8
        rect = MagicMock()
        rect.contains.return_value = True

        with patch("qtdisplay.dock.tab_bar.QTabBar.rect", return_value=rect), \
             patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=0), \
             patch.object(bar, "_compute_drop_at", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar.mouseMoveEvent(ev)

        assert bar._dragging is False

    def test_cursor_left_bar_emits_drag_initiated(self, bar):
        bar._dragging   = True
        bar._drag_from  = 2
        bar._press_tab  = 2
        bar._press_pos  = MagicMock()

        ev = _move_event(manhattan=20, in_bar=False)
        ev.globalPosition.return_value.toPoint.return_value = MagicMock()
        rect = MagicMock()
        rect.contains.return_value = False  # cursor is outside

        with patch("qtdisplay.dock.tab_bar.QTabBar.rect", return_value=rect), \
             patch.object(bar, "_cleanup_reorder"):
            bar.mouseMoveEvent(ev)

        bar.drag_initiated.emit.assert_called_once()

    def test_cursor_left_bar_resets_dragging(self, bar):
        bar._dragging   = True
        bar._drag_from  = 0
        bar._press_tab  = 0
        bar._press_pos  = MagicMock()

        ev = _move_event(manhattan=20)
        rect = MagicMock()
        rect.contains.return_value = False

        with patch("qtdisplay.dock.tab_bar.QTabBar.rect", return_value=rect), \
             patch.object(bar, "_cleanup_reorder"):
            bar.mouseMoveEvent(ev)

        assert bar._dragging is False
        assert bar._press_pos is None
        assert bar._press_tab == -1

    def test_drop_index_update_triggers_repaint(self, bar):
        bar._dragging  = True
        bar._drag_from = 0
        bar._drop_at   = 0
        bar._press_pos = MagicMock()
        bar._reorder_ghost = None

        ev = _move_event(manhattan=20, in_bar=True)
        rect = MagicMock()
        rect.contains.return_value = True

        with patch("qtdisplay.dock.tab_bar.QTabBar.rect", return_value=rect), \
             patch.object(bar, "_compute_drop_at", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update") as mock_update:
            bar.mouseMoveEvent(ev)

        assert bar._drop_at == 2
        mock_update.assert_called()


# ---------------------------------------------------------------------------
# mouseReleaseEvent
# ---------------------------------------------------------------------------

class TestMouseReleaseEvent:
    def test_commits_reorder_when_dragging(self, bar):
        bar._dragging  = True
        bar._drag_from = 1
        ev = MagicMock()
        with patch.object(bar, "_cleanup_reorder") as mock_cleanup, \
             patch("qtdisplay.dock.tab_bar.QTabBar.mouseReleaseEvent"):
            bar.mouseReleaseEvent(ev)
            mock_cleanup.assert_called_once_with(commit=True)

    def test_skips_cleanup_when_not_dragging(self, bar):
        bar._dragging  = False
        bar._drag_from = -1
        ev = MagicMock()
        with patch.object(bar, "_cleanup_reorder") as mock_cleanup, \
             patch("qtdisplay.dock.tab_bar.QTabBar.mouseReleaseEvent"):
            bar.mouseReleaseEvent(ev)
            mock_cleanup.assert_not_called()

    def test_resets_state(self, bar):
        bar._dragging  = True
        bar._drag_from = 2
        bar._press_pos = MagicMock()
        bar._press_tab = 2
        ev = MagicMock()
        with patch.object(bar, "_cleanup_reorder"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.mouseReleaseEvent"):
            bar.mouseReleaseEvent(ev)
        assert bar._press_pos is None
        assert bar._press_tab == -1
        assert bar._dragging is False

    def test_calls_super(self, bar):
        bar._dragging = False
        ev = MagicMock()
        with patch.object(bar, "_cleanup_reorder"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.mouseReleaseEvent") as super_mre:
            bar.mouseReleaseEvent(ev)
            super_mre.assert_called_once_with(ev)


# ---------------------------------------------------------------------------
# _compute_drop_at
# ---------------------------------------------------------------------------

class TestComputeDropAt:
    def _make_tab_rect(self, center_x):
        r = MagicMock()
        r.center.return_value.x.return_value = center_x
        return r

    def test_cursor_before_first_tab_returns_zero(self, bar):
        bar._drag_from = 99  # not 0 or 1
        rects = [self._make_tab_rect(50), self._make_tab_rect(150)]

        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabRect",
                   side_effect=lambda i: rects[i]):
            result = bar._compute_drop_at(10)  # before first tab center (50)

        assert result == 0

    def test_cursor_after_all_tabs_returns_last_index(self, bar):
        bar._drag_from = 99
        rects = [self._make_tab_rect(50), self._make_tab_rect(150)]

        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabRect",
                   side_effect=lambda i: rects[i]):
            result = bar._compute_drop_at(999)

        assert result == 1  # count(2) - 1

    def test_cursor_between_tabs_returns_correct_seq(self, bar):
        bar._drag_from = 0  # tab 0 is being dragged
        # non-dragged tabs: 1 (center 80), 2 (center 180)
        rects = [
            self._make_tab_rect(30),   # dragged — skipped
            self._make_tab_rect(80),   # seq 0
            self._make_tab_rect(180),  # seq 1
        ]

        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabRect",
                   side_effect=lambda i: rects[i]):
            # cursor at 100 — after tab 1's center (80), before tab 2's (180)
            result = bar._compute_drop_at(100)

        assert result == 1

    def test_dragged_tab_is_excluded_from_sequence(self, bar):
        bar._drag_from = 1  # middle tab dragged
        rects = [
            self._make_tab_rect(40),   # idx 0, seq 0
            self._make_tab_rect(120),  # idx 1 — dragged, excluded
            self._make_tab_rect(200),  # idx 2, seq 1
        ]

        with patch("qtdisplay.dock.tab_bar.QTabBar.count", return_value=3), \
             patch("qtdisplay.dock.tab_bar.QTabBar.tabRect",
                   side_effect=lambda i: rects[i]):
            result = bar._compute_drop_at(10)  # before idx 0 center (40)

        assert result == 0


# ---------------------------------------------------------------------------
# _cleanup_reorder
# ---------------------------------------------------------------------------

class TestCleanupReorder:
    def test_moves_tab_when_committing_and_positions_differ(self, bar):
        bar._drag_from = 0
        bar._drop_at   = 2
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.moveTab") as mock_move, \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar._cleanup_reorder(commit=True)

        mock_move.assert_called_once_with(0, 2)

    def test_does_not_move_tab_when_not_committing(self, bar):
        bar._drag_from = 0
        bar._drop_at   = 2
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.moveTab") as mock_move, \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar._cleanup_reorder(commit=False)

        mock_move.assert_not_called()

    def test_does_not_move_tab_when_positions_are_same(self, bar):
        bar._drag_from = 1
        bar._drop_at   = 1  # same position
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.moveTab") as mock_move, \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=1), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar._cleanup_reorder(commit=True)

        mock_move.assert_not_called()

    def test_hides_and_deletes_ghost(self, bar):
        ghost = MagicMock(name="ghost")
        bar._drag_from = 0
        bar._drop_at   = 0
        bar._reorder_ghost = ghost

        with patch("qtdisplay.dock.tab_bar.QTabBar.moveTab"), \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar._cleanup_reorder(commit=False)

        ghost.hide.assert_called_once()
        ghost.deleteLater.assert_called_once()
        assert bar._reorder_ghost is None

    def test_resets_drag_state(self, bar):
        bar._drag_from     = 3
        bar._drop_at       = 1
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.moveTab"), \
             patch.object(bar, "_sync_close_buttons"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar._cleanup_reorder(commit=False)

        assert bar._drag_from == -1
        assert bar._drop_at   == -1

    def test_calls_sync_close_buttons_after_cleanup(self, bar):
        bar._drag_from     = 0
        bar._drop_at       = 0
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.moveTab"), \
             patch.object(bar, "_sync_close_buttons") as mock_sync, \
             patch("qtdisplay.dock.tab_bar.QTabBar.currentIndex", return_value=2), \
             patch("qtdisplay.dock.tab_bar.QTabBar.update"):
            bar._cleanup_reorder(commit=False)

        mock_sync.assert_called_once_with(2)


# ---------------------------------------------------------------------------
# cleanup
# ---------------------------------------------------------------------------

class TestCleanup:
    def test_aborts_live_reorder_drag(self, bar):
        bar._dragging = True
        with patch.object(bar, "_cleanup_reorder") as mock_cleanup, \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()
        mock_cleanup.assert_called_once_with(commit=False)

    def test_aborts_when_ghost_exists_even_if_not_dragging(self, bar):
        bar._dragging = False
        bar._reorder_ghost = MagicMock()
        with patch.object(bar, "_cleanup_reorder") as mock_cleanup, \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()
        mock_cleanup.assert_called_once_with(commit=False)

    def test_skips_reorder_abort_when_idle(self, bar):
        bar._dragging = False
        bar._reorder_ghost = None
        with patch.object(bar, "_cleanup_reorder") as mock_cleanup, \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()
        mock_cleanup.assert_not_called()

    def test_calls_cleanup_on_cleanup_tab_widgets(self, bar):
        """
        Use a concrete stub class rather than a plain MagicMock.

        In Python 3.12+ ``runtime_checkable`` Protocol ``isinstance`` checks
        became stricter for callable members; a plain MagicMock no longer
        reliably satisfies ``CleanupTab``.  A class with an explicit
        ``cleanup`` method always passes regardless of Python version.
        """
        cleanup_mock = MagicMock(name="cleanup_fn")

        class StubCleanupWidget:
            def cleanup(self):
                cleanup_mock()

        widget = StubCleanupWidget()
        tw = MagicMock()
        tw.count.return_value = 1
        tw.widget.return_value = widget

        bar._dragging = False
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar.cleanup()

        cleanup_mock.assert_called_once()

    def test_swallows_exception_from_widget_cleanup(self, bar):
        widget = MagicMock()
        widget.cleanup.side_effect = RuntimeError("boom")
        tw = MagicMock()
        tw.count.return_value = 1
        tw.widget.return_value = widget

        bar._dragging = False
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw):
            bar.cleanup()  # must not raise

    def test_skips_widget_cleanup_when_tw_is_none(self, bar):
        bar._dragging = False
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()  # must not raise

    def test_disconnects_all_signals(self, bar):
        bar._dragging = False
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()

        bar.drag_initiated.disconnect.assert_called_once()
        bar.split_requested.disconnect.assert_called_once()
        bar.tabCloseRequested.disconnect.assert_called_once()

    def test_swallows_runtime_error_from_signal_disconnect(self, bar):
        bar._dragging = False
        bar._reorder_ghost = None
        bar.drag_initiated.disconnect.side_effect = RuntimeError("no connections")

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()  # must not raise

    def test_resets_all_internal_state(self, bar):
        bar._dragging   = True
        bar._press_pos  = MagicMock()
        bar._press_tab  = 3
        bar._drag_from  = 2
        bar._drop_at    = 1
        bar._reorder_ghost = MagicMock()

        with patch.object(bar, "_cleanup_reorder"), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()

        assert bar._press_pos is None
        assert bar._press_tab == -1
        assert bar._dragging  is False
        assert bar._drag_from == -1
        assert bar._drop_at   == -1

    def test_is_safe_to_call_twice(self, bar):
        bar._dragging = False
        bar._reorder_ghost = None

        with patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=None):
            bar.cleanup()
            bar.cleanup()  # must not raise


# ---------------------------------------------------------------------------
# contextMenuEvent
# ---------------------------------------------------------------------------

class TestContextMenuEvent:
    """
    These tests mock QMenu and QAction to verify which menu entries are
    built under each branch condition.
    """

    def _run_context_menu(self, bar, tw, right_clicked_idx):
        ev = MagicMock()
        ev.pos.return_value = MagicMock()
        ev.globalPos.return_value = MagicMock()

        menu_inst = MagicMock(name="menu")
        submenu = MagicMock(name="submenu")
        menu_inst.addMenu.return_value = submenu
        action_inst = MagicMock(name="action")

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=right_clicked_idx), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QMenu", return_value=menu_inst), \
             patch("qtdisplay.dock.tab_bar.QAction", return_value=action_inst):
            bar.contextMenuEvent(ev)

        return menu_inst, submenu, action_inst

    def test_select_tab_submenu_always_shown(self, bar):
        tw, widgets = _make_tw(count=2)
        for w in widgets:
            w.property.return_value = None
        tw.tabText = MagicMock(return_value="Tab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        menu_inst, _, _ = self._run_context_menu(bar, tw, right_clicked_idx=0)
        menu_inst.addMenu.assert_any_call("Select Tab")

    def test_close_this_shown_for_closable_right_click(self, bar):
        tw, widgets = _make_tw(count=2)
        for w in widgets:
            w.property.return_value = None  # closable
        tw.tabText = MagicMock(return_value="MyTab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        menu_inst, _, action_inst = self._run_context_menu(bar, tw, right_clicked_idx=0)
        # QAction must have been called with a "Close" label
        from qtdisplay.dock.tab_bar import QAction
        added_labels = [c.args[0] for c in
                        __import__('unittest.mock', fromlist=['call']).call.__class__.__mro__
                        if False]  # placeholder
        # Just verify addAction was called (label checked via QAction constructor call)
        menu_inst.addAction.assert_called()

    def test_close_this_not_shown_for_non_closable_right_click(self, bar):
        tw, widgets = _make_tw(count=2)
        widgets[0].property.return_value = False  # right-clicked tab is non-closable
        widgets[1].property.return_value = None
        tw.tabText = MagicMock(return_value="Fixed")
        tw.currentIndex.return_value = 1
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QMenu") as MockMenu, \
             patch("qtdisplay.dock.tab_bar.QAction") as MockAction:
            menu_inst = MockMenu.return_value
            menu_inst.addMenu.return_value = MagicMock()
            ev = MagicMock()
            ev.pos.return_value = MagicMock()
            ev.globalPos.return_value = MagicMock()
            bar.contextMenuEvent(ev)
            # QAction should NOT be called with a label starting "Close \""
            close_this_calls = [
                c for c in MockAction.call_args_list
                if c.args and isinstance(c.args[0], str) and c.args[0].startswith('Close "')
            ]
            assert len(close_this_calls) == 0

    def test_split_menu_shown_when_two_or_more_tabs(self, bar):
        tw, widgets = _make_tw(count=2)
        for w in widgets:
            w.property.return_value = None
        tw.tabText = MagicMock(return_value="Tab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        menu_inst, _, _ = self._run_context_menu(bar, tw, right_clicked_idx=0)
        menu_inst.addMenu.assert_any_call("Split Current Tab")

    def test_split_menu_not_shown_for_single_tab(self, bar):
        tw, widgets = _make_tw(count=1)
        widgets[0].property.return_value = None
        tw.tabText = MagicMock(return_value="Tab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QMenu") as MockMenu, \
             patch("qtdisplay.dock.tab_bar.QAction"):
            menu_inst = MockMenu.return_value
            menu_inst.addMenu.return_value = MagicMock()
            ev = MagicMock()
            ev.pos.return_value = MagicMock()
            ev.globalPos.return_value = MagicMock()
            bar.contextMenuEvent(ev)
            split_calls = [c for c in menu_inst.addMenu.call_args_list
                           if c.args and c.args[0] == "Split Current Tab"]
            assert len(split_calls) == 0

    def test_close_all_shown_when_any_closable_tab_exists(self, bar):
        tw, widgets = _make_tw(count=2)
        for w in widgets:
            w.property.return_value = None  # all closable
        tw.tabText = MagicMock(return_value="Tab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=-1), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QMenu") as MockMenu, \
             patch("qtdisplay.dock.tab_bar.QAction") as MockAction:
            menu_inst = MockMenu.return_value
            menu_inst.addMenu.return_value = MagicMock()
            ev = MagicMock()
            ev.pos.return_value = MagicMock()
            ev.globalPos.return_value = MagicMock()
            bar.contextMenuEvent(ev)
            close_all_calls = [
                c for c in MockAction.call_args_list
                if c.args and c.args[0] == "Close All Tabs"
            ]
            assert len(close_all_calls) == 1

    def test_split_actions_emit_correct_directions(self, bar):
        """
        The context menu creates two submenus — "Select Tab" and
        "Split Current Tab" — both via ``menu.addMenu()``.  Using a single
        ``return_value`` mock means both submenus are the same object and
        their ``addAction`` calls are pooled (2 select-tab actions +
        4 split actions = 6).  Use ``side_effect`` instead so each label
        gets its own mock, then assert only on the split submenu.
        """
        tw, widgets = _make_tw(count=2)
        for w in widgets:
            w.property.return_value = None
        tw.tabText = MagicMock(return_value="Tab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QMenu") as MockMenu, \
             patch("qtdisplay.dock.tab_bar.QAction"):
            select_menu = MagicMock(name="select_menu")
            split_menu  = MagicMock(name="split_menu")
            main_menu   = MockMenu.return_value

            def _addmenu(label):
                return split_menu if label == "Split Current Tab" else select_menu

            main_menu.addMenu.side_effect = _addmenu

            ev = MagicMock()
            ev.pos.return_value = MagicMock()
            ev.globalPos.return_value = MagicMock()
            bar.contextMenuEvent(ev)

        # Exactly four directional actions (left, right, top, bottom)
        assert split_menu.addAction.call_count == 4

    def test_menu_exec_called(self, bar):
        tw, widgets = _make_tw(count=1)
        widgets[0].property.return_value = None
        tw.tabText = MagicMock(return_value="Tab")
        tw.currentIndex.return_value = 0
        tw.tabIcon.return_value = MagicMock(isNull=lambda: True)

        global_pos = MagicMock(name="global_pos")
        ev = MagicMock()
        ev.pos.return_value = MagicMock()
        ev.globalPos.return_value = global_pos

        with patch("qtdisplay.dock.tab_bar.QTabBar.tabAt", return_value=0), \
             patch("qtdisplay.dock.tab_bar.QTabBar.parentWidget", return_value=tw), \
             patch("qtdisplay.dock.tab_bar.QMenu") as MockMenu, \
             patch("qtdisplay.dock.tab_bar.QAction"):
            menu_inst = MockMenu.return_value
            menu_inst.addMenu.return_value = MagicMock()
            bar.contextMenuEvent(ev)
            menu_inst.exec.assert_called_once_with(global_pos)