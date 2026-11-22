"""Unit tests for scripts/build_package.py (no network, subprocess mocked)."""

from __future__ import annotations

import json
import sys
import types
import zipfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import build_package as bp

_OTEL_MODULE = "opentelemetry.instrumentation.auto_instrumentation"


@pytest.fixture
def staging(tmp_path: Path) -> Path:
    """A small staging folder with code, a dependency and bytecode caches."""
    root = tmp_path / "staging"
    (root / "pkg" / "__pycache__").mkdir(parents=True)
    (root / "pkg" / "__init__.py").write_text("x = 1\n")
    (root / "pkg" / "__pycache__" / "__init__.cpython-312.pyc").write_bytes(b"c")
    (root / "stray.pyc").write_bytes(b"c")
    (root / "main.py").write_text("print('hi')\n")
    return root


def _names(zip_path: Path) -> set[str]:
    """Return the entry names in a zip."""
    with zipfile.ZipFile(zip_path) as archive:
        return set(archive.namelist())


def test_write_zip_contains_files_with_posix_paths(staging, tmp_path):
    """The zip holds the source files, using forward-slash paths."""
    zip_path = tmp_path / "a.zip"
    bp.write_zip(staging, zip_path)
    assert _names(zip_path) == {"main.py", "pkg/__init__.py"}


def test_write_zip_excludes_bytecode(staging, tmp_path):
    """__pycache__ folders and .pyc files are left out."""
    zip_path = tmp_path / "a.zip"
    bp.write_zip(staging, zip_path)
    assert not any(
        "__pycache__" in name or name.endswith(".pyc")
        for name in _names(zip_path)
    )


def test_write_zip_is_deterministic(staging, tmp_path):
    """Same input twice gives the same SHA-256 (no needless redeploy)."""
    first = bp.write_zip(staging, tmp_path / "a.zip")
    second = bp.write_zip(staging, tmp_path / "b.zip")
    assert first == second


def test_write_zip_hash_changes_with_content(staging, tmp_path):
    """Changing a file changes the SHA-256 (a real change redeploys)."""
    before = bp.write_zip(staging, tmp_path / "a.zip")
    (staging / "main.py").write_text("print('changed')\n")
    after = bp.write_zip(staging, tmp_path / "b.zip")
    assert before != after


def test_write_zip_fixed_timestamp_and_mode(staging, tmp_path):
    """Entries use the fixed 1980 timestamp and rw-r--r-- permissions."""
    zip_path = tmp_path / "a.zip"
    bp.write_zip(staging, zip_path)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            assert info.date_time == bp.ZIP_DATE_TIME
            assert info.external_attr >> 16 == 0o644


def test_copy_source_adds_package_and_shim(tmp_path):
    """The package and main.py shim are copied, without bytecode caches."""
    src = tmp_path / "src_pkg"
    (src / "__pycache__").mkdir(parents=True)
    (src / "app.py").write_text("app = None\n")
    (src / "__pycache__" / "app.cpython-312.pyc").write_bytes(b"c")
    target = tmp_path / "staging"
    target.mkdir()

    with patch.object(bp, "SRC_PACKAGE", src):
        bp.copy_source(target)

    assert (target / "reminder_agent" / "app.py").is_file()
    assert not (target / "reminder_agent" / "__pycache__").exists()
    assert (target / "main.py").read_text() == bp.SHIM_SOURCE


def _run_shim(
    monkeypatch: pytest.MonkeyPatch,
    initialize: Callable[[], None] | None,
    run_name: str = "main",
) -> tuple[list[str], MagicMock]:
    """Execute SHIM_SOURCE with fake ADOT and app modules.

    Args:
        monkeypatch: Pytest fixture, restores ``sys.modules`` afterwards.
        initialize: Body of the fake ``initialize()``; None means ADOT is
            not installed.
        run_name: ``__name__`` for the shim; ``"__main__"`` starts the app.

    Returns:
        The ordered list of events ("initialize", "import_app") and the
        fake app object.
    """
    calls: list[str] = []
    app = MagicMock()

    # Module-level __getattr__ records when the shim imports `app`.
    app_module = types.ModuleType("reminder_agent.app")

    def get_app(name: str) -> MagicMock:
        if name != "app":
            # Python also probes e.g. __path__; behave like a plain module.
            raise AttributeError(name)
        calls.append("import_app")
        return app

    app_module.__getattr__ = get_app
    monkeypatch.setitem(sys.modules, "reminder_agent.app", app_module)

    if initialize is None:
        # None in sys.modules makes the import raise ImportError.
        monkeypatch.setitem(sys.modules, _OTEL_MODULE, None)
    else:
        otel_module = types.ModuleType(_OTEL_MODULE)

        def fake_initialize() -> None:
            calls.append("initialize")
            initialize()

        otel_module.initialize = fake_initialize
        monkeypatch.setitem(sys.modules, _OTEL_MODULE, otel_module)

    exec(compile(bp.SHIM_SOURCE, "main.py", "exec"), {"__name__": run_name})
    return calls, app


def test_shim_starts_tracing_before_app_import(monkeypatch):
    """initialize() runs before the app is imported; no server start."""
    calls, app = _run_shim(monkeypatch, initialize=lambda: None)

    assert calls == ["initialize", "import_app"]
    app.run.assert_not_called()


def test_shim_tracing_error_does_not_stop_app(monkeypatch, capsys):
    """A failing initialize() logs a warning and the app still loads."""
    def boom() -> None:
        raise RuntimeError("boom")

    calls, _ = _run_shim(monkeypatch, initialize=boom)

    assert calls == ["initialize", "import_app"]
    assert "Tracing disabled: boom" in capsys.readouterr().err


def test_shim_without_adot_still_loads_app(monkeypatch, capsys):
    """Missing ADOT (ImportError) logs a warning and the app still loads."""
    calls, _ = _run_shim(monkeypatch, initialize=None)

    assert calls == ["import_app"]
    assert "Tracing disabled" in capsys.readouterr().err


def test_shim_runs_app_as_main(monkeypatch):
    """Started as __main__ (as AgentCore does), the shim runs the server."""
    _, app = _run_shim(monkeypatch, initialize=lambda: None, run_name="__main__")

    app.run.assert_called_once_with()


def test_export_requirements_command(tmp_path):
    """uv export uses the lock as is, without dev deps or our project."""
    with patch.object(bp, "_run") as run:
        bp.export_requirements(tmp_path / "req.txt")
    cmd = run.call_args.args[0]
    assert cmd[:2] == ["uv", "export"]
    for flag in ("--frozen", "--no-dev", "--no-emit-project"):
        assert flag in cmd


def test_install_dependencies_targets_linux_arm64(tmp_path):
    """uv pip install targets Linux ARM64, Python 3.12, wheels only."""
    with patch.object(bp, "_run") as run:
        bp.install_dependencies(tmp_path / "req.txt", tmp_path / "out")
    cmd = run.call_args.args[0]
    assert cmd[cmd.index("--python-platform") + 1] == "aarch64-manylinux2014"
    assert cmd[cmd.index("--python-version") + 1] == "3.12"
    assert cmd[cmd.index("--only-binary") + 1] == ":all:"
    assert cmd[cmd.index("--target") + 1] == str(tmp_path / "out")


def test_main_prints_only_json(tmp_path, capsys):
    """main prints just {path, sha256} on stdout, as Terraform expects."""
    with patch.object(bp, "BUILD_DIR", tmp_path), \
            patch.object(bp, "export_requirements"), \
            patch.object(bp, "install_dependencies"), \
            patch.object(bp, "copy_source"), \
            patch.object(bp, "write_zip", return_value="abc123"):
        bp.main()

    result = json.loads(capsys.readouterr().out)
    assert result == {
        "path": str(tmp_path / "agent.zip"),
        "sha256": "abc123",
    }
