import sys

from PyQt6 import QtWidgets

from review.mpl.common import QRangeSlider

if __name__ == '__main__':
    app = QtWidgets.QApplication(sys.argv)
    rs = QRangeSlider()
    rs.show()

    rs.setMax(100)
    rs.setMin(0.015)
    rs.setRange(.017, .50)
    rs.setBackgroundStyle(
        'background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #222, stop:1 #333);')
    rs.handle.setStyleSheet(
        'background: qlineargradient(x1:0, y1:0, x2:0, y2:1, stop:0 #282, stop:1 #393);')
    # PyQt6: exec_() renamed to exec()
    app.exec()