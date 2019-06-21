from Cython.Build import cythonize
import numpy as np
from setuptools import Distribution, find_packages, setup
from setuptools.extension import Extension


extensions = [
    Extension('jsf64_bitgen.jsf64',
              sources=['jsf64_bitgen/jsf64.pyx', 'src/jsf64/jsf64.c'],
              include_dirs=[np.get_include(), 'src/jsf64', 'src']),
]

setup(
    name='jsf64_bitgen',
    version='0.1',
    ext_modules=cythonize(extensions, compiler_directives=dict(
        language_level=3)),
    packages=find_packages(),
    package_dir={'jsf64_bitgen': './jsf64_bitgen'},
    license='MIT',
    author='Robert Kern',
    author_email='robert.kern@gmail.com',
    description='Demonstration of BitGenerator sans BitGenerator',
    zip_safe=False,
)
