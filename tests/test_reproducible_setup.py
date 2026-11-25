"""Generated Git checkouts only: never provision the developer's runtime."""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def run(*args, cwd, env=None):
    return subprocess.run(args, cwd=cwd, env=env, text=True, capture_output=True, timeout=120)


@pytest.fixture
def checkout(tmp_path):
    root = tmp_path / "source"
    root.mkdir()
    for name in ("bin", "omapreview"):
        shutil.copytree(ROOT / name, root / name, ignore=shutil.ignore_patterns("__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE", ".gitignore"):
        shutil.copy2(ROOT / name, root / name)
    for path in ROOT.glob("requirements*"):
        shutil.copy2(path, root / path.name)
    assert run("git", "init", cwd=root).returncode == 0
    assert run("git", "add", ".", cwd=root).returncode == 0
    assert run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "fixture", cwd=root).returncode == 0
    revision = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
    # Closed network boundary: any attempted uv command is a test failure.
    tools = tmp_path / "tools"
    tools.mkdir()
    uv = tools / "uv"
    uv.write_text("#!/bin/sh\nprintf 'UNEXPECTED_UV_CALL\\n' >&2\nexit 91\n")
    uv.chmod(0o755)
    env = dict(os.environ, PATH=f"{tools}:{os.environ['PATH']}")
    return root, revision, env


@pytest.mark.parametrize("revision", [None, "main", "a" * 39, "g" * 40, "a" * 40])
def test_revision_refused_before_uv(checkout, revision):
    root, _, env = checkout
    args = [] if revision is None else ["--revision", revision]
    result = run(str(root / "bin/setup"), *args, cwd=root, env=env)
    assert result.returncode != 0
    assert "UNEXPECTED_UV_CALL" not in result.stderr
    assert "revision" in result.stderr.lower()
    assert not (root / ".venv").exists()


@pytest.mark.parametrize("change", ["tracked", "untracked", "hidden"])
def test_dirty_source_refused_before_uv(checkout, change):
    root, revision, env = checkout
    target = root / "omapreview/__init__.py"
    if change == "hidden":
        assert run("git", "update-index", "--assume-unchanged", str(target), cwd=root).returncode == 0
    if change == "untracked":
        target = root / "unexpected.py"
    target.write_text(target.read_text() + "\n# dirty\n" if target.exists() else "# unreviewed\n")
    result = run(str(root / "bin/setup"), "--revision", revision, cwd=root, env=env)
    assert result.returncode != 0
    assert "UNEXPECTED_UV_CALL" not in result.stderr
    assert "clean" in result.stderr.lower()
    assert not (root / ".venv").exists()


@pytest.mark.parametrize("dev", [False, True])
def test_locked_wheel_install_contract(checkout, tmp_path, dev):
    import json
    root, revision, env = checkout
    log = tmp_path / "uv.jsonl"
    uv = Path(env["PATH"].split(":")[0]) / "uv"
    uv.write_text('''#!/usr/bin/python -I
import json, pathlib, subprocess, sys
args = sys.argv[1:]
with open(''' + repr(str(log)) + ''', 'a') as stream:
    stream.write(json.dumps([sys.argv[0], *args]) + '\\n')
if 'install' in args and any(item.endswith('requirements-build.txt') for item in args):
    import shutil
    shutil.copy2(sys.argv[0], pathlib.Path(args[args.index('--python') + 1]).parent / 'uv')
if 'venv' in args:
    subprocess.run(['/usr/bin/python', '-m', 'venv', '--without-pip', args[-1]], check=True)
if 'build' in args:
    output = pathlib.Path(args[args.index('--out-dir') + 1])
    output.mkdir(exist_ok=True)
    (output / 'omapreview-0.1.1-py3-none-any.whl').write_bytes(b'fixture')
''')
    result = run(str(root / "bin/setup"), "--revision", revision, *(["--dev"] if dev else []), cwd=root, env=env)
    assert result.returncode == 0, result.stderr
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    installs = [call for call in calls if "install" in call]
    locked = [call for call in installs if "--require-hashes" in call]
    assert len(locked) == (3 if dev else 2)
    assert any("requirements-dev.txt" in item for call in locked for item in call) == dev
    for call in locked:
        assert "--no-deps" in call
        assert "--only-binary" in call and ":all:" in call
        assert "--no-build" not in call  # uv rejects it together with --only-binary
    assert any("requirements-build.txt" in item for call in locked for item in call)
    assert any("requirements-runtime.txt" in item for call in locked for item in call)
    build = next(call for call in calls if "build" in call)
    assert "--offline" in build and "--no-build-isolation" in build
    assert build[0].endswith("/backend/bin/uv")  # hash-verified frontend, not host uv
    wheel = next(call for call in installs if any(item.endswith(".whl") for item in call))
    assert "--no-deps" in wheel and "--offline" in wheel and "--no-index" in wheel
    assert all("-e" not in call and "--editable" not in call for call in calls)


def test_launcher_ignores_checkout_cwd_and_pythonpath(checkout, tmp_path):
    root, _, _ = checkout
    assert run("/usr/bin/python", "-m", "venv", "--without-pip", str(root / ".venv"), cwd=root).returncode == 0
    python = root / ".venv/bin/python"
    site = Path(run(str(python), "-I", "-c", "import sysconfig; print(sysconfig.get_path('purelib'))", cwd=root).stdout.strip())
    installed = site / "omapreview"
    installed.mkdir()
    (installed / "__init__.py").write_text("")
    (installed / "__main__.py").write_text("print('INSTALLED_PACKAGE')\n")
    shadow = tmp_path / "shadow"
    shadow.mkdir()
    (shadow / "omapreview.py").write_text("raise RuntimeError('CWD/PYTHONPATH shadow')\n")
    (root / "omapreview/__main__.py").write_text("raise RuntimeError('checkout shadow')\n")
    result = run(str(root / "bin/omapreview"), "--version", cwd=shadow,
                 env=dict(os.environ, PYTHONPATH=str(shadow)))
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "INSTALLED_PACKAGE"


def test_tampered_artifact_hash_removes_only_new_runtime(checkout, tmp_path):
    """Real uv hash refusal using a tiny generated wheel; no network required."""
    import hashlib
    import zipfile
    root, _, env = checkout
    wheel = tmp_path / "fixture_backend-1.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr("fixture_backend/__init__.py", "")
        archive.writestr("fixture_backend-1.0.dist-info/METADATA", "Metadata-Version: 2.1\nName: fixture-backend\nVersion: 1.0\n")
        archive.writestr("fixture_backend-1.0.dist-info/WHEEL", "Wheel-Version: 1.0\nGenerator: fixture\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        archive.writestr("fixture_backend-1.0.dist-info/RECORD", "")
    real_env = dict(env, PATH=env["PATH"].split(":", 1)[1])
    uv = shutil.which("uv", path=real_env["PATH"])
    assert uv is not None, "uv is required to qualify installer hashes"
    control = tmp_path / "control"
    assert run(uv, "--no-config", "venv", "--python", "/usr/bin/python", "--no-python-downloads", str(control), cwd=root, env=real_env).returncode == 0
    lock = root / "requirements-build.txt"
    requirement = f"fixture-backend @ {wheel.as_uri()} --hash=sha256:"
    lock.write_text(requirement + hashlib.sha256(wheel.read_bytes()).hexdigest() + "\n")
    valid = run(uv, "--no-config", "pip", "install", "--offline", "--no-deps", "--only-binary", ":all:", "--require-hashes", "--python", str(control / "bin/python"), "-r", str(lock), cwd=root, env=real_env)
    assert valid.returncode == 0, valid.stderr
    lock.write_text(requirement + "0" * 64 + "\n")
    assert run("git", "add", ".", cwd=root).returncode == 0
    assert run("git", "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "tampered lock fixture", cwd=root).returncode == 0
    revision = run("git", "rev-parse", "HEAD", cwd=root).stdout.strip()
    result = run(str(root / "bin/setup"), "--revision", revision, cwd=root, env=real_env)
    assert result.returncode != 0
    assert "hash mismatch" in result.stderr.lower(), result.stderr
    assert not (root / ".venv").exists()
    assert (root / "bin/setup").exists()
    assert run("git", "status", "--porcelain", cwd=root).stdout == ""


def test_existing_runtime_preserved(checkout):
    root, revision, env = checkout
    (root / ".venv").mkdir()
    marker = root / ".venv/keep"
    marker.write_bytes(b"existing runtime")
    result = run(str(root / "bin/setup"), "--revision", revision, cwd=root, env=env)
    assert result.returncode != 0
    assert "UNEXPECTED_UV_CALL" not in result.stderr
    assert marker.read_bytes() == b"existing runtime"
