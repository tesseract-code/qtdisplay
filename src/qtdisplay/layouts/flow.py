import sys
from PyQt6.QtCore import Qt, QMargins, QPoint, QRect, QSize
from PyQt6.QtWidgets import (
    QApplication,
    QLayout,
    QPushButton,
    QLabel,
    QLineEdit,
    QCheckBox,
    QRadioButton,
    QComboBox,
    QSizePolicy,
    QWidget
)


class FlowLayout(QLayout):
    """
    Flow Layout that wraps widget based on available width.
    Based on Qt's official example, adapted for PyQt6.
    """

    def __init__(self, parent=None, margin=0, spacing=-1):
        super().__init__(parent)

        if parent is not None:
            self.setContentsMargins(margin, margin, margin, margin)

        self._item_list = []
        self._spacing = spacing

    def addItem(self, item):
        """Add item to layout (required by QLayout)"""
        self._item_list.append(item)

    def count(self):
        """Return number of items"""
        return len(self._item_list)

    def itemAt(self, index):
        """Get item at index"""
        if 0 <= index < len(self._item_list):
            return self._item_list[index]
        return None

    def takeAt(self, index):
        """Remove and return item at index"""
        if 0 <= index < len(self._item_list):
            return self._item_list.pop(index)
        return None

    def expandingDirections(self):
        """Layout doesn't expand"""
        return Qt.Orientation(0)

    def hasHeightForWidth(self):
        """Height depends on width"""
        return True

    def heightForWidth(self, width):
        """Calculate required height for given width"""
        height = self._do_layout(QRect(0, 0, width, 0), True)
        return height

    def setGeometry(self, rect):
        """Position all items within the given rectangle"""
        super().setGeometry(rect)
        self._do_layout(rect, False)

    def sizeHint(self):
        """Preferred size"""
        return self.minimumSize()

    def minimumSize(self):
        """Minimum size needed"""
        size = QSize()

        for item in self._item_list:
            size = size.expandedTo(item.minimumSize())

        margins = self.contentsMargins()
        size += QSize(margins.left() + margins.right(),
                      margins.top() + margins.bottom())
        return size

    def _do_layout(self, rect, test_only):
        """
        Perform layout calculation.

        Args:
            rect: Rectangle to layout within
            test_only: If True, only calculate height

        Returns:
            Height needed for layout
        """
        margins = self.contentsMargins()
        effective_rect = rect.adjusted(
            margins.left(),
            margins.top(),
            -margins.right(),
            -margins.bottom()
        )

        x = effective_rect.x()
        y = effective_rect.y()
        line_height = 0

        spacing = self.spacing()

        for item in self._item_list:
            widget = item.widget()
            if widget is None:
                continue

            # Get the widget's control type from its size policy
            size_policy = widget.sizePolicy()
            control_type = size_policy.controlType()

            # Get spacing from widget style
            style = widget.style()

            # Use the widget's actual control type for spacing
            space_x = spacing + style.layoutSpacing(
                control_type,
                control_type,
                Qt.Orientation.Horizontal
            )
            space_y = spacing + style.layoutSpacing(
                control_type,
                control_type,
                Qt.Orientation.Vertical
            )

            next_x = x + item.sizeHint().width() + space_x

            # Wrap to next line if needed
            if next_x - space_x > effective_rect.right() and line_height > 0:
                x = effective_rect.x()
                y = y + line_height + space_y
                next_x = x + item.sizeHint().width() + space_x
                line_height = 0

            if not test_only:
                item.setGeometry(QRect(QPoint(x, y), item.sizeHint()))

            x = next_x
            line_height = max(line_height, item.sizeHint().height())

        return y + line_height - rect.y()


class FlowLayoutWindow(QWidget):
    """Demo window with flow layout showing various widget types"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PyQt6 Flow Layout - Multiple Widget Types")

        # Create flow layout
        flow_layout = FlowLayout(self, margin=10, spacing=10)

        # Add various widget types
        flow_layout.addWidget(QPushButton("Button 1"))
        flow_layout.addWidget(QLabel("Label Text"))
        flow_layout.addWidget(QLineEdit("Text Input"))
        flow_layout.addWidget(QPushButton("Another Button"))
        flow_layout.addWidget(QCheckBox("Check me"))
        flow_layout.addWidget(QRadioButton("Radio option"))
        flow_layout.addWidget(QComboBox())
        flow_layout.addWidget(QPushButton("Long Button Text Here"))
        flow_layout.addWidget(QLabel("Another Label"))
        flow_layout.addWidget(QLineEdit("More input"))
        flow_layout.addWidget(QPushButton("Short"))
        flow_layout.addWidget(QCheckBox("Another checkbox"))
        flow_layout.addWidget(QPushButton("Final Button"))

        # Configure combo box
        combo = flow_layout.itemAt(6).widget()
        combo.addItems(["Option 1", "Option 2", "Option 3"])

        self.setLayout(flow_layout)
        self.resize(600, 300)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = FlowLayoutWindow()
    window.show()
    sys.exit(app.exec())