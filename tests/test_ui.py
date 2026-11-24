import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest


@pytest.fixture(autouse=True)
def discard_generated_work_on_teardown(monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Discard)

from PySide6.QtWidgets import QLineEdit


def make_pdf(tmp_path, fields=False):
    path = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=500)
    if fields:
        widget = pymupdf.Widget()
        widget.field_name = 'Name'
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(40, 60, 240, 85)
        page.add_widget(widget)
    pdf.new_page(width=400, height=500)
    pdf.save(path)
    pdf.close()
    return path


def test_open_render_and_navigate(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    assert window.open_file(make_pdf(tmp_path))
    assert window.document.page_count == 2
    assert window.scene.sceneRect().width() == 400
    assert window.go_page(1)
    assert window.page_index == 1
    assert not window.go_page(2)
    window.document.dirty = False


def test_fields_commit_before_navigation_and_save(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    source = make_pdf(tmp_path, fields=True)
    original = source.read_bytes()
    assert window.open_file(source)
    field = window.field_widgets[0][1]
    assert isinstance(field, QLineEdit)
    assert window.field_widgets[0][0].pos().x() == 40
    field.setText('Ada Lovelace')
    assert window.go_page(1)
    assert window.document.fields(0)[0]['value'] == 'Ada Lovelace'
    output = tmp_path / 'filled.pdf'
    assert window.save_copy(output)
    assert not window.windowTitle().startswith('*')
    with pymupdf.open(output) as pdf:
        assert list(pdf[0].widgets())[0].field_value == 'Ada Lovelace'
    assert source.read_bytes() == original


def test_text_preview_move_edit_delete_and_commit(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    overlay = window.add_text_overlay(45, 100)
    overlay.setPlainText('Local text')
    overlay.setPos(75, 120)
    assert not window.document.dirty
    assert len(window.pending_items) == 1
    assert window.commit_pending()
    assert 'Local text' in window.document.pdf[0].get_text()
    # Applying edits now retains the owned overlay instead of baking it away.
    assert window.pending_items == [overlay]
    assert window.undo_edit()
    assert 'Local text' not in window.document.pdf[0].get_text()
    overlay = window.add_text_overlay(20, 30)
    overlay.setSelected(True)
    window.delete_selected()
    assert not window.pending_items
    window.document.dirty = False


def test_signature_resize_move_preview_and_saved_geometry(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QBuffer, QIODevice
    image = QImage(120, 40, QImage.Format_ARGB32)
    image.fill(QColor('#222222'))
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    image.save(buffer, 'PNG')
    png = bytes(buffer.data())
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    item = window.add_signature_overlay(20, 30, png)
    item.setPos(70, 100)
    item.resize_to(180, 60)
    assert not window.document.dirty
    output = tmp_path / 'signed.pdf'
    assert window.save_copy(output)
    with pymupdf.open(output) as pdf:
        rect = pdf[0].get_image_rects(pdf[0].get_images()[0][0])[0]
        assert tuple(rect) == pytest.approx((70, 100, 250, 160))


def test_commit_refreshes_page_preview(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    before = window.background_item.pixmap().toImage()
    window.add_text_overlay(40, 50).setPlainText('Visible immediately')
    window.commit_pending()
    # Managed marks are painted once, by editable overlays, not the background.
    assert before == window.background_item.pixmap().toImage()
    assert window.pending_items[0].toPlainText() == 'Visible immediately'
    assert 'Visible immediately' in window.document.pdf[0].get_text()
    window.document.dirty = False


def test_capture_draw_import_clear_and_reuse(qtbot, tmp_path):
    from omapreview.ui import MainWindow, SignatureDialog
    from omapreview.core import SignatureStore
    from PySide6.QtCore import Qt, QPoint
    from PySide6.QtGui import QImage, QColor
    store = SignatureStore(tmp_path / 'marks')
    dialog = SignatureDialog('signature', store)
    qtbot.addWidget(dialog)
    dialog.show()
    qtbot.mousePress(dialog.canvas, Qt.LeftButton, pos=QPoint(30, 50))
    qtbot.mouseMove(dialog.canvas, QPoint(160, 90))
    qtbot.mouseRelease(dialog.canvas, Qt.LeftButton, pos=QPoint(160, 90))
    assert dialog.canvas.png_bytes()
    dialog.canvas.clear()
    assert dialog.canvas.png_bytes() is None
    jpg = tmp_path / 'mark.jpg'
    image = QImage(80, 30, QImage.Format_RGB32)
    image.fill(QColor('black'))
    image.save(str(jpg), 'JPG')
    assert dialog.import_image(jpg)
    assert dialog.save_mark()
    assert store.load('signature').startswith(b'\x89PNG')
    window = MainWindow(signature_store=store)
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    assert window.use_mark('signature')
    window.place_at(45, 60)
    assert len(window.pending_items) == 1
    window.pending_items.clear()


def test_unsaved_cancel_and_failed_save_block_close_and_open(qtbot, tmp_path, monkeypatch):
    from omapreview.ui import MainWindow
    from PySide6.QtWidgets import QMessageBox, QFileDialog
    from PySide6.QtGui import QCloseEvent
    window = MainWindow()
    qtbot.addWidget(window)
    source = make_pdf(tmp_path, fields=True)
    window.open_file(source)
    window.field_widgets[0][1].setText('Unsaved')
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Cancel)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert not window.open_file(source)
    assert window.field_widgets[0][1].text() == 'Unsaved'
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Save)
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: ('', ''))
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    monkeypatch.setattr(QFileDialog, 'getSaveFileName', lambda *a, **kw: (str(source), ''))
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.Ok)
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Discard)
    event = QCloseEvent()
    window.closeEvent(event)
    assert event.isAccepted()
    window.document.dirty = False
    window.field_widgets = []


def test_password_retry_cancel_and_bad_pdf_preserve_current(qtbot, tmp_path, monkeypatch):
    from omapreview.ui import MainWindow
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    window = MainWindow()
    qtbot.addWidget(window)
    source = make_pdf(tmp_path)
    window.open_file(source)
    encrypted = tmp_path / 'protected.pdf'
    with pymupdf.open(source) as pdf:
        pdf.save(encrypted, encryption=pymupdf.PDF_ENCRYPT_AES_256,
                 owner_pw='owner-only', user_pw='test-password')
    answers = iter([('wrong', True), ('test-password', True)])
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: QMessageBox.Ok)
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: next(answers))
    assert window.open_file(encrypted)
    assert window.document.path == encrypted
    monkeypatch.setattr(QInputDialog, 'getText', lambda *a, **kw: ('', False))
    assert not window.open_file(encrypted)
    assert window.document.path == encrypted
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: errors.append(a))
    bad = tmp_path / 'broken.pdf'
    bad.write_bytes(b'not a pdf')
    assert not window.open_file(bad)
    assert errors
    assert window.document.path == encrypted


def test_cli_help_version_and_optional_pdf(qtbot, tmp_path, monkeypatch):
    import subprocess
    import sys
    from PySide6.QtWidgets import QApplication
    from omapreview.__main__ import main
    from omapreview import __version__
    for flag, expected in [('--help', 'PDF'), ('--version', __version__)]:
        result = subprocess.run([sys.executable, '-m', 'omapreview', flag],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode == 0
        assert expected in result.stdout
    source = make_pdf(tmp_path)
    opened = []

    def event_loop():
        from omapreview.ui import MainWindow
        windows = [w for w in QApplication.topLevelWidgets() if isinstance(w, MainWindow) and w.isVisible()]
        opened.extend(w.document.path for w in windows if w.document)
        for w in windows:
            w.close()
        return 0

    monkeypatch.setattr(QApplication, 'exec', staticmethod(event_loop))
    assert main([str(source)]) == 0
    assert source in opened


def test_text_tool_mouse_keyboard_and_pdf_point_size(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from PySide6.QtCore import Qt, QPointF
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(make_pdf(tmp_path))
    window.text_action.trigger()
    point = window.view.mapFromScene(QPointF(50, 60))
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton, pos=point)
    assert len(window.pending_items) == 1
    item = window.pending_items[0]
    qtbot.keyClicks(window.view.viewport(), 'Typed on page')
    assert item.toPlainText() == 'Typed on page'
    size = item.font().pointSizeF() * window.view.viewport().logicalDpiY() / 72
    window.pending_items.clear()
    assert size == 12


def test_pending_mark_mouse_drag_resize_delete(qtbot, tmp_path):
    from omapreview.ui import MainWindow, DrawCanvas
    from PySide6.QtCore import Qt, QPointF, QPoint
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(make_pdf(tmp_path))
    canvas = DrawCanvas()
    qtbot.addWidget(canvas)
    qtbot.mousePress(canvas, Qt.LeftButton, pos=QPoint(20, 40))
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=QPoint(100, 60))
    item = window.add_signature_overlay(50, 60, canvas.png_bytes())
    start = window.view.mapFromScene(item.mapToScene(QPointF(15, 5)))
    end = start + QPoint(50, 40)
    qtbot.mousePress(window.view.viewport(), Qt.LeftButton, pos=start)
    qtbot.mouseMove(window.view.viewport(), end)
    qtbot.mouseRelease(window.view.viewport(), Qt.LeftButton, pos=end)
    moved = item.pos().x() > 50
    width = item.width
    corner = window.view.mapFromScene(item.mapToScene(QPointF(item.width - 2, item.height - 2)))
    qtbot.mousePress(window.view.viewport(), Qt.LeftButton, pos=corner)
    qtbot.mouseMove(window.view.viewport(), corner + QPoint(70, 20))
    qtbot.mouseRelease(window.view.viewport(), Qt.LeftButton, pos=corner + QPoint(70, 20))
    resized = item.width > width
    qtbot.keyClick(window.view, Qt.Key_Delete)
    removed = not window.pending_items
    window.pending_items.clear()
    assert moved
    assert resized
    assert removed


def test_native_controls_readonly_checkbox_and_blank_choice(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    path = tmp_path / 'controls.pdf'
    pdf = pymupdf.open()
    page = pdf.new_page()
    for i, (name, kind, value) in enumerate([
        ('locked', pymupdf.PDF_WIDGET_TYPE_TEXT, 'Keep'),
        ('check', pymupdf.PDF_WIDGET_TYPE_CHECKBOX, 'Off'),
        ('choice', pymupdf.PDF_WIDGET_TYPE_COMBOBOX, ''),
    ]):
        widget = pymupdf.Widget()
        widget.field_name, widget.field_type, widget.field_value = name, kind, value
        widget.rect = pymupdf.Rect(40, 60 + i * 40, 200, 85 + i * 40)
        if name == 'locked':
            widget.field_flags = pymupdf.PDF_FIELD_IS_READ_ONLY
        if name == 'choice':
            widget.choice_values = ['Red', 'Blue']
        page.add_widget(widget)
    pdf.save(path)
    pdf.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(path)
    controls = {f['name']: w for _, w, f in window.field_widgets}
    assert not controls['locked'].isEnabled()
    initially_clean = not window.has_changes()
    controls['check'].setChecked(True)
    controls['choice'].setCurrentText('Blue')
    window.commit_pending()
    values = {f['name']: f['value'] for f in window.document.fields(0)}
    window.document.dirty = False
    assert initially_clean
    assert values['locked'] == 'Keep'
    assert values['check'] != 'Off'
    assert values['choice'] == 'Blue'


def test_radio_controls_are_exclusive_and_last_selection_persists(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from test_core import radio_pdf
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(radio_pdf(tmp_path))
    first, second = [widget for _, widget, _ in window.field_widgets]
    second.click()
    first.click()
    exclusive = first.isChecked() and not second.isChecked()
    window.commit_pending()
    fields = window.document.fields(0)
    states = [window.document.pdf.xref_get_key(f['xref'], 'AS')[1] for f in fields]
    window.document.dirty = False
    assert exclusive
    assert states == ['/Alpha', '/Off']


def test_text_preview_uses_committed_font_size(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    window.add_text_overlay(50, 80).setPlainText('Twelve point')
    window.commit_pending()
    spans = [span for block in window.document.pdf[0].get_text('dict')['blocks']
             if 'lines' in block for line in block['lines'] for span in line['spans']]
    window.document.dirty = False
    assert spans[0]['size'] == pytest.approx(12, abs=.1)


def test_undo_after_save_is_unsaved_work(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path))
    window.add_text_overlay(50, 80).setPlainText('Saved once')
    window.save_copy(tmp_path / 'first-copy.pdf')
    assert not window.has_changes()
    window.undo_edit()
    changed = window.has_changes()
    window.document.dirty = False
    assert changed


def test_replace_menu_preserves_mark_kind(qtbot, monkeypatch):
    from omapreview.ui import MainWindow
    from PySide6.QtWidgets import QToolButton
    window = MainWindow()
    qtbot.addWidget(window)
    calls = []
    monkeypatch.setattr(window, 'use_mark', lambda kind, replace=False: calls.append((kind, replace)))
    for kind in ('signature', 'initials'):
        button = next(b for b in window.toolbar.findChildren(QToolButton) if b.text() == kind.title())
        next(a for a in button.menu().actions() if a.text() == f'Replace saved {kind}…').trigger()
    assert calls == [('signature', True), ('initials', True)]


def test_blurred_text_field_does_not_double_paint_pdf_text(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from PySide6.QtCore import QRectF
    from PySide6.QtGui import QImage, QPainter
    window = MainWindow()
    qtbot.addWidget(window)
    window.open_file(make_pdf(tmp_path, fields=True))
    widget = window.field_widgets[0][1]
    widget.setText('Only the native appearance')
    window.commit_pending()
    image = widget.grab().toImage()
    expected = QImage(image.size(), QImage.Format_ARGB32)
    painter = QPainter(expected)
    scale = window.render_scale
    painter.drawPixmap(QRectF(expected.rect()), window.background_item.pixmap(), QRectF(40 * scale, 60 * scale, 200 * scale, 25 * scale))
    painter.end()
    window.document.dirty = False
    assert not widget.hasFocus()
    interior = (2, 2, image.width() - 4, image.height() - 4)
    assert image.copy(*interior).convertToFormat(QImage.Format_RGB32) == expected.copy(*interior).convertToFormat(QImage.Format_RGB32)


def test_focused_native_editor_navigation_and_focus_commit(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(make_pdf(tmp_path, fields=True))
    widget = window.field_widgets[0][1]
    widget.setFocus()
    qtbot.keyClicks(widget, 'Focused edit')
    assert window.go_page(1)
    assert window.document.fields(0)[0]['value'] == 'Focused edit'
    window.document.dirty = False


def test_text_overlay_drags_by_edge_before_commit(qtbot, tmp_path):
    from omapreview.ui import MainWindow
    from PySide6.QtCore import Qt, QPointF, QPoint
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(make_pdf(tmp_path))
    item = window.add_text_overlay(60, 80)
    item.setPlainText('Move me')
    start = window.view.mapFromScene(item.mapToScene(QPointF(1, 5)))
    qtbot.mousePress(window.view.viewport(), Qt.LeftButton, pos=start)
    qtbot.mouseMove(window.view.viewport(), start + QPoint(50, 35))
    qtbot.mouseRelease(window.view.viewport(), Qt.LeftButton, pos=start + QPoint(50, 35))
    moved = item.pos().x() > 60
    window.pending_items.clear()
    assert moved
