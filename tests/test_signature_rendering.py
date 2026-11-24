"""Actual pixel-level signature resampling checks, with generated ink only."""
from PySide6.QtCore import QBuffer, QIODevice, QRectF, Qt
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from omapreview.ui import SignatureOverlay


def ink_png():
    ink = QImage(900, 300, QImage.Format_ARGB32_Premultiplied)
    ink.fill(Qt.transparent)
    painter = QPainter(ink)
    painter.setRenderHint(QPainter.Antialiasing)
    painter.setPen(QPen(QColor('#151515'), 7.2, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(25, 220, 870, 45)
    painter.end()
    buffer = QBuffer()
    buffer.open(QIODevice.WriteOnly)
    ink.save(buffer, 'PNG')
    return bytes(buffer.data())


def render(item, *, reference=None):
    image = QImage(230, 110, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.white)
    painter = QPainter(image)
    painter.translate(5.3, 3.7)
    painter.scale(1.37, 1.37)
    if reference is None:
        item.paint(painter, None)
    else:
        painter.setRenderHint(QPainter.SmoothPixmapTransform, reference)
        painter.drawPixmap(item.boundingRect(), item.pixmap, QRectF(item.pixmap.rect()))
    painter.end()
    return image


def test_placed_signature_uses_smooth_resampling_at_fractional_scale(qapp):
    item = SignatureOverlay(ink_png())
    item.resize_to(133.3, 44.4)
    actual = render(item)
    smooth = render(item, reference=True)
    nearest = render(item, reference=False)
    assert smooth != nearest  # This fixture detects nearest-neighbor grain.
    assert actual == smooth


def test_saved_pdf_requests_smooth_signature_image_interpolation(tmp_path, qapp):
    import pymupdf
    from omapreview.core import Document
    source = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    pdf.new_page(width=300, height=300)
    pdf.save(source)
    pdf.close()
    document = Document(source)
    document.add_signature(0, [30, 80, 163.3, 124.43], ink_png())
    saved = tmp_path / 'saved.pdf'
    document.save_copy(saved)
    with pymupdf.open(saved) as reopened:
        images = reopened[0].get_images()
        assert len(images) == 1
        xref, smask = images[0][:2]
        assert reopened.xref_get_key(xref, 'Interpolate') == ('bool', 'true')
        if smask:
            assert reopened.xref_get_key(smask, 'Interpolate') == ('bool', 'true')
