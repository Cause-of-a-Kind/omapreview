from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QToolButton
from omapreview.core import SignatureStore
from omapreview.ui import MainWindow


def test_saved_mark_can_be_forgotten_without_affecting_other_mark(qtbot, tmp_path):
    store = SignatureStore(tmp_path / 'marks')
    image = QImage(80, 30, QImage.Format_ARGB32)
    image.fill(QColor('#222222'))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    png = bytes(buffer.data())
    store.save('signature', png)
    store.save('initials', png)
    window = MainWindow(signature_store=store)
    qtbot.addWidget(window)
    button = next(b for b in window.toolbar.findChildren(QToolButton) if b.text() == 'Signature')
    action = next((a for a in button.menu().actions() if a.text() == 'Forget saved signature'), None)
    assert action is not None
    action.trigger()
    assert store.load('signature') is None
    assert store.load('initials') is not None
