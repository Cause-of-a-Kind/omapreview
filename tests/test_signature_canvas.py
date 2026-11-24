import pytest
from PySide6.QtCore import Qt, QPoint
from PySide6.QtGui import QImage


def draw(qtbot, canvas, points):
    canvas.show()
    qtbot.mousePress(canvas, Qt.LeftButton, pos=QPoint(*points[0]))
    for point in points[1:-1]:
        qtbot.mouseMove(canvas, QPoint(*point))
    qtbot.mouseRelease(canvas, Qt.LeftButton, pos=QPoint(*points[-1]))


def test_signature_curves_round_sample_corners_and_preserve_endpoints(qtbot):
    from omapreview.signature_canvas import DrawCanvas
    canvas = DrawCanvas()
    qtbot.addWidget(canvas)
    points = [(20, 100), (100, 20), (180, 100), (260, 20)]
    draw(qtbot, canvas, points)
    # Raw polylines hit the sharp intermediate vertex; a smoothed curve does not.
    assert canvas.image.pixelColor(100 * 3, 20 * 3).alpha() == 0
    for x, y in (points[0], points[-1]):
        assert canvas.image.pixelColor(x * 3, y * 3).alpha() > 0
    exported = QImage.fromData(canvas.png_bytes(), 'PNG')
    assert not exported.isNull()
    assert exported.width() > 700  # Supersampled, tightly cropped transparent PNG.
    assert exported.pixelColor(0, 0).alpha() == 0


def test_clear_and_separate_strokes_never_join(qtbot):
    from omapreview.signature_canvas import DrawCanvas
    canvas = DrawCanvas()
    qtbot.addWidget(canvas)
    draw(qtbot, canvas, [(30, 60), (80, 70)])
    draw(qtbot, canvas, [(300, 130), (350, 140)])
    assert canvas.image.pixelColor(180 * 3, 100 * 3).alpha() == 0
    canvas.clear()
    assert not canvas.has_ink
    assert canvas.png_bytes() is None
    draw(qtbot, canvas, [(100, 100), (100, 100)])
    assert canvas.png_bytes() is not None
    assert canvas.image.pixelColor(30 * 3, 60 * 3).alpha() == 0


def test_import_image_and_add_stroke_preserves_both(qtbot):
    from omapreview.signature_canvas import DrawCanvas
    from PySide6.QtGui import QColor
    canvas = DrawCanvas()
    qtbot.addWidget(canvas)
    imported = QImage(600, 200, QImage.Format_ARGB32)
    imported.fill(Qt.transparent)
    imported.setPixelColor(50, 50, QColor('black'))
    canvas.set_image(imported)
    assert canvas.png_bytes() is not None
    before = canvas.image.pixelColor(50 * 3, 50 * 3)
    draw(qtbot, canvas, [(300, 100), (400, 100)])
    assert canvas.image.pixelColor(50 * 3, 50 * 3) == before
    assert canvas.image.pixelColor(350 * 3, 100 * 3).alpha() > 0
