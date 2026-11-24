"""Generated marks only; never inspect or migrate operator signature storage."""
import os
import stat
import uuid

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage

from omapreview.core import SignatureStore


def png():
    image = QImage(20, 16, QImage.Format.Format_RGBA8888)
    image.fill(QColor('transparent'))
    for y in range(5, 9):
        for x in range(4, 13):
            image.setPixelColor(x, y, QColor('black'))
    output = QBuffer()
    output.open(QIODevice.OpenModeFlag.WriteOnly)
    assert image.save(output, 'PNG')
    return bytes(output.data())


@pytest.mark.parametrize('kind', ['signature', 'initials'])
def test_primary_catalog_is_read_only_and_legacy_compatible(tmp_path, kind):
    store = SignatureStore(tmp_path / 'marks')
    assert store.list_marks(kind) == []
    store.save(kind, png())
    original = (store.root / f'{kind}.png').read_bytes()
    before = {p.name: p.stat().st_mtime_ns for p in store.root.iterdir()}
    assert store.list_marks(kind) == [
        {'id': 'primary', 'name': f'Primary {kind}', 'primary': True}
    ]
    assert store.load_mark(kind, 'primary') == original == store.load(kind)
    assert {p.name: p.stat().st_mtime_ns for p in store.root.iterdir()} == before


@pytest.mark.parametrize('kind', ['signature', 'initials'])
def test_additional_marks_roundtrip_without_replacing_primary(tmp_path, kind):
    store = SignatureStore(tmp_path / 'marks')
    store.save(kind, png())
    primary = store.load(kind)
    ids = [store.save_additional(kind, name, png()) for name in ['Zulu', 'Alice', 'Alice']]
    assert len(set(ids)) == 3
    assert all(str(uuid.UUID(value)) == value for value in ids)
    fresh = SignatureStore(store.root)
    expected = [{'id': 'primary', 'name': f'Primary {kind}', 'primary': True}]
    expected += sorted([
        {'id': value, 'name': name, 'primary': False}
        for value, name in zip(ids, ['Zulu', 'Alice', 'Alice'])
    ], key=lambda item: (item['name'], item['id']))
    assert fresh.list_marks(kind) == expected
    for value in ids:
        data = fresh.load_mark(kind, value)
        assert data == primary
        assert QImage.fromData(data).size().toTuple() == (9, 4)
    assert fresh.load(kind) == primary
    other = 'initials' if kind == 'signature' else 'signature'
    assert fresh.list_marks(other) == []
    assert fresh.load_mark(other, ids[0]) is None


@pytest.mark.parametrize('kind', ['signature', 'initials'])
def test_delete_one_mark_keeps_other_marks_and_legacy(tmp_path, kind):
    store = SignatureStore(tmp_path / 'marks')
    store.save(kind, png())
    first = store.save_additional(kind, 'One', png())
    second = store.save_additional(kind, 'Two', png())
    store.delete_mark(kind, first)
    store.delete_mark(kind, first)
    assert store.load_mark(kind, first) is None
    assert store.load_mark(kind, second) == store.load(kind)
    assert [mark['id'] for mark in store.list_marks(kind)] == ['primary', second]
    store.delete_mark(kind, 'primary')
    assert store.load(kind) is None
    assert [mark['id'] for mark in store.list_marks(kind)] == [second]


@pytest.mark.parametrize('name', ['', '   ', 'x' * 81, 'line\nbreak', 'tab\t', 'null\0',
                                  '\u007f', '\u0085', '\u202ehidden', '../escape', 'a/b', 'a\\b', 123])
def test_unsafe_names_refused_before_storage(tmp_path, name):
    store = SignatureStore(tmp_path / 'marks')
    with pytest.raises(ValueError, match='name'):
        store.save_additional('signature', name, png())
    assert list(store.root.iterdir()) == []


def test_human_names_are_bounded_labels_not_paths(tmp_path):
    store = SignatureStore(tmp_path / 'marks')
    name = 'Élodie — ' + 'x' * 71
    mark_id = store.save_additional('signature', name, png())
    assert store.list_marks('signature') == [{'id': mark_id, 'name': name, 'primary': False}]
    assert all(name not in str(path.relative_to(store.root)) for path in store.root.rglob('*'))


@pytest.mark.parametrize('mark_id', ['../escape', '/tmp/forged', '', None, 42,
                                      '00000000000000000000000000000000',
                                      'B9F0D8E4-97D6-47E8-A2EB-BB59FDDF23BC'])
@pytest.mark.parametrize('operation', ['load_mark', 'delete_mark'])
def test_forged_ids_rejected_even_with_empty_library(tmp_path, mark_id, operation):
    store = SignatureStore(tmp_path / 'marks')
    with pytest.raises(ValueError, match='id'):
        getattr(store, operation)('signature', mark_id)
    assert list(store.root.iterdir()) == []


def entry_path(store, mark_id, kind='signature'):
    return store.root / 'additional' / kind / mark_id


@pytest.mark.parametrize('part', ['additional', 'kind', 'entry', 'name.json', 'signature.png'])
@pytest.mark.parametrize('operation', ['list', 'load', 'delete'])
def test_library_refuses_symlinks_without_touching_target(tmp_path, part, operation):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Test', png())
    entry = entry_path(store, mark_id)
    target = {'additional': store.root / 'additional', 'kind': entry.parent,
              'entry': entry, 'name.json': entry / 'name.json',
              'signature.png': entry / 'signature.png'}[part]
    moved = tmp_path / 'untouched'
    target.rename(moved)
    target.symlink_to(moved)
    before = {str(p.relative_to(tmp_path)): p.read_bytes()
              for p in moved.rglob('*') if p.is_file()} if moved.is_dir() else moved.read_bytes()
    action = {'list': lambda: store.list_marks('signature'),
              'load': lambda: store.load_mark('signature', mark_id),
              'delete': lambda: store.delete_mark('signature', mark_id)}[operation]
    with pytest.raises(ValueError, match='[Uu]nsafe'):
        action()
    after = {str(p.relative_to(tmp_path)): p.read_bytes()
             for p in moved.rglob('*') if p.is_file()} if moved.is_dir() else moved.read_bytes()
    assert after == before


@pytest.mark.parametrize('filename', ['name.json', 'signature.png'])
@pytest.mark.parametrize('operation', ['list_marks', 'load_mark', 'delete_mark'])
def test_hardlinked_record_refused_without_unlinking(tmp_path, filename, operation):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Test', png())
    path = entry_path(store, mark_id) / filename
    backup = tmp_path / 'hardlink'
    os.link(path, backup)
    original = backup.read_bytes()
    args = ('signature',) if operation == 'list_marks' else ('signature', mark_id)
    with pytest.raises(ValueError, match='[Uu]nsafe'):
        getattr(store, operation)(*args)
    assert path.exists() and backup.read_bytes() == original


@pytest.mark.parametrize('operation,damage', [
    (operation, damage)
    for operation in ['list_marks', 'load_mark', 'delete_mark']
    for damage in ['missing-name', 'missing-png', 'bad-name', 'bad-json',
                   'bad-png', 'unexpected-file', 'forged-directory']
    if damage != 'forged-directory' or operation == 'list_marks'
])
def test_malformed_published_records_fail_closed(tmp_path, damage, operation):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Test', png())
    entry = entry_path(store, mark_id)
    if damage == 'missing-name':
        (entry / 'name.json').unlink()
    elif damage == 'missing-png':
        (entry / 'signature.png').unlink()
    elif damage == 'bad-name':
        (entry / 'name.json').write_bytes(b'"../bad"')
    elif damage == 'bad-json':
        (entry / 'name.json').write_bytes(b'{')
    elif damage == 'bad-png':
        (entry / 'signature.png').write_bytes(b'not a png')
    elif damage == 'unexpected-file':
        (entry / 'surprise').write_bytes(b'keep me')
    else:
        entry.rename(entry.parent / 'forged')
    before = {str(p.relative_to(store.root)): p.read_bytes()
              for p in store.root.rglob('*') if p.is_file()}
    args = ('signature',) if operation == 'list_marks' else ('signature', mark_id)
    with pytest.raises(ValueError):
        getattr(store, operation)(*args)
    assert {str(p.relative_to(store.root)): p.read_bytes()
            for p in store.root.rglob('*') if p.is_file()} == before


def test_additional_storage_is_private(tmp_path):
    store = SignatureStore(tmp_path / 'marks')
    store.save_additional('signature', 'Private', png())
    for path in [store.root, *store.root.rglob('*')]:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)


@pytest.mark.parametrize('filename,size', [('name.json', 1025), ('signature.png', 4 * 1024 * 1024 + 1)])
def test_record_reads_are_bounded(tmp_path, filename, size):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Test', png())
    path = entry_path(store, mark_id) / filename
    with path.open('ab') as output:
        output.write(b' ' * size)
    with pytest.raises(ValueError, match='[Bb]ound|[Ll]imit|[Ss]ize'):
        store.load_mark('signature', mark_id)


def test_discovery_is_bounded_including_unpublished_entries(tmp_path):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Test', png())
    library = entry_path(store, mark_id).parent
    for _ in range(256):
        (library / f'.pending-{uuid.uuid4()}').mkdir(mode=0o700)
    with pytest.raises(ValueError, match='[Ll]imit|[Mm]any'):
        store.list_marks('signature')
    with pytest.raises(ValueError, match='[Ll]imit|[Mm]any'):
        store.save_additional('signature', 'No room', png())


@pytest.mark.parametrize('damage', ['bytes', 'dimensions'])
def test_additional_png_size_is_bounded_before_publication(tmp_path, damage):
    store = SignatureStore(tmp_path / 'marks')
    if damage == 'bytes':
        data = png() + b'\0' * (4 * 1024 * 1024)
    else:
        image = QImage(2049, 1025, QImage.Format.Format_RGBA8888)
        image.fill(QColor('black'))
        output = QBuffer()
        output.open(QIODevice.OpenModeFlag.WriteOnly)
        assert image.save(output, 'PNG')
        data = bytes(output.data())
    with pytest.raises(ValueError, match='[Bb]ound|[Ll]imit|[Ss]ize'):
        store.save_additional('signature', 'Too big', data)
    assert list(store.root.iterdir()) == []


def test_save_does_not_replace_existing_uuid_directory(tmp_path, monkeypatch):
    store = SignatureStore(tmp_path / 'marks')
    first = store.save_additional('signature', 'Existing', png())
    collision = uuid.uuid4()
    target = entry_path(store, str(collision))
    target.mkdir(mode=0o700)
    sequence = iter([collision, uuid.uuid4()])
    monkeypatch.setattr('omapreview.core.uuid.uuid4', lambda: next(sequence))
    with pytest.raises(FileExistsError):
        store.save_additional('signature', 'Must not replace', png())
    assert target.is_dir() and list(target.iterdir()) == []
    assert store.load_mark('signature', first) is not None
    assert not list(target.parent.glob('.pending-*'))


def test_pending_symlink_is_not_silently_ignored(tmp_path):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Existing', png())
    outside = tmp_path / 'outside'
    outside.mkdir(mode=0o700)
    (entry_path(store, mark_id).parent / f'.pending-{uuid.uuid4()}').symlink_to(outside)
    with pytest.raises(ValueError, match='[Uu]nsafe'):
        store.list_marks('signature')
    assert list(outside.iterdir()) == []


def test_store_rejects_replaced_ancestor_before_reading(tmp_path):
    parent = tmp_path / 'parent'
    store = SignatureStore(parent / 'marks')
    mark_id = store.save_additional('signature', 'Existing', png())
    moved = tmp_path / 'moved'
    parent.rename(moved)
    parent.symlink_to(moved)
    with pytest.raises(ValueError, match='[Ss]ymlink|[Uu]nsafe'):
        store.load_mark('signature', mark_id)


def test_publication_hides_incomplete_entry_and_exposes_complete_record(tmp_path, monkeypatch):
    store = SignatureStore(tmp_path / 'marks')
    store.save('signature', png())
    before = store.list_marks('signature')
    real_open = os.open
    real_rename = os.rename
    observed = []

    def open_record(path, flags, *args, **kwargs):
        if path == 'signature.png' and flags & os.O_CREAT:
            observed.append('incomplete')
            assert store.list_marks('signature') == before
        return real_open(path, flags, *args, **kwargs)

    def publish(source, destination, *args, **kwargs):
        observed.append('complete')
        assert store.list_marks('signature') == before
        result = real_rename(source, destination, *args, **kwargs)
        assert store.load_mark('signature', destination) == store.load('signature')
        return result

    monkeypatch.setattr('omapreview.core.os.open', open_record)
    monkeypatch.setattr('omapreview.core.os.rename', publish)
    mark_id = store.save_additional('signature', 'Complete', png())
    assert observed == ['incomplete', 'complete']
    assert [mark['id'] for mark in store.list_marks('signature')] == ['primary', mark_id]


def test_failed_publication_cleans_staging_without_changing_saved_marks(tmp_path, monkeypatch):
    store = SignatureStore(tmp_path / 'marks')
    store.save('signature', png())
    store.save_additional('signature', 'Kept', png())
    before = {str(p.relative_to(store.root)): p.read_bytes()
              for p in store.root.rglob('*') if p.is_file()}

    def fail_publish(*args, **kwargs):
        raise OSError('injected publication failure')

    monkeypatch.setattr('omapreview.core.os.rename', fail_publish)
    with pytest.raises(OSError, match='injected'):
        store.save_additional('signature', 'Never published', png())
    assert {str(p.relative_to(store.root)): p.read_bytes()
            for p in store.root.rglob('*') if p.is_file()} == before
    assert not list((store.root / 'additional' / 'signature').glob('.pending-*'))


@pytest.mark.parametrize('data', [b'not png', b'\x89PNG\r\n\x1a\ninvalid'])
def test_invalid_png_does_not_retain_an_entry(tmp_path, data):
    store = SignatureStore(tmp_path / 'marks')
    with pytest.raises(ValueError):
        store.save_additional('signature', 'Invalid', data)
    assert list(store.root.iterdir()) == []


@pytest.mark.parametrize('part', ['additional', 'kind', 'entry', 'name.json', 'signature.png'])
def test_nonprivate_library_records_are_refused_not_repaired(tmp_path, part):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Private', png())
    entry = entry_path(store, mark_id)
    target = {'additional': entry.parent.parent, 'kind': entry.parent, 'entry': entry,
              'name.json': entry / 'name.json', 'signature.png': entry / 'signature.png'}[part]
    target.chmod(0o755 if target.is_dir() else 0o644)
    before = target.stat().st_mode
    with pytest.raises(ValueError, match='[Uu]nsafe'):
        store.list_marks('signature')
    assert target.stat().st_mode == before


@pytest.mark.parametrize('filename', ['name.json', 'signature.png'])
def test_foreign_record_ownership_is_refused(tmp_path, monkeypatch, filename):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = store.save_additional('signature', 'Private', png())
    target = entry_path(store, mark_id) / filename
    inode = target.stat().st_ino
    real_fstat = os.fstat

    def foreign_owner(fd):
        info = real_fstat(fd)
        if info.st_ino == inode:
            values = list(info)
            values[4] = info.st_uid + 1
            return os.stat_result(values)
        return info

    monkeypatch.setattr('omapreview.core.os.fstat', foreign_owner)
    with pytest.raises(ValueError, match='[Uu]nsafe'):
        store.load_mark('signature', mark_id)
    assert target.exists()


@pytest.mark.parametrize('kind', ['../escape', 'other', None])
def test_all_library_methods_reject_invalid_kinds(tmp_path, kind):
    store = SignatureStore(tmp_path / 'marks')
    mark_id = str(uuid.uuid4())
    for action in [lambda: store.list_marks(kind),
                   lambda: store.save_additional(kind, 'Name', png()),
                   lambda: store.load_mark(kind, mark_id),
                   lambda: store.delete_mark(kind, mark_id)]:
        with pytest.raises(ValueError, match='Kind'):
            action()
    assert list(store.root.iterdir()) == []


def test_listing_tolerates_pending_directory_disappearing_at_publication(tmp_path, monkeypatch):
    from contextlib import contextmanager
    store = SignatureStore(tmp_path / 'marks')
    store.save_additional('signature', 'Kept', png())
    expected = store.list_marks('signature')
    library = store.root / 'additional' / 'signature'
    pending = library / f'.pending-{uuid.uuid4()}'
    pending.mkdir(mode=0o700)
    real_directory = store._private_directory

    @contextmanager
    def vanish(name, **kwargs):
        if name == pending.name:
            pending.rmdir()  # Same disappearance a concurrent publish/cleanup causes.
        with real_directory(name, **kwargs) as fd:
            yield fd

    monkeypatch.setattr(store, '_private_directory', vanish)
    assert store.list_marks('signature') == expected
