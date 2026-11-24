"""Generate a disposable example PDF; no signatures or personal data."""
from pathlib import Path
import argparse
import pymupdf


def make_example(destination):
    doc = pymupdf.open()
    page = doc.new_page(width=612, height=792)
    page.insert_text((52, 65), 'Omapreview', fontsize=26)
    page.insert_text((52, 92), 'Fill a field. Add a note. Place your signature.', fontsize=12)
    page.insert_text((52, 151), 'Name', fontsize=11)
    field = pymupdf.Widget()
    field.field_name = 'name'
    field.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    field.rect = pymupdf.Rect(52, 162, 380, 194)
    field.field_value = ''
    field.border_width = 1
    field.border_color = (0.65, 0.65, 0.65)
    field.text_fontsize = 12
    page.add_widget(field)
    page.insert_text((78, 240), 'I have reviewed this example.', fontsize=11)
    check = pymupdf.Widget()
    check.field_name = 'reviewed'
    check.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    check.rect = pymupdf.Rect(52, 224, 69, 241)
    page.add_widget(check)
    page.insert_text((52, 302), 'Add text anywhere below (this is not a form field):', fontsize=11)
    page.draw_line((52, 359), (550, 359), color=(0.7, 0.7, 0.7))
    page.insert_text((52, 445), 'Signature', fontsize=11)
    page.draw_line((52, 520), (350, 520), color=(0.7, 0.7, 0.7))
    page.insert_text((414, 445), 'Initials', fontsize=11)
    page.draw_line((414, 520), (550, 520), color=(0.7, 0.7, 0.7))
    page.insert_text((52, 699), 'Example document only. Save a copy to keep the original unchanged.', fontsize=10)
    doc.save(str(destination))
    doc.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('destination', type=Path)
    args = parser.parse_args()
    if args.destination.exists():
        parser.error('Destination already exists')
    make_example(args.destination)
