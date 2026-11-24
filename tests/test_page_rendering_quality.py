"""Page previews must rasterize for their physical display size, not upscale 1.5×."""
import math
import pymupdf
from PySide6.QtCore import Qt
from omapreview.ui import MainWindow


def make_pdf(tmp_path, fields=False):
    path = tmp_path / 'sharp-source.pdf'
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=500)
    page.insert_text((30, 35), 'Fine PDF text and diagonal rules', fontsize=8)
    page.draw_line((25, 55), (350, 58), width=.35)
    if fields:
        field = pymupdf.Widget()
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.field_name = 'Name'
        field.field_value = 'Filled text'
        field.rect = pymupdf.Rect(30, 80, 220, 110)
        page.add_widget(field)
    pdf.save(path)
    pdf.close()
    return path


def test_fit_width_raster_matches_physical_view_size(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(1200, 850)
    window.show()
    qtbot.waitExposed(window)
    assert window.open_file(make_pdf(tmp_path))
    required = math.ceil(400 * window.view.transform().m11() * window.view.devicePixelRatioF())
    assert window.background_item.pixmap().width() >= required - 1
    assert window.background_item.transformationMode() == Qt.SmoothTransformation


def test_zoom_rerasterizes_page_without_touching_fields_or_annotations(qtbot, tmp_path):
    window = MainWindow()
    window.confirm_leave = lambda: True
    qtbot.addWidget(window)
    window.show()
    source = make_pdf(tmp_path, fields=True)
    original = source.read_bytes()
    window.open_file(source)
    item = window.add_text_overlay(30, 160)
    item.setPlainText('Keep this editable')
    window.commit_pending()
    identity = item.mark_id
    before_width = window.background_item.pixmap().width()
    before_rect = item.sceneBoundingRect()
    field = window.field_widgets[0][1]
    before_field = field.text()
    next(action for action in window.toolbar.actions() if action.text() == '+').trigger()
    assert window.background_item.pixmap().width() > before_width
    assert window.pending_items[0] is item
    assert item.mark_id == identity
    assert item.sceneBoundingRect() == before_rect
    assert field.text() == before_field
    assert field.source_rect.width() == 190 * window.render_scale
    assert source.read_bytes() == original
    window.confirm_leave = lambda: True


def test_extreme_zoom_bounds_raster_allocation_before_rendering(qtbot, tmp_path, monkeypatch):
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(make_pdf(tmp_path))
    native_render = window.document.render
    observed = []
    def guarded_render(page_index, scale=1.5, include_marks=True):
        rect = window.document.pdf[page_index].rect
        pixels = math.ceil(rect.width * scale) * math.ceil(rect.height * scale)
        assert pixels <= 24_000_000, 'Page raster allocation is unbounded at high zoom'
        observed.append(scale)
        return native_render(page_index, scale=scale, include_marks=include_marks)
    monkeypatch.setattr(window.document, 'render', guarded_render)
    window.zoom_by(50)
    assert observed
    assert window.background_item.pixmap().width() * window.background_item.pixmap().height() <= 24_000_000


def test_fit_width_refreshes_when_window_grows(qtbot, tmp_path):
    window = MainWindow()
    qtbot.addWidget(window)
    window.resize(850, 650)
    window.show()
    window.open_file(make_pdf(tmp_path))
    width = window.background_item.pixmap().width()
    window.resize(1400, 900)
    qtbot.waitUntil(lambda: window.background_item.pixmap().width() > width, timeout=2000)
    expected = (window.view.viewport().width() - 36) / 400
    assert abs(window.view.transform().m11() - expected) < .02


def test_high_dpi_change_uses_physical_pixels_without_resizing_fields(qtbot, tmp_path, monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(make_pdf(tmp_path, fields=True))
    native = window.field_widgets[0][0]
    field_rect = native.geometry()
    before = window.background_item.pixmap().width()
    monkeypatch.setattr(window.view.viewport(), 'devicePixelRatioF', lambda: 2.)
    QCoreApplication.sendEvent(window.view, QEvent(QEvent.DevicePixelRatioChange))
    qtbot.waitUntil(lambda: window.background_item.pixmap().width() >= before * 1.9, timeout=2000)
    assert native.geometry() == field_rect
    assert abs(window.field_widgets[0][1].source_rect.x() - 30 * window.render_scale) < .01
