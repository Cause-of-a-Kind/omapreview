import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def test_install_rescans_before_enabling_new_plugin(tmp_path):
    home = tmp_path / 'home'
    fakebin = tmp_path / 'fakebin'
    fakebin.mkdir()
    marker = tmp_path / 'rescanned'
    (fakebin / 'omarchy-shell').write_text('#!/bin/bash\n[[ "$*" == "shell rescanPlugins" ]] || exit 90\ntouch "$MARKER"\n')
    (fakebin / 'omarchy').write_text('#!/bin/bash\nif [[ "$*" == "plugin enable coak.omapreview" ]]; then test -f "$MARKER"; else exec /usr/bin/omarchy "$@"; fi\n')
    for tool in fakebin.iterdir():
        tool.chmod(0o755)
    env = dict(os.environ, HOME=str(home), XDG_DATA_HOME=str(home / '.local/share'),
               PATH=f'{fakebin}:{os.environ["PATH"]}', MARKER=str(marker))
    result = subprocess.run([str(ROOT / 'bin/install')], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
    assert marker.exists()
    assert (home / '.config/omarchy/plugins/coak.omapreview/manifest.json').is_file()
