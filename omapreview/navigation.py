"""Virtual continuous paper surface; one live editor, bounded preview rasters.

Page geometry is cheap metadata. Only visible inactive paper is rasterized, and
all editing stays in the original page-local graphics scene.
"""
from bisect import bisect_right
from collections import OrderedDict
import math
from PySide6.QtCore import Qt, QRectF, QSize, QAbstractListModel, QPointF
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QMouseEvent
from PySide6.QtWidgets import QScrollArea, QWidget, QFrame, QListView, QStyledItemDelegate, QStyle, QApplication, QGraphicsProxyWidget


def raster(document, index, scale, *, include_marks=True):
    rect = document.pdf[index].rect
    scale = min(scale, 16383 / rect.width, 16383 / rect.height,
                math.sqrt(24_000_000 / (rect.width * rect.height)) * .999)
    pix = document.render(index, scale=scale, include_marks=include_marks)
    return QPixmap.fromImage(QImage(pix.samples, pix.width, pix.height, pix.stride,
        QImage.Format_RGB888 if pix.n == 3 else QImage.Format_RGBA8888).copy())


class PaperCanvas(QWidget):
    def __init__(self, scroll):
        super().__init__()
        self.scroll = scroll
        self._forwarding_gesture = False
        self.setAutoFillBackground(True)
        palette = self.palette()
        palette.setColor(self.backgroundRole(), QColor('#35383e'))
        self.setPalette(palette)

    def paintEvent(self, event):
        owner = self.scroll.owner
        if not owner.document:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        for index in self.scroll.visible_pages():
            if index == owner.page_index:
                continue
            rect = self.scroll.page_rects[index].adjusted(18, 0, -18, 0)
            painter.fillRect(rect, Qt.white)
            pix = self.scroll.preview(index)
            painter.drawPixmap(rect, pix, QRectF(pix.rect()))

    def mousePressEvent(self, event):
        index = self.scroll.page_at(event.position().y())
        paper = self.scroll.page_rects[index].adjusted(18, 0, -18, 0)
        if not paper.contains(event.position()):
            return super().mousePressEvent(event)
        if not self.scroll.owner.go_page(index, scroll=False):
            event.accept()
            return
        self._forwarding_gesture = True
        self.scroll.view.setFocus(Qt.MouseFocusReason)
        self.forward_pointer(event)
        # A newly attached scene can focus its proxy without focusing the
        # embedded editor. Give native fields real focus for painting/context.
        focus = self.scroll.owner.scene.focusItem()
        if isinstance(focus, QGraphicsProxyWidget) and not self.scroll.view.placing:
            focus.widget().setFocus(Qt.MouseFocusReason)

    def wheelEvent(self, event):
        # Inactive paper is not an editor: wheel always pans the document.
        self.scroll.wheelEvent(event)

    def forward_pointer(self, event):
        viewport = self.scroll.view.viewport()
        point = QPointF(viewport.mapFrom(self, event.position().toPoint()))
        forwarded = QMouseEvent(event.type(), point, event.globalPosition(),
                                event.button(), event.buttons(), event.modifiers())
        QApplication.sendEvent(viewport, forwarded)
        event.accept()

    def mouseMoveEvent(self, event):
        if self._forwarding_gesture:
            self.forward_pointer(event)
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._forwarding_gesture:
            self.forward_pointer(event)
            self._forwarding_gesture = False
        else:
            super().mouseReleaseEvent(event)


class ContinuousPages(QScrollArea):
    gap = 20

    def __init__(self, owner, view):
        super().__init__()
        self.owner, self.view = owner, view
        self.document = None
        self.page_rects = []
        self._tops = []
        self.sizes = []
        self.cache = OrderedDict()
        self._layout_busy = False
        self._accepted_scroll = 0
        self.setFrameShape(QFrame.NoFrame)
        self.setWidgetResizable(False)
        self.canvas = PaperCanvas(self)
        self.setWidget(self.canvas)
        view.setParent(self.canvas)
        view.setFrameShape(QFrame.NoFrame)
        # Forwarded preview gestures must not bubble back into their canvas.
        view.setAttribute(Qt.WA_NoMousePropagation, True)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.verticalScrollBar().setSingleStep(48)
        self.verticalScrollBar().valueChanged.connect(self.scrolled)

    def reset_document(self):
        self.document = self.owner.document
        self.cache.clear()
        self.sizes = [(p.rect.width, p.rect.height) for p in self.document.pdf]
        self._accepted_scroll = 0
        self.relayout(preserve=False)
        self.verticalScrollBar().setValue(0)

    def relayout(self, preserve=True):
        if not self.sizes:
            return
        self._layout_busy = True
        index = self.owner.page_index
        old_top = self.page_rects[index].top() if self.page_rects else 0
        old_scale = getattr(self, 'scale', 1.)
        offset = (self.verticalScrollBar().value() - old_top) / old_scale
        self.scale = abs(self.view.transform().m11())
        width = max(self.viewport().width(), math.ceil(max(w for w, h in self.sizes) * self.scale) + 36)
        y = self.gap
        self.page_rects = []
        for w, h in self.sizes:
            sw, sh = math.ceil(w * self.scale) + 36, math.ceil(h * self.scale) + 2
            self.page_rects.append(QRectF((width - sw) // 2, y, sw, sh))
            y += sh + self.gap
        self._tops = [r.top() for r in self.page_rects]
        self.canvas.resize(width, math.ceil(y))
        self.position_editor()
        if preserve:
            self.verticalScrollBar().setValue(round(self.page_rects[index].top() + offset * self.scale))
        self._accepted_scroll = self.verticalScrollBar().value()
        self._layout_busy = False
        self.canvas.update()

    def position_editor(self):
        if not self.page_rects:
            return
        rect = self.page_rects[self.owner.page_index]
        self.view.setGeometry(rect.toRect())
        self.view.show()
        self.view.raise_()

    def page_at(self, y):
        return max(0, min(len(self.page_rects) - 1, bisect_right(self._tops, y) - 1))

    def visible_pages(self):
        top = self.verticalScrollBar().value()
        return range(self.page_at(top), self.page_at(top + self.viewport().height()) + 1)

    def preview(self, index):
        # Visible adjacent paper needs the same physical density as the editor;
        # only the separate thumbnail rail should use tiny preview rasters.
        density = self.view.viewport().devicePixelRatioF()
        key = (index, self.scale * density)
        if key not in self.cache:
            self.cache[key] = raster(self.document, index, key[1])
            while len(self.cache) > 4:
                self.cache.popitem(last=False)
        self.cache.move_to_end(key)
        return self.cache[key]

    def invalidate(self, index=None):
        if index is None:
            self.cache.clear()
        else:
            for key in list(self.cache):
                if key[0] == index:
                    del self.cache[key]
        self.canvas.update()

    def scrolled(self, value):
        if self._layout_busy or not self.page_rects:
            return
        index = self.page_at(value + min(80, self.viewport().height() // 4))
        if value > 0 and value == self.verticalScrollBar().maximum():
            index = len(self.page_rects) - 1
        if index != self.owner.page_index and not self.owner.go_page(index, scroll=False):
            self._layout_busy = True
            self.verticalScrollBar().setValue(self._accepted_scroll)
            self._layout_busy = False
            return
        self._accepted_scroll = value
        self.canvas.update()

    def jump(self, index):
        self._layout_busy = True
        self.verticalScrollBar().setValue(round(self.page_rects[index].top()))
        self._accepted_scroll = self.verticalScrollBar().value()
        self._layout_busy = False
        self.position_editor()
        self.canvas.update()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self.sizes:
            self.owner._raster_timer.start()


class PageNumbers(QAbstractListModel):
    def __init__(self, owner):
        super().__init__(owner)
        self.count = 0

    def rowCount(self, parent=None):
        return self.count

    def data(self, index, role=Qt.DisplayRole):
        if index.isValid() and role in (Qt.DisplayRole, Qt.AccessibleTextRole):
            return f'Page {index.row() + 1}'

    def reset(self, count):
        self.beginResetModel()
        self.count = count
        self.endResetModel()


class ThumbnailDelegate(QStyledItemDelegate):
    def sizeHint(self, option, index):
        return QSize(132, 166)

    def paint(self, painter, option, index):
        rail = self.parent()
        painter.save()
        selected = bool(option.state & QStyle.State_Selected)
        painter.fillRect(option.rect.adjusted(3, 3, -3, -3), QColor('#385676' if selected else '#292c31'))
        pix = rail.preview(index.row())
        box = option.rect.adjusted(15, 10, -15, -30)
        size = pix.size().scaled(box.size(), Qt.KeepAspectRatio)
        target = QRectF(box.x() + (box.width() - size.width()) / 2,
                        box.y() + (box.height() - size.height()) / 2, size.width(), size.height())
        painter.setRenderHint(QPainter.SmoothPixmapTransform)
        painter.drawPixmap(target, pix, QRectF(pix.rect()))
        painter.setPen(QColor('#ffffff' if selected else '#c8ccd2'))
        painter.drawText(option.rect.adjusted(0, 137, 0, -5), Qt.AlignCenter, str(index.row() + 1))
        painter.restore()


class PageThumbnails(QListView):
    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.document = None
        self.cache = OrderedDict()
        self.setAccessibleName('PDF pages')
        self.setFixedWidth(150)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollMode(QListView.ScrollPerPixel)
        self.setUniformItemSizes(True)
        self.setStyleSheet('QListView { background: #292c31; border: none; }')
        self.setModel(PageNumbers(self))
        self.setItemDelegate(ThumbnailDelegate(self))
        self.clicked.connect(self.activate_page)

    def reset_document(self):
        self.document = self.owner.document
        self.cache.clear()
        self.model().reset(self.document.page_count)
        self.setCurrentIndex(self.model().index(0, 0))
        self.verticalScrollBar().setValue(0)

    def preview(self, index):
        if index not in self.cache:
            rect = self.document.pdf[index].rect
            self.cache[index] = raster(self.document, index, min(110 / rect.width, 126 / rect.height))
            while len(self.cache) > 24:
                self.cache.popitem(last=False)
        self.cache.move_to_end(index)
        return self.cache[index]

    def invalidate(self, index=None):
        if index is None:
            self.cache.clear()
        else:
            self.cache.pop(index, None)
        self.viewport().update()

    def select_page(self, index):
        target = self.model().index(index, 0)
        self.setCurrentIndex(target)
        self.scrollTo(target, QListView.EnsureVisible)

    def activate_page(self, index):
        self.owner.go_page(index.row())
        self.select_page(self.owner.page_index)
