"""Contextual text options and separated page controls use generated PDFs only."""
import pymupdf
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtWidgets import QToolBar
from omapreview.ui import MainWindow


def make_pdf(tmp_path):
    path = tmp_path / 'layout-source.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=450, height=600)
        field = pymupdf.Widget()
        field.field_name = 'Name'
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pymupdf.Rect(40, 65, 310, 100)
        page.add_widget(field)
        pdf.new_page(width=450, height=600)
        pdf.save(path)
    return path


@pytest.fixture
def window(qtbot, tmp_path):
    result = MainWindow()
    result.confirm_leave = lambda: True
    result.resize(1200, 850)
    qtbot.addWidget(result)
    result.show()
    assert result.open_file(make_pdf(tmp_path))
    return result


def test_text_size_row_only_appears_for_text_context(window, qtbot):
    assert hasattr(window, 'text_toolbar'), 'Text size needs its own contextual row'
    assert not window.text_toolbar.isVisible()
    assert window.font_size_input.parentWidget() is window.text_toolbar
    window.text_action.trigger()
    assert window.text_toolbar.isVisible()
    assert window.text_toolbar.geometry().top() > window.toolbar.geometry().top()
    window.font_size_input.setValue(18)
    window.set_mode('select')
    assert not window.text_toolbar.isVisible()
    mark = window.add_text_overlay(40, 170)
    mark.setPlainText('Selected text')
    assert window.text_toolbar.isVisible()
    assert window.font_size_input.value() == 18
    window.scene.clearFocus()
    window.scene.clearSelection()
    qtbot.waitUntil(lambda: not window.text_toolbar.isVisible())
    mark.setSelected(True)
    assert window.text_toolbar.isVisible()


def test_page_controls_are_last_and_right_aligned(window, qtbot):
    assert window.toolbar.actions()[-1] is window.next_action
    qtbot.wait(20)
    previous = window.toolbar.widgetForAction(window.previous_action)
    next_button = window.toolbar.widgetForAction(window.next_action)
    assert previous.geometry().left() > window.toolbar.width() * .75
    assert next_button.geometry().right() >= window.toolbar.width() - 24
    assert window.page_label.text().strip() == '1 / 2'


def test_native_field_row_stays_for_size_editing_then_hides_on_blank_click(window, qtbot):
    proxy, field, _ = window.field_widgets[0]
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(proxy.mapToScene(QPointF(15, 15))))
    assert field.hasFocus()
    assert window.text_toolbar.isVisible()
    editor = window.font_size_input.lineEdit()
    qtbot.mouseClick(editor, Qt.LeftButton)
    qtbot.keyClick(editor, Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClicks(editor, '16')
    qtbot.keyClick(editor, Qt.Key_Return)
    assert window.text_toolbar.isVisible()
    assert window.document.fields(0)[0]['font_size'] == 16
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(QPointF(390, 400)))
    qtbot.waitUntil(lambda: not window.text_toolbar.isVisible(), timeout=1000)


def test_native_field_row_hides_on_direct_field_to_blank_click(window, qtbot):
    proxy, field, _ = window.field_widgets[0]
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(proxy.mapToScene(QPointF(15, 15))))
    assert field.hasFocus() and window.text_toolbar.isVisible()
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(QPointF(390, 400)))
    qtbot.waitUntil(lambda: not window.text_toolbar.isVisible(), timeout=1000)


def test_page_jump_clears_text_options_without_losing_the_mark(window, qtbot):
    mark = window.add_text_overlay(40, 170)
    mark.setPlainText('Keep this on page one')
    assert window.text_toolbar.isVisible()
    window.next_action.trigger()
    assert window.page_index == 1
    assert not window.text_toolbar.isVisible()
    assert window.document.marks(0)[0]['text'] == 'Keep this on page one'
    window.previous_action.trigger()
    assert window.pending_items[0].toPlainText() == 'Keep this on page one'
    assert not window.text_toolbar.isVisible()
