import os
import time
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QUrl, Qt, QTimer
from PyQt6.QtGui import QIcon, QPixmap, QSurfaceFormat, QDesktopServices
from PyQt6.QtWidgets import (
    QFrame, QStatusBar, QHBoxLayout, QMessageBox
)

from image.gl.utils import get_surface_format
from image.gl_imshow import GLImageShow
from image.load.factory import Backend
from image.load.load import load_image
from pycore.files import FileExtensionCategory, FileInfo
from qtdisplay.dock.mngr import DockManager
from qtgui.file.code.editor import CodeEditorWidget, CodeEditor, \
    _LANG_TO_SYMBOLS
from qtgui.pdf_viewer import PDFViewer
from qtgui.terminal.widget import TerminalWidget
from qtgui.file.watch.widget import DirectoryWidget
from qtgui.pixmap import colorize_pixmap
from qtgui.video.playback import VideoPlaybackWidget
from qtgui.vtk_utils.viewer3D import ModelViewerWidget

try:
    from qtgui.file.code.symbols import SymbolsWidget
    _SYMBOLS_AVAILABLE = True
except ImportError:
    _SYMBOLS_AVAILABLE = False


class WorkspaceManager(QFrame):
    """
    A self-contained workspace widget that composes a DockManager with:
      - a code editor panel (center)
      - an OpenGL image viewer panel (center)
      - a directory browser panel (left)
      - a symbols panel (left, below Project) — requires symbols_view.py
      - an embedded terminal panel (bottom)
      - a status bar with usage hints
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dock_manager: DockManager | None = None
        self._dir_mngr: DirectoryWidget | None = None
        self._terminal_widget: TerminalWidget | None = None
        self._sym_widget: "SymbolsWidget | None" = None

        # Cache of colorized icons, keyed by SVG path, to avoid redundant
        # pixmap colorization on every file-open event.
        self._icon_cache: dict[str, QIcon] = {}

        # Tracks open file paths to prevent duplicate tabs.
        self._open_paths: set[Path] = set()

        # Debounce timer: symbols refresh 500 ms after the last keystroke
        # so we don't re-parse on every character typed in the editor.
        self._sym_timer = QTimer(self)
        self._sym_timer.setSingleShot(True)
        self._sym_timer.setInterval(500)
        self._sym_timer.timeout.connect(self._refresh_symbols)

        self._setup_ui()
        self._setup_panels()
        self._setup_status_bar()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _icon(self, svg_path: str) -> QIcon:
        if svg_path not in self._icon_cache:
            self._icon_cache[svg_path] = QIcon(
                colorize_pixmap(QPixmap(svg_path), self.palette().highlightedText().color())
            )
        return self._icon_cache[svg_path]

    def _warn(self, title: str, exc: Exception) -> None:
        """Show a critical message box for a caught exception."""
        QMessageBox.critical(self, title, str(exc))

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _setup_ui(self):
        layout = QHBoxLayout(self)
        layout.setSpacing(0)
        layout.setContentsMargins(0, 0, 0, 0)

        self._dir_mngr = DirectoryWidget(start_dir=os.getcwd())
        self._code_editor = CodeEditorWidget(show_symbols=False)
        self._terminal_widget = TerminalWidget()

        if _SYMBOLS_AVAILABLE:
            self._sym_widget = SymbolsWidget()

        # _dock_manager takes ownership of all panels added via add_panel();
        # _dir_mngr and _terminal_widget are not added to the layout directly.
        self._dock_manager = DockManager(title="Workspace Manager")
        layout.addWidget(self._dock_manager)

    def _setup_panels(self):
        # ── Center: Code Editor ───────────────────────────────────────
        self._dock_manager.add_panel(
            "center",
            self._code_editor,
            "Code",
            self._icon("line-icons:file-code.svg"),
            closable=False
        )

        # ── Left: Directory Browser ───────────────────────────────────
        self._dock_manager.add_panel(
            "left",
            self._dir_mngr,
            "Project",
            self._icon("line-icons:folder-5.svg"),
            closable=False
        )

        self._dir_mngr.file_opened.connect(self._on_file_opened)

        # ── Left: Symbols (below Project, only when available) ────────
        if self._sym_widget is not None:
            self._dock_manager.add_panel(
                "right",
                self._sym_widget,
                "Symbols",
                self._icon("other-icons:list-check-3.svg"),
                closable=False,
            )
            self._sym_widget.symbol_activated.connect(
                self._on_symbol_activated
            )
            # Refresh whenever the user switches tabs in the code editor
            self._code_editor._tabs.currentChanged.connect(
                self._on_editor_tab_changed
            )

        # ── Bottom: Terminal ──────────────────────────────────────────
        self._dock_manager.add_panel(
            "bottom",
            self._terminal_widget,
            "Terminal",
            self._icon("line-icons:code-box.svg"),
            closable=False
        )

    def _setup_status_bar(self):
        sb = QStatusBar()
        sb.showMessage(
            "Drag a tab onto the light blue region to dock  ·  "
            "Drag outside the center region to float  ·  "
            "Blue border = focused region"
        )
        self._dock_manager.setStatusBar(sb)

    # ------------------------------------------------------------------
    # Symbols panel
    # ------------------------------------------------------------------

    def _refresh_symbols(self) -> None:
        """Re-parse the active editor's source and push it to the symbols panel."""
        if self._sym_widget is None:
            return

        ed = self._code_editor._tabs.currentWidget()
        if not isinstance(ed, CodeEditor):
            self._sym_widget.clear()
            return

        lang_tag = _LANG_TO_SYMBOLS.get(ed.language.name)
        if lang_tag is None:
            # Plain Text or an unsupported language
            self._sym_widget.clear()
            return

        self._sym_widget.load_source(ed.toPlainText(), lang_tag)

    def _on_editor_tab_changed(self, _idx: int) -> None:
        """Refresh symbols immediately when the active editor tab changes."""
        self._sym_timer.stop()
        self._refresh_symbols()

    def _on_symbol_activated(self, line: int, col: int) -> None:
        """Navigate the active editor to the line/column the symbol reports."""
        ed = self._code_editor._tabs.currentWidget()
        if isinstance(ed, CodeEditor) and hasattr(ed, "goto_line_col"):
            ed.goto_line_col(line, col)
        elif isinstance(ed, CodeEditor):
            ed.goto_line(line)

    def _connect_editor_content_signals(self, ed: CodeEditor) -> None:
        """
        Connect *ed*'s content-change signal to the debounce timer.

        Called each time a new tab is created via _open_in_editor so that
        edits made in that tab also trigger a symbols refresh.
        """
        ed.document().contentsChanged.connect(self._sym_timer.start)

    # ------------------------------------------------------------------
    # File dispatch
    # ------------------------------------------------------------------

    def _on_file_opened(self, info: FileInfo) -> None:

        match info.category:
            case FileExtensionCategory.CODE:
                self._open_in_editor(info.path)
            case FileExtensionCategory.IMAGE:
                try:
                    buf, meta = load_image(info.path, backend=Backend.PILLOW)
                    imshow_view = GLImageShow()
                    # flipud compensates for OpenGL's bottom-left origin
                    # convention so the image renders the right way up.
                    imshow_view.set_data(np.flipud(buf.data))
                except Exception as exc:
                    self._open_paths.discard(info.path)
                    self._warn("Image Load Error", exc)
                    return

                self._dock_manager.add_panel(
                    "center",
                    imshow_view,
                    f"{info.path.stem}",
                    self._icon("line-icons:image.svg"),
                )

            case FileExtensionCategory.VIDEO:
                try:
                    playback = VideoPlaybackWidget(video_path=info.path)
                except Exception as exc:
                    self._open_paths.discard(info.path)
                    self._warn("Video Load Error", exc)
                    return

                self._dock_manager.add_panel(
                    "center",
                    playback,
                    f"{info.path.stem}",
                    self._icon("line-icons:film.svg"),
                )

                # Defer start() until the panel is fully shown so the
                # renderer has a valid surface to draw into.
                QTimer.singleShot(0, playback.start)

            case FileExtensionCategory.PDF:
                try:
                    viewer = PDFViewer()
                    viewer._load(str(info.path))
                except Exception as exc:
                    self._open_paths.discard(info.path)
                    self._warn("PDF Load Error", exc)
                    return

                self._dock_manager.add_panel(
                    "center",
                    viewer,
                    f"{info.path.stem}",
                    self._icon("line-icons:file.svg"),
                )

            case FileExtensionCategory.MODEL_3D:
                try:
                    viewer = ModelViewerWidget()
                    viewer.load_model(str(info.path))
                except Exception as exc:
                    self._open_paths.discard(info.path)
                    self._warn("Model Load Error", exc)
                    return

                self._dock_manager.add_panel(
                    "center",
                    viewer,
                    f"{info.path.stem}",
                    self._icon("line-icons:box-3-line.svg"),
                )

            case _:
                # Fall back to the OS default for anything unrecognised.
                self._open_paths.discard(info.path)
                QDesktopServices.openUrl(QUrl.fromLocalFile(str(info.path)))

    def _open_in_editor(self, path: Path) -> None:
        """Open *path* in the code editor, switching to it if already loaded."""
        path_str = str(path)

        # Re-use an existing tab if the file is already open
        tabs = self._code_editor._tabs
        for i in range(tabs.count()):
            ed = tabs.widget(i)
            if getattr(ed, "filepath", None) == path_str:
                tabs.setCurrentIndex(i)
                # currentChanged fires → _on_editor_tab_changed → refresh
                self._dock_manager.focus_panel(self._code_editor)
                return

        # Open a fresh tab, wire its content changes, then refresh symbols
        ed = self._code_editor.add_new_tab(path_str)
        self._connect_editor_content_signals(ed)
        self._dock_manager.focus_panel(self._code_editor)
        # Refresh immediately — don't wait for the debounce timer
        self._sym_timer.stop()
        self._refresh_symbols()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def dock_manager(self) -> DockManager | None:
        """Return the underlying DockManager for advanced customization."""
        return self._dock_manager

    def cleanup(self):
        self._sym_timer.stop()

        if self._dock_manager is not None:
            self._dir_mngr.file_opened.disconnect(self._on_file_opened)
            if self._sym_widget is not None:
                self._code_editor._tabs.currentChanged.disconnect(
                    self._on_editor_tab_changed
                )
            self._dock_manager.cleanup()
            self._dock_manager = None

        if self._terminal_widget is not None:
            self._terminal_widget.close()
            self._terminal_widget = None

        if self._dir_mngr is not None:
            self._dir_mngr.close()
            self._dir_mngr = None

        self._sym_widget = None
        self._open_paths.clear()
        self._icon_cache.clear()

    def closeEvent(self, event):
        # Accept the event first so Qt begins tearing down child widgets in an
        # orderly fashion, then release our own references.
        event.accept()
        super().closeEvent(event)
        self.cleanup()


# ----------------------------------------------------------------------
# Stand-alone entry point (mirrors the original __main__ block)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    import sys
    from qtcore.app import Application

    app = Application(argv=sys.argv)
    app.show_splash(min_display_ms=500)
    app.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
    QSurfaceFormat.setDefaultFormat(get_surface_format())
    workspace = WorkspaceManager()
    workspace.showMaximized()

    app.finish_splash(main_window=workspace.window())

    app.aboutToQuit.connect(workspace.close)

    sys.exit(app.exec())