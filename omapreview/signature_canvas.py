"""Smooth, high-resolution local signature capture without changing handwriting."""
from PySide6.QtCore import Qt, QPointF, QBuffer, QIODevice, QRectF
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QBitmap, QRegion
from PySide6.QtWidgets import QWidget


class DrawCanvas(QWidget):
    """Midpoint quadratic strokes, rendered at 3× for clean signature placement."""

    SCALE = 3

    def __init__(self):
        super().__init__()
        self.setFixedSize(600, 200)
        self.setAccessibleName('Draw your signature')
        self.setAttribute(Qt.WA_StaticContents)
        self.image = QImage(600 * self.SCALE, 200 * self.SCALE,
                            QImage.Format_ARGB32_Premultiplied)
        self.clear()

    def clear(self):
        self.image.fill(Qt.transparent)
        self._base = self.image.copy()
        self._strokes = []
        self._active = None
        self.last_point = None
        self.has_ink = False
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), Qt.white)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawImage(QRectF(self.rect()), self.image)

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        self._active = []
        self._strokes.append(self._active)
        self.stroke(event.position())
        event.accept()

    def mouseMoveEvent(self, event):
        if self._active is not None:
            self.stroke(event.position())
            event.accept()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self._active is not None:
            self.stroke(event.position())
            self._active = None
            self.last_point = None
            event.accept()

    def stroke(self, point):
        if self._active is None:
            return
        point = QPointF(min(599.0, max(0.0, point.x())),
                        min(199.0, max(0.0, point.y())))
        if self._active and (point - self._active[-1]).manhattanLength() < .25:
            return
        self._active.append(point)
        self.last_point = point
        self.has_ink = True
        self._render_strokes()
        self.update()

    def _render_strokes(self):
        self.image = self._base.copy()
        painter = QPainter(self.image)
        painter.setRenderHint(QPainter.Antialiasing)
        painter.scale(self.SCALE, self.SCALE)
        painter.setPen(QPen(QColor('#151515'), 2.4, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        for points in self._strokes:
            if len(points) == 1:
                painter.drawPoint(points[0])
                continue
            path = QPainterPath(points[0])
            path.lineTo((points[0] + points[1]) / 2)
            for index in range(1, len(points) - 1):
                path.quadTo(points[index], (points[index] + points[index + 1]) / 2)
            path.lineTo(points[-1])
            painter.drawPath(path)
        painter.end()

    def set_image(self, image):
        self.clear()
        image = image.scaled(self.image.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
        painter = QPainter(self.image)
        painter.drawImage((self.image.width() - image.width()) // 2,
                          (self.image.height() - image.height()) // 2, image)
        painter.end()
        self._base = self.image.copy()
        self.has_ink = not image.isNull()
        self.update()

    def png_bytes(self):
        if not self.has_ink:
            return None
        bounds = QRegion(QBitmap.fromImage(self.image.createAlphaMask())).boundingRect()
        if bounds.isEmpty():
            return None
        buffer = QBuffer()
        buffer.open(QIODevice.WriteOnly)
        self.image.copy(bounds).save(buffer, 'PNG')
        return bytes(buffer.data())
