"""Adjacent visible pages must not reintroduce low-resolution PDF upscaling."""
import math
import pymupdf
from omapreview.ui import MainWindow


def test_inactive_page_preview_matches_current_display_density(qtbot, tmp_path):
    source = tmp_path / 'two-pages.pdf'
    with pymupdf.open() as pdf:
        for _ in range(2):
            page = pdf.new_page(width=400, height=500)
            page.insert_text((35, 55), 'Small text stays clear while scrolling', fontsize=8)
        pdf.save(source)
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(source)
    window.zoom_by(1.5)
    density = window.view.viewport().devicePixelRatioF()
    required = math.ceil(400 * abs(window.view.transform().m11()) * density)
    preview = window.page_scroll.preview(1)
    assert preview.width() >= required - 1
    assert len(window.page_scroll.cache) <= 4
