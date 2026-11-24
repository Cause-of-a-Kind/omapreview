"""Generated documents, real continuous-scroll and thumbnail gestures."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import pymupdf
import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QApplication
from omapreview.ui import MainWindow


def document(tmp_path, count=3):
    path = tmp_path / 'continuous.pdf'
    pdf = pymupdf.open()
    for i in range(count):
        page = pdf.new_page(width=360 + (i % 3)*60, height=480 + (i % 3)*90)
        page.insert_text((25, 35), f'PAGE {i+1}')
        widget = pymupdf.Widget()
        widget.field_name = f'Name{i}'
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        widget.rect = pymupdf.Rect(30, 60, 250, 90)
        page.add_widget(widget)
    pdf.save(path)
    pdf.close()
    return path


def opened(qtbot, tmp_path, count=3):
    w = MainWindow()
    w.confirm_leave = lambda: True
    qtbot.addWidget(w)
    w.resize(1000, 720)
    w.show()
    assert w.open_file(document(tmp_path, count))
    qtbot.wait(120)
    return w


def test_wheel_moves_continuous_surface_and_scrollbar_updates_current_page(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    assert hasattr(w, 'page_scroll'), 'Missing continuous document surface'
    surface = w.page_scroll
    assert len(surface.page_rects) == 3
    assert surface.page_rects[0].bottom() < surface.page_rects[1].top()
    assert surface.verticalScrollBar().maximum() > surface.page_rects[1].top()
    start = surface.verticalScrollBar().value()
    point = QPointF(20, 150)
    event = QWheelEvent(point, w.view.viewport().mapToGlobal(point.toPoint()), QPoint(), QPoint(0, -120), Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(w.view.viewport(), event)
    assert surface.verticalScrollBar().value() > start
    assert w.page_index == 0, 'A wheel notch must scroll pixels, not replace pages'
    surface.verticalScrollBar().setValue(int(surface.page_rects[1].top()))
    qtbot.waitUntil(lambda: w.page_index == 1)
    assert w.page_label.text().strip() == '2 / 3'
    assert w.view.parentWidget() is surface.canvas
    assert w.view.y() == int(surface.page_rects[1].top())


def test_thumbnail_click_jumps_and_scroll_tracks_highlight(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    assert hasattr(w, 'thumbnails'), 'Missing left page thumbnail navigation'
    rail = w.thumbnails
    assert rail.isVisible()
    assert rail.model().rowCount() == 3
    target = rail.model().index(2, 0)
    rail.scrollTo(target)
    qtbot.mouseClick(rail.viewport(), Qt.LeftButton, pos=rail.visualRect(target).center())
    assert w.page_index == 2
    assert rail.currentIndex().row() == 2
    assert w.page_scroll.verticalScrollBar().value() > w.page_scroll.page_rects[1].bottom()
    w.page_scroll.verticalScrollBar().setValue(0)
    qtbot.waitUntil(lambda: w.page_index == 0)
    assert rail.currentIndex().row() == 0


def test_leaving_edited_page_refreshes_thumbnail_and_saved_editability(qtbot, tmp_path):
    from omapreview.core import Document
    w = opened(qtbot, tmp_path)
    original = w.document.path.read_bytes()
    before = w.thumbnails.preview(0).toImage()
    field = w.field_widgets[0][1]
    field.setText('Continuous native field')
    item = w.add_text_overlay(35, 150)
    item.setPlainText('Reselect after scrolling')
    target = w.thumbnails.model().index(1, 0)
    qtbot.mouseClick(w.thumbnails.viewport(), Qt.LeftButton, pos=w.thumbnails.visualRect(target).center())
    assert w.page_index == 1
    assert w.thumbnails.preview(0).toImage() != before, 'Thumbnail still shows pre-edit page'
    assert w.go_page(0)
    assert w.field_widgets[0][1].text() == 'Continuous native field'
    assert len(w.pending_items) == 1
    reloaded = w.pending_items[0]
    point = w.view.mapFromScene(reloaded.sceneBoundingRect().center())
    qtbot.mouseClick(w.view.viewport(), Qt.LeftButton, pos=point)
    assert reloaded.isSelected()
    reloaded.setPos(55, 170)
    output = tmp_path / 'saved.pdf'
    assert w.save_copy(output)
    reopened = Document(output)
    assert reopened.fields(0)[0]['value'] == 'Continuous native field'
    assert reopened.marks(0)[0]['text'] == 'Reselect after scrolling'
    assert reopened.marks(0)[0]['rect'][0] == 55
    assert w.document.path.read_bytes() == original


def test_mixed_size_pages_keep_one_scale_and_zoom_updates_whole_surface(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    scale = w.view.transform().m11()
    rects = [QPointF(r.width(), r.height()) for r in w.page_scroll.page_rects]
    assert w.go_page(1)
    assert w.view.transform().m11() == scale, 'Page transitions must not rescale the document'
    assert [QPointF(r.width(), r.height()) for r in w.page_scroll.page_rects] == rects
    before = w.page_scroll.page_rects[1].height()
    w.zoom_by(1.25)
    assert abs(w.page_scroll.page_rects[1].height() / before - 1.25) < .01
    offset = 120
    w.page_scroll.verticalScrollBar().setValue(int(w.page_scroll.page_rects[1].top()) + offset)
    local_before = (w.page_scroll.verticalScrollBar().value() - w.page_scroll.page_rects[1].top()) / w.view.transform().m11()
    w.resize(1200, 760)
    qtbot.wait(150)
    assert w.view.transform().m11() == scale * 1.25
    local_after = (w.page_scroll.verticalScrollBar().value() - w.page_scroll.page_rects[1].top()) / w.view.transform().m11()
    assert abs(local_after - local_before) < 1


def test_scroll_to_end_selects_short_last_page(qtbot, tmp_path):
    path = tmp_path / 'short-last.pdf'
    pdf = pymupdf.open()
    for height in (800, 900, 100):
        pdf.new_page(width=400, height=height)
    pdf.save(path)
    pdf.close()
    w = MainWindow()
    w.confirm_leave = lambda: True
    qtbot.addWidget(w)
    w.show()
    assert w.open_file(path)
    qtbot.wait(120)
    bar = w.page_scroll.verticalScrollBar()
    bar.setValue(bar.maximum())
    assert w.page_index == 2, 'At document end the short final page must become editable'


@pytest.mark.parametrize('kind', ['native', 'text'])
def test_failed_commit_keeps_scroll_route_thumbnail_and_live_editors(qtbot, tmp_path, monkeypatch, kind):
    from PySide6.QtWidgets import QMessageBox
    w = opened(qtbot, tmp_path)
    warnings = []
    monkeypatch.setattr(QMessageBox, 'warning', lambda *args: warnings.append(args))
    original = w.document.path.read_bytes()
    if kind == 'native':
        editor = w.field_widgets[0][1]
        qtbot.mouseClick(w.view.viewport(), Qt.LeftButton,
                         pos=w.view.mapFromScene(w.field_widgets[0][0].sceneBoundingRect().center()))
        qtbot.keyClicks(editor, 'Keep failed field draft')
        failure_method = 'set_field'
    else:
        editor = w.add_text_overlay(30, 140)
        editor.setPlainText('Keep failed text draft')
        failure_method = 'add_text'
    def fail(*args, **kwargs):
        raise OSError('Generated commit failure')
    monkeypatch.setattr(w.document, failure_method, fail)
    scene = w.scene
    fields = w.field_widgets
    items = w.pending_items
    bar = w.page_scroll.verticalScrollBar()
    position = bar.value()
    bar.setValue(int(w.page_scroll.page_rects[1].top()))
    assert w.page_index == 0
    assert bar.value() == position
    assert w.scene is scene and w.field_widgets is fields and w.pending_items is items
    target = w.thumbnails.model().index(1, 0)
    qtbot.mouseClick(w.thumbnails.viewport(), Qt.LeftButton, pos=w.thumbnails.visualRect(target).center())
    assert w.page_index == 0
    assert w.thumbnails.currentIndex().row() == 0
    assert w.scene is scene
    assert w.has_changes()
    assert warnings
    assert w.document.path.read_bytes() == original


def test_large_document_lazily_renders_bounded_previews_and_resets(qtbot, tmp_path, monkeypatch):
    from omapreview.core import Document
    calls = []
    render = Document.render
    def observed(self, index, scale=1.5, include_marks=True):
        calls.append((index, scale))
        return render(self, index, scale=scale, include_marks=include_marks)
    monkeypatch.setattr(Document, 'render', observed)
    w = opened(qtbot, tmp_path, 60)
    assert len(set(index for index, scale in calls)) < 8
    assert len(calls) < 20
    assert len(w.page_scroll.cache) <= 4
    for index in range(60):
        pix = w.thumbnails.preview(index)
        assert pix.width() <= 111 and pix.height() <= 127
    assert len(w.thumbnails.cache) == 24
    for index in range(8):
        w.page_scroll.preview(index)
    assert len(w.page_scroll.cache) == 4
    assert w.go_page(59)
    other = tmp_path / 'other'
    other.mkdir()
    assert w.open_file(document(other, 2))
    qtbot.wait(120)
    assert len(w.page_scroll.page_rects) == 2
    assert w.thumbnails.model().rowCount() == 2
    assert w.page_index == w.thumbnails.currentIndex().row() == 0
    assert all(index < 2 for index in w.thumbnails.cache)
    assert all(key[0] < 2 for key in w.page_scroll.cache)
    assert w.page_scroll.document is w.document is w.thumbnails.document


def test_viewport_paints_multiple_real_pages_with_a_paper_gap(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    w.zoom_by(.4)
    w.page_scroll.verticalScrollBar().setValue(0)
    qtbot.wait(100)
    surface = w.page_scroll
    image = surface.viewport().grab().toImage()
    for index in (0, 1):
        rect = surface.page_rects[index]
        point = surface.canvas.mapTo(surface.viewport(), QPoint(int(rect.center().x()), int(rect.top() + 12)))
        assert image.rect().contains(point), 'Both pages must be visible in the same viewport'
        color = image.pixelColor(point)
        assert min(color.red(), color.green(), color.blue()) > 245
    gap = surface.canvas.mapTo(surface.viewport(), QPoint(int(surface.page_rects[0].center().x()),
                int(surface.page_rects[0].bottom() + 10)))
    assert image.pixelColor(gap).lightness() < 100


def test_real_wheel_crosses_pages_without_replacing_surface(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    canvas = w.page_scroll.canvas
    initial_scene = w.scene
    for _ in range(20):
        point = QPointF(15, 100)
        event = QWheelEvent(point, w.view.viewport().mapToGlobal(point.toPoint()), QPoint(), QPoint(0, -120),
                            Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
        QApplication.sendEvent(w.view.viewport(), event)
        if w.page_index > 0:
            break
    assert w.page_index == 1
    assert w.scene is not initial_scene
    assert w.page_scroll.canvas is canvas
    assert len(w.page_scroll.page_rects) == 3
    assert w.thumbnails.currentIndex().row() == 1


def test_context_row_height_changes_keep_document_scroll_anchor(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    w.go_page(1)
    bar = w.page_scroll.verticalScrollBar()
    bar.setValue(int(w.page_scroll.page_rects[1].top()) + 100)
    position = bar.value()
    scale = w.view.transform().m11()
    w.set_mode('text')
    qtbot.wait(180)
    assert w.text_toolbar.isVisible()
    assert w.view.transform().m11() == scale
    assert bar.value() == position
    w.set_mode('select')
    qtbot.wait(180)
    assert not w.text_toolbar.isVisible()
    assert w.view.transform().m11() == scale
    assert bar.value() == position


def test_radio_group_and_signature_survive_scroll_save_undo_snapshots(qtbot, tmp_path):
    from test_form_editing_regressions import multipage_radios, click_field
    from test_core import ink_png
    from omapreview.core import Document
    source, groups = multipage_radios(tmp_path)
    with pymupdf.open(source) as pdf:
        pdf.new_page(width=450, height=620)
        data = pdf.tobytes()
    source.write_bytes(data)
    original = source.read_bytes()
    w = MainWindow()
    w.confirm_leave = lambda: True
    qtbot.addWidget(w)
    w.show()
    assert w.open_file(source)
    qtbot.wait(120)
    click_field(qtbot, w, 0)
    signature = w.add_signature_overlay(60, 260, ink_png())
    signature.resize_to(130, 50)
    before = w.thumbnails.preview(0).toImage()
    w.page_scroll.verticalScrollBar().setValue(int(w.page_scroll.page_rects[1].top()))
    assert w.page_index == 1
    assert w.thumbnails.preview(0).toImage() != before
    click_field(qtbot, w, 0)
    assert w.document.pdf.xref_get_key(groups[0][1][0], 'AS')[1] == '/Off'
    assert w.document.pdf.xref_get_key(groups[0][1][1], 'AS')[1] == '/Option1'
    w.page_scroll.verticalScrollBar().setValue(0)
    assert not w.field_widgets[0][1].isChecked()
    restored = w.pending_items[0]
    qtbot.mouseClick(w.view.viewport(), Qt.LeftButton,
                     pos=w.view.mapFromScene(restored.sceneBoundingRect().center()))
    assert restored.isSelected()
    restored.setPos(100, 300)
    assert w.commit_pending()
    assert w.undo_edit()
    assert w.pending_items[0].pos() == QPointF(60, 260)
    output = tmp_path / 'scroll-saved.pdf'
    assert w.save_copy(output)
    reopened = Document(output)
    assert reopened.marks(0)[0]['kind'] == 'signature'
    assert reopened.marks(0)[0]['rect'][0] == 60
    assert reopened.pdf.xref_get_key(groups[0][1][1], 'AS')[1] == '/Option1'
    assert len(reopened.pdf[0].get_image_info()) == 1
    assert set(reopened.pdf[0].get_pixmap(clip=pymupdf.Rect(60, 260, 190, 310)).samples) != {255}
    assert source.read_bytes() == original


def test_empty_welcome_hides_page_rail(qtbot):
    w = MainWindow()
    qtbot.addWidget(w)
    w.show()
    assert not w.thumbnails.isVisible()
    assert not w.page_scroll.isVisible()


def test_scrollbar_keyboard_routes_to_last_and_first_page(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    bar = w.page_scroll.verticalScrollBar()
    qtbot.keyClick(bar, Qt.Key_End)
    assert w.page_index == 2
    assert w.thumbnails.currentIndex().row() == 2
    qtbot.keyClick(bar, Qt.Key_Home)
    assert w.page_index == 0
    assert w.thumbnails.currentIndex().row() == 0


def test_single_click_inactive_page_field_activates_and_edits(qtbot, tmp_path):
    w = opened(qtbot, tmp_path)
    w.zoom_by(.4)
    qtbot.wait(100)
    rect = w.page_scroll.page_rects[1]
    scale = w.view.transform().m11()
    point = QPoint(round(rect.x() + 18 + 70 * scale), round(rect.y() + 75 * scale))
    qtbot.mouseClick(w.page_scroll.canvas, Qt.LeftButton, pos=point)
    assert w.page_index == 1
    field = w.field_widgets[0][1]
    assert w.scene.focusItem() is w.field_widgets[0][0], 'The activating click must focus the native proxy'
    assert field.hasFocus(), 'Native editor must paint typed input, not its old PDF crop'
    assert w._font_target is field
    qtbot.keyClicks(w.view.viewport(), 'Clicked inactive page')
    assert field.text() == 'Clicked inactive page'
    assert w.go_page(2)
    assert w.document.fields(1)[0]['value'] == 'Clicked inactive page'


@pytest.mark.parametrize('kind', ['text', 'signature'])
def test_single_click_inactive_page_places_mark_on_that_page(qtbot, tmp_path, kind):
    from test_core import ink_png
    w = opened(qtbot, tmp_path)
    w.zoom_by(.4)
    if kind == 'text':
        w.set_mode('text')
    else:
        w.mark_bytes = ink_png()
        w.set_mode('signature')
    qtbot.wait(100)
    rect = w.page_scroll.page_rects[1]
    scale = w.view.transform().m11()
    point = QPoint(round(rect.x() + 18 + 50 * scale), round(rect.y() + 140 * scale))
    qtbot.mouseClick(w.page_scroll.canvas, Qt.LeftButton, pos=point)
    assert w.page_index == 1
    assert len(w.pending_items) == 1, 'The activating click must also place the mark'
    if kind == 'text':
        qtbot.keyClicks(w.view.viewport(), 'Placed on inactive page')
    assert w.go_page(0)
    assert len(w.document.marks(1)) == 1
    assert w.document.marks(1)[0]['kind'] == kind
    assert not w.document.marks(0)


def test_focused_scroll_scene_retirement_survives_in_subprocess(tmp_path):
    import subprocess
    import sys
    import textwrap
    code = textwrap.dedent('''
        import resource, sys
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        from pathlib import Path
        import pymupdf
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt, QEvent
        from PySide6.QtTest import QTest
        from omapreview.ui import MainWindow
        app = QApplication([])
        source = Path(sys.argv[1]) / 'lifetime.pdf'
        with pymupdf.open() as pdf:
            for index in range(3):
                page = pdf.new_page(width=400 + index*20, height=550 + index*30)
                field = pymupdf.Widget()
                field.field_name = str(index)
                field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
                field.rect = pymupdf.Rect(30, 60, 230, 90)
                page.add_widget(field)
            pdf.save(source)
        window = MainWindow()
        window.confirm_leave = lambda: True
        window.show()
        assert window.open_file(source)
        def settle():
            app.processEvents()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
        for cycle in range(20):
            native = window.field_widgets[0][1]
            native.setFocus()
            QTest.keyClicks(native, 'Draft ' + str(cycle))
            window.page_scroll.verticalScrollBar().setValue(int(window.page_scroll.page_rects[1].top()))
            assert window.page_index == 1
            settle()
            overlay = window.add_text_overlay(40, 180)
            QTest.keyClicks(window.view.viewport(), 'Owned ' + str(cycle))
            window.page_scroll.verticalScrollBar().setValue(0)
            assert window.page_index == 0
            settle()
            target = window.thumbnails.model().index(1, 0)
            QTest.mouseClick(window.thumbnails.viewport(), Qt.LeftButton,
                            pos=window.thumbnails.visualRect(target).center())
            assert window.page_index == 1
            window.pending_items[0].setSelected(True)
            window.delete_selected()
            assert window.undo_edit()
            settle()
            window.page_scroll.verticalScrollBar().setValue(0)
            settle()
        assert not window._retired_scenes
        window.close()
        settle()
        print('20 focused continuous scroll/thumbnail/undo cycles survived')
    ''')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '20 focused continuous scroll/thumbnail/undo cycles survived' in result.stdout


def test_wheel_over_inactive_choice_scrolls_without_changing_choice(qtbot, tmp_path):
    path = document(tmp_path)
    with pymupdf.open(path) as pdf:
        field = pymupdf.Widget()
        field.field_name = 'Choice'
        field.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
        field.choice_values = ['Alpha', 'Beta', 'Gamma']
        field.field_value = 'Alpha'
        field.rect = pymupdf.Rect(30, 110, 240, 140)
        pdf[1].add_widget(field)
        data = pdf.tobytes()
    path.write_bytes(data)
    w = MainWindow()
    w.confirm_leave = lambda: True
    qtbot.addWidget(w)
    w.show()
    assert w.open_file(path)
    qtbot.wait(100)
    w.zoom_by(.4)
    qtbot.wait(100)
    rect = w.page_scroll.page_rects[1]
    scale = w.view.transform().m11()
    point = QPointF(rect.x() + 18 + 70 * scale, rect.y() + 125 * scale)
    bar = w.page_scroll.verticalScrollBar()
    before = bar.value()
    event = QWheelEvent(point, w.page_scroll.canvas.mapToGlobal(point.toPoint()), QPoint(), QPoint(0, -120),
                        Qt.NoButton, Qt.NoModifier, Qt.NoScrollPhase, False)
    QApplication.sendEvent(w.page_scroll.canvas, event)
    assert bar.value() > before, 'Inactive paper must pass wheel scrolling to the document'
    assert next(f for f in w.document.fields(1) if f['name'] == 'Choice')['value'] == 'Alpha'
    assert not w.document.dirty
