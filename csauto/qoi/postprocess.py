"""Walk a RUNS directory and assemble a campaign-level table of QoIs.

Schema of the produced table:

    case_id | <doe columns...> | <qoi columns...> | _status | _errors

One row per case directory under RUNS/. `_status` is "ok" if every recipe
extracted successfully for that case, "error" otherwise; `_errors` carries
a semicolon-separated list of human-readable error messages.

The module is stdlib-only — CSV / JSON / TSV are written without pandas.
Users wanting parquet/feather/pickle convert downstream from the CSV.
"""

from __future__ import annotations

import csv
import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..doe import read_doe_row
from .errors import QoIError
from .extractor import Context
from .recipe import Recipe
from .registry import get_extractor


@dataclass(frozen=True)
class CaseResult:
    """Outcome of running every configured recipe against one case."""

    case_id: str
    doe_row: dict[str, str] = field(default_factory=dict)
    qois: dict[str, float] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "ok" if not self.errors else "error"


def extract_case_qois(case_dir: Path, recipes: Sequence[Recipe]) -> CaseResult:
    """Apply every recipe to one case directory and gather the QoIs.

    Errors raised by individual extractors are caught and recorded in the
    returned CaseResult: the corresponding QoI value is set to NaN so the
    row remains usable in pandas/Excel without exploding the whole campaign.
    """
    case_id = case_dir.name
    doe_row, _columns = read_doe_row(case_dir)
    qois: dict[str, float] = {}
    errors: list[str] = []

    for recipe in recipes:
        try:
            extractor_cls = get_extractor(recipe.type)
        except QoIError as exc:
            qois[recipe.name] = math.nan
            errors.append(f"{recipe.name}: {exc}")
            continue
        extractor = extractor_cls()
        ctx = Context(case_dir=case_dir, doe_row=doe_row, runs_dir=case_dir.parent)
        try:
            result = extractor.extract(ctx, recipe)
        except QoIError as exc:
            qois[recipe.name] = math.nan
            errors.append(f"{recipe.name}: {exc}")
            continue
        except Exception as exc:
            qois[recipe.name] = math.nan
            errors.append(f"{recipe.name}: unexpected {type(exc).__name__}: {exc}")
            continue
        # An extractor may return several columns (composite recipes); merge them all.
        for column, value in result.items():
            qois[column] = float(value) if value is not None else math.nan

    return CaseResult(case_id=case_id, doe_row=doe_row, qois=qois, errors=errors)


def extract_runs_qois(
    runs_dir: Path,
    recipes: Sequence[Recipe],
    *,
    cases: Sequence[str] | None = None,
) -> list[CaseResult]:
    """Apply every recipe across every (selected) case under runs_dir.

    Args:
        runs_dir: The RUNS/ directory.
        recipes: Recipes from the config (typically Config.qoi_recipes).
        cases: Optional iterable of case IDs to restrict the walk. If None,
               every directory starting with "case" is processed.
    """
    if not runs_dir.is_dir():
        raise QoIError(f"runs_dir not found: {runs_dir}")
    selected_dirs = _select_case_dirs(runs_dir, cases)
    return [extract_case_qois(case_dir, recipes) for case_dir in selected_dirs]


def doe_column_order(results: Sequence[CaseResult]) -> list[str]:
    """Stable column order: union of all DOE keys, preserving first-seen order.

    The synthetic 'case_id' column produced by csauto's doe_row.csv writer is
    dropped here because the campaign table already has a dedicated 'case_id'
    column at the front.
    """
    seen: dict[str, None] = {}
    for result in results:
        for key in result.doe_row:
            if key == "case_id":
                continue
            seen.setdefault(key, None)
    return list(seen)


def qoi_column_order(results: Sequence[CaseResult]) -> list[str]:
    """Same idea for QoI columns: stable union across all cases."""
    seen: dict[str, None] = {}
    for result in results:
        for key in result.qois:
            seen.setdefault(key, None)
    return list(seen)


def write_table(
    results: Sequence[CaseResult],
    out_path: Path,
    *,
    format: str | None = None,
) -> None:
    """Write the campaign table.

    Format is inferred from out_path's suffix (.csv, .tsv, .json) when
    `format` is None. Pass `format` explicitly to override.
    """
    fmt = (format or _format_from_suffix(out_path)).lower()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rows = _rows_for_table(results)
    columns = _columns_for_table(results)
    if fmt == "csv":
        _write_delimited(rows, columns, out_path, delimiter=",")
    elif fmt == "tsv":
        _write_delimited(rows, columns, out_path, delimiter="\t")
    elif fmt == "json":
        _write_json(rows, columns, out_path)
    else:
        raise ValueError(f"Unsupported output format: {fmt!r} (expected csv, tsv, or json)")


def _format_from_suffix(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if not suffix:
        raise ValueError(f"Cannot infer output format from {path}: no extension. Use --format csv|tsv|json.")
    return suffix


def _select_case_dirs(runs_dir: Path, cases: Sequence[str] | None) -> list[Path]:
    all_case_dirs = sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("case"))
    if cases is None:
        return all_case_dirs
    wanted = set(cases)
    return [p for p in all_case_dirs if p.name in wanted]


def _columns_for_table(results: Sequence[CaseResult]) -> list[str]:
    doe_cols = doe_column_order(results)
    qoi_cols = qoi_column_order(results)
    return ["case_id", *doe_cols, *qoi_cols, "_status", "_errors"]


def _rows_for_table(results: Sequence[CaseResult]) -> list[dict[str, Any]]:
    doe_cols = doe_column_order(results)
    qoi_cols = qoi_column_order(results)
    rows: list[dict[str, Any]] = []
    for result in results:
        row: dict[str, Any] = {"case_id": result.case_id}
        for col in doe_cols:
            row[col] = result.doe_row.get(col, "")
        for col in qoi_cols:
            row[col] = result.qois.get(col, math.nan)
        row["_status"] = result.status
        row["_errors"] = "; ".join(result.errors)
        rows.append(row)
    return rows


def _write_delimited(
    rows: Iterable[dict[str, Any]],
    columns: Sequence[str],
    out_path: Path,
    *,
    delimiter: str,
) -> None:
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(columns), delimiter=delimiter)
        writer.writeheader()
        for row in rows:
            writer.writerow({col: _delimited_cell(row.get(col, "")) for col in columns})


def _delimited_cell(value: Any) -> str:
    if isinstance(value, float) and math.isnan(value):
        return ""  # pandas reads empty cells as NaN — best for downstream
    return "" if value is None else str(value)


def _write_json(
    rows: Iterable[dict[str, Any]],
    columns: Sequence[str],
    out_path: Path,
) -> None:
    cleaned: list[dict[str, Any]] = []
    for row in rows:
        cleaned_row: dict[str, Any] = {}
        for col in columns:
            value = row.get(col)
            if isinstance(value, float) and math.isnan(value):
                cleaned_row[col] = None
            else:
                cleaned_row[col] = value
        cleaned.append(cleaned_row)
    out_path.write_text(json.dumps(cleaned, indent=2) + "\n", encoding="utf-8")


__all__ = [
    "CaseResult",
    "doe_column_order",
    "extract_case_qois",
    "extract_runs_qois",
    "qoi_column_order",
    "write_table",
]
