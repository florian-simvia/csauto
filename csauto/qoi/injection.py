"""Inject the auto-generated user-file(s) into case directories.

Two modes:

* ``managed`` (default): csauto owns ``cs_user_extra_operations.cpp``. Refuses
  if ``cs_user_extra_operations`` is already defined anywhere in SRC/, even
  in a file with a different name (the user can place that function in
  cs_user_source_terms.cpp, cs_user_boundary_conditions.cpp, or any other
  user file).

* ``injected``: csauto writes a separate pair (``cs_user_csauto_qoi.cpp`` +
  ``cs_user_csauto_qoi.h``) that exposes a single ``csauto_qoi_dispatch()``
  function. The user keeps ownership of ``cs_user_extra_operations`` and is
  expected to ``#include`` the header and call ``csauto_qoi_dispatch(domain)``
  from inside their own function — csauto never edits user code.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

from .cpp import assemble_dispatcher_files, assemble_user_extra_operations
from .errors import QoIError
from .recipe import Recipe

_USER_FILE_BASENAME = "cs_user_extra_operations"
_USER_FILE_EXTENSIONS = (".cpp", ".cxx", ".cc", ".c")
_TARGET_FILENAME = f"{_USER_FILE_BASENAME}.cpp"

_INJECTED_BASENAME = "cs_user_csauto_qoi"
_INJECTED_SOURCE_FILENAME = f"{_INJECTED_BASENAME}.cpp"
_INJECTED_HEADER_FILENAME = f"{_INJECTED_BASENAME}.h"

VALID_MODES = ("managed", "injected")
_DEFAULT_MODE = "managed"

# Matches a function definition (not a declaration) of cs_user_extra_operations.
# The trailing `{` makes a forward declaration like
#   void cs_user_extra_operations(cs_domain_t *);
# safely ignored.
_EXTRA_OPS_DEFINITION_RE = re.compile(
    r"\bcs_user_extra_operations\s*\(\s*cs_domain_t\s*\*[^)]*\)\s*\{",
    re.MULTILINE,
)
_DISPATCH_CALL_RE = re.compile(r"\bcsauto_qoi_dispatch\s*\(")
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)
_LINE_COMMENT_RE = re.compile(r"//[^\n]*")
_STRING_LITERAL_RE = re.compile(r'"(?:\\.|[^"\\])*"', re.DOTALL)


@dataclass(frozen=True)
class InjectionReport:
    """Outcome of one case injection — useful for the caller to surface info."""

    case_dir: Path
    mode: str
    files_written: list[Path] = field(default_factory=list)
    user_file_with_extra_ops: Path | None = None
    dispatch_call_present: bool = False

    @property
    def needs_user_action(self) -> bool:
        """True only in injected mode when the user hasn't wired the dispatch call yet."""
        if self.mode != "injected":
            return False
        return not self.dispatch_call_present


def inject_user_files_into_case(
    case_dir: Path,
    recipes: Sequence[Recipe],
    *,
    mode: str = _DEFAULT_MODE,
) -> InjectionReport:
    """Write the auto-generated user file(s) for one case.

    The returned report lists the files written and (in injected mode) whether
    the user's cs_user_extra_operations function already calls
    csauto_qoi_dispatch. An empty report means there was nothing to do (no
    recipes, or no recipe needed C++ injection).
    """
    if mode not in VALID_MODES:
        raise QoIError(f"Unknown qoi_mode {mode!r}. Expected one of {VALID_MODES}.")
    empty = InjectionReport(case_dir=case_dir, mode=mode)
    if not recipes:
        return empty

    if mode == "managed":
        return _inject_managed(case_dir, recipes)
    if mode == "injected":
        return _inject_injected(case_dir, recipes)
    return empty  # pragma: no cover - VALID_MODES guard above


def inject_user_files_into_runs(
    runs_dir: Path,
    recipes: Sequence[Recipe],
    *,
    mode: str = _DEFAULT_MODE,
) -> list[InjectionReport]:
    """Inject user files in every caseXXXX/ under runs_dir.

    Stops at the first hard error so the user sees a clear message instead of
    a half-injected campaign. Returns one report per processed case.
    """
    if not recipes:
        return []
    if not runs_dir.is_dir():
        raise QoIError(f"runs_dir not found: {runs_dir}")

    reports: list[InjectionReport] = []
    for case_dir in sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("case")):
        reports.append(inject_user_files_into_case(case_dir, recipes, mode=mode))
    return reports


# --- Managed mode -----------------------------------------------------------


def _inject_managed(case_dir: Path, recipes: Sequence[Recipe]) -> InjectionReport:
    source = assemble_user_extra_operations(recipes)
    if source is None:
        return InjectionReport(case_dir=case_dir, mode="managed")

    src_dir = case_dir / "SRC"
    existing = _find_existing_extra_ops_definition(src_dir)
    target = src_dir / _TARGET_FILENAME

    if existing is not None and not _is_csauto_managed(existing):
        raise QoIError(
            f"cs_user_extra_operations is already defined in {existing.name} "
            f"(case {case_dir.name}). csauto's 'managed' mode requires this "
            f"function to be exclusive to csauto's generated user file. "
            f"Either move that definition out of {existing.name} (and let "
            f"csauto manage cs_user_extra_operations.cpp), or switch to "
            f"qoi_mode = 'injected' in your csauto.toml to coexist with it."
        )

    src_dir.mkdir(exist_ok=True)
    target.write_text(source, encoding="utf-8")
    return InjectionReport(case_dir=case_dir, mode="managed", files_written=[target])


# --- Injected mode ----------------------------------------------------------


def _inject_injected(case_dir: Path, recipes: Sequence[Recipe]) -> InjectionReport:
    rendered = assemble_dispatcher_files(recipes)
    if rendered is None:
        return InjectionReport(case_dir=case_dir, mode="injected")
    header_text, source_text = rendered

    src_dir = case_dir / "SRC"
    src_dir.mkdir(exist_ok=True)

    header_path = src_dir / _INJECTED_HEADER_FILENAME
    source_path = src_dir / _INJECTED_SOURCE_FILENAME
    for path in (header_path, source_path):
        if path.is_file() and not _is_csauto_managed(path):
            raise QoIError(
                f"{path.name} already exists in {case_dir.name}/SRC/ but was not "
                f"written by csauto. Remove it, rename it, or switch modes."
            )
    header_path.write_text(header_text, encoding="utf-8")
    source_path.write_text(source_text, encoding="utf-8")

    user_file = _find_existing_extra_ops_definition(src_dir)
    dispatch_present = False
    if user_file is not None:
        dispatch_present = _file_calls_dispatch(user_file)

    return InjectionReport(
        case_dir=case_dir,
        mode="injected",
        files_written=[header_path, source_path],
        user_file_with_extra_ops=user_file,
        dispatch_call_present=dispatch_present,
    )


# --- Source-file inspection helpers ----------------------------------------


def _find_existing_extra_ops_definition(src_dir: Path) -> Path | None:
    """Return the first file in SRC/ that defines cs_user_extra_operations, if any.

    Scans every C/C++ source file (.cpp, .cxx, .cc, .c). Comments and string
    literals are stripped before matching so a function call or doc reference
    does not trigger a false positive. csauto-managed files are skipped so
    that re-running prepare doesn't loop through the previously-injected file.
    """
    if not src_dir.is_dir():
        return None
    for path in _iter_source_files(src_dir):
        if _is_csauto_managed(path):
            continue
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


def _file_calls_dispatch(path: Path) -> bool:
    """Return True if the file references csauto_qoi_dispatch in actual code."""
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False
    return _DISPATCH_CALL_RE.search(_strip_noise(text)) is not None


def _strip_noise(source: str) -> str:
    """Remove string literals and comments so regex matches the actual code."""
    source = _STRING_LITERAL_RE.sub('""', source)
    source = _BLOCK_COMMENT_RE.sub("", source)
    source = _LINE_COMMENT_RE.sub("", source)
    return source


def _is_csauto_managed(path: Path) -> bool:
    """True if the file was previously written by csauto (safe to overwrite)."""
    try:
        head = path.read_text(encoding="utf-8", errors="ignore")[:512]
    except OSError:
        return False
    return "auto-generated by csauto" in head


__all__ = [
    "VALID_MODES",
    "InjectionReport",
    "inject_user_files_into_case",
    "inject_user_files_into_runs",
]
