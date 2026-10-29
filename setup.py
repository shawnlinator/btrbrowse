try:
    from setuptools import setup
except ImportError:
    from distutils.core import setup

config = {
    'name': 'btrbrowse',
    'version': '0.1',
    'packages': ['btrbrowse'],
    'description': 'Graphical browser for btrbk snapshots, based on snapper-gui',
    'author': 'Ricardo Vieira, btrbrowse contributors',
    'url': 'https://github.com/shawnlinator/btrbrowse',
    'package_data': {"btrbrowse": ["glade/*.glade",
                                   "icons/*.svg",
                                   "ui/*.ui"]},
    'data_files': [('share/applications', ['btrbrowse.desktop'])],
    'entry_points': {'gui_scripts':
                         ['btrbrowse = btrbrowse.application:start_ui']}
}

setup(**config)
