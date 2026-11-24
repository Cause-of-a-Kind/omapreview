"""Generated forms: real Qt gestures plus saved PDF values and raster proofs."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pymupdf
import pytest
from PySide6.QtCore import Qt, QPointF
from PySide6.QtWidgets import QMessageBox
from omapreview.core import Document
from omapreview.ui import MainWindow


@pytest.fixture
def window(qtbot, monkeypatch):
    monkeypatch.setattr(QMessageBox, 'question', lambda *a, **kw: QMessageBox.Discard)
    # A failed commit must fail the assertion, not leave a modal running forever.
    errors = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *a, **kw: errors.append(a))
    result = MainWindow()
    result.form_errors = errors
    qtbot.addWidget(result)
    result.show()
    return result


def basic_form(tmp_path):
    path = tmp_path / 'generated-form.pdf'
    with pymupdf.open() as pdf:
        page = pdf.new_page(width=400, height=500)
        for name, kind, value, rect in [
            ('check', pymupdf.PDF_WIDGET_TYPE_CHECKBOX, 'Yes', (40, 40, 62, 62)),
            ('text', pymupdf.PDF_WIDGET_TYPE_TEXT, 'Erase all of me', (40, 90, 260, 120)),
        ]:
            w = pymupdf.Widget()
            w.field_name, w.field_type, w.field_value = name, kind, value
            w.rect = pymupdf.Rect(rect)
            page.add_widget(w)
        pdf.new_page(width=400, height=500)
        pdf.save(path)
    return path


def click_field(qtbot, window, index):
    proxy, widget, _ = window.field_widgets[index]
    # Hit the actual view/proxy, not button.click() or a direct model mutation.
    point = window.view.mapFromScene(proxy.mapToScene(QPointF(8, 10)))
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton, pos=point)
    qtbot.wait(5)
    return widget


def raster(pdf, page=0, rect=(42, 42, 60, 60)):
    return pdf[page].get_pixmap(clip=pymupdf.Rect(rect), matrix=pymupdf.Matrix(2, 2)).samples


def multipage_radios(tmp_path, no_toggle=False):
    path = tmp_path / 'multipage-radios.pdf'
    with pymupdf.open() as pdf:
        pdf.new_page(width=400, height=500)
        pdf.new_page(width=400, height=500)
        groups = []
        for number, pages in enumerate(((0, 1), (0, 0))):
            refs = []
            for index, page_index in enumerate(pages):
                page = pdf[page_index]
                w = pymupdf.Widget()
                w.field_name = 'Same visible label'
                w.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
                w.rect = pymupdf.Rect(40 + index * 50, 160 + number * 60,
                                      62 + index * 50, 182 + number * 60)
                ref = page.add_widget(w).xref
                refs.append(ref)
                on = pdf.xref_get_key(ref, 'AP/N/Yes')[1]
                off = pdf.xref_get_key(ref, 'AP/N/Off')[1]
                pdf.xref_set_key(ref, 'AP/N', f'<< /Off {off} /Option{index} {on} >>')
            parent = pdf.get_new_xref()
            flags = 32768 | (16384 if no_toggle else 0)
            kids = ' '.join(f'{ref} 0 R' for ref in refs)
            pdf.update_object(parent, f'<< /FT /Btn /Ff {flags} /T (Same visible label) /V /Off /Kids [{kids}] >>')
            for ref in refs:
                pdf.xref_set_key(ref, 'Parent', f'{parent} 0 R')
                pdf.xref_set_key(ref, 'Ff', str(flags))
                pdf.xref_set_key(ref, 'T', 'null')
                pdf.xref_set_key(ref, 'V', 'null')
            groups.append((parent, refs))
        pdf.xref_set_key(pdf.pdf_catalog(), 'AcroForm/Fields', '[' + ' '.join(f'{p} 0 R' for p, _ in groups) + ']')
        pdf.save(path)
    return path, groups


@pytest.mark.parametrize('no_toggle', [False, True])
def test_radio_group_identity_across_pages_and_duplicate_labels(window, qtbot, tmp_path, no_toggle):
    source, groups = multipage_radios(tmp_path, no_toggle)
    original = source.read_bytes()
    assert window.open_file(source)
    (parent, refs), (other_parent, other_refs) = groups
    def click_ref(ref):
        index = next(i for i, (_, _, f) in enumerate(window.field_widgets) if f['xref'] == ref)
        click_field(qtbot, window, index)
    click_ref(other_refs[0])
    for cycle in range(3):
        click_ref(refs[0])
        assert window.go_page(1)
        assert not window.field_widgets[0][1].isChecked()
        click_ref(refs[1])
        assert window.document.pdf.xref_get_key(refs[0], 'AS')[1] == '/Off'
        assert window.document.pdf.xref_get_key(refs[1], 'AS')[1] == '/Option1'
        assert window.go_page(0)
        assert not window.field_widgets[0][1].isChecked()
        click_ref(refs[0])
        click_ref(refs[0])
        assert window.document.pdf.xref_get_key(parent, 'V')[1] == '/Off'
        assert window.document.pdf.xref_get_key(other_parent, 'V')[1] == '/Option0'
        assert [window.document.pdf.xref_get_key(ref, 'AS')[1] for ref in refs] == ['/Off', '/Off']
        assert set(raster(window.document.pdf, page=0, rect=(40, 160, 112, 182))) == {255}
        assert set(raster(window.document.pdf, page=1, rect=(40, 160, 112, 182))) == {255}
        output = tmp_path / f'multipage-cleared-{cycle}.pdf'
        assert window.save_copy(output)
        reopened = Document(output)
        assert reopened.pdf.xref_get_key(parent, 'V')[1] == '/Off'
        assert reopened.pdf.xref_get_key(other_parent, 'V')[1] == '/Option0'
        for page_index in (0, 1):
            assert raster(reopened.pdf, page=page_index, rect=(40, 160, 112, 182)) == raster(window.document.pdf, page=page_index, rect=(40, 160, 112, 182))
    assert not window.form_errors
    assert source.read_bytes() == original


def test_readonly_fields_refuse_mouse_keyboard_and_core_updates(window, qtbot, tmp_path):
    source = basic_form(tmp_path)
    with pymupdf.open(source) as pdf:
        for w in pdf[0].widgets():
            parent = pdf.xref_get_key(w.xref, 'Parent')
            ref = int(parent[1].split()[0]) if parent[0] == 'xref' else w.xref
            pdf.xref_set_key(ref, 'Ff', '1')
        source.write_bytes(pdf.tobytes())
    assert window.open_file(source)
    before = window.document.pdf.tobytes(no_new_id=True)
    for index, (_, widget, field) in enumerate(window.field_widgets):
        assert field['readonly'] and not widget.isEnabled()
        click_field(qtbot, window, index)
        qtbot.keyClick(window.view.viewport(), Qt.Key_Backspace)
        with pytest.raises(ValueError, match='not editable'):
            window.document.set_field(0, field['xref'], False if index == 0 else '')
    assert window.commit_pending()
    assert window.document.pdf.tobytes(no_new_id=True) == before
    assert not window.form_errors


def test_checkbox_clicks_apply_each_on_off_cycle_immediately(window, qtbot, tmp_path):
    source = basic_form(tmp_path)
    original = source.read_bytes()
    assert window.open_file(source)
    checked_pixels = raster(window.document.pdf)
    for cycle in range(3):
        click_field(qtbot, window, 0)
        assert window.document.fields(0)[0]['value'] == 'Off'
        assert raster(window.document.pdf) != checked_pixels
        output = tmp_path / f'checkbox-off-{cycle}.pdf'
        assert window.save_copy(output)
        with pymupdf.open(output) as pdf:
            w = next(pdf[0].widgets())
            assert w.field_value == 'Off'
            assert pdf.xref_get_key(w.xref, 'AS')[1] == '/Off'
            assert raster(pdf) == raster(window.document.pdf)
        click_field(qtbot, window, 0)
        assert window.document.fields(0)[0]['value'] == 'Yes'
        assert raster(window.document.pdf) == checked_pixels
    assert not window.form_errors
    assert source.read_bytes() == original


def test_native_text_exact_empty_keyboard_blur_navigation_save_undo(window, qtbot, tmp_path):
    source = basic_form(tmp_path)
    original = source.read_bytes()
    assert window.open_file(source)
    refs = [f['xref'] for f in window.document.fields(0)]
    field = click_field(qtbot, window, 1)
    qtbot.keyClick(field, Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClick(field, Qt.Key_Backspace)
    assert field.text() == ''
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(QPointF(320, 350)))
    qtbot.wait(10)
    assert window.commit_pending()
    assert window.document.fields(0)[1]['value'] == ''
    rect = (42, 92, 258, 118)
    assert set(raster(window.document.pdf, rect=rect)) == {255}
    assert window.go_page(1) and window.go_page(0)
    assert window.field_widgets[1][1].text() == ''
    image = window.field_widgets[1][1].grab().toImage()
    assert all(image.pixelColor(x, y).lightness() > 245
               for x in range(5, image.width() - 5) for y in range(5, image.height() - 5))
    output = tmp_path / 'empty-native.pdf'
    assert window.save_copy(output)
    reopened = Document(output)
    assert [f['xref'] for f in reopened.fields(0)] == refs
    assert reopened.fields(0)[1]['value'] == ''
    assert set(raster(reopened.pdf, rect=rect)) == {255}
    assert window.undo_edit()
    assert window.field_widgets[1][1].text() == 'Erase all of me'
    assert source.read_bytes() == original
    assert not window.form_errors


def test_owned_text_clear_all_keeps_empty_editable_box_and_undo(window, qtbot, tmp_path):
    source = basic_form(tmp_path)
    original = source.read_bytes()
    assert window.open_file(source)
    item = window.add_text_overlay(50, 180)
    qtbot.keyClicks(window.view.viewport(), 'Remove every character')
    assert window.commit_pending()
    identity = item.mark_id
    qtbot.keyClick(window.view.viewport(), Qt.Key_A, modifier=Qt.ControlModifier)
    qtbot.keyClick(window.view.viewport(), Qt.Key_Backspace)
    assert item.toPlainText() == ''
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(QPointF(320, 350)))
    qtbot.wait(10)
    assert window.commit_pending()
    marks = window.document.marks(0)
    assert len(marks) == 1 and marks[0]['id'] == identity and marks[0]['text'] == ''
    assert set(raster(window.document.pdf, rect=(50, 180, 250, 216))) == {255}
    assert window.go_page(1) and window.go_page(0)
    assert window.pending_items[0].toPlainText() == ''
    output = tmp_path / 'empty-owned.pdf'
    assert window.save_copy(output)
    reopened = Document(output)
    assert reopened.marks(0)[0]['text'] == ''
    assert set(raster(reopened.pdf, rect=(50, 180, 250, 216))) == {255}
    assert window.undo_edit()
    assert window.pending_items[0].toPlainText() == 'Remove every character'
    assert source.read_bytes() == original
    assert not window.form_errors


def test_selected_owned_text_delete_after_native_focus_removes_box_and_undo(window, qtbot, tmp_path):
    source = basic_form(tmp_path)
    assert window.open_file(source)
    item = window.add_text_overlay(50, 180)
    qtbot.keyClicks(window.view.viewport(), 'Delete the whole box')
    assert window.commit_pending()
    click_field(qtbot, window, 1)
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(QPointF(75, 190)))
    assert item.isSelected()
    assert item.textInteractionFlags() == Qt.NoTextInteraction
    qtbot.keyClick(window.view.viewport(), Qt.Key_Delete)
    qtbot.wait(5)
    assert not window.pending_items and not window.document.marks(0)
    assert window.document.fields(0)[1]['value'] == 'Erase all of me'
    assert set(raster(window.document.pdf, rect=(50, 180, 250, 216))) == {255}
    output = tmp_path / 'deleted-owned.pdf'
    assert window.save_copy(output)
    assert not Document(output).marks(0)
    assert window.undo_edit()
    assert window.pending_items[0].toPlainText() == 'Delete the whole box'
    assert not window.form_errors


def test_radio_switch_clear_cycles_follow_appearance_not_parent_value(window, qtbot, tmp_path):
    from test_core import radio_pdf
    source = radio_pdf(tmp_path)
    with pymupdf.open(source) as pdf:
        refs = [w.xref for w in pdf[0].widgets()]
        parent = int(pdf.xref_get_key(refs[0], 'Parent')[1].split()[0])
        pdf.xref_set_key(refs[0], 'AS', '/Alpha')
        pdf.xref_set_key(parent, 'V', '/Alpha')
        source.write_bytes(pdf.tobytes())
    original = source.read_bytes()
    assert window.open_file(source)
    assert [w.isChecked() for _, w, _ in window.field_widgets] == [True, False]
    expected = ['Alpha', 'Beta']
    for cycle in range(3):
        for step, (index, selected) in enumerate(((1, 1), (0, 0), (0, None))):
            click_field(qtbot, window, index)
            assert [w.isChecked() for _, w, _ in window.field_widgets] == [selected == i for i in range(2)]
            states = ['/' + expected[i] if i == selected else '/Off' for i in range(2)]
            assert [window.document.pdf.xref_get_key(r, 'AS')[1] for r in refs] == states
            assert window.document.pdf.xref_get_key(parent, 'V')[1] == ('/Off' if selected is None else states[selected])
            output = tmp_path / f'radio-{cycle}-{index}-{selected}.pdf'
            assert window.save_copy(output)
            with pymupdf.open(output) as pdf:
                assert [pdf.xref_get_key(r, 'AS')[1] for r in refs] == states
                assert raster(pdf, rect=(30, 120, 100, 140)) == raster(window.document.pdf, rect=(30, 120, 100, 140))
        assert not any(w.isChecked() for _, w, _ in window.field_widgets)
        assert window.go_page(0)
        assert not any(w.isChecked() for _, w, _ in window.field_widgets)
        click_field(qtbot, window, 0)
    assert window.undo_edit()
    assert not any(w.isChecked() for _, w, _ in window.field_widgets)
    assert not window.form_errors
    assert source.read_bytes() == original
