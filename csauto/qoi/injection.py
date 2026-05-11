"""Inject the auto-generated cs_user_extra_operations.cpp into case directories.

Mode 'managed' only — refuses if cs_user_extra_operations is already defined
anywhere in SRC/, even in a file with a different name (the user can place
that function in cs_user_source_terms.cpp, cs_user_boundary_conditions.cpp,
or any other user file). A future phase will add mode 'injected' (coexistence
with a user-authored definition).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from .cpp import assemble_user_extra_operations
from .errors import QoIError
from .recipe import Recipe

_USER_FILE_BASENAME = "cs_user_extra_operations"
_USER_FILE_EXTENSIONS = (".cpp", ".cxx", ".cc", ".c")
_TARGET_FILENAME = f"{_USER_FILE_BASENAME}.cpp"

# Matches a function definition (not a declaration) of cs_user_extra_operations.
# The trailing `{` makes a forward declaration like
#   void cs_user_extra_operations(cs_domain_t *);
# safely ignored.
_EXTRA_OPS_DEFINITION_RE = re.compile(
    r"\bcs_user_extra_operations\s*\(\s*cs_domain_t\s*\*[^)]*\)\s*\{",
    re.MULTILINE,
)
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)


def inject_user_files_into_case(case_dir: Path, recipes: Sequence[Recipe]) -> Path | None:
    """Write the auto-generated user file for one case.

    Returns the path written, or None if no recipe needed C++ injection.
    Raises QoIError if cs_user_extra_operations is already defined anywhere
    in caseXXXX/SRC/ — that's the 'managed' mode contract.
    """
    if not recipes:
        return None
    source = assemble_user_extra_operations(recipes)
    if source is None:
        return None

    src_dir = case_dir / "SRC"
    existing = _find_existing_extra_ops_definition(src_dir)
    target = src_dir / _TARGET_FILENAME

    if existing is not None and not _is_csauto_managed(existing):
        raise QoIError(
            f"cs_user_extra_operations is already defined in {existing.name} "
            f"(case {case_dir.name}). csauto's 'managed' mode requires this "
            f"function to be exclusive to csauto's generated user file. "
            f"Either move that definition out of {existing.name} (and let "
            f"csauto manage cs_user_extra_operations.cpp), or remove [[qoi]] "
            f"from your csauto.toml. A future 'injected' coexistence mode is "
            f"planned but not yet available."
        )

    src_dir.mkdir(exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return target


def inject_user_files_into_runs(runs_dir: Path, recipes: Sequence[Recipe]) -> list[Path]:
    """Inject user files in every caseXXXX/ under runs_dir.

    Stops at the first error so the user sees a clear message instead of a
    half-injected campaign.
    """
    if not recipes:
        return []
    if not runs_dir.is_dir():
        raise QoIError(f"runs_dir not found: {runs_dir}")

    written: list[Path] = []
    for case_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("case")):
        path = inject_user_files_into_case(case_dir, recipes)
        if path is not None:
            written.append(path)
    return written


def _find_existing_extra_ops_definition(src_dir: Path) -> Path | None:
    """Return the first file in SRC/ that defines cs_user_extra_operations, if any.

    Scans every C/C++ source file (.cpp, .cxx, .cc, .c). Comments and string
    literals are stripped before matching so a function call or doc reference
    does not trigger a false positive.
    """
    if not src_dir.is_dir():
        return None
    for path in _iter_source_files(src_dir):
        if _defines_extra_operations(path):
            return path
    return None


def _iter_source_files(src_dir: Path) -> list[Path]:
    files: list[Path] = []
    for ext in _USER_FILE_EXTENSIONS:
        files.extend(src_dir.glob(f"*{ext}"))
    return sorted(files)


def _defines_extra_operations(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    stripped = _strip_noise(text)
    return _EXTRA_OPS_DEFINITION_RE.search(stripped) is not None


def _strip_noise(source: str) -> str:
    """Remove string literals and comments so regex matches the actual code."""
    source = _STRING_LITERAL_RE.sub('""', source)
    source = _BLOCK_COMMENT_RE.sub("", source)
    source = _LINE_COMMENT_RE.sub("", source)
    return source


def _is_csauto_managed(path: Path) -> bool:
    """True if the file was previously written by csauto (safe to overwrite).

    We use a marker line in the generated file header rather than a side-channel
    metadata file: it survives repository operations and is impossible to spoof
    accidentally.
    """
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:512]
    except OSError:
        return False
    return "auto-generated by csauto" in head


__all__ = [
    "inject_user_files_into_case",
    "inject_user_files_into_runs",
]
