"""Signature-library and sign-once flows use generated marks only."""
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtWidgets import QDialog, QDialogButtonBox
from omapreview.ui import SignatureDialog


def generated_ink():
    image = QImage(300, 100, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setPen(QPen(QColor('#151515'), 4, Qt.SolidLine, Qt.RoundCap))
    painter.drawLine(20, 70, 270, 30)
    painter.end()
    return image


def test_sign_once_dialog_uses_ink_without_saving_to_library(qtbot, tmp_path, monkeypatch):
    library_root = tmp_path / 'not-created-library'
    monkeypatch.setenv('XDG_DATA_HOME', str(library_root))
    dialog = SignatureDialog('signature', None, save_mode='once')
    qtbot.addWidget(dialog)
    dialog.canvas.set_image(generated_ink())
    dialog.show()
    buttons = dialog.findChild(QDialogButtonBox)
    accept = buttons.button(QDialogButtonBox.Save)
    assert accept.text() == 'Use signature'
    qtbot.mouseClick(accept, Qt.LeftButton)
    assert dialog.result() == QDialog.Accepted
    assert dialog.result_png.startswith(b'\x89PNG')
    assert not library_root.exists()


def test_sign_once_places_and_saves_pdf_without_changing_saved_marks(qtbot, tmp_path):
    import pymupdf
    from PySide6.QtCore import QTimer, QPointF
    from PySide6.QtWidgets import QApplication
    from omapreview.ui import MainWindow
    from omapreview.core import SignatureStore
    source = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    pdf.new_page(width=400, height=500)
    pdf.save(source)
    pdf.close()
    store = SignatureStore(tmp_path / 'library')
    saved = SignatureDialog('signature', store)
    qtbot.addWidget(saved)
    saved.canvas.set_image(generated_ink())
    assert saved.save_mark()
    before = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    window = MainWindow(signature_store=store)
    qtbot.addWidget(window)
    window.show()
    assert window.open_file(source)
    def sign():
        modal = QApplication.activeModalWidget()
        assert isinstance(modal, SignatureDialog)
        modal.canvas.set_image(generated_ink())
        button = modal.findChild(QDialogButtonBox).button(QDialogButtonBox.Save)
        qtbot.mouseClick(button, Qt.LeftButton)
    assert hasattr(window, 'use_once'), 'Sign once action is missing'
    QTimer.singleShot(0, sign)
    assert window.use_once('signature')
    qtbot.mouseClick(window.view.viewport(), Qt.LeftButton,
                     pos=window.view.mapFromScene(QPointF(60, 100)))
    assert len(window.pending_items) == 1
    assert window.mark_bytes is None
    assert window.mode == 'select'
    output = tmp_path / 'signed-once.pdf'
    assert window.save_copy(output)
    with pymupdf.open(output) as reopened:
        assert len(reopened[0].get_image_info()) == 1
    after = {p.relative_to(store.root): p.read_bytes() for p in store.root.rglob('*') if p.is_file()}
    assert after == before


def test_additional_signature_capture_keeps_primary_and_has_a_name(qtbot, tmp_path):
    from omapreview.core import SignatureStore
    store = SignatureStore(tmp_path / 'library')
    primary = SignatureDialog('signature', store)
    qtbot.addWidget(primary)
    primary.canvas.set_image(generated_ink())
    primary.save_mark()
    primary_png = store.load('signature')
    dialog = SignatureDialog('signature', store, save_mode='additional')
    qtbot.addWidget(dialog)
    dialog.name_edit.setText('Second signer')
    dialog.canvas.set_image(generated_ink())
    dialog.show()
    qtbot.mouseClick(dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.Save), Qt.LeftButton)
    assert dialog.result() == QDialog.Accepted
    assert dialog.saved_id != 'primary'
    assert store.load('signature') == primary_png
    assert store.load_mark('signature', dialog.saved_id)
    assert any(row['name'] == 'Second signer' for row in store.list_marks('signature'))


def test_saved_signature_picker_selects_and_forgets_individual_marks(qtbot, tmp_path):
    from omapreview.core import SignatureStore
    from omapreview import ui
    store = SignatureStore(tmp_path / 'library')
    capture = SignatureDialog('signature', store)
    qtbot.addWidget(capture)
    capture.canvas.set_image(generated_ink())
    capture.save_mark()
    extra_id = store.save_additional('signature', 'Second signer', store.load('signature'))
    assert hasattr(ui, 'SignatureLibraryDialog'), 'Saved signatures picker is missing'
    chooser = ui.SignatureLibraryDialog('signature', store)
    qtbot.addWidget(chooser)
    chooser.show()
    assert chooser.list_widget.count() == 2
    assert chooser.list_widget.item(0).data(Qt.UserRole) == 'primary'
    chooser.list_widget.setCurrentRow(1)
    qtbot.mouseClick(chooser.use_button, Qt.LeftButton)
    assert chooser.result() == QDialog.Accepted
    assert chooser.selected_id == extra_id
    chooser = ui.SignatureLibraryDialog('signature', store)
    qtbot.addWidget(chooser)
    chooser.show()
    chooser.list_widget.setCurrentRow(1)
    qtbot.mouseClick(chooser.remove_button, Qt.LeftButton)
    assert chooser.list_widget.count() == 1
    assert store.load_mark('signature', extra_id) is None
    assert store.load('signature') is not None


def test_signature_menu_chooses_additional_and_creates_another_without_replacing_primary(qtbot, tmp_path):
    import pymupdf
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QToolButton
    from omapreview.core import SignatureStore
    from omapreview import ui
    store = SignatureStore(tmp_path / 'library')
    capture = SignatureDialog('signature', store)
    qtbot.addWidget(capture)
    capture.canvas.set_image(generated_ink())
    capture.save_mark()
    primary = store.load('signature')
    extra = store.save_additional('signature', 'Another signer', primary)
    source = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    pdf.new_page(width=400, height=500)
    pdf.save(source)
    pdf.close()
    window = ui.MainWindow(signature_store=store)
    qtbot.addWidget(window)
    window.show()
    window.open_file(source)
    button = next(b for b in window.toolbar.findChildren(QToolButton) if b.text() == 'Signature')
    actions = {a.text(): a for a in button.menu().actions()}
    assert {'Sign once…', 'Add saved signature…', 'Saved signatures…'} <= actions.keys()
    def choose():
        modal = QApplication.activeModalWidget()
        assert isinstance(modal, ui.SignatureLibraryDialog)
        modal.list_widget.setCurrentRow(1)
        qtbot.mouseClick(modal.use_button, Qt.LeftButton)
    QTimer.singleShot(0, choose)
    actions['Saved signatures…'].trigger()
    assert window.mode == 'signature'
    assert window.mark_bytes == store.load_mark('signature', extra)
    window.place_at(40, 60)
    assert window.commit_pending()
    assert len(window.document.marks(0)) == 1
    def add():
        modal = QApplication.activeModalWidget()
        assert isinstance(modal, SignatureDialog)
        modal.name_edit.setText('Third signer')
        modal.canvas.set_image(generated_ink())
        qtbot.mouseClick(modal.findChild(QDialogButtonBox).button(QDialogButtonBox.Save), Qt.LeftButton)
    QTimer.singleShot(0, add)
    actions['Add saved signature…'].trigger()
    assert window.mode == 'signature'
    assert len(store.list_marks('signature')) == 3
    assert store.load('signature') == primary
    window.place_at(40, 160)
    assert window.save_copy(tmp_path / 'two-signers.pdf')
    with pymupdf.open(tmp_path / 'two-signers.pdf') as reopened:
        assert len(reopened[0].get_image_info()) == 2


def test_cancel_sign_once_never_initializes_signature_library(qtbot, tmp_path, monkeypatch):
    import pymupdf
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from omapreview.ui import MainWindow
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    source = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(source)
    pdf.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    assert window.open_file(source)
    before = source.read_bytes()
    QTimer.singleShot(0, lambda: QApplication.activeModalWidget().reject())
    assert not window.use_once('signature')
    assert window._store is None
    assert not (tmp_path / 'data').exists()
    assert not window.pending_items
    assert source.read_bytes() == before


def test_select_tool_cancels_unplaced_one_time_signature(qtbot, tmp_path, monkeypatch):
    import pymupdf
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication
    from omapreview.ui import MainWindow
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    source = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    pdf.new_page()
    pdf.save(source)
    pdf.close()
    window = MainWindow()
    qtbot.addWidget(window)
    window.show()
    window.open_file(source)
    def accept_once():
        modal = QApplication.activeModalWidget()
        modal.canvas.set_image(generated_ink())
        modal.save_mark()
    QTimer.singleShot(0, accept_once)
    assert window.use_once('signature')
    assert window.mark_bytes
    window.set_mode('select')
    assert window.mark_bytes is None
    assert window._store is None
    assert not (tmp_path / 'data').exists()
    assert not window.pending_items
