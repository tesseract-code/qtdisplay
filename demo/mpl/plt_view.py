

"""
Usage examples for PlotNd — an interactive N-dimensional array viewer.

Run any example directly:
    python plotnd_examples.py
"""

import sys

import numpy as np
from PyQt6.QtWidgets import QApplication

from review.mpl.plt_view import PlotNd


def example_3d_volume():
    """
    Basic 3D volume — the most common use case.
    The first two dimensions form the image plane; the third is scrolled
    through with the mouse wheel on the side plot.
    """
    # Simulate a 3D MRI-like volume: 64 slices of 128x128
    z, y, x = np.ogrid[-1:1:64j, -1:1:128j, -1:1:128j]
    data = np.exp(-(x**2 + y**2 + z**2) / 0.3)          # Gaussian blob
    data += 0.05 * np.random.default_rng(0).standard_normal(data.shape)

    return PlotNd(
        data,
        names=('Y', 'X', 'Z'),
        title='3D Volume — Gaussian Blob',
    )


def example_custom_indices():
    """
    Physical axis labels.
    Pass `indices` so that axes show real-world units (nm, eV, ps …)
    instead of raw array indices.
    """
    shape = (80, 80, 50)
    y_nm   = np.linspace(0, 200, shape[0])   # 0–200 nm
    x_nm   = np.linspace(0, 200, shape[1])   # 0–200 nm
    e_eV   = np.linspace(1.5, 3.0, shape[2]) # 1.5–3.0 eV

    # Simulate a photoluminescence map: intensity peaks shift with position
    Y, X, E = np.meshgrid(y_nm, x_nm, e_eV, indexing='ij')
    center  = 2.0 + 0.5 * (X / 200) + 0.3 * (Y / 200)  # spatially varying peak
    data    = np.exp(-((E - center) ** 2) / 0.04)
    data   += 0.02 * np.random.default_rng(1).standard_normal(data.shape)

    return PlotNd(
        data,
        names=('y (nm)', 'x (nm)', 'Energy (eV)'),
        indices=[y_nm, x_nm, e_eV],
        title='Photoluminescence Map',
    )


def example_4d_timeseries():
    """
    4D data: two spatial + one spectral + one time dimension.
    Each extra dimension beyond the first two gets its own side plot that
    can be scrolled independently.
    """
    shape = (64, 64, 20, 10)   # (Y, X, wavelength, time)
    rng   = np.random.default_rng(2)

    wavelengths = np.linspace(400, 700, shape[2])   # nm
    times       = np.linspace(0, 9, shape[3])       # seconds

    Y, X = np.mgrid[0:shape[0], 0:shape[1]]
    data = np.zeros(shape)
    for t_idx, t in enumerate(times):
        for w_idx, w in enumerate(wavelengths):
            # Blob that drifts in space over time and shifts in wavelength
            cy, cx = shape[0] * (0.3 + 0.04 * t), shape[1] * (0.4 + 0.03 * t)
            spatial = np.exp(-((Y - cy)**2 + (X - cx)**2) / 80)
            spectral = np.exp(-((w - (500 + 5 * t))**2) / 1000)
            data[:, :, w_idx, t_idx] = spatial * spectral

    data += 0.01 * rng.standard_normal(data.shape)

    return PlotNd(
        data,
        names=('Y', 'X', 'Wavelength (nm)', 'Time (s)'),
        indices=[
            np.arange(shape[0]),
            np.arange(shape[1]),
            wavelengths,
            times,
        ],
        init_coords=(shape[0] // 2, shape[1] // 2, shape[2] // 2, 0),
        title='4D Spectral Time-Series',
    )


def example_set_limits_and_cmap():
    """
    Demonstrates programmatic control: setting the colormap and display
    limits after construction, and updating the data array in-place.
    """
    data = np.random.default_rng(3).standard_normal((100, 100, 30))

    widget = PlotNd(data, names=('Y', 'X', 'Z'), title='Custom Limits & Colormap')

    # Restrict the displayed range to ±2σ and switch to a diverging colormap.
    widget.setLimits(-2.0, 2.0)
    widget.setColorMap('RdBu_r')

    # The `data` property is a live reference — replacing it triggers a redraw.
    new_data = np.random.default_rng(4).standard_normal((100, 100, 30))
    widget.data = new_data

    return widget


# ---------------------------------------------------------------------------
# Entry point — run all examples at once so you can compare them side by side.
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    app = QApplication(sys.argv)

    widgets = [
        example_3d_volume(),
        example_custom_indices(),
        example_4d_timeseries(),
        example_set_limits_and_cmap(),
    ]

    sys.exit(app.exec())