from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

MIN_SATURNE_VERSION: tuple[int, int] = (9, 0)

_VERSION_RE = re.compile(r"(\d+)\.(\d+)(?:\.(\d+))?")


def detect_saturne_version(saturne_bin: str | None = None, timeout: float = 5.0) -> tuple[int, ...] | None:
    """Detect the installed code_saturne version.

    Returns a tuple like (9, 0) or (8, 3, 1), or None if detection fails
    (binary missing, error running, output unparseable). Never raises.

    The caller is expected to fall back gracefully when None is returned.
    """
    bin_path = _resolve_binary(saturne_bin)
    if bin_path is None:
        return None
    try:
        result = subprocess.run(
            [bin_path, "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    return _parse_version(output)


def _resolve_binary(saturne_bin: str | None) -> str | None:
    if saturne_bin:
        path = Path(saturne_bin).expanduser()
        if path.is_file():
            return str(path)
        return None
    return shutil.which("code_saturne")


def _parse_version(text: str) -> tuple[int, ...] | None:
    match = _VERSION_RE.search(text)
    if not match:
        return None
    parts = tuple(int(p) for p in match.groups() if p is not None)
    return parts or None


def is_compatible(version: tuple[int, ...] | None, minimum: tuple[int, ...] = MIN_SATURNE_VERSION) -> bool:
    """Return True if the detected version is >= the minimum.

    Unknown versions (None) are treated as not compatible so callers can warn
    explicitly rather than silently proceeding.
    """
    if version is None:
        return False
    return version >= minimum


def format_version(version: tuple[int, ...] | None) -> str:
    if version is None:
        return "unknown"
    return ".".join(str(p) for p in version)


__all__ = [
    "MIN_SATURNE_VERSION",
    "detect_saturne_version",
    "format_version",
    "is_compatible",
]
