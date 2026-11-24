"""Command-line entry point; help and version do not initialize Qt."""
import argparse
import sys
from pathlib import Path

from . import __version__


def main(argv=None):
    parser = argparse.ArgumentParser(description='Fill and visually sign a PDF locally.')
    parser.add_argument('pdf', nargs='?', type=Path, help='PDF file to open')
    parser.add_argument('--version', action='version', version=f'Omapreview {__version__}')
    args = parser.parse_args(argv)

    from PySide6.QtGui import QColor, QPalette
    from PySide6.QtWidgets import QApplication
    from .ui import MainWindow

    app = QApplication.instance() or QApplication([sys.argv[0]])
    app.setApplicationName('Omapreview')
    app.setDesktopFileName('coak.omapreview')
    app.setOrganizationName('Omapreview')
    app.setApplicationVersion(__version__)
    app.setStyle('Fusion')
    palette = QPalette()
    for role, color in [
        (QPalette.Window, '#1a1b26'), (QPalette.WindowText, '#c0caf5'),
        (QPalette.Base, '#16161e'), (QPalette.AlternateBase, '#24283b'),
        (QPalette.Text, '#c0caf5'), (QPalette.Button, '#24283b'),
        (QPalette.ButtonText, '#c0caf5'), (QPalette.Highlight, '#3d5981'),
        (QPalette.HighlightedText, '#ffffff'), (QPalette.ToolTipBase, '#24283b'),
        (QPalette.ToolTipText, '#c0caf5'),
    ]:
        palette.setColor(role, QColor(color))
    palette.setColor(QPalette.Disabled, QPalette.Text, QColor('#626880'))
    palette.setColor(QPalette.Disabled, QPalette.ButtonText, QColor('#626880'))
    app.setPalette(palette)
    window = MainWindow()
    window.show()
    if args.pdf:
        window.open_file(args.pdf)
    return app.exec()


if __name__ == '__main__':
    raise SystemExit(main())
