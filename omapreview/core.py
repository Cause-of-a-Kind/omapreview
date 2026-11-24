"""Offline PDF editing core. SPDX-License-Identifier: AGPL-3.0-or-later."""
from pathlib import Path
from html import escape
from contextlib import contextmanager
import os
import re
import stat
import tempfile
import json
import base64
import uuid
import math
import pymupdf


class Document:
    """An in-memory working copy; coordinates are top-left, rotation zero."""

    def __init__(self, path, password=None):
        self.path = Path(path)
        self._source_path = self.path.resolve()
        source_stat = self.path.stat()
        self._source_inode = (source_stat.st_dev, source_stat.st_ino)
        self.pdf = pymupdf.open(stream=self.path.read_bytes(), filetype='pdf')
        if self.pdf.needs_pass and (not password or not self.pdf.authenticate(password)):
            self.pdf.close()
            raise ValueError('A valid PDF password is required')
        had_rotation = False
        for page in self.pdf:
            if page.rotation:
                had_rotation = True
                rotation = page.rotation
                crop = page.cropbox
                page.set_cropbox(page.mediabox)
                visible = crop * page.rotation_matrix
                # remove_rotation() calls Widget.update(), which can rewrite
                # radio states and existing appearances. Retain native objects;
                # take only the normalized rectangles from its transformation.
                native = {w.xref: self.pdf.xref_object(w.xref) for w in page.widgets()}
                appearances = {}
                for xref in native:
                    for state in ('N', 'R', 'D'):
                        kind, value = self.pdf.xref_get_key(xref, f'AP/{state}')
                        if kind != 'null':
                            appearances[xref, state] = self._rotated_appearance(value, rotation)
                parents = {}
                for xref in native:
                    kind, parent = self.pdf.xref_get_key(xref, 'Parent')
                    if kind == 'xref':
                        ref = int(parent.split()[0])
                        parents[ref] = self.pdf.xref_object(ref)
                page.remove_rotation()
                for xref, original in native.items():
                    rect = self.pdf.xref_get_key(xref, 'Rect')[1]
                    self.pdf.update_object(xref, original)
                    self.pdf.xref_set_key(xref, 'Rect', rect)
                for xref, original in parents.items():
                    self.pdf.update_object(xref, original)
                for (xref, state), value in appearances.items():
                    self.pdf.xref_set_key(xref, f'AP/{state}', value)
                page.set_cropbox(visible)
        # Drop MuPDF's cached annotation appearances after xref-level repair.
        if had_rotation:
            normalized = pymupdf.open(stream=self.pdf.tobytes(), filetype='pdf')
            self.pdf.close()
            self.pdf = normalized
        self.dirty = False
        self._undo = []
        self._redo = []

    def _rotated_appearance(self, value, rotation):
        """Clone appearance streams before native rotation can regenerate them."""
        def clone(match):
            old = int(match.group(1))
            new = self.pdf.get_new_xref()
            self.pdf.update_object(new, self.pdf.xref_object(old))
            if self.pdf.xref_is_stream(old):
                self.pdf.update_stream(new, self.pdf.xref_stream(old))
                kind, matrix = self.pdf.xref_get_key(old, 'Matrix')
                transform = pymupdf.Matrix(*map(float, matrix.strip('[]').split())) if kind == 'array' else pymupdf.Matrix(1, 1)
                transform *= pymupdf.Matrix(-rotation)
                self.pdf.xref_set_key(new, 'Matrix', '[' + ' '.join(map(str, transform)) + ']')
            else:
                self.pdf.update_object(new, self._rotated_appearance(self.pdf.xref_object(old), rotation))
            return f'{new} 0 R'
        return re.sub(r'(\d+)\s+\d+\s+R', clone, value)

    @property
    def can_undo(self):
        return bool(self._undo)

    @property
    def can_redo(self):
        return bool(self._redo)

    def _restore(self, state):
        restored = pymupdf.open(stream=state[0], filetype='pdf')
        self.pdf.close()
        self.pdf = restored
        self.dirty = state[1]

    def undo(self):
        if not self._undo:
            return False
        self._redo.append((self.pdf.tobytes(), self.dirty))
        self._restore(self._undo.pop())
        return True

    def redo(self):
        if not self._redo:
            return False
        self._undo.append((self.pdf.tobytes(), self.dirty))
        self._restore(self._redo.pop())
        return True

    @contextmanager
    def _edit(self):
        state = (self.pdf.tobytes(), self.dirty)
        try:
            yield
        except Exception:
            self._restore(state)
            raise
        else:
            self._undo.append(state)
            self._redo.clear()
            self.dirty = True

    @property
    def page_count(self):
        return self.pdf.page_count

    def render(self, page_index, scale=1.5, include_marks=True):
        page = self.pdf[page_index]
        if include_marks:
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        original = self.pdf.xref_get_key(page.xref, 'Contents')[1]
        owned = {ref for mark in self.marks(page_index) for ref in mark['streams']}
        retained = [ref for ref in page.get_contents() if ref not in owned]
        try:
            self.pdf.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{ref} 0 R' for ref in retained) + ']')
            return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale))
        finally:
            self.pdf.xref_set_key(page.xref, 'Contents', original)

    def fields(self, page_index):
        types = {
            pymupdf.PDF_WIDGET_TYPE_TEXT: 'text',
            pymupdf.PDF_WIDGET_TYPE_CHECKBOX: 'checkbox',
            pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON: 'radio',
            pymupdf.PDF_WIDGET_TYPE_COMBOBOX: 'choice',
            pymupdf.PDF_WIDGET_TYPE_LISTBOX: 'choice',
            pymupdf.PDF_WIDGET_TYPE_SIGNATURE: 'signature',
        }
        page = self.pdf[page_index]
        result = []
        for w in page.widgets():
            parent = self.pdf.xref_get_key(w.xref, 'Parent')
            group = int(parent[1].split()[0]) if parent[0] == 'xref' else w.xref
            state = self.pdf.xref_get_key(w.xref, 'AS')
            checked = state[0] == 'name' and state[1] != '/Off'
            result.append(dict(xref=w.xref, name=w.field_name, type=types.get(w.field_type, 'unsupported'),
                               value=w.field_value, rect=list(map(float, w.rect)),
                               choices=list(w.choice_values or []), group=group, checked=checked,
                               readonly=bool(w.field_flags & pymupdf.PDF_FIELD_IS_READ_ONLY)))
            if w.field_type == pymupdf.PDF_WIDGET_TYPE_TEXT:
                result[-1].update(font_size=float(w.text_fontsize or 0),
                                  effective_font_size=self.field_font_size(page_index, w.xref),
                                  font_name=w.text_font or '')
        return result

    def field_font_size(self, page_index, xref, value=None) -> float:
        """Return a fitted default in PDF points, without changing the widget."""
        page = self.pdf[page_index]
        widget = page.load_widget(xref)
        if widget is None or widget.field_type != pymupdf.PDF_WIDGET_TYPE_TEXT:
            raise ValueError('Font size requires a text widget')
        native = float(widget.text_fontsize or 0)
        limit = native if math.isfinite(native) and native > 0 else 12.0
        font = self._field_font(page_index, widget)
        text = str(widget.field_value or '') if value is None else str(value)
        text = text.replace('\r\n', '\n').replace('\r', '\n')
        width = max(0.01, widget.rect.width - 4)
        height = max(0.01, widget.rect.height - 4)
        line_height = max(1.2, font.ascender - font.descender)
        if widget.field_flags & pymupdf.PDF_TX_FIELD_IS_MULTILINE:
            def fits(size):
                lines = 0
                for paragraph in text.split('\n'):
                    lines += 1
                    used = 0.0
                    for token in re.findall(r'\S+\s*|\s+', paragraph):
                        advance = font.text_length(token, fontsize=size)
                        if used and used + advance > width:
                            lines += 1
                            used = 0.0
                        # Long unbroken tokens must also fit, not be clipped.
                        if advance > width:
                            return False
                        used += advance
                return lines * line_height * size <= height
            low, high = 0.0, limit
            if fits(high):
                return float(high)
            for _ in range(32):
                middle = (low + high) / 2
                if fits(middle):
                    low = middle
                else:
                    high = middle
            return max(0.001, float(low))
        length = font.text_length(text.replace('\n', ' '), fontsize=1)
        size = min(limit, height / line_height, width / max(length, 0.001))
        return max(0.001, float(size))

    def _field_font(self, page_index, widget):
        """Measure the declared resource when embedded, otherwise Base14/fallback."""
        name = widget.text_font or 'Helv'
        # Widget appearances and AcroForm DR may declare fonts not listed in
        # page resources. Match the resource alias, not a guessed family name.
        roots = [(widget.xref, 'AP/N/Resources/Font'),
                 (self.pdf.pdf_catalog(), 'AcroForm/DR/Font')]
        for root, key in roots:
            kind, resources = self.pdf.xref_get_key(root, key)
            if kind not in ('dict', 'xref'):
                continue
            if kind == 'xref':
                resources = self.pdf.xref_object(int(resources.split()[0]))
            for alias, ref in re.findall(r'/([^\s/]+)\s+(\d+)\s+\d+\s+R', resources):
                if alias != self._pdf_name(name)[1:]:
                    continue
                try:
                    base, ext, _, data = self.pdf.extract_font(int(ref))
                    if data:
                        return pymupdf.Font(fontbuffer=data)
                    return pymupdf.Font(fontname=base.split('+')[-1])
                except (ValueError, RuntimeError):
                    pass
        try:
            return pymupdf.Font(fontname=name)
        except (ValueError, RuntimeError):
            return pymupdf.Font(fontname='helv')

    @staticmethod
    def _pdf_name(value):
        return '/' + ''.join(chr(b) if chr(b).isalnum() and b < 128 else f'#{b:02X}'
                             for b in value.encode())

    def set_field(self, page_index, xref, value, *, font_size=None):
        """Write native values; explicit text sizes are exact, omitted sizes fit.

        Manual sizes are never silently shrunk during this write (the PDF may
        clip an oversized choice). Later omitted-size edits use the stored size
        as an upper bound. fields() reports both stored and fitted sizes.
        """
        field = next((f for f in self.fields(page_index) if f['xref'] == xref), None)
        if field is None:
            raise ValueError('Unknown widget on this page')
        if field['readonly'] or field['type'] in ('signature', 'unsupported'):
            raise ValueError('Widget is not editable')
        if font_size is not None:
            if field['type'] != 'text':
                raise ValueError('Font size requires an editable text widget')
            try:
                if isinstance(font_size, bool):
                    raise ValueError
                font_size = float(font_size)
                if not math.isfinite(font_size) or not 1 <= font_size <= 144:
                    raise ValueError
            except (TypeError, ValueError, OverflowError):
                raise ValueError('Font size must be finite and between 1 and 144 points') from None
        if field['type'] == 'choice' and value not in field['choices']:
            raise ValueError('Value is not an available choice')
        chosen_size = (float(font_size) if font_size is not None else
                       self.field_font_size(page_index, xref, value)) if field['type'] == 'text' else None
        page = self.pdf[page_index]
        widget = page.load_widget(xref)
        if field['type'] == 'checkbox':
            on = widget.on_state()
            if value not in (True, False, 'Off', on):
                raise ValueError('Invalid checkbox state')
            value = on if value is True or value == on else 'Off'
            # Selecting an existing appearance preserves the PDF's own glyph,
            # border and colors; Widget.update() regenerates and changes them.
            with self._edit():
                name = self._pdf_name(value)
                self.pdf.xref_set_key(xref, 'AS', name)
                self.pdf.xref_set_key(xref, 'V', name)
                parent = self.pdf.xref_get_key(xref, 'Parent')
                if parent[0] == 'xref':
                    self.pdf.xref_set_key(int(parent[1].split()[0]), 'V', name)
                # xref writes do not invalidate MuPDF's loaded annotation cache.
                self._restore((self.pdf.tobytes(), self.dirty))
            return
        if field['type'] == 'radio':
            on = widget.on_state()
            if value is True:
                value = on
            elif value is False:
                value = 'Off'
            if value not in (on, 'Off'):
                raise ValueError('Invalid radio state')
            parent = self.pdf.xref_get_key(xref, 'Parent')
            with self._edit():
                for other_page in self.pdf:
                    for other in other_page.widgets():
                        other_parent = self.pdf.xref_get_key(other.xref, 'Parent')
                        same_group = (parent[0] == 'xref' and parent == other_parent) or other.xref == xref
                        if other.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON and same_group:
                            state = value if other.xref == xref else 'Off'
                            self.pdf.xref_set_key(other.xref, 'AS', self._pdf_name(state))
                            # V belongs to the field parent, not each radio widget.
                            self.pdf.xref_set_key(other.xref, 'V', 'null' if parent[0] == 'xref' else self._pdf_name(state))
                if parent[0] == 'xref':
                    self.pdf.xref_set_key(int(parent[1].split()[0]), 'V', self._pdf_name(value))
                self._restore((self.pdf.tobytes(), self.dirty))
            return
        da = ''
        with self._edit():
            if field['type'] == 'text':
                widget.text_fontsize = chosen_size
                # PyMuPDF 2.x skips DA updates on widgets with a field parent.
                # A widget-local DA preserves sibling typography and is inherited
                # by MuPDF's appearance generator.
                color = widget.text_color or (0, 0, 0)
                operator = {1: 'g', 3: 'rg', 4: 'k'}.get(len(color), 'rg')
                da = ' '.join(map(str, color)) + f' {operator} {self._pdf_name(widget.text_font or "Helv")} {chosen_size:g} Tf'
                self.pdf.xref_set_key(xref, 'DA', pymupdf.get_pdf_str(da))
            if field['type'] == 'text' and value == '':
                # Installed PyMuPDF skips pdf_set_field_value for falsey text.
                # Set exact-empty V explicitly, then regenerate the appearance.
                self.pdf.xref_set_key(xref, 'V', '()')
                parent = self.pdf.xref_get_key(xref, 'Parent')
                if parent[0] == 'xref':
                    self.pdf.xref_set_key(int(parent[1].split()[0]), 'V', '()')
            widget.field_value = value
            if field['type'] == 'text':
                # Widget.update() normalizes every custom font alias to Helv.
                # Keep its validation and native writer, but supply the exact DA
                # already measured above, including on parentless field-widgets.
                widget._validate()
                widget._text_da = da
                try:
                    pymupdf.TOOLS._save_widget(widget._annot, widget)
                finally:
                    widget._text_da = ''
            else:
                widget.update()

    def add_signature(self, page_index, rect, png_bytes):
        self._valid_rect(rect)
        mark = dict(id=str(uuid.uuid4()), kind='signature', rect=list(rect),
                    png=base64.b64encode(png_bytes).decode('ascii'))
        with self._edit():
            page = self.pdf[page_index]
            marks = self.marks(page_index)
            self._draw_mark(page, mark)
            self._write_marks(page, marks + [mark])
        return mark['id']

    def save_copy(self, path, overwrite=False):
        target = Path(path)

        def check():
            if target.resolve() == self._source_path:
                raise ValueError('Cannot overwrite original PDF')
            try:
                info = target.lstat()
            except FileNotFoundError:
                return
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('Symlink destinations are not allowed')
            if (info.st_dev, info.st_ino) == self._source_inode:
                raise ValueError('Cannot overwrite original PDF inode')
            if not overwrite:
                raise FileExistsError(target)

        check()
        fd, temporary = tempfile.mkstemp(prefix='.omapreview-', suffix='.pdf', dir=target.parent)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(self.pdf.tobytes())
                output.flush()
                os.fsync(output.fileno())
            check()
            if overwrite:
                os.replace(temporary, target)
            else:
                os.link(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    # Private, versioned page metadata owns only explicitly tagged content streams.
    # Original page streams, annotations, widgets and metadata are never reclaimed.
    _MARK_KEY = 'OmapreviewMarks'
    _MARK_LIMIT = 16 * 1024 * 1024

    @staticmethod
    def _marker(identity):
        return f'% Omapreview mark v1 {identity}\n'.encode('ascii')

    def marks(self, page_index):
        page = self.pdf[page_index]
        kind, value = self.pdf.xref_get_key(page.xref, self._MARK_KEY)
        if kind != 'string' or len(value) > self._MARK_LIMIT:
            return []
        try:
            payload = json.loads(value)
            if payload['version'] != 1 or len(payload['marks']) > 10000:
                return []
            contents = set(page.get_contents())
            result = []
            for mark in payload['marks']:
                identity = str(uuid.UUID(mark['id']))
                if identity != mark['id'] or mark['kind'] not in ('text', 'signature'):
                    continue
                self._valid_rect(mark['rect'])
                streams = mark['streams']
                if not streams or not all(type(ref) is int and ref in contents and
                        self.pdf.xref_stream(ref).startswith(self._marker(identity)) for ref in streams):
                    continue
                if mark['kind'] == 'text' and (not isinstance(mark['text'], str) or
                        not 1 <= float(mark['font_size']) <= 144):
                    continue
                if mark['kind'] == 'signature' and not self._valid_signature_png(mark.get('png')):
                    continue
                result.append(mark)
            return result
        except (ValueError, TypeError, KeyError, AttributeError, RuntimeError):
            return []

    @staticmethod
    def _valid_signature_png(encoded):
        """Admit only bounded, decodable PNGs as editable saved signatures."""
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImageReader
        limit = 4 * 1024 * 1024
        if not isinstance(encoded, str) or len(encoded) > 4 * ((limit + 2) // 3):
            return False
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError:
            return False
        if len(data) > limit or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            return False
        buffer = QBuffer()
        buffer.setData(data)
        buffer.open(QIODevice.ReadOnly)
        reader = QImageReader(buffer, b'PNG')
        size = reader.size()
        if (not 0 < size.width() <= 16383 or not 0 < size.height() <= 16383
                or size.width() * size.height() > 2 * 1024 * 1024):
            return False
        return not reader.read().isNull()

    @staticmethod
    def _valid_rect(rect):
        if len(rect) != 4 or not all(math.isfinite(float(v)) for v in rect):
            raise ValueError('A finite rectangle is required')
        if rect[2] - rect[0] < 10 or rect[3] - rect[1] < 10:
            raise ValueError('The annotation box is too small')

    def _write_marks(self, page, marks):
        payload = json.dumps(dict(version=1, marks=marks), ensure_ascii=True)
        if len(payload) > self._MARK_LIMIT:
            raise ValueError('Editable annotation metadata is too large')
        self.pdf.xref_set_key(page.xref, self._MARK_KEY, pymupdf.get_pdf_str(payload))

    def _remove_mark_streams(self, page, mark):
        retained = [ref for ref in page.get_contents() if ref not in mark['streams']]
        self.pdf.xref_set_key(page.xref, 'Contents', '[' + ' '.join(f'{ref} 0 R' for ref in retained) + ']')

    def _draw_mark(self, page, mark):
        page.wrap_contents()
        before = set(page.get_contents())
        if mark['kind'] == 'signature':
            image_xref = page.insert_image(pymupdf.Rect(mark['rect']), stream=base64.b64decode(mark['png'], validate=True))
            # Retain the full-resolution ink and request interpolation in PDF readers.
            self.pdf.xref_set_key(image_xref, 'Interpolate', 'true')
            mask_type, mask_ref = self.pdf.xref_get_key(image_xref, 'SMask')
            if mask_type == 'xref':
                self.pdf.xref_set_key(int(mask_ref.split()[0]), 'Interpolate', 'true')
        else:
            markup = '<div>' + escape(mark['text']).replace('\n', '<br>') + '</div>'
            rect = pymupdf.Rect(mark['rect'])
            if not page.rect.contains(rect):
                raise ValueError('Text needs room inside the page; move or resize its box')
            while True:
                spare, _ = page.insert_htmlbox(rect, markup, scale_low=1,
                    css=f'* {{font-family: sans-serif; font-size: {float(mark["font_size"])}pt;}}')
                if spare >= 0:
                    break
                if rect.y1 >= page.rect.y1:
                    raise ValueError('Text does not fit; widen its box or make room higher on the page')
                rect.y1 = min(page.rect.y1, rect.y0 + max(rect.height + 12, rect.height * 1.4))
            mark['rect'] = list(rect)
        mark['streams'] = [ref for ref in page.get_contents() if ref not in before]
        for ref in mark['streams']:
            self.pdf.update_stream(ref, self._marker(mark['id']) + self.pdf.xref_stream(ref))

    def add_text(self, page_index, rect, text, font_size=12):
        self._valid_rect(rect)
        if not 1 <= float(font_size) <= 144:
            raise ValueError('Font size must be between 1 and 144 points')
        mark = dict(id=str(uuid.uuid4()), kind='text', rect=list(rect),
                    text=str(text), font_size=float(font_size))
        with self._edit():
            page = self.pdf[page_index]
            marks = self.marks(page_index)
            self._draw_mark(page, mark)
            self._write_marks(page, marks + [mark])
        return mark['id']

    def update_mark(self, page_index, identity, **changes):
        marks = self.marks(page_index)
        mark = next((m for m in marks if m['id'] == identity), None)
        if mark is None:
            raise ValueError('Unknown app-owned mark')
        if set(changes) - {'rect', 'text', 'font_size'}:
            raise ValueError('Unsupported mark change')
        updated = dict(mark, **changes)
        self._valid_rect(updated['rect'])
        if not 1 <= float(updated.get('font_size', 12)) <= 144:
            raise ValueError('Invalid font size')
        if updated == mark:
            return False
        with self._edit():
            page = self.pdf[page_index]
            self._remove_mark_streams(page, mark)
            self._draw_mark(page, updated)
            marks[marks.index(mark)] = updated
            self._write_marks(page, marks)
        return True

    def delete_mark(self, page_index, identity):
        marks = self.marks(page_index)
        mark = next((m for m in marks if m['id'] == identity), None)
        if mark is None:
            raise ValueError('Unknown app-owned mark')
        with self._edit():
            page = self.pdf[page_index]
            self._remove_mark_streams(page, mark)
            marks.remove(mark)
            self._write_marks(page, marks)


class SignatureStore:
    """Owner-private reusable PNG marks, not certificate-based digital signing.

    Legacy ``signature.png`` / ``initials.png`` remain the primary marks; reads
    never migrate them. Extras live at ``additional/<kind>/<UUID>/`` with a
    ``name.json`` string and trimmed ``signature.png``. Labels are 1–80 human
    characters, never paths. Publication renames a complete private directory.

    Published malformed/unsafe entries fail closed with ValueError, without
    repair or deletion. Only private ``.pending-<UUID>`` directories are omitted
    from listings (including interrupted writes/deletes); reads do not recover
    them. Discovery is capped at 256 entries per kind, including pending work;
    metadata at 1 KiB, PNGs at 4 MiB / 2 megapixels. Extra records must retain
    current-user ownership, 0700 directories and single-link 0600 regular files.
    """

    def __init__(self, root=None):
        self.root = Path(root) if root is not None else Path(
            os.environ.get('XDG_DATA_HOME') or Path.home() / '.local' / 'share'
        ) / 'omapreview' / 'signatures'
        self.root = self.root.absolute()
        for directory in reversed((self.root, *self.root.parents)):
            if directory.is_symlink():
                raise ValueError('Signature directory cannot contain symlinks')
            directory.mkdir(mode=0o700, exist_ok=True)
        self._secure_root()

    @contextmanager
    def _root_directory(self):
        fd = os.open(self.root.anchor, os.O_RDONLY | os.O_DIRECTORY)
        try:
            for component in self.root.parts[1:]:
                try:
                    child = os.open(component, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                    dir_fd=fd)
                except OSError as error:
                    raise ValueError('Unsafe signature directory; symlinks are forbidden') from error
                os.close(fd)
                fd = child
            if os.fstat(fd).st_uid != os.getuid():
                raise ValueError('Signature directory must be owned by the current user')
            yield fd
        finally:
            os.close(fd)

    def _secure_root(self):
        with self._root_directory() as fd:
            os.fchmod(fd, 0o700)

    def _path(self, kind):
        if kind not in ('signature', 'initials'):
            raise ValueError('Kind must be signature or initials')
        self._secure_root()
        path = self.root / f'{kind}.png'
        try:
            info = path.lstat()
        except FileNotFoundError:
            return path
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
            raise ValueError('Signature file must be an owned regular file without links')
        return path

    @staticmethod
    def _trim_png(png_bytes):
        from PySide6.QtGui import QImage
        from PySide6.QtCore import QBuffer, QIODevice
        if not isinstance(png_bytes, bytes) or not png_bytes.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('A valid PNG is required')
        image = QImage.fromData(png_bytes, 'PNG').convertToFormat(QImage.Format.Format_RGBA8888)
        if image.isNull():
            raise ValueError('A valid PNG is required')
        raw = bytes(image.constBits())
        left, top = image.width(), image.height()
        right = bottom = -1
        for y in range(image.height()):
            for x in range(image.width()):
                offset = y * image.bytesPerLine() + x * 4
                r, g, b, a = raw[offset:offset + 4]
                if a and min(r, g, b) < 250:
                    left, top = min(left, x), min(top, y)
                    right, bottom = max(right, x), max(bottom, y)
        if right < 0:
            raise ValueError('PNG contains no visible ink')
        image = image.copy(left, top, right - left + 1, bottom - top + 1)
        buffer = QBuffer()
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        image.save(buffer, 'PNG')
        return bytes(buffer.data())

    def save(self, kind, png_bytes):
        target = self._path(kind)
        data = self._trim_png(png_bytes)
        fd, temporary = tempfile.mkstemp(prefix='.signature-', dir=self.root)
        try:
            with os.fdopen(fd, 'wb') as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, target)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def load(self, kind):
        path = self._path(kind)
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        except FileNotFoundError:
            return None
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_nlink != 1:
                raise ValueError('Unsafe signature file')
            os.fchmod(source.fileno(), 0o600)
            return source.read()

    def delete(self, kind):
        self._path(kind).unlink(missing_ok=True)

    @staticmethod
    @contextmanager
    def _private_directory(name, *, parent=None, create=False):
        """Pin an owned private directory, never following its final component."""
        if create:
            try:
                os.mkdir(name, 0o700, dir_fd=parent)
            except FileExistsError:
                pass
        try:
            fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                         dir_fd=parent)
        except FileNotFoundError:
            raise
        except OSError as error:
            raise ValueError('Unsafe signature library directory') from error
        try:
            info = os.fstat(fd)
            if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) != 0o700:
                raise ValueError('Unsafe signature library directory ownership or permissions')
            yield fd
        finally:
            os.close(fd)

    @contextmanager
    def _library(self, kind, create=False):
        from contextlib import ExitStack
        self._path(kind)
        with ExitStack() as stack:
            root_fd = stack.enter_context(self._root_directory())
            try:
                extra_fd = stack.enter_context(self._private_directory(
                    'additional', parent=root_fd, create=create))
                library_fd = stack.enter_context(self._private_directory(
                    kind, parent=extra_fd, create=create))
            except FileNotFoundError:
                if create:
                    raise
                library_fd = None
            yield library_fd

    _MAX_ENTRIES = 256
    _MAX_PNG_BYTES = 4 * 1024 * 1024
    _MAX_PIXELS = 2 * 1024 * 1024

    @staticmethod
    def _bounded_names(directory_fd, limit):
        names = []
        with os.scandir(directory_fd) as entries:
            for entry in entries:
                if len(names) >= limit:
                    raise ValueError('Signature library entry limit exceeded')
                names.append(entry.name)
        return names

    def _bounded_png(self, data):
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtGui import QImageReader
        if not isinstance(data, bytes) or len(data) > self._MAX_PNG_BYTES:
            raise ValueError('PNG size limit exceeded')
        buffer = QBuffer()
        buffer.setData(data)
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        reader = QImageReader(buffer, b'PNG')
        size = reader.size()
        if (size.width() <= 0 or size.height() <= 0
                or size.width() * size.height() > self._MAX_PIXELS):
            raise ValueError('PNG dimensions exceed size limit or are invalid')
        return self._trim_png(data)

    def _read_record_file(self, entry_fd, filename):
        try:
            fd = os.open(filename, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                         dir_fd=entry_fd)
        except OSError as error:
            raise ValueError('Unsafe or missing signature library file') from error
        with os.fdopen(fd, 'rb') as source:
            info = os.fstat(source.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                    or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) != 0o600):
                raise ValueError('Unsafe signature library file')
            limit = 1024 if filename == 'name.json' else self._MAX_PNG_BYTES
            if info.st_size > limit:
                raise ValueError('Signature library file size limit exceeded')
            data = source.read(limit + 1)
            if len(data) > limit:
                raise ValueError('Signature library file size limit exceeded')
            return data

    def _read_record(self, entry_fd):
        if set(self._bounded_names(entry_fd, 2)) != {'name.json', 'signature.png'}:
            raise ValueError('Unsafe signature library record contents')
        name_data = self._read_record_file(entry_fd, 'name.json')
        try:
            name = self._validate_name(json.loads(name_data))
        except (UnicodeError, ValueError) as error:
            raise ValueError('Unsafe signature library name record') from error
        data = self._read_record_file(entry_fd, 'signature.png')
        self._bounded_png(data)
        return name, data

    @staticmethod
    def _validate_name(name):
        import unicodedata
        if (not isinstance(name, str) or not 1 <= len(name) <= 80
                or not name.strip() or '/' in name or '\\' in name
                or any(unicodedata.category(char).startswith('C') for char in name)):
            raise ValueError('Mark name must be 1–80 visible characters without path separators')
        return name

    @staticmethod
    def _validate_id(mark_id):
        if (not isinstance(mark_id, str)
                or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', mark_id)):
            raise ValueError('Invalid mark id: canonical UUID required')
        return mark_id

    def save_additional(self, kind, name, png_bytes):
        name = self._validate_name(name)
        data = self._bounded_png(png_bytes)
        mark_id = str(uuid.uuid4())
        temporary = f'.pending-{uuid.uuid4()}'
        with self._library(kind, create=True) as library_fd:
            assert library_fd is not None
            if len(self._bounded_names(library_fd, self._MAX_ENTRIES)) >= self._MAX_ENTRIES:
                raise ValueError('Signature library entry limit exceeded')
            os.mkdir(temporary, 0o700, dir_fd=library_fd)
            published = False
            try:
                with self._private_directory(temporary, parent=library_fd) as entry_fd:
                    for filename, payload in [('name.json', json.dumps(name).encode('utf-8')),
                                              ('signature.png', data)]:
                        fd = os.open(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                                     0o600, dir_fd=entry_fd)
                        with os.fdopen(fd, 'wb') as output:
                            output.write(payload)
                            output.flush()
                            os.fsync(output.fileno())
                    os.fsync(entry_fd)
                try:
                    os.stat(mark_id, dir_fd=library_fd, follow_symlinks=False)
                except FileNotFoundError:
                    pass
                else:
                    raise FileExistsError('Signature library id already exists')
                os.rename(temporary, mark_id, src_dir_fd=library_fd, dst_dir_fd=library_fd)
                published = True
                os.fsync(library_fd)
            finally:
                if not published:
                    with self._private_directory(temporary, parent=library_fd) as entry_fd:
                        for filename in ('name.json', 'signature.png'):
                            try:
                                os.unlink(filename, dir_fd=entry_fd)
                            except FileNotFoundError:
                                pass
                    os.rmdir(temporary, dir_fd=library_fd)
        return mark_id

    def list_marks(self, kind):
        path = self._path(kind)
        result = ([{'id': 'primary', 'name': f'Primary {kind}', 'primary': True}]
                  if path.exists() else [])
        extras = []
        with self._library(kind) as library_fd:
            if library_fd is not None:
                for mark_id in self._bounded_names(library_fd, self._MAX_ENTRIES):
                    if mark_id.startswith('.pending-'):
                        self._validate_id(mark_id[len('.pending-'):])
                        try:
                            with self._private_directory(mark_id, parent=library_fd):
                                pass  # Unpublished work is never exposed or recovered on read.
                        except FileNotFoundError:
                            pass  # Concurrent publication/cleanup removed the pending name.
                        continue
                    self._validate_id(mark_id)
                    try:
                        with self._private_directory(mark_id, parent=library_fd) as entry_fd:
                            name, _ = self._read_record(entry_fd)
                    except FileNotFoundError as error:
                        raise ValueError('Unsafe signature library record') from error
                    extras.append({'id': mark_id, 'name': name, 'primary': False})
        return result + sorted(extras, key=lambda item: (item['name'], item['id']))

    def load_mark(self, kind, mark_id):
        if mark_id == 'primary':
            return self.load(kind)
        self._validate_id(mark_id)
        with self._library(kind) as library_fd:
            if library_fd is None:
                return None
            try:
                with self._private_directory(mark_id, parent=library_fd) as entry_fd:
                    return self._read_record(entry_fd)[1]
            except FileNotFoundError:
                return None

    def delete_mark(self, kind, mark_id):
        if mark_id == 'primary':
            return self.delete(kind)
        self._validate_id(mark_id)
        with self._library(kind) as library_fd:
            if library_fd is None:
                return
            try:
                with self._private_directory(mark_id, parent=library_fd) as entry_fd:
                    self._read_record(entry_fd)
                    temporary = f'.pending-{uuid.uuid4()}'
                    os.rename(mark_id, temporary, src_dir_fd=library_fd, dst_dir_fd=library_fd)
                    for filename in ('name.json', 'signature.png'):
                        os.unlink(filename, dir_fd=entry_fd)
                    os.rmdir(temporary, dir_fd=library_fd)
                    os.fsync(library_fd)
            except FileNotFoundError:
                return
