"""Crash regressions run in a disposable offscreen Qt subprocess."""
import os
import subprocess
import sys
import textwrap


def test_toolbar_mouse_undo_navigation_with_focused_fields_survives(tmp_path):
    code = textwrap.dedent('''
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        import sys
        from pathlib import Path
        import pymupdf
        from PySide6.QtWidgets import QApplication
        from PySide6.QtCore import Qt, QEvent
        from PySide6.QtTest import QTest
        from omapreview.ui import MainWindow
        app = QApplication([])
        root = Path(sys.argv[1])
        source = root / 'generated.pdf'
        pdf = pymupdf.open()
        page = pdf.new_page(width=400, height=500)
        field = pymupdf.Widget()
        field.field_name = 'Generated name'
        field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
        field.rect = pymupdf.Rect(40, 60, 240, 85)
        page.add_widget(field)
        pdf.new_page(width=400, height=500)
        pdf.save(source)
        pdf.close()
        window = MainWindow()
        window.confirm_leave = lambda: True
        window.show()
        assert window.open_file(source)
        def settle():
            app.processEvents()
            app.sendPostedEvents(None, QEvent.DeferredDelete)
            app.processEvents()
        def click(action):
            QTest.mouseClick(window.toolbar.widgetForAction(action), Qt.LeftButton)
            settle()
        for n in range(35):
            native = window.field_widgets[0][1]
            native.setFocus()
            QTest.keyClicks(native, 'Generated entry ' + str(n))
            settle()
            click(window.next_action)
            assert window.page_index == 1
            assert window.document.fields(0)[0]['value']
            click(window.previous_action)
            assert window.page_index == 0
            overlay = window.add_text_overlay(50, 160)
            QTest.keyClicks(window.view.viewport(), 'Generated text ' + str(n))
            click(window.undo_action)
            assert not window.pending_items
            window.add_text_overlay(50, 160).setPlainText('Retained ' + str(n))
            assert window.save_copy(root / 'copy.pdf', overwrite=True)
            click(window.next_action)
            click(window.previous_action)
            assert len(window.pending_items) == 1
            window.pending_items[0].setSelected(True)
            window.delete_selected()
            assert not window.pending_items
            settle()
        settle()
        assert not window._retired_scenes
        window.close()
        settle()
        print('35 focused toolbar undo/navigation/save cycles survived')
    ''')
    result = subprocess.run([sys.executable, '-c', code, str(tmp_path)],
                            env={**os.environ, 'QT_QPA_PLATFORM': 'offscreen'},
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    assert '35 focused toolbar undo/navigation/save cycles survived' in result.stdout
