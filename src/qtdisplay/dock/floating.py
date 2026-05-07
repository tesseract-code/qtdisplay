from __future__ import annotations

import weakref
from typing import TYPE_CHECKING

from PyQt6.QtCore import QTimer
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QMainWindow, QWidget

from qtdisplay.dock.region import DockRegion

if TYPE_CHECKING:
    from qtdisplay.dock.mngr import DockManager


class FloatingDock(QMainWindow):
    """
    Detached dock window created when a panel is floated.

    The window hosts a single DockRegion. It keeps only a weak reference to
    the manager so floating windows do not extend the manager lifetime.
    """

    def __init__(
        self,
        widget: QWidget,
        title: str,
        icon: QIcon | None,
        manager: DockManager,
    ) -> None:
        super().__init__()

        self._manager_ref = weakref.ref(manager)
        self._cleaned_up = False

        self.setWindowTitle(title)
        if icon and not icon.isNull():
            self.setWindowIcon(icon)

        region = DockRegion("floating", manager)
        region.add_panel(widget, title, icon)
        region.became_empty.connect(self._close_when_empty)

        self.setCentralWidget(region)

        w = max(widget.width(), 400)
        h = max(widget.height() + 32, 300)
        self.resize(w, h)

    @property
    def manager(self) -> DockManager | None:
        return self._manager_ref()

    @property
    def region(self) -> DockRegion | None:
        central = self.centralWidget()
        return central if isinstance(central, DockRegion) else None

    def _close_when_empty(self) -> None:
        """
        Close after the current signal stack unwinds.

        Using a named slot avoids a lambda that strongly captures ``self`` and
        makes cleanup/disconnect behavior easier to reason about.
        """
        QTimer.singleShot(0, self.close)

    def cleanup(self) -> None:
        """
        Tear down the floating window and the DockRegion it hosts.

        This method is idempotent.
        """
        if self._cleaned_up:
            return

        self._cleaned_up = True

        region = self.region
        if region is not None:
            try:
                region.became_empty.disconnect(self._close_when_empty)
            except (RuntimeError, TypeError):
                pass

            region.cleanup()

        self._manager_ref = lambda: None  # type: ignore[assignment]

    def closeEvent(self, event) -> None:
        """
        Close the floating window, respecting non-closable tabs.

        Closable tabs are closed through the normal region/tab-bar close path.
        If non-closable tabs remain, the close is ignored.
        """
        region = self.region
        if region is not None:
            region.close_closable_tabs()

            if region.count() > 0:
                event.ignore()
                return

        mgr = self.manager
        if mgr is not None:
            # Prefer a public manager method if available.
            unregister = getattr(mgr, "unregister_floating", None)
            if callable(unregister):
                unregister(self)
            else:
                mgr.unregister_floating(self)

        super().closeEvent(event)