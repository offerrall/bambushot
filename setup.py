from setuptools import setup

setup(
    name='bambushot',
    version='0.1.0',
    description='Fire prints on Bambu Lab printers without re-uploading every time.',
    author='',
    py_modules=['bambushot'],
    python_requires='>=3.10',
    install_requires=['paho-mqtt>=2.0.0'],
)