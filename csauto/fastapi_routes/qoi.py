"""FastAPI routes for QoI auto-postprocessing.

Exposes the campaign-level QoI table to the dashboard. The route re-loads
config and re-walks RUNS/ on every request: it's cheap (parses small CSVs
already on disk), and ensures any change to csauto.toml takes effect without
restarting the server.
"""

from __future__ import annotations

import math
from typing import Any

from ..config import load_config
from ..qoi.errors import QoIError
from ..qoi.postprocess import (
    doe_column_order,
    extract_runs_qois,
    qoi_column_order,
)


def register_qoi_routes(app: Any, ctx: Any, components: dict[str, Any]) -> None:
    BaseModel = components["BaseModel"]
    Header = components["Header"]

    class QoIRecipeSummary(BaseModel):
        name: str
        type: str

    class QoIResultRow(BaseModel):
        case_id: str
        doe: dict[str, str]
        qois: dict[str, float | None]
        status: str
        errors: list[str]

    class QoIResultsResponse(BaseModel):
        recipes: list[QoIRecipeSummary]
        doe_columns: list[str]
        qoi_columns: list[str]
        rows: list[QoIResultRow]
        n_total: int
        n_errors: int
        mode: str

    @app.get("/api/qoi/results", response_model=QoIResultsResponse)
    def get_qoi_results(
        x_csauto_token: str | None = Header(default=None),
        authorization: str | None = Header(default=None),
    ) -> QoIResultsResponse:
        ctx.require_auth(x_csauto_token, authorization)

        # Re-load config from disk so live edits to csauto.toml take effect.
        config = load_config(None)
        recipes = config.qoi_recipes
        recipe_summaries = [QoIRecipeSummary(name=r.name, type=r.type) for r in recipes]
        if not recipes:
            return QoIResultsResponse(
                recipes=recipe_summaries,
                doe_columns=[],
                qoi_columns=[],
                rows=[],
                n_total=0,
                n_errors=0,
                mode=config.qoi_mode,
            )

        try:
            results = extract_runs_qois(ctx.runs_dir, recipes)
        except QoIError as exc:
            raise ctx.http_exception_cls(status_code=400, detail=str(exc)) from exc

        doe_cols = doe_column_order(results)
        qoi_cols = qoi_column_order(results)

        rows: list[QoIResultRow] = []
        for r in results:
            doe = {col: r.doe_row.get(col, "") for col in doe_cols}
            qois: dict[str, float | None] = {}
            for col in qoi_cols:
                value = r.qois.get(col)
                if value is None or (isinstance(value, float) and math.isnan(value)):
                    qois[col] = None
                else:
                    qois[col] = float(value)
            rows.append(
                QoIResultRow(
                    case_id=r.case_id,
                    doe=doe,
                    qois=qois,
                    status=r.status,
                    errors=list(r.errors),
                )
            )
        n_errors = sum(1 for r in results if r.errors)

        return QoIResultsResponse(
            recipes=recipe_summaries,
            doe_columns=doe_cols,
            qoi_columns=qoi_cols,
            rows=rows,
            n_total=len(results),
            n_errors=n_errors,
            mode=config.qoi_mode,
        )


__all__ = ["register_qoi_routes"]
