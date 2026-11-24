from PySide6.QtWidgets import QApplication
from omapreview.__main__ import main


def test_desktop_identity_matches_installed_desktop_entry(qapp, monkeypatch):
    monkeypatch.setattr(QApplication, 'exec', lambda self: 0)
    before = set(qapp.topLevelWidgets())
    try:
        assert main([]) == 0
        assert qapp.desktopFileName() == 'coak.omapreview'
    finally:
        for window in set(qapp.topLevelWidgets()) - before:
            window.close()
