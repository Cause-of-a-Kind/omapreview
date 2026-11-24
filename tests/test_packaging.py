import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_launcher_help_without_display():
    env = dict(os.environ, DISPLAY='', WAYLAND_DISPLAY='')
    result = subprocess.run([str(ROOT / 'bin/omapreview'), '--help'], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert 'PDF' in result.stdout


def test_manifest_validates_in_omarchy(tmp_path):
    import shutil
    shutil.copy2(ROOT / 'manifest.json', tmp_path)
    shutil.copytree(ROOT / 'qml', tmp_path / 'qml')
    result = subprocess.run(['omarchy', 'plugin', 'validate', str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    manifest = json.loads((ROOT / 'manifest.json').read_text())
    assert manifest['id'] == 'coak.omapreview'


def test_desktop_entry_validates():
    result = subprocess.run(['desktop-file-validate', str(ROOT / 'assets/coak.omapreview.desktop')], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr


def test_installer_does_not_overwrite_unrelated_launchers(tmp_path):
    home = tmp_path / 'home'
    target = home / '.local/bin/omapreview'
    target.parent.mkdir(parents=True)
    target.write_text('unrelated executable\n')
    result = subprocess.run([str(ROOT / 'bin/install'), '--desktop-only'], env=dict(os.environ, HOME=str(home)), capture_output=True, text=True)
    assert result.returncode != 0
    assert target.read_text() == 'unrelated executable\n'
