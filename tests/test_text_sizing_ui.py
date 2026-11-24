"""Point-sized text controls exercise real UI selection and saved PDF output."""
import pymupdf
import pytest
from PySide6.QtCore import Qt, QPointF
from omapreview.ui import MainWindow


def make_pdf(tmp_path, font_size=0):
    path = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    page = pdf.new_page(width=500, height=650)
    field = pymupdf.Widget()
    field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    field.field_name = 'Name'
    field.field_value = 'Ada'
    field.text_fontsize = font_size
    field.rect = pymupdf.Rect(40, 55, 340, 105)
    page.add_widget(field)
    pdf.save(path)
    pdf.close()
    return path


@pytest.fixture
def window(qtbot, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args, **kwargs: errors.append(args))
    result = MainWindow()
    result.confirm_leave = lambda: True
    qtbot.addWidget(result)
    result.show()
    yield result
    assert not errors, errors


def test_selected_annotation_size_persists_and_restores_after_reopen(window, qtbot, tmp_path):
    source = make_pdf(tmp_path)
    original = source.read_bytes()
    assert window.open_file(source)
    item = window.add_text_overlay(40, 160, 220, 80)
    item.setPlainText('Readable annotation')
    item.setSelected(True)
    assert hasattr(window, 'font_size_input'), 'Text size control is missing'
    assert window.font_size_input.isEnabled()
    window.font_size_input.setValue(20)
    assert window.commit_pending()
    assert item.text_size == 20
    assert window.document.marks(0)[0]['font_size'] == 20
    output = tmp_path / 'sized.pdf'
    assert window.save_copy(output)
    with pymupdf.open(output) as saved:
        spans = [span for block in saved[0].get_text('dict')['blocks'] if 'lines' in block
                 for line in block['lines'] for span in line['spans']]
        assert any('Readable annotation' in span['text'] and span['size'] == pytest.approx(20)
                   for span in spans)
    assert window.open_file(output)
    restored = window.pending_items[0]
    assert restored.text_size == 20
    restored.setSelected(True)
    assert window.font_size_input.value() == 20
    assert source.read_bytes() == original


def test_native_field_size_control_matches_saved_appearance(window, qtbot, tmp_path):
    source = make_pdf(tmp_path)
    window.open_file(source)
    proxy, field, metadata = window.field_widgets[0]
    point = window.view.mapFromScene(proxy.mapToScene(QPointF(20, 15)))
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton, pos=point)
    assert field.hasFocus()
    assert window.font_size_input.isEnabled()
    assert window.font_size_input.value() <= 12
    window.font_size_input.setValue(18)
    field.setText('Readable form field')
    assert window.commit_pending()
    assert window.document.fields(0)[0]['font_size'] == 18
    output = tmp_path / 'sized-field.pdf'
    assert window.save_copy(output)
    with pymupdf.open(output) as saved:
        assert next(saved[0].widgets()).text_fontsize == 18
    assert window.open_file(output)
    proxy, field, metadata = window.field_widgets[0]
    assert metadata['font_size'] == 18
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(proxy.mapToScene(QPointF(20, 15))))
    assert window.font_size_input.value() == 18


def test_mouse_selected_annotation_size_changes_only_that_box_and_undo(window, qtbot, tmp_path):
    window.open_file(make_pdf(tmp_path, font_size=10))
    first = window.add_text_overlay(40, 160, 200, 70)
    first.setPlainText('First box')
    second = window.add_text_overlay(40, 300, 200, 70)
    second.setPlainText('Second box')
    window.commit_pending()
    window.scene.clearFocus()
    window.scene.clearSelection()
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(first.mapToScene(QPointF(15, 15))))
    assert first.isSelected() and not second.isSelected()
    editor = window.font_size_input.lineEdit()
    qtbot.mouseClick(editor, Qt.LeftButton)
    qtbot.keyClick(editor, Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClicks(editor, '22')
    qtbot.keyClick(editor, Qt.Key_Return)
    assert first.text_size == 22
    assert second.text_size == 12
    assert window.document.fields(0)[0]['font_size'] == 10
    assert window.undo_edit()
    assert sorted(mark['font_size'] for mark in window.document.marks(0)) == [12, 12]
    assert all(item.text_size == 12 for item in window.pending_items)


def test_text_tool_can_set_default_size_before_drawing_a_box(window, qtbot, tmp_path):
    window.open_file(make_pdf(tmp_path))
    window.text_action.trigger()
    assert window.font_size_input.isEnabled()
    window.font_size_input.setValue(24)
    first = window.view.mapFromScene(QPointF(50, 170))
    last = window.view.mapFromScene(QPointF(300, 250))
    qtbot.mousePress(window.view.viewport(), Qt.LeftButton, pos=first)
    qtbot.mouseMove(window.view.viewport(), last)
    qtbot.mouseRelease(window.view.viewport(), Qt.LeftButton, pos=last)
    qtbot.keyClicks(window.view.viewport(), 'Large enough to read')
    assert window.commit_pending()
    assert window.pending_items[0].text_size == 24
    assert window.document.marks(0)[0]['font_size'] == 24


def test_native_auto_size_stays_consistent_during_typing_blur_and_manual_change(window, qtbot, tmp_path):
    window.open_file(make_pdf(tmp_path))
    proxy, field, _ = window.field_widgets[0]
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(proxy.mapToScene(QPointF(20, 15))))
    qtbot.keyClick(field, Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClicks(field, 'Grace Hopper')
    before = field.editor_size
    assert 1 <= before <= 12
    assert field.font().pointSizeF() * field.logicalDpiY() / 72 == pytest.approx(before)
    editor = window.font_size_input.lineEdit()
    qtbot.mouseClick(editor, Qt.LeftButton)
    qtbot.wait(5)
    assert window.document.fields(0)[0]['font_size'] == pytest.approx(before)
    qtbot.keyClick(editor, Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClicks(editor, '16')
    qtbot.keyClick(editor, Qt.Key_Return)
    assert window.document.fields(0)[0]['font_size'] == 16
    assert field.editor_size == 16
    assert field.text() == 'Grace Hopper'


def test_explicit_native_size_is_not_silently_changed_in_editor(window, qtbot, tmp_path):
    window.open_file(make_pdf(tmp_path))
    proxy, field, _ = window.field_widgets[0]
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(proxy.mapToScene(QPointF(20, 15))))
    window.font_size_input.setValue(60)
    assert window.document.fields(0)[0]['font_size'] == 60
    assert field.editor_size == 60
    assert window.font_size_input.value() == 60
