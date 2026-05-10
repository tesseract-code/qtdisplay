from setuptools import setup, Extension
from Cython.Build import cythonize
import numpy as np

extensions = [
    Extension(
        name="qtdisplay.chart.model.data._backend.points_vector",
        sources=["src/qtdisplay/chart/model/data/_backend/points_vector.pyx"],
        include_dirs=[np.get_include()],
        language="c++",
    )
]

setup(
    ext_modules=cythonize(
        extensions,
        compiler_directives={"language_level": "3"},
    )
)
