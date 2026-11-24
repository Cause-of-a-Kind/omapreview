"""Generated saved-signature metadata and failed-open regressions only."""
import base64
import json
import os
import uuid

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QMessageBox

from omapreview.core import Document
from omapreview.ui import MainWindow


@pytest.fixture(autouse=True)
def generated_dialogs(monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **k: QMessageBox.Discard)
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **k: None)


def make_pdf(path):
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        page.insert_text((20, 30), 'Generated original content')
        pdf.save(path)
    return path


def malformed_signature(path, png=None):
    make_pdf(path)
    with pymupdf.open(path) as pdf:
        page = pdf[0]
        identity = str(uuid.uuid4())
        stream = pdf.get_new_xref()
        pdf.update_object(stream, '<<>>')
        pdf.update_stream(stream, Document._marker(identity) + b'q Q\n')
        contents = page.get_contents() + [stream]
        pdf.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{ref} 0 R' for ref in contents) + ']')
        mark = dict(id=identity, kind='signature', rect=[40, 60, 160, 100], streams=[stream])
        if png is not None:
            mark['png'] = png
        pdf.xref_set_key(page.xref, Document._MARK_KEY,
                         pymupdf.get_pdf_str(json.dumps(dict(version=1, marks=[mark]))))
        pdf.saveIncr()
    return path


def assert_editor_usable(window):
    assert not window._rebuilding
    item = window.add_text_overlay(30, 150)
    item.setPlainText('Still editable')
    assert window.commit_pending()
    assert 'Still editable' in window.document.pdf[0].get_text()


def signature_png(width=120, height=40):
    image = QImage(width, height, QImage.Format_ARGB32)
    image.fill(QColor('#222222'))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(buffer.data())


def bad_payload(case):
    if case == 'wrong-type':
        return [1, 2]
    if case == 'invalid-base64':
        return 'not base64!!!'
    if case == 'not-image':
        return base64.b64encode(b'not an image').decode('ascii')
    if case == 'truncated-png':
        return base64.b64encode(signature_png()[:40]).decode('ascii')
    if case == 'oversized-bytes':
        return base64.b64encode(signature_png() + b'\0' * (4 * 1024 * 1024)).decode('ascii')
    # Tiny generated file with a valid PNG dimension header: never allocate it.
    import struct
    import zlib
    dimensions = {'oversized-pixels': (1800, 1200), 'oversized-edge': (20000, 1)}
    data = bytearray(signature_png())
    data[16:24] = struct.pack('>II', *dimensions[case])
    data[29:33] = struct.pack('>I', zlib.crc32(data[12:29]))
    return base64.b64encode(data).decode('ascii')


@pytest.mark.parametrize('case', ['wrong-type', 'invalid-base64', 'not-image',
                                  'truncated-png', 'oversized-bytes',
                                  'oversized-pixels', 'oversized-edge'])
def test_invalid_saved_signature_is_not_exposed_to_editor(qtbot, tmp_path, case):
    path = malformed_signature(tmp_path / f'{case}.pdf', bad_payload(case))
    document = Document(path)
    try:
        assert document.marks(0) == []
    finally:
        document.pdf.close()
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_file(path)
    assert not window.pending_items
    assert_editor_usable(window)


@pytest.mark.parametrize('size', [(120, 40), (1800, 600)])
def test_valid_saved_signature_remains_editable(qtbot, tmp_path, size):
    source = make_pdf(tmp_path / 'source.pdf')
    document = Document(source)
    identity = document.add_signature(0, [40, 60, 160, 100], signature_png(*size))
    saved = tmp_path / 'saved.pdf'
    document.save_copy(saved)
    document.pdf.close()
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_file(saved)
    item, = window.pending_items
    assert item.mark_id == identity
    item.setPos(80, 100)
    assert window.commit_pending()
    output = tmp_path / 'moved.pdf'
    assert window.save_copy(output)
    reopened = Document(output)
    try:
        mark, = reopened.marks(0)
        assert mark['id'] == identity
        assert mark['rect'] == pytest.approx([80, 100, 200, 140])
    finally:
        reopened.pdf.close()


@pytest.mark.parametrize('failure', ['render', 'signature'])
def test_failed_open_preserves_original_usable_editor(qtbot, tmp_path, monkeypatch, failure):
    window = MainWindow()
    qtbot.addWidget(window)
    source = make_pdf(tmp_path / 'original.pdf')
    with pymupdf.open(source) as pdf:
        page = pdf[0]
        field = pymupdf.Widget()
        field.field_name = 'Generated name'
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pymupdf.Rect(30, 60, 220, 85)
        page.add_widget(field)
        pdf.new_page(width=400, height=500)
        pdf.saveIncr()
    assert window.open_file(source)
    assert window.go_page(1)
    pending = window.add_text_overlay(30, 150)
    pending.setPlainText('Retained pending text')
    original, scene = window.document, window.scene
    window.set_mode('text')
    candidate = Document(make_pdf(tmp_path / 'candidate-source.pdf'))
    candidate.add_signature(0, [40, 60, 160, 100], signature_png())
    saved = tmp_path / 'candidate.pdf'
    candidate.save_copy(saved)
    candidate.pdf.close()
    created = []
    original_init = Document.__init__

    def record_document(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(Document, '__init__', record_document)
    with monkeypatch.context() as faults:
        if failure == 'render':
            render = Document.render

            def fail_candidate(self, *args, **kwargs):
                if self is not original:
                    raise ValueError('Generated initialization failure')
                return render(self, *args, **kwargs)

            faults.setattr(Document, 'render', fail_candidate)
        else:
            def fail_signature(*args, **kwargs):
                raise ValueError('Generated overlay initialization failure')
            faults.setattr('omapreview.ui.SignatureOverlay.__init__', fail_signature)
        assert not window.open_file(saved)
    assert window.document is original
    assert not original.pdf.is_closed
    assert created and created[0].pdf.is_closed
    assert window.scene is scene
    assert window.page_index == 1
    assert window.mode == 'text'
    assert window.page_scroll.document is original
    assert window.thumbnails.document is original
    assert window.pending_items == [pending]
    assert not window._rebuilding
    assert window.commit_pending()
    assert 'Retained pending text' in original.pdf[1].get_text()
    assert window.go_page(0)
    window.field_widgets[0][1].setText('Editor survived')
    assert window.commit_pending()
    assert original.fields(0)[0]['value'] == 'Editor survived'


def test_scene_initialization_exception_resets_rebuilding(qtbot, tmp_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_file(make_pdf(tmp_path / 'original.pdf'))
    scene = window.scene
    with monkeypatch.context() as faults:
        def fail_render(*args, **kwargs):
            raise ValueError('Generated render failure')
        faults.setattr(window.document, 'render', fail_render)
        with pytest.raises(ValueError, match='Generated render failure'):
            window.show_page()
    assert not window._rebuilding
    assert window.scene is scene
    assert_editor_usable(window)


def test_missing_saved_signature_payload_cannot_poison_editor(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_file(make_pdf(tmp_path / 'original.pdf'))
    original = window.document
    malformed = malformed_signature(tmp_path / 'missing-png.pdf')
    opened = window.open_file(malformed)
    if not opened:
        assert window.document is original
    assert_editor_usable(window)
    document = Document(malformed)
    try:
        assert document.marks(0) == []
    finally:
        document.pdf.close()
