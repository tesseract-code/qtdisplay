from __future__ import annotations

import weakref

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon, QSurfaceFormat
from PyQt6.QtWidgets import QMainWindow, QWidget

from image.gl.backend import GL
from image.gl.utils import get_surface_format
from qtdisplay.dock.region import DockRegion


class FloatingDock(QMainWindow):
    """
    A detached panel created when a tab is dropped outside any dock region.

    Holds a weak reference to the manager so it can notify it on close
    without creating a retain cycle.
    """

    def __init__(
            self,
            widget: QWidget,
            title: str,
            icon: QIcon | None,
            manager: 'DockManager',
    ) -> None:
        super().__init__()
        self.setWindowTitle(title)
        if icon and not icon.isNull():
            self.setWindowIcon(icon)

        self._manager_ref = weakref.ref(manager)

        region = DockRegion("floating", manager)
        region.add_panel(widget, title, icon)
        self.setCentralWidget(region)

        region.became_empty.connect(lambda: QTimer.singleShot(0, self.close))

        w = max(widget.width(), 400)
        h = max(widget.height() + 32, 300)
        self.resize(w, h)

    @property
    def manager(self) -> 'DockManager':
        return self._manager_ref()

    # ── teardown ──────────────────────────────────────────────────────────────

    def cleanup(self) -> None:
        """
        Tear down the floating window and the :class:`DockRegion` it hosts.

        Call order
        ----------
        1. Retrieve the central :class:`DockRegion` and call its
           :meth:`~DockRegion.cleanup` method, which cascades to the tab bar
           and every contained tab widget.
        2. Poison the manager weak reference so no code running after this
           point can accidentally re-enter the partially-destroyed manager.

        Intended to be called by the manager's own cleanup routine *before*
        ``close()`` / ``deleteLater()`` so all resources are released while
        every object is still fully alive.

        This method is idempotent — calling it more than once is safe.
        """
        region = self.centralWidget()
        if isinstance(region, DockRegion):
            region.cleanup()

        # Poison the weak ref — manager() will now return None.
        self._manager_ref = lambda: None  # type: ignore[assignment]

    def closeEvent(self, event) -> None:
        """
        Close the window, respecting non-closable tabs.

        Before allowing the window to close, every closable tab is closed
        through the normal ``_request_close`` path (so :class:`CleanupTab`
        widgets are torn down correctly).  If any non-closable tabs remain
        afterwards the close event is ignored and the window stays open —
        there is nowhere safe to send those widgets.

        When all tabs are closable (the common case) the region becomes empty,
        the manager is notified, and the window is destroyed as usual.
        """
        region = self.centralWidget()
        if isinstance(region, DockRegion):
            region.close_closable_tabs()
            if region.count() > 0:
                # Non-closable tabs are still present — block the close.
                event.ignore()
                return

        mgr = self.manager
        if mgr is not None:
            mgr._cleanup_floating(self)
        super().closeEvent(event)