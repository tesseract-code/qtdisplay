import numpy as np
from PyQt6.QtWidgets import QApplication

from qtdisplay.mpl_charts.multi_plt import MultiPlot

if __name__ == '__main__':
    import sys
    import matplotlib.pyplot as plt
    app = QApplication(sys.argv)
    sh = (1024, 1024)
    ims = [[plt.imshow(np.random.random(sh)), plt.text(100, 100, str(i))] for i in range(3)]
    mp = MultiPlot(ims, "Hey")
    plt.gcf().subplots_adjust(left=0, bottom=0, right=1, top=1, wspace=0, hspace=0)
    mp.ax.get_xaxis().set_visible(False)
    mp.ax.get_yaxis().set_visible(False)
    [mp.imshow(np.random.random(sh)) for i in range(3)]
    mp.show()

    fig, ax = plt.subplots()
    lines = [ax.plot(np.random.random((50,))) for i in range(3)]
    mp2 = MultiPlot(lines, 'Lines')
    mp2.show()

    # PyQt6: exec_() renamed to exec()
    sys.exit(app.exec())