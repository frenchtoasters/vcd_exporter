from setuptools import setup, find_packages
import vcd_exporter

setup(
    name='vcd_exporter',
    version=vcd_exporter.__version__,
    author=vcd_exporter.__author__,
    description='VMware vCloud Director Exporter for Prometheus',
    long_description=open('README.md').read(),
    url='https://gitlab.com/frenchtoasters/vcd_exporter',
    keywords=['VMware', 'vCD', 'Prometheus'],
    license=vcd_exporter.__license__,
    packages=find_packages(exclude=['*.test', '*.test.*']),
    include_package_data=True,
    install_requires=open('requirements.txt').readlines(),
    entry_points={
        'console_scripts': [
            'vcd_exporter=vcd_exporter.vcd_exporter:main'
        ]
    }
)
