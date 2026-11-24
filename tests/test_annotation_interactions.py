"""Generated PDFs only: durable ownership and real annotation gestures."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
import pytest


@pytest.fixture(autouse=True)
def discard_generated_work_on_teardown(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Discard)

from omapreview.core import Document
from test_core import make_pdf, ink_png


def test_managed_text_roundtrip_move_delete_without_touching_original(tmp_path):
    source = make_pdf(tmp_path)
    original = source.read_bytes()
    doc = Document(source)
    mark = doc.add_text(0, [20, 150, 220, 210], 'Café 日本語')
    assert mark is not None
    output = tmp_path / 'editable.pdf'
    doc.save_copy(output)
    reopened = Document(output)
    marks = reopened.marks(0)
    assert len(marks) == 1 and marks[0]['id'] == mark
    reopened.update_mark(0, mark, rect=[160, 250, 360, 310], text='Changed Ελληνικά')
    final = tmp_path / 'moved.pdf'
    reopened.save_copy(final)
    with pymupdf.open(final) as pdf:
        assert 'Café' not in pdf[0].get_text()
        assert pdf[0].get_text().count('Changed') == 1
        assert 'Original' in pdf[0].get_text()
        assert pdf[0].get_pixmap(clip=pymupdf.Rect(20, 150, 150, 210)).samples == bytes([255]) * (130 * 60 * 3)
    reopened.delete_mark(0, mark)
    assert reopened.marks(0) == []
    assert reopened.pdf[0].get_text().strip() == 'Original'
    assert reopened.undo() and len(reopened.marks(0)) == 1
    assert reopened.redo() and reopened.marks(0) == []
    assert source.read_bytes() == original


def test_managed_signature_save_move_resize_delete_restores_pixels(qapp, tmp_path):
    source = make_pdf(tmp_path)
    doc = Document(source)
    base = doc.render(0, scale=1).samples
    mark = doc.add_signature(0, [20, 180, 180, 260], ink_png())
    assert mark is not None
    # MuPDF may rerasterize source glyphs with <=2 levels of AA variation
    # after resource changes; require the old signature region to be exactly blank.
    def source_only(samples):
        assert max(abs(a - b) for a, b in zip(samples, base)) <= 2
    source_only(doc.render(0, scale=1, include_marks=False).samples)
    output = tmp_path / 'signature-owned.pdf'
    doc.save_copy(output)
    copy = Document(output)
    assert copy.marks(0)[0]['id'] == mark
    copy.update_mark(0, mark, rect=[200, 350, 360, 430])
    copy.save_copy(output.with_name('signature-moved.pdf'))
    assert copy.render(0, scale=1).samples != base
    source_only(copy.render(0, scale=1, include_marks=False).samples)
    copy.delete_mark(0, mark)
    source_only(copy.render(0, scale=1).samples)
    blank = copy.pdf[0].get_pixmap(clip=pymupdf.Rect(20, 180, 180, 260))
    assert set(blank.samples) == {255}


def test_ui_native_blur_navigation_save_keep_editable_marks(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from test_ui import make_pdf as ui_pdf
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(ui_pdf(tmp_path, fields=True))
    item = window.add_text_overlay(50, 150)
    item.setPlainText('Retained text')
    window.field_widgets[0][1].setFocus()
    qtbot.wait(10)
    window.field_widgets[0][1].setText('Native entry')
    assert window.commit_pending()
    retained = len(window.pending_items) == 1
    assert window.go_page(1) and window.go_page(0)
    restored = len(window.pending_items) == 1
    output = tmp_path / 'retained.pdf'
    assert window.save_copy(output)
    clean = not window.has_changes()
    editable = len(window.pending_items) == 1
    if editable:
        window.pending_items[0].setPos(100, 250)
        moved_dirty = window.has_changes()
        assert window.save_copy(output, overwrite=True)
    else:
        moved_dirty = False
    window.document.dirty = False
    assert retained and restored and editable and clean and moved_dirty
    with pymupdf.open(output) as pdf:
        assert pdf[0].get_text().count('Retained text') == 1
        assert next(pdf[0].widgets()).field_value == 'Native entry'
    reopened = Document(output)
    assert reopened.marks(0)[0]['rect'][0:2] == pytest.approx([100, 250])


@pytest.mark.parametrize('kind', ['text', 'signature'])
def test_saved_marks_reselect_body_drag_corner_resize_and_delete(qtbot, tmp_path, kind):
    from omapreview.ui import MainWindow, TextOverlay
    from test_ui import make_pdf as ui_pdf
    from PySide6.QtCore import Qt, QPointF, QPoint
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(ui_pdf(tmp_path, fields=True))
    if kind == 'text':
        item = window.add_text_overlay(50, 150)
        item.setPlainText('Select then move')
    else:
        item = window.add_signature_overlay(50, 150, ink_png())
    assert window.save_copy(tmp_path / f'{kind}.pdf')
    window.go_page(1)
    window.go_page(0)
    item = window.pending_items[0]
    viewport = window.view.viewport()
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=window.view.mapFromScene(QPointF(350, 400)))
    assert not item.isSelected()
    start = window.view.mapFromScene(item.mapToScene(QPointF(30, 10)))
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=start)
    selected = item.isSelected()
    if isinstance(item, TextOverlay):
        assert item.textInteractionFlags() == Qt.NoTextInteraction
    old = item.pos()
    qtbot.mousePress(viewport, Qt.LeftButton, pos=start)
    qtbot.mouseMove(viewport, start + QPoint(60, 40))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=start + QPoint(60, 40))
    moved = item.pos().x() > old.x() + 10
    rect = item.boundingRect()
    ratio = rect.width() / rect.height()
    corner = window.view.mapFromScene(item.mapToScene(rect.bottomRight() - QPointF(2, 2)))
    qtbot.mousePress(viewport, Qt.LeftButton, pos=corner)
    qtbot.mouseMove(viewport, corner + QPoint(75, 30))
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=corner + QPoint(75, 30))
    resized = item.boundingRect().width() > rect.width() + 10
    assert window.has_changes()
    if kind == 'signature':
        assert item.boundingRect().width() / item.boundingRect().height() == pytest.approx(ratio)
    qtbot.keyClick(window.view, Qt.Key_Delete)
    removed = not window.pending_items and not window.document.marks(0)
    assert window.undo_edit()
    restored = len(window.pending_items) == 1
    window.document.dirty = False
    assert selected and moved and resized and removed and restored


def test_drag_text_area_wraps_grows_and_double_click_edits(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from test_ui import make_pdf as ui_pdf
    from PySide6.QtCore import Qt, QPointF
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(ui_pdf(tmp_path))
    assert window.mode == 'select'
    assert window.select_action.isChecked()
    window.text_action.trigger()
    viewport = window.view.viewport()
    a = window.view.mapFromScene(QPointF(50, 150))
    b = window.view.mapFromScene(QPointF(180, 210))
    qtbot.mousePress(viewport, Qt.LeftButton, pos=a)
    qtbot.mouseMove(viewport, b)
    qtbot.mouseRelease(viewport, Qt.LeftButton, pos=b)
    item = window.pending_items[0]
    assert item.boundingRect().width() == pytest.approx(130, abs=1)
    assert item.boundingRect().height() == pytest.approx(60, abs=1)
    text = 'Wrapping text with many words and no lost ending'
    qtbot.keyClicks(viewport, text)
    assert item.toPlainText() == text
    item.resize_to(65, 20)
    assert item.document().size().height() > 40
    assert item.boundingRect().height() >= item.document().size().height()
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=window.view.mapFromScene(QPointF(350, 400)))
    qtbot.wait(5)
    point = window.view.mapFromScene(item.mapToScene(QPointF(20, 7)))
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=point)
    assert item.textInteractionFlags() == Qt.NoTextInteraction
    qtbot.mouseDClick(viewport, Qt.LeftButton, pos=point)
    assert item.textInteractionFlags() & Qt.TextEditable
    qtbot.keyClick(viewport, Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClicks(viewport, 'Edited twice')
    output = tmp_path / 'wrap.pdf'
    assert window.save_copy(output)
    assert not window.has_changes()
    assert window.mode == 'select'
    with pymupdf.open(output) as pdf:
        assert 'Edited' in pdf[0].get_text() and 'twice' in pdf[0].get_text()
    window.document.dirty = False


def test_text_never_silently_shrinks_or_clips_when_box_is_too_small(tmp_path):
    doc = Document(make_pdf(tmp_path))
    text = 'A wrapping paragraph that should stay twelve point until its final ending'
    mark_id = doc.add_text(0, [30, 150, 130, 170], text)
    mark = doc.marks(0)[0]
    assert mark['id'] == mark_id
    assert mark['rect'][3] > 170
    spans = [s for b in doc.pdf[0].get_text('dict')['blocks'] if 'lines' in b
             for line in b['lines'] for s in line['spans'] if s['bbox'][1] > 100]
    assert all(s['size'] == pytest.approx(12, abs=.1) for s in spans)
    assert 'ending' in doc.pdf[0].get_text()
    before = doc.pdf[0].get_text()
    with pytest.raises(ValueError, match='fit|room'):
        doc.update_mark(0, mark_id, rect=[30, 480, 60, 500], text=text * 10)
    assert doc.pdf[0].get_text() == before


def test_wrapped_unicode_save_updates_box_and_is_clean(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from test_ui import make_pdf as ui_pdf
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(ui_pdf(tmp_path))
    item = window.add_text_overlay(30, 150, 90, 20)
    item.setPlainText('Café Ελληνικά 日本語 narrow wrapping retains every final word')
    output = tmp_path / 'unicode-wrapped.pdf'
    assert window.save_copy(output)
    assert not window.has_changes()
    mark = window.document.marks(0)[0]
    assert mark['rect'] == pytest.approx(window.overlay_values(item)['rect'])
    assert mark['font_size'] == 12
    window.go_page(1)
    window.go_page(0)
    assert not window.has_changes()
    assert window.pending_items[0].toPlainText() == mark['text']
    assert Document(output).marks(0)[0]['text'] == mark['text']


def test_select_mark_above_native_field_leaves_uncovered_field_editable(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from test_ui import make_pdf as ui_pdf
    from PySide6.QtCore import Qt, QPointF
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(ui_pdf(tmp_path, fields=True))
    item = window.add_text_overlay(40, 60, 90, 25)
    item.setPlainText('Above field')
    window.commit_pending()
    window.select_action.trigger()
    assert item.textInteractionFlags() == Qt.NoTextInteraction
    viewport = window.view.viewport()
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=window.view.mapFromScene(QPointF(60, 70)))
    assert item.isSelected()
    qtbot.mouseClick(viewport, Qt.LeftButton, pos=window.view.mapFromScene(QPointF(190, 70)))
    native = window.field_widgets[0][1]
    qtbot.waitUntil(native.hasFocus)
    assert native.hasFocus()
    qtbot.keyClicks(viewport, 'Ada')
    qtbot.keyClick(viewport, Qt.Key_Home)
    qtbot.keyClick(viewport, Qt.Key_Delete)
    assert native.text() == 'da'
    assert window.pending_items == [item]
    assert window.save_copy(tmp_path / 'overlapping.pdf')


def test_clearing_text_keeps_owned_identity_editable_and_undoable(tmp_path):
    doc = Document(make_pdf(tmp_path))
    identity = doc.add_text(0, [30, 150, 230, 190], 'Erase then retype')
    assert doc.update_mark(0, identity, text='')
    assert len(doc.marks(0)) == 1
    assert doc.marks(0)[0]['id'] == identity
    assert doc.update_mark(0, identity, text='Retyped')
    assert doc.pdf[0].get_text().count('Retyped') == 1
    assert doc.undo()
    assert doc.marks(0)[0]['text'] == ''


@pytest.mark.parametrize('kind', ['text', 'signature'])
def test_delete_new_draft_can_be_undone(qtbot, tmp_path, kind):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    if kind == 'text':
        item = window.add_text_overlay(30, 150)
        item.setPlainText('Draft text')
    else:
        item = window.add_signature_overlay(30, 150, ink_png())
    item.setSelected(True)
    window.delete_selected()
    assert not window.pending_items
    assert window.undo_edit()
    assert len(window.pending_items) == 1
    window.document.dirty = False


def test_real_pdf_evidence_revisions_preserve_original_annotations(qapp, tmp_path):
    source = make_pdf(tmp_path)
    with pymupdf.open(source) as pdf:
        page = pdf[0]
        note = page.add_text_annot((350, 30), 'Original review note')
        note.set_info(title='Original reviewer')
        source.write_bytes(pdf.tobytes())
    original = source.read_bytes()
    doc = Document(source)
    text_id = doc.add_text(0, [35, 140, 300, 190], 'Before moving Café 日本語')
    signature_id = doc.add_signature(0, [30, 260, 190, 340], ink_png())
    output = tmp_path / 'annotation-editing-evidence.pdf'
    doc.save_copy(output)
    doc.update_mark(0, text_id, rect=[90, 180, 290, 260], text='Updated Café Ελληνικά 日本語 wrapping text, retained at twelve points.')
    doc.update_mark(0, signature_id, rect=[210, 370, 330, 430])
    doc.save_copy(output, overwrite=True)
    copy = Document(output)
    assert {m['id'] for m in copy.marks(0)} == {text_id, signature_id}
    assert source.read_bytes() == original
    with pymupdf.open(output) as pdf:
        text = pdf[0].get_text()
        assert text.count('Updated') == 1 and 'Before moving' not in text
        assert 'Café' in text and 'Ελληνικά' in text and '日本語' in text
        assert len(pdf[0].get_image_info()) == 1  # Actual paint operations, not unused resources.
        notes = list(pdf[0].annots())
        assert len(notes) == 1
        assert notes[0].info['content'] == 'Original review note'
        assert notes[0].info['title'] == 'Original reviewer'
        assert set(pdf[0].get_pixmap(clip=pymupdf.Rect(30, 260, 190, 340)).samples) == {255}
        pdf[0].get_pixmap(matrix=pymupdf.Matrix(2, 2)).save(str(tmp_path / 'annotation-editing-evidence.png'))
    print(f'Generated evidence: {output}')


def test_failed_fit_modal_cannot_reenter_native_blur_commit(qtbot, tmp_path, monkeypatch):
    from omapreview.ui import MainWindow
    from PySide6.QtWidgets import QMessageBox
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    item = window.add_text_overlay(30, 480, 40, 20)
    item.setPlainText('Too much text near page bottom ' * 10)
    warnings = []
    def warn(*args):
        warnings.append(args)
        if len(warnings) == 1:
            assert not window.commit_pending()  # Focus blur while the modal owns the event loop.
        return QMessageBox.Ok
    monkeypatch.setattr(QMessageBox, 'warning', warn)
    assert not window.save_copy(tmp_path / 'must-not-exist.pdf')
    assert len(warnings) == 1
    assert window.pending_items == [item] and window.has_changes()
    assert not (tmp_path / 'must-not-exist.pdf').exists()
    item.setPos(30, 140)
    item.resize_to(250, 250)
    assert window.save_copy(tmp_path / 'fits-after-resize.pdf')


def test_open_resets_select_tool_consistently(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    source = make_pdf(tmp_path)
    assert window.open_file(source)
    window.text_action.trigger()
    assert window.mode == 'text' and window.text_action.isChecked()
    assert window.open_file(source)
    assert window.mode == 'select' and window.select_action.isChecked()
    assert not window.text_action.isChecked() and not window.view.placing
