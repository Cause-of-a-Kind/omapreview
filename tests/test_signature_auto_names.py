"""Additional signatures should not require an extra naming decision."""
import pytest
from PySide6.QtCore import Qt, QBuffer, QIODevice
from PySide6.QtGui import QImage, QPainter, QPen
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QMessageBox
from omapreview.core import SignatureStore
from omapreview.ui import SignatureDialog


def ink_image():
    image = QImage(180, 60, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setPen(QPen(Qt.black, 3))
    painter.drawLine(10, 45, 165, 15)
    painter.end()
    return image


def png():
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    ink_image().save(buffer, 'PNG')
    return bytes(buffer.data())


@pytest.mark.parametrize('kind', ['signature', 'initials'])
def test_unnamed_additional_marks_get_distinct_names_without_replacing_primary(qtbot, tmp_path, monkeypatch, kind):
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: errors.append(args))
    store = SignatureStore(tmp_path / 'library')
    store.save(kind, png())
    primary = store.load(kind)
    for supplied_name in ('', '   '):
        dialog = SignatureDialog(kind, store, save_mode='additional')
        qtbot.addWidget(dialog)
        dialog.canvas.set_image(ink_image())
        dialog.name_edit.setText(supplied_name)
        dialog.show()
        qtbot.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save), Qt.LeftButton)
        assert dialog.result() == QDialog.Accepted, errors
        assert store.load_mark(kind, dialog.saved_id)
    assert not errors
    assert store.load(kind) == primary
    names = [row['name'] for row in store.list_marks(kind) if not row['primary']]
    base = kind.title()
    assert names == [f'{base} 2', f'{base} 3']


def test_automatic_names_skip_existing_labels_and_work_without_a_primary(qtbot, tmp_path):
    store = SignatureStore(tmp_path / 'library')
    existing_id = store.save_additional('signature', 'signature 1', png())
    dialog = SignatureDialog('signature', store, save_mode='additional')
    qtbot.addWidget(dialog)
    dialog.canvas.set_image(ink_image())
    assert dialog.save_mark()
    assert store.load('signature') is None
    assert {row['name'] for row in store.list_marks('signature')} == {'signature 1', 'Signature 2'}
    assert store.load_mark('signature', existing_id)


def test_invalid_custom_name_has_help_and_keeps_ink_for_retry(qtbot, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    store = SignatureStore(tmp_path / 'library')
    dialog = SignatureDialog('signature', store, save_mode='additional')
    qtbot.addWidget(dialog)
    dialog.canvas.set_image(ink_image())
    ink = dialog.canvas.png_bytes()
    dialog.name_edit.setText('Work / personal')
    assert not dialog.save_mark()
    assert warnings and 'automatic name' in warnings[0][2]
    assert dialog.canvas.png_bytes() == ink
    assert store.list_marks('signature') == []
    dialog.name_edit.clear()
    assert dialog.save_mark()
    assert dialog.result() == QDialog.Accepted
    assert store.list_marks('signature')[0]['name'] == 'Signature 1'
