"""Native widget typography, using generated PDFs only."""
import math

import pymupdf
import pytest

from omapreview.core import Document


def native_doc(tmp_path, *, size=0, rect=(30, 30, 330, 150), value='Hi', flags=0,
               kind=pymupdf.PDF_WIDGET_TYPE_TEXT, font='Helv'):
    pdf = pymupdf.open()
    page = pdf.new_page(width=400, height=400)
    widget = pymupdf.Widget()
    widget.field_name = 'entry'
    widget.field_type = kind
    widget.field_value = value if kind == pymupdf.PDF_WIDGET_TYPE_TEXT else 'Off'
    widget.rect = pymupdf.Rect(rect)
    widget.text_fontsize = size
    widget.text_font = font
    widget.field_flags = flags
    page.add_widget(widget)
    source = tmp_path / 'source.pdf'
    pdf.save(source)
    pdf.close()
    return Document(source)


def spans(doc):
    # Widget appearances are not returned by page.get_text: inspect the actual
    # appearance Form XObject through its page-equivalent PDF representation.
    page = doc.pdf[0]
    widget = page.load_widget(doc.fields(0)[0]['xref'])
    kind, ref = doc.pdf.xref_get_key(widget.xref, 'AP/N')
    assert kind == 'xref'
    ap = int(ref.split()[0])
    probe = pymupdf.open(stream=doc.pdf.tobytes(), filetype='pdf')
    probe.xref_set_key(probe[0].xref, 'Contents', f'{ap} 0 R')
    resources = probe.xref_get_key(ap, 'Resources')[1]
    probe.xref_set_key(probe[0].xref, 'Resources', resources)
    probe.xref_set_key(probe[0].xref, 'Annots', '[]')
    bbox = probe.xref_get_key(ap, 'BBox')[1]
    probe.xref_set_key(probe[0].xref, 'MediaBox', bbox)
    probe.xref_set_key(probe[0].xref, 'CropBox', bbox)
    data = probe.tobytes()
    probe.close()
    probe = pymupdf.open(stream=data, filetype='pdf')
    result = [s for b in probe[0].get_text('dict')['blocks'] if 'lines' in b
              for line in b['lines'] for s in line['spans']]
    probe.close()
    return result


def test_auto_tall_field_reports_and_writes_sane_size(tmp_path):
    doc = native_doc(tmp_path)
    before = doc.path.read_bytes()
    field = doc.fields(0)[0]
    assert field['font_size'] == 0.0
    assert field['font_name'] == 'Helv'
    assert 0 < field['effective_font_size'] <= 12
    assert doc.field_font_size(0, field['xref']) == field['effective_font_size']
    assert not doc.dirty and not doc.can_undo
    doc.set_field(0, field['xref'], 'Hi')
    target = tmp_path / 'copy.pdf'
    doc.save_copy(target)
    copy = Document(target)
    assert copy.fields(0)[0]['font_size'] == pytest.approx(field['effective_font_size'])
    assert [s['size'] for s in spans(copy)] == pytest.approx([field['effective_font_size']])
    assert doc.path.read_bytes() == before


def test_full_long_value_fits_width_and_short_field_height(tmp_path):
    doc = native_doc(tmp_path, rect=(30, 30, 150, 50))
    xref = doc.fields(0)[0]['xref']
    value = 'Wide WWW name (literal) \\ text ' * 4
    size = doc.field_font_size(0, xref, value)
    assert 0 < size < 12
    assert pymupdf.Font('helv').text_length(value, fontsize=size) <= 116
    doc.set_field(0, xref, value)
    assert doc.fields(0)[0]['font_size'] == pytest.approx(size, abs=0.001)
    assert ''.join(s['text'] for s in spans(doc)) == value
    assert all(s['bbox'][2] <= 120 for s in spans(doc))
    assert doc.field_font_size(0, xref, 'short') <= size + 0.001


@pytest.mark.parametrize('size', [10, 18, 24])
def test_explicit_size_is_exact_in_saved_native_appearance_and_undo(tmp_path, size):
    doc = native_doc(tmp_path, size=8)
    xref = doc.fields(0)[0]['xref']
    doc.set_field(0, xref, 'Manual', font_size=size)
    target = tmp_path / 'manual.pdf'
    doc.save_copy(target)
    copy = Document(target)
    assert copy.fields(0)[0]['font_size'] == size
    assert copy.fields(0)[0]['effective_font_size'] == size
    assert [s['size'] for s in spans(copy)] == pytest.approx([size])
    doc.set_field(0, xref, '', font_size=size)
    assert doc.fields(0)[0]['value'] == ''
    assert doc.fields(0)[0]['font_size'] == size
    assert spans(doc) == []
    doc.undo()
    assert doc.fields(0)[0]['value'] == 'Manual'
    doc.undo()
    assert doc.fields(0)[0]['font_size'] == 8
    assert doc.fields(0)[0]['value'] == 'Hi'
    doc.redo()
    assert doc.fields(0)[0]['font_size'] == size


@pytest.mark.parametrize('size', [0, -1, 0.5, 145, float('nan'), float('inf'), 'bad', True])
def test_invalid_explicit_sizes_refuse_without_any_mutation(tmp_path, size):
    doc = native_doc(tmp_path, size=8)
    before = doc.pdf.tobytes(no_new_id=True)
    with pytest.raises(ValueError):
        doc.set_field(0, doc.fields(0)[0]['xref'], 'Changed', font_size=size)
    assert doc.pdf.tobytes(no_new_id=True) == before
    assert not doc.dirty and not doc.can_undo


@pytest.mark.parametrize('kind, flags', [
    (pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_FIELD_IS_READ_ONLY),
    (pymupdf.PDF_WIDGET_TYPE_CHECKBOX, 0),
])
def test_explicit_size_refuses_readonly_and_nontext_without_mutation(tmp_path, kind, flags):
    doc = native_doc(tmp_path, kind=kind, flags=flags)
    before = doc.pdf.tobytes(no_new_id=True)
    with pytest.raises(ValueError):
        doc.set_field(0, doc.fields(0)[0]['xref'], True, font_size=10)
    assert doc.pdf.tobytes(no_new_id=True) == before
    assert not doc.dirty and not doc.can_undo


def test_multiline_uses_wrapping_and_fits_all_lines(tmp_path):
    doc = native_doc(tmp_path, rect=(30, 30, 180, 110),
                     flags=pymupdf.PDF_TX_FIELD_IS_MULTILINE)
    value = 'First line\nSecond line wraps with words across the field\nThird line'
    xref = doc.fields(0)[0]['xref']
    size = doc.field_font_size(0, xref, value)
    assert 10 <= size <= 12
    doc.set_field(0, xref, value)
    appearance = spans(doc)
    assert [s['size'] for s in appearance] == pytest.approx([size] * len(appearance), abs=0.001)
    assert ''.join(s['text'] for s in appearance).replace(' ', '') == value.replace('\n', '').replace(' ', '')
    assert all(0 <= s['bbox'][0] and s['bbox'][2] <= 150 and s['bbox'][3] <= 80
               for s in appearance)


def test_declared_custom_resource_uses_actual_metrics_and_preserves_name(tmp_path):
    doc = native_doc(tmp_path, rect=(30, 30, 150, 70))
    xref = doc.fields(0)[0]['xref']
    page = doc.pdf[0]
    fontref = page.insert_font(fontname='Custom', fontbuffer=pymupdf.Font('cour').buffer)
    doc.pdf.xref_set_key(doc.pdf.pdf_catalog(), 'AcroForm/DR/Font/Custom', f'{fontref} 0 R')
    doc.pdf.xref_set_key(xref, 'DA', pymupdf.get_pdf_str('/Custom 0 Tf 0 g'))
    # A parentless field-widget exercises Widget.update's font sanitization too.
    parent = int(doc.pdf.xref_get_key(xref, 'Parent')[1].split()[0])
    for key in ('T', 'FT', 'V', 'Ff'):
        kind, value = doc.pdf.xref_get_key(parent, key)
        if kind != 'null':
            doc.pdf.xref_set_key(xref, key, pymupdf.get_pdf_str(value) if kind == 'string' else value)
    doc.pdf.xref_set_key(xref, 'Parent', 'null')
    doc._restore((doc.pdf.tobytes(), False))
    text = 'i' * 80
    size = doc.field_font_size(0, xref, text)
    assert pymupdf.Font('cour').text_length(text, fontsize=size) <= 116.001
    assert size < 3
    doc.set_field(0, xref, text)
    assert doc.fields(0)[0]['font_name'] == 'Custom'
    assert [s['size'] for s in spans(doc)] == pytest.approx([size], abs=0.001)


def test_existing_eight_point_default_and_unicode_exact_value(tmp_path):
    doc = native_doc(tmp_path, size=8)
    xref = doc.fields(0)[0]['xref']
    value = 'Café (\\) Ελληνικά 日本語 <literal>'
    doc.set_field(0, xref, value)
    assert doc.fields(0)[0]['font_size'] == 8
    assert doc.fields(0)[0]['value'] == value
    assert math.isfinite(doc.field_font_size(0, xref, value))
    doc.set_field(0, xref, '')
    assert doc.fields(0)[0]['font_size'] == 8
    assert doc.fields(0)[0]['value'] == ''
