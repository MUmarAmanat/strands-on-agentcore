"""Build the AgentCore Runtime zip (direct code deploy, no Docker).

AgentCore runs Python 3.12 on Linux ARM64 and installs nothing itself, so
the zip must hold our code plus every runtime dependency built for that
platform. uv downloads the Linux ARM64 wheels, so this works on any OS.

Flow:
    1. ``export_requirements`` writes the locked runtime deps (no dev deps).
    2. ``install_dependencies`` installs them into the staging folder.
    3. ``copy_source`` copies ``reminder_agent`` and writes the ``main.py``
       shim that AgentCore starts.
    4. ``write_zip`` zips the staging folder deterministically.
    5. ``main`` prints ``{"path", "sha256"}`` as JSON for Terraform's
       ``external`` data source. All other output goes to stderr.

Run with ``uv run python scripts/build_package.py``.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_PACKAGE = ROOT / "src" / "reminder_agent"
BUILD_DIR = ROOT / "build"

PYTHON_VERSION = "3.12"
PLATFORM = "aarch64-manylinux2014"

# Fixed timestamp (the zip format minimum), so equal input gives equal bytes.
ZIP_DATE_TIME = (1980, 1, 1, 0, 0, 0)
EXCLUDED_DIRS = {"__pycache__"}
EXCLUDED_SUFFIXES = {".pyc"}

# AgentCore starts a file by path. Running reminder_agent/app.py directly
# would break its package imports, so a root-level shim imports it instead.
#
# The shim also starts OpenTelemetry tracing (ADOT) BEFORE the app, and so
# Strands and boto3, are imported: instrumentation patches libraries at
# import time. AWS docs use the `opentelemetry-instrument` launcher for this,
# but that script is generated for the build OS (Windows here) and may not
# run on the Linux runtime; initialize() is the same call, in plain Python.
# Tracing is fail-open: if it cannot start, the reminder still runs.
SHIM_SOURCE = (
    '"""AgentCore entry point: start tracing, then the reminder agent."""\n'
    "\n"
    "import sys\n"
    "\n"
    "try:\n"
    "    # Patch libraries before the app (Strands, boto3) is imported.\n"
    "    from opentelemetry.instrumentation.auto_instrumentation import (\n"
    "        initialize,\n"
    "    )\n"
    "\n"
    "    initialize()\n"
    "except Exception as exc:  # Tracing must never stop the reminder.\n"
    '    print(f"Tracing disabled: {exc}", file=sys.stderr)\n'
    "\n"
    "from reminder_agent.app import app  # noqa: E402\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    app.run()\n"
)


def _run(cmd: list[str]) -> None:
    """Run a command from the repo root, sending its output to stderr.

    Args:
        cmd: Command and arguments.

    Raises:
        subprocess.CalledProcessError: If the command fails.
    """
    # stdout is reserved for the JSON result that Terraform reads.
    subprocess.run(cmd, check=True, cwd=ROOT, stdout=sys.stderr)


def export_requirements(requirements_file: Path) -> None:
    """Write the locked runtime dependencies to a requirements file.

    Args:
        requirements_file: Output path.
    """
    _run([
        "uv", "export",
        "--frozen",           # use uv.lock as is; never re-resolve
        "--no-dev",           # skip pytest, python-dotenv
        "--no-hashes",
        "--no-emit-project",  # our code is copied, not installed
        "--output-file", str(requirements_file),
    ])


def install_dependencies(requirements_file: Path, target: Path) -> None:
    """Install Linux ARM64 / Python 3.12 wheels into ``target``.

    Args:
        requirements_file: Requirements written by ``export_requirements``.
        target: Staging folder that becomes the zip root.
    """
    _run([
        "uv", "pip", "install",
        "--requirement", str(requirements_file),
        "--target", str(target),
        "--python-platform", PLATFORM,
        "--python-version", PYTHON_VERSION,
        "--only-binary", ":all:",  # no source builds for a foreign platform
    ])


def _is_excluded(path: Path) -> bool:
    """Return True for bytecode caches, which must not enter the zip."""
    return (
        path.suffix in EXCLUDED_SUFFIXES
        or any(part in EXCLUDED_DIRS for part in path.parts)
    )


def copy_source(target: Path) -> None:
    """Copy the ``reminder_agent`` package and write the ``main.py`` shim.

    Args:
        target: Staging folder that becomes the zip root.
    """
    shutil.copytree(
        SRC_PACKAGE,
        target / "reminder_agent",
        ignore=shutil.ignore_patterns(*EXCLUDED_DIRS, "*.pyc"),
    )
    (target / "main.py").write_text(SHIM_SOURCE, encoding="utf-8")


def write_zip(source_dir: Path, zip_path: Path) -> str:
    """Zip ``source_dir`` deterministically and return the zip's SHA-256.

    Files are added in sorted order with a fixed timestamp and fixed
    permissions, so unchanged input always gives an identical zip and
    Terraform only redeploys when something really changed.

    Args:
        source_dir: Folder whose contents become the zip root.
        zip_path: Output zip path.

    Returns:
        Hex SHA-256 of the written zip.
    """
    files = sorted(
        path for path in source_dir.rglob("*")
        if path.is_file()
        and not _is_excluded(path.relative_to(source_dir))
    )
    with zipfile.ZipFile(zip_path, "w") as archive:
        for path in files:
            info = zipfile.ZipInfo(
                path.relative_to(source_dir).as_posix(), ZIP_DATE_TIME
            )
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16  # rw-r--r-- on Linux
            archive.writestr(info, path.read_bytes())
    return hashlib.sha256(zip_path.read_bytes()).hexdigest()


def main() -> None:
    """Build ``build/agent.zip`` and print its path and SHA-256 as JSON."""
    staging = BUILD_DIR / "staging"
    requirements_file = BUILD_DIR / "requirements.txt"
    zip_path = BUILD_DIR / "agent.zip"

    # Start clean so files from an older build never leak into the zip.
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    export_requirements(requirements_file)
    install_dependencies(requirements_file, staging)
    copy_source(staging)
    sha256 = write_zip(staging, zip_path)

    print(json.dumps({"path": str(zip_path), "sha256": sha256}))


if __name__ == "__main__":
    main()
