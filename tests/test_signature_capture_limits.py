from PySide6.QtCore import Qt, QBuffer, QIODevice
from PySide6.QtGui import QImage
from omapreview.core import SignatureStore


def test_additional_signature_accepts_full_resolution_drawing_canvas(qapp, tmp_path):
    # The app's 600×200 capture surface renders at 3× resolution.
    image = QImage(1800, 600, QImage.Format_ARGB32)
    image.fill(Qt.black)
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    store = SignatureStore(tmp_path / 'library')
    identity = store.save_additional('signature', 'Full canvas mark', bytes(buffer.data()))
    assert store.load_mark('signature', identity)
