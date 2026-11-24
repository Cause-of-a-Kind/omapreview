"""Native PDF workspace. Scene coordinates are always PDF points."""
from pathlib import Path
import base64
import math

from PySide6.QtCore import Qt, Signal, QTimer, QRectF, QSize, QEvent
from PySide6.QtGui import QAction, QImage, QPixmap, QKeySequence, QFont, QColor, QPen, QPainter, QPainterPath, QIcon
from PySide6.QtWidgets import (
    QMainWindow, QGraphicsScene, QGraphicsView, QToolBar, QLabel,
    QPushButton, QStackedWidget, QWidget, QVBoxLayout, QFileDialog, QMessageBox,
    QLineEdit, QCheckBox, QRadioButton, QComboBox, QGraphicsTextItem, QGraphicsItem,
    QGraphicsObject, QGraphicsProxyWidget, QDialog, QDialogButtonBox, QHBoxLayout, QMenu, QToolButton, QInputDialog, QButtonGroup,
    QListWidget, QListWidgetItem, QDoubleSpinBox, QSizePolicy, QApplication,
)

from .core import Document
from .signature_canvas import DrawCanvas
from .navigation import ContinuousPages, PageThumbnails


class FieldLineEdit(QLineEdit):
    """Let the PDF paint its real appearance except while actively editing."""

    def __init__(self, text, owner, rect):
        super().__init__(text)
        self.owner = owner
        self.field_xref = None
        self.font_size_override = None
        self.editor_size = 12.
        self.textEdited.connect(self.fit_editor_text)
        x0, y0, x1, y1 = rect
        self.pdf_rect = QRectF(x0, y0, x1 - x0, y1 - y0)

    def set_editor_size(self, size):
        self.editor_size = float(size)
        font = QFont(self.font())
        font.setPointSizeF(self.editor_size * 72 / self.logicalDpiY())
        self.setFont(font)

    def fit_editor_text(self, text):
        if self.field_xref is None or self.owner._rebuilding:
            return
        size = self.font_size_override
        if size is None:
            size = self.owner.document.field_font_size(self.owner.page_index, self.field_xref, value=text)
        self.set_editor_size(size)
        if self.owner._font_target is self:
            self.owner.show_text_size(size, enabled=True)

    def focusInEvent(self, event):
        super().focusInEvent(event)
        if self.field_xref is not None:
            self.owner.activate_field_size(self)

    @property
    def source_rect(self):
        rect = self.pdf_rect
        scale = self.owner.render_scale
        return QRectF(rect.x() * scale, rect.y() * scale,
                      rect.width() * scale, rect.height() * scale)

    def paintEvent(self, event):
        if self.hasFocus():
            painter = QPainter(self)
            painter.fillRect(self.rect(), Qt.white)
            painter.end()
            super().paintEvent(event)
        else:
            painter = QPainter(self)
            painter.drawPixmap(QRectF(self.rect()), self.owner.background_item.pixmap(), self.source_rect)
            painter.setPen(QPen(QColor('#a9bed5'), .5, Qt.DotLine))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))


class SignatureDialog(QDialog):
    def __init__(self, kind, store, parent=None, *, save_mode='primary'):
        super().__init__(parent)
        if save_mode not in ('primary', 'once', 'additional'):
            raise ValueError('Unknown signature capture mode')
        self.kind, self.store = kind, store
        self.save_mode = save_mode
        self.result_png = None
        self.saved_id = None
        self.setWindowTitle('Sign once' if save_mode == 'once' else f'Save {kind}')
        layout = QVBoxLayout(self)
        description = ('Draw below or import an image. Used only in this PDF, not saved to your library.'
                       if save_mode == 'once' else 'Draw below or import an image. Saved only on this computer.')
        layout.addWidget(QLabel(description))
        if save_mode == 'additional':
            layout.addWidget(QLabel(f'{kind.title()} name (optional)'))
            self.name_edit = QLineEdit()
            self.name_edit.setMaxLength(80)
            self.name_edit.setPlaceholderText('Leave blank for an automatic name')
            self.name_edit.setAccessibleName(f'{kind.title()} name (optional)')
            layout.addWidget(self.name_edit)
        self.canvas = DrawCanvas()
        layout.addWidget(self.canvas)
        row = QHBoxLayout()
        import_button = QPushButton('Import image…')
        import_button.clicked.connect(self.choose_image)
        clear_button = QPushButton('Clear')
        clear_button.clicked.connect(self.canvas.clear)
        row.addWidget(import_button)
        row.addWidget(clear_button)
        row.addStretch()
        layout.addLayout(row)
        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        if save_mode == 'once':
            buttons.button(QDialogButtonBox.Save).setText(f'Use {kind}')
        buttons.accepted.connect(self.save_mark)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Import signature', '', 'Images (*.png *.jpg *.jpeg)')
        if path:
            self.import_image(path)

    def import_image(self, path):
        image = QImage(str(path))
        if image.isNull():
            QMessageBox.warning(self, 'Cannot import image', 'Choose a readable PNG or JPEG image.')
            return False
        self.canvas.set_image(image)
        return True

    def save_mark(self):
        data = self.canvas.png_bytes()
        if not data:
            QMessageBox.information(self, 'Empty signature', 'Draw or import a mark first.')
            return False
        try:
            if self.save_mode == 'additional':
                name = self.name_edit.text()
                if not name.strip():
                    marks = self.store.list_marks(self.kind)
                    existing = {mark['name'].casefold() for mark in marks}
                    number = 2 if any(mark['primary'] for mark in marks) else 1
                    while f'{self.kind.title()} {number}'.casefold() in existing:
                        number += 1
                    name = f'{self.kind.title()} {number}'
                self.saved_id = self.store.save_additional(self.kind, name, data)
            elif self.save_mode == 'primary':
                self.store.save(self.kind, data)
                self.saved_id = 'primary'
        except Exception as exc:
            if self.save_mode == 'additional' and isinstance(exc, ValueError) and 'Mark name' in str(exc):
                QMessageBox.warning(self, 'Signature name',
                    'Use up to 80 visible characters without / or \\, or leave it blank for an automatic name.')
                self.name_edit.setFocus()
                self.name_edit.selectAll()
            else:
                QMessageBox.warning(self, 'Cannot save signature', str(exc))
            return False
        self.result_png = data
        self.accept()
        return True


class SignatureLibraryDialog(QDialog):
    """A small named picker; the primary stays on the toolbar's main button."""

    def __init__(self, kind, store, parent=None):
        super().__init__(parent)
        self.kind, self.store = kind, store
        self.selected_id = None
        self.setWindowTitle(f'Saved {"signatures" if kind == "signature" else "initials"}')
        self.resize(510, 360)
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel('Choose a saved mark. Your primary stays on the main toolbar button.'))
        self.list_widget = QListWidget()
        self.list_widget.setAccessibleName('Saved signatures' if kind == 'signature' else 'Saved initials')
        self.list_widget.setIconSize(QSize(160, 54))
        self.list_widget.itemDoubleClicked.connect(lambda item: self.use_selected())
        layout.addWidget(self.list_widget)
        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        self.use_button = buttons.button(QDialogButtonBox.Ok)
        self.use_button.setText(f'Use {kind}')
        self.remove_button = buttons.addButton('Forget selected', QDialogButtonBox.ActionRole)
        self.remove_button.clicked.connect(self.forget_selected)
        buttons.accepted.connect(self.use_selected)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)
        self.list_widget.currentRowChanged.connect(self.update_buttons)
        self.refresh()

    def refresh(self):
        self.list_widget.clear()
        for mark in self.store.list_marks(self.kind):
            data = self.store.load_mark(self.kind, mark['id'])
            if data is None:
                continue
            pixmap = QPixmap()
            if not pixmap.loadFromData(data):
                raise ValueError('Cannot read saved signature image')
            thumbnail = QPixmap(160, 54)
            thumbnail.fill(Qt.white)
            painter = QPainter(thumbnail)
            painter.setRenderHint(QPainter.SmoothPixmapTransform)
            scaled = pixmap.scaled(148, 42, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            painter.drawPixmap((160 - scaled.width()) // 2, (54 - scaled.height()) // 2, scaled)
            painter.end()
            item = QListWidgetItem(QIcon(thumbnail), mark['name'])
            item.setData(Qt.UserRole, mark['id'])
            self.list_widget.addItem(item)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        self.update_buttons()

    def update_buttons(self, *args):
        available = self.list_widget.currentItem() is not None
        self.use_button.setEnabled(available)
        self.remove_button.setEnabled(available)

    def use_selected(self):
        item = self.list_widget.currentItem()
        if item is not None:
            self.selected_id = item.data(Qt.UserRole)
            self.accept()

    def forget_selected(self):
        item = self.list_widget.currentItem()
        if item is None:
            return
        try:
            self.store.delete_mark(self.kind, item.data(Qt.UserRole))
            self.refresh()
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot forget signature', str(exc))


class PageView(QGraphicsView):
    page_clicked = Signal(float, float)
    text_area_created = Signal(float, float, float, float)
    delete_pressed = Signal()
    viewport_changed = Signal()

    def __init__(self, scene):
        super().__init__(scene)
        self.setRenderHints(QPainter.Antialiasing | QPainter.TextAntialiasing | QPainter.SmoothPixmapTransform)
        self.placing = False
        self.text_placing = False
        self.area_origin = None
        self.area_preview = None

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.viewport_changed.emit()

    def event(self, event):
        result = super().event(event)
        if event.type() in (QEvent.DevicePixelRatioChange, QEvent.ScreenChangeInternal):
            self.viewport_changed.emit()
        return result

    def mousePressEvent(self, event):
        if self.placing and event.button() == Qt.LeftButton:
            point = self.mapToScene(event.position().toPoint())
            if self.sceneRect().contains(point):
                if self.text_placing:
                    self.area_origin = point
                    self.area_preview = self.scene().addRect(QRectF(point, point), QPen(QColor('#6b9ad5'), 1, Qt.DashLine))
                    self.area_preview.setZValue(1000)
                else:
                    self.page_clicked.emit(point.x(), point.y())
                event.accept()
                return
        focused = self.scene().focusItem()
        if isinstance(focused, TextOverlay):
            point = self.mapToScene(event.position().toPoint())
            if not focused.sceneBoundingRect().contains(point):
                focused.clearFocus()
                focused.setTextInteractionFlags(Qt.NoTextInteraction)
        super().mousePressEvent(event)


    def mouseMoveEvent(self, event):
        if self.area_origin is not None:
            point = self.mapToScene(event.position().toPoint())
            self.area_preview.setRect(QRectF(self.area_origin, point).normalized().intersected(self.sceneRect()))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.area_origin is not None:
            point = self.mapToScene(event.position().toPoint())
            rect = QRectF(self.area_origin, point).normalized().intersected(self.sceneRect())
            self.scene().removeItem(self.area_preview)
            self.area_preview = None
            origin = self.area_origin
            self.area_origin = None
            if rect.width() < 5 and rect.height() < 5:
                rect = QRectF(origin.x(), origin.y(), min(200., self.sceneRect().right() - origin.x()), 36.)
            self.text_area_created.emit(rect.x(), rect.y(), max(30., rect.width()), max(20., rect.height()))
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def keyPressEvent(self, event):
        focus = self.scene().focusItem()
        editing = isinstance(focus, QGraphicsProxyWidget) or (
            isinstance(focus, TextOverlay) and bool(focus.textInteractionFlags() & Qt.TextEditable))
        if event.key() == Qt.Key_Delete and not editing:
            self.delete_pressed.emit()
            event.accept()
            return
        super().keyPressEvent(event)


    def wheelEvent(self, event):
        scroll = getattr(self, 'document_scroll', None)
        if scroll is None:
            return super().wheelEvent(event)
        focus = self.scene().focusItem()
        point = self.mapToScene(event.position().toPoint())
        if focus is not None and focus.sceneBoundingRect().contains(point) and (
                isinstance(focus, QGraphicsProxyWidget) or
                isinstance(focus, TextOverlay) and focus.textInteractionFlags() & Qt.TextEditable):
            return super().wheelEvent(event)
        bar = scroll.verticalScrollBar()
        delta = event.pixelDelta().y() or event.angleDelta().y() / 120 * bar.singleStep() * 3
        bar.setValue(round(bar.value() - delta))
        event.accept()


class TextOverlay(QGraphicsTextItem):
    """An editable wrapping box; selection and text editing are distinct modes."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.mark_id = None
        self.drag_origin = None
        self.resizing = False
        self.box_height = 36.
        self.text_size = 12.
        self.set_text_size(self.text_size)
        self.setDefaultTextColor(QColor('#111111'))
        self.setTextWidth(200)
        self.document().setDocumentMargin(1)
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable |
                      QGraphicsItem.ItemIsFocusable)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        self.setToolTip('Drag to move · corner to wrap/resize · double-click to edit · Delete removes')

    def boundingRect(self):
        rect = super().boundingRect()
        return QRectF(0, 0, rect.width(), max(getattr(self, 'box_height', 36.), rect.height()))

    def shape(self):
        path = QPainterPath()
        path.addRect(self.boundingRect())
        return path

    def resize_to(self, width, height):
        self.prepareGeometryChange()
        self.box_height = max(20., height)
        self.setTextWidth(max(30., width))
        self.update()

    def set_text_size(self, size):
        size = float(size)
        if not math.isfinite(size) or not 1 <= size <= 144:
            raise ValueError('Text size must be between 1 and 144 points')
        self.prepareGeometryChange()
        self.text_size = size
        font = QFont('sans-serif')
        # One scene unit is one PDF point; Qt point fonts otherwise add screen DPI.
        font.setPointSizeF(size * 72 / self.owner.view.viewport().logicalDpiY())
        self.setFont(font)
        self.update()

    def start_editing(self):
        self.setTextInteractionFlags(Qt.TextEditorInteraction)
        self.setSelected(True)
        self.setFocus()

    def paint(self, painter, option, widget=None):
        super().paint(painter, option, widget)
        if self.isSelected():
            rect = self.boundingRect()
            painter.setPen(QPen(QColor('#6b9ad5'), .7, Qt.DashLine))
            painter.drawRect(rect.adjusted(.5, .5, -.5, -.5))
            painter.fillRect(QRectF(rect.right() - 8, rect.bottom() - 8, 8, 8), QColor('#6b9ad5'))

    def mouseDoubleClickEvent(self, event):
        self.start_editing()
        super().mouseDoubleClickEvent(event)

    def mousePressEvent(self, event):
        rect = self.boundingRect()
        self.resizing = self.isSelected() and event.pos().x() >= rect.right() - 12 and event.pos().y() >= rect.bottom() - 12
        edge = event.pos().x() < 6 or event.pos().x() > rect.right() - 6
        if self.resizing or edge or self.textInteractionFlags() == Qt.NoTextInteraction:
            if not event.modifiers() & Qt.ControlModifier:
                self.scene().clearSelection()
            self.setSelected(True)
            self.drag_origin = event.scenePos()
            self.original_position = self.pos()
            self.original_rect = rect
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.drag_origin is not None:
            delta = event.scenePos() - self.drag_origin
            if self.resizing:
                self.resize_to(self.original_rect.width() + delta.x(), self.original_rect.height() + delta.y())
            else:
                self.setPos(self.original_position + delta)
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.drag_origin is not None:
            self.drag_origin = None
            self.resizing = False
            self.owner.commit_pending()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)
        self.setTextInteractionFlags(Qt.NoTextInteraction)
        QTimer.singleShot(0, self.commit_after_focus)

    def commit_after_focus(self):
        if self in self.owner.pending_items and not self.hasFocus():
            self.owner.commit_pending()


class SignatureOverlay(QGraphicsObject):
    def __init__(self, png_bytes, owner=None):
        super().__init__()
        self.mark_id = None
        self.owner = owner
        self.png_bytes = png_bytes
        self.pixmap = QPixmap()
        if not self.pixmap.loadFromData(png_bytes):
            raise ValueError('Cannot decode signature image')
        self.width = 160.0
        self.height = self.width * self.pixmap.height() / self.pixmap.width()
        self.resizing = False
        self.setFlags(QGraphicsItem.ItemIsMovable | QGraphicsItem.ItemIsSelectable)
        self.setToolTip('Drag to move · drag bottom-right corner to resize · Delete to remove')

    def boundingRect(self):
        return QRectF(0, 0, self.width, self.height)

    def resize_to(self, width, height):
        self.prepareGeometryChange()
        self.width = max(20., width)
        self.height = self.width * self.pixmap.height() / self.pixmap.width()
        self.update()

    def paint(self, painter, option, widget=None):
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        painter.drawPixmap(self.boundingRect(), self.pixmap, QRectF(self.pixmap.rect()))
        if self.isSelected():
            painter.setPen(QPen(QColor('#6b9ad5'), 1, Qt.DashLine))
            painter.drawRect(self.boundingRect().adjusted(.5, .5, -.5, -.5))
            painter.fillRect(QRectF(self.width - 8, self.height - 8, 8, 8), QColor('#6b9ad5'))

    def mousePressEvent(self, event):
        self.resizing = (event.pos().x() >= self.width - 12 and
                         event.pos().y() >= self.height - 12)
        if self.resizing:
            self.setSelected(True)
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.resizing:
            width = max(20., event.pos().x())
            self.resize_to(width, width * self.pixmap.height() / self.pixmap.width())
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self.resizing = False
        super().mouseReleaseEvent(event)
        if self.owner:
            self.owner.commit_pending()


class MainWindow(QMainWindow):
    def __init__(self, signature_store=None):
        super().__init__()
        self.document = None
        self.page_index = 0
        self.field_widgets = []
        self.pending_items = []
        self.mode = 'select'
        self.radio_groups = {}
        self._retired_scenes = {}
        self._rebuilding = False
        self._committing = False
        self._store = signature_store
        self.render_scale = 1.5
        self.default_text_size = 12.
        self._font_target = None
        self.setWindowTitle('Omapreview')
        self.resize(1000, 820)
        self.scene = QGraphicsScene(self)
        self.view = PageView(self.scene)
        self.view.page_clicked.connect(self.place_at)
        self.view.text_area_created.connect(self.create_text_area)
        self.view.delete_pressed.connect(self.delete_selected)
        self.view.setAlignment(Qt.AlignHCenter | Qt.AlignTop)
        self._fit_width_active = True
        self._raster_timer = QTimer(self)
        self._raster_timer.setSingleShot(True)
        self._raster_timer.setInterval(80)
        self._raster_timer.timeout.connect(self.refresh_viewport)
        self.view.viewport_changed.connect(lambda: self._raster_timer.start())
        self.stack = QStackedWidget()
        welcome = QWidget()
        layout = QVBoxLayout(welcome)
        layout.addStretch()
        label = QLabel('Fill. Sign. Keep it local.')
        label.setAlignment(Qt.AlignCenter)
        layout.addWidget(label)
        button = QPushButton('Open PDF')
        button.clicked.connect(self.choose_open)
        layout.addWidget(button, alignment=Qt.AlignCenter)
        layout.addStretch()
        self.stack.addWidget(welcome)
        self.page_scroll = ContinuousPages(self, self.view)
        self.view.document_scroll = self.page_scroll
        self.thumbnails = PageThumbnails(self)
        workspace = QWidget()
        workspace_layout = QHBoxLayout(workspace)
        workspace_layout.setContentsMargins(0, 0, 0, 0)
        workspace_layout.setSpacing(0)
        workspace_layout.addWidget(self.thumbnails)
        workspace_layout.addWidget(self.page_scroll, 1)
        self.stack.addWidget(workspace)
        self.setCentralWidget(self.stack)
        self.toolbar = QToolBar('Document')
        self.toolbar.setMovable(False)
        self.addToolBar(self.toolbar)
        self.open_action = self.action('Open', self.choose_open, 'Ctrl+O')
        self.select_action = self.action('Select', lambda: self.set_mode('select'))
        self.text_action = self.action('Text', lambda: self.set_mode('text'))
        self.select_action.setCheckable(True)
        self.text_action.setCheckable(True)
        self.select_action.setChecked(True)
        self.text_toolbar = QToolBar('Text options')
        self.text_toolbar.setMovable(False)
        self.text_toolbar.setFloatable(False)
        self.addToolBarBreak(Qt.TopToolBarArea)
        self.addToolBar(Qt.TopToolBarArea, self.text_toolbar)
        self.text_toolbar.addWidget(QLabel(' Text size '))
        self.font_size_input = QDoubleSpinBox()
        self.font_size_input.setRange(1, 144)
        self.font_size_input.setDecimals(1)
        self.font_size_input.setSingleStep(1)
        self.font_size_input.setSuffix(' pt')
        self.font_size_input.setValue(self.default_text_size)
        self.font_size_input.setAccessibleName('Text size')
        self.font_size_input.setToolTip('Size of the selected text box or active PDF text field')
        self.font_size_input.setKeyboardTracking(False)
        self.font_size_input.setEnabled(False)
        self.font_size_input.valueChanged.connect(self.apply_text_size)
        self.text_toolbar.addWidget(self.font_size_input)
        self.text_toolbar.setVisible(False)
        self.text_toolbar.toggleViewAction().setEnabled(False)
        self._text_context_timer = QTimer(self)
        self._text_context_timer.setSingleShot(True)
        self._text_context_timer.timeout.connect(self.refresh_text_context)
        QApplication.instance().focusChanged.connect(self.queue_text_context_refresh)
        for kind in ('signature', 'initials'):
            button = QToolButton()
            button.setText(kind.title())
            button.setPopupMode(QToolButton.MenuButtonPopup)
            button.clicked.connect(lambda checked=False, k=kind: self.use_mark(k))
            menu = QMenu(button)
            menu.addAction('Sign once…', lambda k=kind: self.use_once(k))
            menu.addAction(f'Add saved {kind}…', lambda k=kind: self.add_saved_mark(k))
            saved_label = 'Saved signatures…' if kind == 'signature' else 'Saved initials…'
            menu.addAction(saved_label, lambda k=kind: self.choose_saved_mark(k))
            menu.addSeparator()
            menu.addAction(f'Replace saved {kind}…', lambda k=kind: self.use_mark(k, replace=True))
            menu.addAction(f'Forget saved {kind}', lambda k=kind: self.forget_mark(k))
            button.setMenu(menu)
            self.toolbar.addWidget(button)
        self.undo_action = self.action('Undo', self.undo_edit, 'Ctrl+Z')
        self.delete_action = QAction('Remove selected mark', self)
        self.delete_action.triggered.connect(self.delete_selected)
        self.addAction(self.delete_action)
        self.save_action = self.action('Save a copy', self.choose_save, 'Ctrl+Shift+S')
        self.toolbar.addSeparator()
        self.action('Fit width', self.fit_width)
        self.action('−', lambda: self.zoom_by(.8))
        self.action('+', lambda: self.zoom_by(1.25))
        navigation_spacer = QWidget()
        navigation_spacer.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.toolbar.addWidget(navigation_spacer)
        self.toolbar.addSeparator()
        self.previous_action = self.action('‹', lambda: self.go_page(self.page_index - 1))
        self.previous_action.setToolTip('Previous page')
        self.page_label = QLabel('No document')
        self.toolbar.addWidget(self.page_label)
        self.next_action = self.action('›', lambda: self.go_page(self.page_index + 1))
        self.next_action.setToolTip('Next page')
        self.statusBar().showMessage('Local only · originals stay untouched')

    def queue_text_context_refresh(self, *args):
        self._text_context_timer.start(0)

    def refresh_text_context(self):
        if self._rebuilding:
            return
        target = self._font_target
        if isinstance(target, FieldLineEdit):
            current = any(widget is target for _, widget, _ in self.field_widgets)
            focus = QApplication.focusWidget()
            editing_size = focus is self.font_size_input or (
                focus is not None and self.font_size_input.isAncestorOf(focus))
            if current and (target.hasFocus() or editing_size):
                return
            self.sync_text_size()
        elif target is None:
            self.sync_text_size()

    @staticmethod
    def native_display_size(field):
        stored = float(field.get('font_size') or 0)
        return stored if math.isfinite(stored) and stored > 0 else field.get('effective_font_size', 12.)

    def show_text_size(self, size, *, enabled):
        self.font_size_input.blockSignals(True)
        self.font_size_input.setValue(size)
        self.font_size_input.setEnabled(enabled)
        self.font_size_input.blockSignals(False)
        self.text_toolbar.setVisible(enabled)

    def activate_field_size(self, widget):
        if self._rebuilding or not widget.isEnabled():
            return
        self.scene.clearSelection()
        self._font_target = widget
        self.show_text_size(widget.editor_size, enabled=True)

    def sync_text_size(self):
        if self._rebuilding:
            return
        selected = self.scene.selectedItems()
        text = [item for item in selected if isinstance(item, TextOverlay)]
        self._font_target = text[0] if len(text) == 1 and len(selected) == 1 else None
        self.font_size_input.blockSignals(True)
        if self._font_target is not None:
            self.font_size_input.setValue(self._font_target.text_size)
        visible = self._font_target is not None or self.mode == 'text'
        self.font_size_input.setEnabled(visible)
        self.font_size_input.blockSignals(False)
        self.text_toolbar.setVisible(visible)

    def apply_text_size(self, size):
        target = self._font_target
        if isinstance(target, FieldLineEdit):
            current = next((field for _, widget, field in self.field_widgets if widget is target), None)
            if current is None or current['readonly']:
                return
            previous = target.editor_size
            target.font_size_override = size
            target.set_editor_size(size)
            if not self.commit_pending():
                target.font_size_override = None
                target.set_editor_size(previous)
                self.show_text_size(previous, enabled=True)
            return
        if isinstance(target, TextOverlay) and target in self.pending_items:
            previous = target.text_size
            target.set_text_size(size)
            if not self.commit_pending():
                target.set_text_size(previous)
                self.sync_text_size()
                return
        self.default_text_size = size

    def action(self, title, callback, shortcut=None):
        action = QAction(title, self)
        action.triggered.connect(callback)
        if shortcut:
            action.setShortcut(QKeySequence(shortcut))
        self.toolbar.addAction(action)
        return action

    def choose_open(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Open PDF', '', 'PDF files (*.pdf)')
        if path:
            self.open_file(path)

    def open_file(self, path):
        if not self.confirm_leave():
            return False
        try:
            password = None
            prompt = 'This PDF requires a password.'
            while True:
                try:
                    document = Document(path, password=password)
                    break
                except ValueError as exc:
                    if 'password' not in str(exc).lower():
                        raise
                    password, accepted = QInputDialog.getText(
                        self, 'Protected PDF', prompt, QLineEdit.Password)
                    if not accepted:
                        return False
                    prompt = 'Incorrect password. Try again.'
            old_document, old_index = self.document, self.page_index
            self.document = document
            self.page_index = 0
            try:
                # Build the first editor before discarding the current one or
                # resetting its navigation/mode. show_page retains it on failure.
                self.show_page()
            except Exception:
                self.document, self.page_index = old_document, old_index
                document.pdf.close()
                raise
            self.mode = 'select'
            self.view.placing = False
            self.view.unsetCursor()
            self.stack.setCurrentIndex(1)
            self.page_scroll.reset_document()
            self.thumbnails.reset_document()
            self.set_mode('select')
            self.fit_width()
            if old_document:
                old_document.pdf.close()
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot open PDF', str(exc))
            return False

    def has_changes(self):
        if not self.document:
            return False
        if self.document.dirty or any(self.overlay_changed(item) for item in self.pending_items):
            return True
        return any(self.field_changed(widget, field)[1]
                   for _, widget, field in self.field_widgets if not field['readonly'])

    @staticmethod
    def field_changed(widget, field):
        if isinstance(widget, QLineEdit):
            value = widget.text()
            override = getattr(widget, 'font_size_override', None)
            size_changed = override is not None and override != field.get('font_size')
            return value, value != str(field['value'] or '') or size_changed
        if isinstance(widget, QComboBox):
            value = widget.currentText()
            return value, value != str(field['value'] or '')
        value = widget.isChecked()
        return value, value != field['checked']

    def radio_clicked(self, widget, field):
        # Qt exclusive groups cannot turn the selected member off. Keep the
        # group nonexclusive and perform switch-or-clear explicitly instead.
        if widget.isChecked():
            for button in self.radio_groups[field['group']].buttons():
                if button is not widget:
                    button.setChecked(False)
        self.commit_pending()

    def confirm_leave(self):
        if not self.has_changes():
            return True
        answer = QMessageBox.question(
            self, 'Unsaved changes', 'Save a copy before leaving this PDF?',
            QMessageBox.Save | QMessageBox.Discard | QMessageBox.Cancel, QMessageBox.Cancel)
        if answer == QMessageBox.Save:
            return self.choose_save()
        return answer == QMessageBox.Discard

    def closeEvent(self, event):
        if self.confirm_leave():
            event.accept()
        else:
            event.ignore()

    def _retire_scene(self, scene, fields, items, groups):
        # Do not delete focused Python graphics subclasses in a signal stack.
        for _, widget, _ in fields:
            widget.blockSignals(True)
        scene.clearFocus()
        scene.clearSelection()
        key = id(scene)
        self._retired_scenes[key] = (scene, fields, items, groups)
        scene.destroyed.connect(lambda: QTimer.singleShot(0, lambda: self._retired_scenes.pop(key, None)))
        scene.deleteLater()
        for group in groups.values():
            group.deleteLater()

    def show_page(self):
        old = (self.scene, self.field_widgets, self.pending_items, self.radio_groups)
        background = getattr(self, 'background_item', None)
        target, enabled, scale = self._font_target, self.font_size_input.isEnabled(), self.render_scale
        self._rebuilding = True
        try:
            self._font_target = None
            self.font_size_input.setEnabled(False)
            self.scene = QGraphicsScene(self)
            self.scene.selectionChanged.connect(self.sync_text_size)
            self.radio_groups = {}
            self.field_widgets = []
            self.pending_items = []
            self._populate_page_scene()
        except Exception:
            self._retire_scene(self.scene, self.field_widgets, self.pending_items, self.radio_groups)
            self.scene, self.field_widgets, self.pending_items, self.radio_groups = old
            self.background_item = background
            self._font_target, self.render_scale = target, scale
            self.font_size_input.setEnabled(enabled)
            raise
        else:
            self.view.setScene(self.scene)
            self._retire_scene(*old)
        finally:
            self._rebuilding = False
        self.sync_text_size()
        self.page_label.setText(f' {self.page_index + 1} / {self.document.page_count} ')
        self.previous_action.setEnabled(self.page_index > 0)
        self.next_action.setEnabled(self.page_index + 1 < self.document.page_count)
        self.setWindowTitle(f'{"* " if self.document.dirty else ""}{self.document.path.name} — Omapreview')
        self.page_scroll.position_editor()
        self.thumbnails.select_page(self.page_index)

    def _populate_page_scene(self):
        """Initialize a candidate scene without replacing the visible editor."""
        self.background_item = self.scene.addPixmap(QPixmap())
        self.background_item.setZValue(-10)
        self.refresh_background()
        rect = self.document.pdf[self.page_index].rect
        self.scene.setSceneRect(0, 0, rect.width, rect.height)
        for field in self.document.fields(self.page_index):
            kind, value = field['type'], field['value']
            if kind == 'text':
                widget = FieldLineEdit(str(value or ''), self, field['rect'])
                widget.field_xref = field['xref']
                widget.set_editor_size(self.native_display_size(field))
                widget.setFrame(False)
                widget.editingFinished.connect(self.commit_pending)
            elif kind in ('checkbox', 'radio'):
                widget = QCheckBox() if kind == 'checkbox' else QRadioButton()
                widget.setAutoExclusive(False)
                widget.setChecked(field['checked'])
                if kind == 'radio':
                    if field['group'] not in self.radio_groups:
                        group = QButtonGroup(self)
                        group.setExclusive(False)
                        self.radio_groups[field['group']] = group
                    self.radio_groups[field['group']].addButton(widget)
                    widget.clicked.connect(lambda checked, w=widget, f=field: self.radio_clicked(w, f))
                    widget.setToolTip((field['name'] or kind) + ' · click selected option to clear')
                else:
                    widget.clicked.connect(self.commit_pending)
            elif kind == 'choice':
                widget = QComboBox()
                widget.addItems(field['choices'])
                widget.setCurrentIndex(widget.findText(str(value or '')))
            else:
                continue
            widget.setAccessibleName(field['name'] or kind)
            if kind != 'radio':
                widget.setToolTip(field['name'] or kind)
            widget.setEnabled(not field['readonly'])
            font_style = '' if kind == 'text' else 'font-size: 12px;'
            widget.setStyleSheet('background: rgba(205, 225, 255, 35); color: #161616;' + font_style)
            proxy = self.scene.addWidget(widget)
            x0, y0, x1, y1 = field['rect']
            proxy.setPos(x0, y0)
            proxy.resize(x1 - x0, y1 - y0)
            self.field_widgets.append((proxy, widget, field))
        for mark in self.document.marks(self.page_index):
            x0, y0, x1, y1 = mark['rect']
            if mark['kind'] == 'text':
                item = self.add_text_overlay(x0, y0, font_size=mark['font_size'])
                item.resize_to(x1 - x0, y1 - y0)
                item.setPlainText(mark['text'])
                item.clearFocus()
                item.setTextInteractionFlags(Qt.NoTextInteraction)
            else:
                item = self.add_signature_overlay(x0, y0, base64.b64decode(mark['png']))
                item.resize_to(x1 - x0, y1 - y0)
            item.mark_id = mark['id']
            item.setSelected(False)

    def go_page(self, index, *, scroll=True):
        if not self.document or not 0 <= index < self.document.page_count:
            return False
        if index == self.page_index:
            if scroll:
                self.page_scroll.jump(index)
            return True
        if not self.commit_pending():
            return False
        self.page_scroll.invalidate(self.page_index)
        self.page_index = index
        self.show_page()
        if self._fit_width_active:
            self.fit_width()
        if scroll:
            self.page_scroll.jump(index)
        return True

    def commit_pending(self):
        if not self.document or self._rebuilding:
            return True
        if self._committing:
            return False
        self._committing = True
        try:
            committed_groups = set()
            for _, widget, field in self.field_widgets:
                if field['readonly']:
                    continue
                value, changed = self.field_changed(widget, field)
                if field['type'] == 'radio':
                    group = field['group']
                    if group in committed_groups or not changed:
                        continue
                    # Commit a group's final selection once, not the old member's
                    # False followed by another edit for the new member's True.
                    selected = next(((w, f) for _, w, f in self.field_widgets
                                     if f['type'] == 'radio' and f['group'] == group and w.isChecked()), None)
                    if selected:
                        widget, field = selected
                        value = True
                    committed_groups.add(group)
                if changed:
                    if field['type'] == 'text' and widget.font_size_override is not None:
                        self.document.set_field(self.page_index, field['xref'], value, font_size=widget.font_size_override)
                    else:
                        self.document.set_field(self.page_index, field['xref'], value)
            current = {field['xref']: field for field in self.document.fields(self.page_index)}
            for _, widget, field in self.field_widgets:
                field.update(current[field['xref']])
                if field['type'] in ('radio', 'checkbox'):
                    widget.setChecked(field['checked'])
                elif field['type'] == 'text':
                    widget.font_size_override = None
                    widget.set_editor_size(self.native_display_size(field))
            for item in self.pending_items:
                values = self.overlay_values(item)
                if item.mark_id:
                    self.document.update_mark(self.page_index, item.mark_id, **values)
                elif isinstance(item, SignatureOverlay):
                    item.mark_id = self.document.add_signature(self.page_index, values['rect'], item.png_bytes)
                elif item.toPlainText().strip():
                    item.mark_id = self.document.add_text(self.page_index, **values)
            saved = {mark['id']: mark for mark in self.document.marks(self.page_index)}
            for item in self.pending_items:
                if item.mark_id in saved:
                    x0, y0, x1, y1 = saved[item.mark_id]['rect']
                    item.resize_to(x1 - x0, y1 - y0)
            if self.document.dirty:
                self.setWindowTitle(f'* {self.document.path.name} — Omapreview')
                self.refresh_background()
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot apply edit', str(exc))
            return False
        finally:
            self._committing = False

    def refresh_background(self):
        if not self.document:
            return
        self.page_scroll.invalidate()
        self.thumbnails.invalidate()
        requested = max(1.5, abs(self.view.transform().m11()) * self.view.viewport().devicePixelRatioF())
        rect = self.document.pdf[self.page_index].rect
        # Bound full-page rasters before MuPDF allocates them. Leave rounding room.
        self.render_scale = min(requested, 16383 / rect.width, 16383 / rect.height,
                                math.sqrt(24_000_000 / (rect.width * rect.height)) * .999)
        pix = self.document.render(self.page_index, scale=self.render_scale, include_marks=False)
        image = QImage(pix.samples, pix.width, pix.height, pix.stride,
                       QImage.Format_RGB888 if pix.n == 3 else QImage.Format_RGBA8888).copy()
        self.background_item.setPixmap(QPixmap.fromImage(image))
        self.background_item.setScale(1 / self.render_scale)
        self.background_item.setTransformationMode(Qt.SmoothTransformation)
        for _, widget, _ in self.field_widgets:
            widget.update()

    @staticmethod
    def overlay_values(item):
        rect = item.sceneBoundingRect()
        values = dict(rect=[rect.left(), rect.top(), rect.right(), rect.bottom()])
        if isinstance(item, TextOverlay):
            values.update(text=item.toPlainText(), font_size=item.text_size)
        return values

    def overlay_changed(self, item):
        if not getattr(item, 'mark_id', None):
            return isinstance(item, SignatureOverlay) or bool(item.toPlainText().strip())
        mark = next((m for m in self.document.marks(self.page_index) if m['id'] == item.mark_id), None)
        return mark is None or any(mark.get(key) != value for key, value in self.overlay_values(item).items())

    def add_signature_overlay(self, x, y, png_bytes):
        item = SignatureOverlay(png_bytes, self)
        item.setZValue(10)
        self.scene.addItem(item)
        item.setPos(x, y)
        self.scene.clearSelection()
        item.setSelected(True)
        self.pending_items.append(item)
        return item

    @property
    def store(self):
        if self._store is None:
            from .core import SignatureStore
            self._store = SignatureStore()
        return self._store

    def forget_mark(self, kind):
        try:
            self.store.delete(kind)
            if self.mode == kind:
                self.mode = 'select'
                self.mark_bytes = None
                self.view.placing = False
                self.view.unsetCursor()
            self.statusBar().showMessage(f'Saved {kind} forgotten. Marks already placed on PDFs are unchanged.')
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot forget signature', str(exc))
            return False

    def add_saved_mark(self, kind):
        if not self.document:
            return False
        try:
            dialog = SignatureDialog(kind, self.store, self, save_mode='additional')
            if dialog.exec() != QDialog.Accepted:
                return False
            self.mark_bytes = self.store.load_mark(kind, dialog.saved_id)
            self.set_mode(kind)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot save signature', str(exc))
            return False

    def choose_saved_mark(self, kind):
        if not self.document:
            return False
        try:
            dialog = SignatureLibraryDialog(kind, self.store, self)
            if dialog.exec() != QDialog.Accepted:
                return False
            data = self.store.load_mark(kind, dialog.selected_id)
            if data is None:
                raise ValueError('That saved signature is no longer available')
            self.mark_bytes = data
            self.set_mode(kind)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot load signature', str(exc))
            return False

    def use_once(self, kind):
        if not self.document:
            return False
        dialog = SignatureDialog(kind, None, self, save_mode='once')
        if dialog.exec() != QDialog.Accepted:
            return False
        self.mark_bytes = dialog.result_png
        self.set_mode(kind)
        self.statusBar().showMessage('Click to place this signature · not added to saved signatures')
        return True

    def use_mark(self, kind, replace=False):
        if not self.document:
            return False
        try:
            data = self.store.load(kind)
            if replace or data is None:
                dialog = SignatureDialog(kind, self.store, self)
                if dialog.exec() != QDialog.Accepted:
                    return False
                data = self.store.load(kind)
            self.mark_bytes = data
            self.set_mode(kind)
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot load signature', str(exc))
            return False

    def set_mode(self, mode):
        if not self.document:
            return
        for item in self.pending_items:
            if isinstance(item, TextOverlay):
                item.setTextInteractionFlags(Qt.NoTextInteraction)
                item.clearFocus()
        self.mode = mode
        if mode not in ('signature', 'initials'):
            self.mark_bytes = None
        self.view.placing = mode != 'select'
        self.view.text_placing = mode == 'text'
        self.select_action.setChecked(mode == 'select')
        self.text_action.setChecked(mode == 'text')
        if mode == 'text':
            self.scene.clearSelection()
            self._font_target = None
            self.show_text_size(self.default_text_size, enabled=True)
        elif mode == 'select':
            self.sync_text_size()
        else:
            self._font_target = None
            self.font_size_input.setEnabled(False)
            self.text_toolbar.setVisible(False)
        if self.view.placing:
            self.view.setCursor(Qt.CrossCursor)
        else:
            self.view.unsetCursor()
        self.statusBar().showMessage('Drag a text box or click to place' if mode == 'text' else
                                    'Select and drag to move · corner to resize · double-click text to edit')

    def place_at(self, x, y):
        if self.mode == 'text':
            return self.create_text_area(x, y, 200., 36.)
        if self.mode in ('signature', 'initials'):
            self.add_signature_overlay(x, y, self.mark_bytes)
            self.mark_bytes = None
        self.set_mode('select')

    def create_text_area(self, x, y, width, height):
        self.set_mode('select')
        return self.add_text_overlay(x, y, width, height)

    def add_text_overlay(self, x, y, width=200., height=36., *, font_size=None):
        item = TextOverlay(self)
        item.set_text_size(self.default_text_size if font_size is None else font_size)
        item.resize_to(width, height)
        item.setZValue(10)
        self.scene.addItem(item)
        item.setPos(x, y)
        self.pending_items.append(item)
        if not self._rebuilding:
            item.start_editing()
        return item

    def delete_selected(self):
        if not self.document or not self.commit_pending():
            return
        for item in list(self.pending_items):
            if item.isSelected():
                if item.mark_id:
                    self.document.delete_mark(self.page_index, item.mark_id)
                self.pending_items.remove(item)
                self.scene.removeItem(item)

    def undo_edit(self):
        if not self.document:
            return False
        if not self.commit_pending():
            return False
        changed = self.document.undo()
        if changed:
            # Undo is new unsaved work even if the core restores an initially clean snapshot.
            self.document.dirty = True
        self.show_page()
        return changed

    def choose_save(self):
        if not self.document:
            return False
        suggested = self.document.path.with_name(self.document.path.stem + '-filled.pdf')
        path, _ = QFileDialog.getSaveFileName(self, 'Save a copy', str(suggested), 'PDF files (*.pdf)')
        return self.save_copy(path, overwrite=Path(path).exists()) if path else False

    def save_copy(self, path, overwrite=False):
        if not self.document or not self.commit_pending():
            return False
        try:
            self.document.save_copy(path, overwrite=overwrite)
            self.document.dirty = False
            self.setWindowTitle(f'{self.document.path.name} — Omapreview')
            self.statusBar().showMessage(f'Saved copy: {path}')
            return True
        except Exception as exc:
            QMessageBox.warning(self, 'Cannot save copy', str(exc))
            return False

    def refresh_viewport(self):
        if not self.document:
            return
        if self._fit_width_active:
            self.fit_width()
        else:
            self.page_scroll.relayout()
            self.refresh_background()

    def zoom_by(self, factor):
        if self.document:
            self._fit_width_active = False
            self._raster_timer.stop()
            self.view.scale(factor, factor)
            self.page_scroll.relayout()
            self.refresh_background()

    def fit_width(self):
        if self.document:
            self._fit_width_active = True
            self._raster_timer.stop()
            width = max(w for w, h in self.page_scroll.sizes)
            factor = max(0.1, (self.page_scroll.viewport().width() - 36) / width)
            self.view.resetTransform()
            self.view.scale(factor, factor)
            self.page_scroll.relayout()
            self.refresh_background()
