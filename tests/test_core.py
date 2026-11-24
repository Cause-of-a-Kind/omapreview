"""Real-file core acceptance tests; all fixtures are generated locally."""
from pathlib import Path
import os
import stat
import pytest
import pymupdf
from omapreview.core import Document


def make_pdf(tmp_path, *, rotation=0, crop=False, encrypted=False):
    path = tmp_path / 'source.pdf'
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=500)
    page.insert_text((70, 90), 'Original')
    if crop:
        page.set_cropbox(pymupdf.Rect(40, 50, 350, 450))
    page.set_rotation(rotation)
    options = dict(encryption=pymupdf.PDF_ENCRYPT_AES_256, owner_pw='owner', user_pw='secret') if encrypted else {}
    pdf.save(path, **options)
    pdf.close()
    return path


def test_open_normalizes_rotation_without_changing_source(tmp_path):
    path = make_pdf(tmp_path, rotation=90)
    before = path.read_bytes()
    doc = Document(path)
    assert doc.path == path
    assert doc.page_count == 1
    assert isinstance(doc.pdf, pymupdf.Document)
    assert doc.pdf[0].rotation == 0
    assert tuple(doc.pdf[0].rect) == (0, 0, 500, 400)
    assert not doc.dirty
    assert path.read_bytes() == before


def test_encrypted_requires_correct_password(tmp_path):
    path = make_pdf(tmp_path, encrypted=True)
    for password in (None, 'wrong'):
        with pytest.raises(ValueError, match='password'):
            Document(path, password=password)
    assert Document(path, password='secret').page_count == 1


@pytest.mark.parametrize('rotation', [0, 90, 180, 270])
def test_crop_render_keeps_visible_page(tmp_path, rotation):
    path = make_pdf(tmp_path, rotation=rotation, crop=True)
    original = pymupdf.open(path)
    expected = original[0].get_pixmap()
    doc = Document(path)
    actual = doc.render(0, scale=1)
    assert (actual.width, actual.height) == (expected.width, expected.height)
    assert actual.samples == expected.samples


def widget_pdf(tmp_path):
    path = make_pdf(tmp_path)
    pdf = pymupdf.open(path)
    page = pdf[0]
    for i, (name, kind, value, flags) in enumerate([
        ('name', pymupdf.PDF_WIDGET_TYPE_TEXT, 'Existing value', 0),
        ('locked', pymupdf.PDF_WIDGET_TYPE_TEXT, 'Keep', pymupdf.PDF_FIELD_IS_READ_ONLY),
        ('check', pymupdf.PDF_WIDGET_TYPE_CHECKBOX, 'Off', 0),
        ('choice', pymupdf.PDF_WIDGET_TYPE_COMBOBOX, 'Red', 0),
        ('signature', pymupdf.PDF_WIDGET_TYPE_SIGNATURE, '', 0),
    ]):
        widget = pymupdf.Widget()
        widget.field_name, widget.field_type, widget.field_value = name, kind, value
        widget.field_flags = flags
        widget.rect = pymupdf.Rect(40, 120 + i * 45, 200, 150 + i * 45)
        if name == 'choice':
            widget.choice_values = ['Red', 'Blue']
        page.add_widget(widget)
    data = pdf.tobytes()
    pdf.close()
    path.write_bytes(data)
    return path


def test_fields_describe_native_values(tmp_path):
    doc = Document(widget_pdf(tmp_path))
    fields = {f['name']: f for f in doc.fields(0)}
    assert fields['name']['value'] == 'Existing value'
    assert fields['name']['type'] == 'text'
    assert fields['locked']['readonly'] is True
    assert fields['check']['type'] == 'checkbox'
    assert fields['choice']['choices'] == ['Red', 'Blue']
    assert fields['choice']['type'] == 'choice'
    assert fields['signature']['type'] == 'signature'
    assert all(isinstance(x, float) for x in fields['name']['rect'])
    assert isinstance(fields['name']['xref'], int)


def test_set_field_native_update_and_validation(tmp_path):
    doc = Document(widget_pdf(tmp_path))
    fields = {f['name']: f for f in doc.fields(0)}
    doc.set_field(0, fields['name']['xref'], 'Typed text')
    doc.set_field(0, fields['check']['xref'], True)
    doc.set_field(0, fields['choice']['xref'], 'Blue')
    assert doc.dirty
    values = {f['name']: f['value'] for f in doc.fields(0)}
    assert values['name'] == 'Typed text'
    assert values['check'] != 'Off'
    assert values['choice'] == 'Blue'
    for name, value in [('locked', 'No'), ('choice', 'Green'), ('signature', 'No')]:
        with pytest.raises(ValueError):
            doc.set_field(0, fields[name]['xref'], value)
    with pytest.raises(ValueError):
        doc.set_field(0, 999999, 'No')
    doc.render(0)
    assert {f['name']: f['value'] for f in doc.fields(0)} == values


def test_save_copy_roundtrip_and_source_protection(tmp_path):
    source = widget_pdf(tmp_path)
    before = source.read_bytes()
    doc = Document(source)
    doc.set_field(0, doc.fields(0)[0]['xref'], 'Round trip')
    target = tmp_path / 'copy.pdf'
    doc.save_copy(target)
    assert Document(target).fields(0)[0]['value'] == 'Round trip'
    assert source.read_bytes() == before
    with pytest.raises(FileExistsError):
        doc.save_copy(target)
    doc.save_copy(target, overwrite=True)
    hardlink = tmp_path / 'hard.pdf'
    hardlink.hardlink_to(source)
    symlink = tmp_path / 'link.pdf'
    symlink.symlink_to(source)
    dangling = tmp_path / 'dangling.pdf'
    dangling.symlink_to(tmp_path / 'absent.pdf')
    for forbidden in (source, hardlink, symlink, dangling):
        with pytest.raises((ValueError, FileExistsError)):
            doc.save_copy(forbidden, overwrite=True)
    assert source.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ['copy.pdf', 'dangling.pdf', 'hard.pdf', 'link.pdf', 'source.pdf']


def test_add_unicode_plain_text_survives_save(tmp_path):
    doc = Document(widget_pdf(tmp_path))
    text = 'Café Ελληνικά 日本語 <b>literal</b> & text'
    doc.add_text(0, [20, 20, 380, 110], text, font_size=12)
    assert doc.dirty
    extracted = doc.pdf[0].get_text()
    assert 'Café' in extracted
    assert 'Ελληνικά' in extracted
    assert '日本語' in extracted
    assert '<b>literal</b> & text' in extracted
    target = tmp_path / 'unicode.pdf'
    doc.render(0)
    doc.save_copy(target)
    copy = Document(target)
    assert copy.fields(0)[0]['value'] == 'Existing value'
    assert '日本語' in copy.pdf[0].get_text()


def ink_png():
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QBuffer, QIODevice
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill(QColor('transparent'))
    for x in range(20, 60):
        for y in range(12, 28):
            image.setPixelColor(x, y, QColor('black'))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(buffer, 'PNG')
    return bytes(buffer.data())


def test_signature_image_roundtrip(tmp_path):
    doc = Document(make_pdf(tmp_path))
    before = doc.render(0).samples
    doc.add_signature(0, [20, 200, 180, 280], ink_png())
    assert doc.dirty
    assert doc.render(0).samples != before
    target = tmp_path / 'signed.pdf'
    doc.save_copy(target)
    assert len(Document(target).pdf[0].get_images()) == 1


def test_undo_redo_all_edits_and_branch(tmp_path):
    doc = Document(widget_pdf(tmp_path))
    assert not doc.can_undo and not doc.can_redo
    assert not doc.undo() and not doc.redo()
    xref = doc.fields(0)[0]['xref']
    doc.set_field(0, xref, 'Edited')
    doc.add_text(0, [10, 350, 300, 400], 'Undo text')
    doc.add_signature(0, [20, 400, 180, 480], ink_png())
    assert doc.undo()
    assert not doc.pdf[0].get_images()
    assert 'Undo text' in doc.pdf[0].get_text()
    assert doc.undo()
    assert 'Undo text' not in doc.pdf[0].get_text()
    assert doc.fields(0)[0]['value'] == 'Edited'
    assert doc.undo()
    assert doc.fields(0)[0]['value'] == 'Existing value'
    assert not doc.dirty
    assert not doc.undo()
    assert doc.redo() and doc.redo() and doc.redo()
    assert doc.dirty and not doc.can_redo
    assert len(doc.pdf[0].get_images()) == 1
    assert doc.undo()
    doc.set_field(0, xref, 'Branch')
    assert not doc.can_redo
    assert not doc.redo()


def radio_pdf(tmp_path):
    path = make_pdf(tmp_path)
    pdf = pymupdf.open(path)
    page = pdf[0]
    refs = []
    for i, state in enumerate(('Alpha', 'Beta')):
        widget = pymupdf.Widget()
        widget.field_name = 'group'
        widget.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
        widget.rect = pymupdf.Rect(30 + i * 50, 120, 50 + i * 50, 140)
        added = page.add_widget(widget)
        refs.append(added.xref)
        yes = pdf.xref_get_key(added.xref, 'AP/N/Yes')[1]
        off = pdf.xref_get_key(added.xref, 'AP/N/Off')[1]
        pdf.xref_set_key(added.xref, 'AP/N', f'<< /Off {off} /{state} {yes} >>')
    parent = pdf.get_new_xref()
    pdf.update_object(parent, f'<< /FT /Btn /Ff 32768 /T (group) /V /Off /Kids [{" ".join(f"{r} 0 R" for r in refs)}] >>')
    for ref in refs:
        pdf.xref_set_key(ref, 'Parent', f'{parent} 0 R')
        pdf.xref_set_key(ref, 'Ff', '32768')
        pdf.xref_set_key(ref, 'T', 'null')
    pdf.xref_set_key(pdf.pdf_catalog(), 'AcroForm/Fields', f'[{parent} 0 R]')
    data = pdf.tobytes()
    pdf.close()
    path.write_bytes(data)
    return path


def test_radio_group_selects_one_native_button(tmp_path):
    doc = Document(radio_pdf(tmp_path))
    fields = doc.fields(0)
    assert [f['type'] for f in fields] == ['radio', 'radio']
    doc.set_field(0, fields[0]['xref'], 'Alpha')
    doc.set_field(0, fields[1]['xref'], 'Beta')
    assert doc.pdf.xref_get_key(fields[0]['xref'], 'AS')[1] == '/Off'
    assert doc.pdf.xref_get_key(fields[1]['xref'], 'AS')[1] == '/Beta'
    target = tmp_path / 'radio.pdf'
    doc.save_copy(target)
    copy = Document(target)
    assert copy.pdf.xref_get_key(fields[0]['xref'], 'AS')[1] == '/Off'
    assert copy.pdf.xref_get_key(fields[1]['xref'], 'AS')[1] == '/Beta'
    with pytest.raises(ValueError):
        doc.set_field(0, fields[0]['xref'], 'Invalid')


def test_signature_store_private_trimmed_roundtrip(tmp_path, monkeypatch):
    from omapreview.core import SignatureStore
    from PySide6.QtGui import QImage
    monkeypatch.setenv('XDG_DATA_HOME', str(tmp_path / 'data'))
    store = SignatureStore()
    root = tmp_path / 'data' / 'omapreview' / 'signatures'
    assert store.load('signature') is None
    store.save('signature', ink_png())
    data = store.load('signature')
    image = QImage.fromData(data, 'PNG')
    assert (image.width(), image.height()) == (40, 16)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE((root / 'signature.png').stat().st_mode) == 0o600
    store.save('initials', ink_png())
    store.save('signature', ink_png())
    assert store.load('initials')
    store.delete('signature')
    store.delete('signature')
    assert store.load('signature') is None
    assert sorted(p.name for p in root.iterdir()) == ['initials.png']


@pytest.mark.parametrize('kind', ['../escape', 'other', '', '/absolute'])
def test_signature_store_rejects_unknown_kind(tmp_path, kind):
    from omapreview.core import SignatureStore
    store = SignatureStore(tmp_path / 'store')
    if kind == '/absolute':
        kind = str(tmp_path / 'absolute')
    for action in (lambda: store.save(kind, ink_png()), lambda: store.load(kind), lambda: store.delete(kind)):
        with pytest.raises(ValueError):
            action()


@pytest.mark.parametrize('data', [b'not png', b'\x89PNG\r\n\x1a\ninvalid', b''])
def test_signature_store_rejects_invalid_png_without_replacing(tmp_path, data):
    from omapreview.core import SignatureStore
    store = SignatureStore(tmp_path / 'store')
    store.save('signature', ink_png())
    before = store.load('signature')
    with pytest.raises(ValueError, match='PNG'):
        store.save('signature', data)
    assert store.load('signature') == before


def test_signature_store_refuses_symlink_roots_and_entries(tmp_path):
    from omapreview.core import SignatureStore
    real = tmp_path / 'real'
    real.mkdir(mode=0o755)
    alias = tmp_path / 'alias'
    alias.symlink_to(real, target_is_directory=True)
    with pytest.raises(ValueError):
        SignatureStore(alias)
    assert stat.S_IMODE(real.stat().st_mode) == 0o755
    with pytest.raises(ValueError):
        SignatureStore(alias / 'nested')
    store = SignatureStore(tmp_path / 'store')
    private = tmp_path / 'outside.png'
    private.write_bytes(ink_png())
    target = store.root / 'signature.png'
    target.symlink_to(private)
    for action in (lambda: store.save('signature', ink_png()), lambda: store.load('signature'), lambda: store.delete('signature')):
        with pytest.raises(ValueError):
            action()
    assert private.read_bytes() == ink_png()


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_rotated_cropped_fields_keep_values_and_coordinates(tmp_path, rotation):
    path = widget_pdf(tmp_path)
    pdf = pymupdf.open(path)
    page = pdf[0]
    page.set_cropbox(pymupdf.Rect(20, 50, 350, 450))
    page.set_rotation(rotation)
    original_rect = next(page.widgets()).rect * page.rotation_matrix
    data = pdf.tobytes()
    pdf.close()
    path.write_bytes(data)
    doc = Document(path)
    field = doc.fields(0)[0]
    assert field['rect'] == pytest.approx(list(original_rect))
    assert field['value'] == 'Existing value'
    doc.set_field(0, field['xref'], 'Changed')
    doc.render(0)
    target = tmp_path / 'rotated-copy.pdf'
    doc.save_copy(target)
    assert Document(target).fields(0)[0]['value'] == 'Changed'


def test_normalization_preserves_selected_radio(tmp_path):
    path = radio_pdf(tmp_path)
    pdf = pymupdf.open(path)
    widgets = list(pdf[0].widgets())
    xref = widgets[0].xref
    parent = int(pdf.xref_get_key(xref, 'Parent')[1].split()[0])
    pdf.xref_set_key(xref, 'AS', '/Alpha')
    pdf.xref_set_key(parent, 'V', '/Alpha')
    pdf[0].set_rotation(90)
    data = pdf.tobytes()
    pdf.close()
    path.write_bytes(data)
    doc = Document(path)
    assert doc.pdf.xref_get_key(xref, 'AS')[1] == '/Alpha'
    assert doc.pdf.xref_get_key(parent, 'V')[1] == '/Alpha'
    assert doc.pdf[0].load_widget(xref).on_state() == 'Alpha'


@pytest.mark.parametrize('background', ['white', 'transparent'])
def test_signature_store_rejects_blank_png(tmp_path, background):
    from omapreview.core import SignatureStore
    from PySide6.QtGui import QImage, QColor
    from PySide6.QtCore import QBuffer, QIODevice
    image = QImage(20, 20, QImage.Format.Format_ARGB32)
    image.fill(QColor(background))
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, 'PNG')
    store = SignatureStore(tmp_path / 'store')
    with pytest.raises(ValueError, match='ink'):
        store.save('signature', bytes(buffer.data()))
    assert store.load('signature') is None


def test_radio_boolean_edit_and_undo(tmp_path):
    doc = Document(radio_pdf(tmp_path))
    xref = doc.fields(0)[0]['xref']
    doc.set_field(0, xref, True)
    assert doc.pdf.xref_get_key(xref, 'AS')[1] == '/Alpha'
    doc.set_field(0, xref, False)
    assert doc.pdf.xref_get_key(xref, 'AS')[1] == '/Off'
    assert doc.undo()
    assert doc.pdf.xref_get_key(xref, 'AS')[1] == '/Alpha'


def test_failed_image_edit_preserves_redo_and_values(tmp_path):
    doc = Document(widget_pdf(tmp_path))
    xref = doc.fields(0)[0]['xref']
    doc.set_field(0, xref, 'To redo')
    assert doc.undo()
    with pytest.raises(Exception):
        doc.add_signature(0, [20, 20, 100, 100], b'invalid image')
    assert not doc.dirty and not doc.can_undo and doc.can_redo
    assert doc.fields(0)[0]['value'] == 'Existing value'
    assert doc.redo()
    assert doc.fields(0)[0]['value'] == 'To redo'


@pytest.mark.parametrize('rotation', [90, 180, 270])
def test_normalization_keeps_widget_appearance(tmp_path, rotation):
    path = widget_pdf(tmp_path)
    pdf = pymupdf.open(path)
    pdf[0].set_rotation(rotation)
    data = pdf.tobytes()
    pdf.close()
    path.write_bytes(data)
    original = pymupdf.open(path)
    expected_pix = original[0].get_pixmap()
    expected = expected_pix.samples
    doc = Document(path)
    actual = doc.render(0, scale=1).samples
    # Unsigned signature fields have a dynamic MuPDF signing icon, not a
    # stable filled appearance. Compare editable fields, including text glyphs.
    for field in doc.fields(0):
        if field['type'] == 'signature':
            continue
        x0, y0, x1, y1 = map(int, field['rect'])
        offsets = (3 * (y * expected_pix.width + x) + c
                   for y in range(y0, y1) for x in range(x0, x1) for c in range(3))
        assert sum(abs(actual[i] - expected[i]) > 2 for i in offsets) < 100
