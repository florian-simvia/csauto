from __future__ import annotations

import json
import urllib.request
from pathlib import Path

import pytest

_ACTIVE_TEST_CLIENT = None


def _http_get(url: str) -> tuple[int, str]:
    global _ACTIVE_TEST_CLIENT
    if _ACTIVE_TEST_CLIENT is None:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status, resp.read().decode("utf-8")
    path = url.split("http://testserver", 1)[1] if "testserver" in url else url
    response = _ACTIVE_TEST_CLIENT.get(path)
    return response.status_code, response.text


def _start_server(runs_dir: Path) -> tuple[str, object]:
    global _ACTIVE_TEST_CLIENT
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi.testclient import TestClient

    from csauto.fastapi_app import create_fastapi_app

    app = create_fastapi_app(runs_dir)
    client = TestClient(app)
    _ACTIVE_TEST_CLIENT = client
    return "http://testserver", client


def _make_case_with_force_csv(runs_dir: Path, case_id: str, boundary: str, fx: float) -> Path:
    case_dir = runs_dir / case_id
    (case_dir / "DATA").mkdir(parents=True)
    (case_dir / "DATA" / "setup.xml").write_text("<root/>", encoding="utf-8")
    (case_dir / "doe_row.csv").write_text(f"case_id,u\n{case_id},30.0\n", encoding="utf-8")
    monitoring = case_dir / "RESU" / "001" / "monitoring"
    monitoring.mkdir(parents=True)
    (monitoring / f"csauto_forces_{boundary}.csv").write_text(
        f"t,Fx,Fy,Fz,Fn,Ftx,Fty,Ftz\n0.1,{fx},0,0,0,0,0,0\n",
        encoding="utf-8",
    )
    return case_dir


def _write_csauto_toml(runs_dir: Path, body: str) -> Path:
    toml_path = runs_dir / "csauto.toml"
    toml_path.write_text(body, encoding="utf-8")
    return toml_path


def test_qoi_results_empty_when_no_recipes(runs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No [[qoi]] in config → 200 with empty rows + empty columns."""
    global _ACTIVE_TEST_CLIENT
    _make_case_with_force_csv(runs_dir, "case0001", "wing", fx=12.0)
    _write_csauto_toml(runs_dir, "")  # no qoi section
    monkeypatch.chdir(runs_dir)

    base_url, client = _start_server(runs_dir)
    try:
        status, body = _http_get(f"{base_url}/api/qoi/results")
        assert status == 200
        data = json.loads(body)
        assert data["recipes"] == []
        assert data["doe_columns"] == []
        assert data["qoi_columns"] == []
        assert data["rows"] == []
        assert data["n_total"] == 0
        assert data["n_errors"] == 0
        assert data["mode"] == "managed"
    finally:
        client.close()
        _ACTIVE_TEST_CLIENT = None


def test_qoi_results_with_recipes_returns_extracted_table(runs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """[[qoi]] configured + matching CSV → row with computed Cd."""
    global _ACTIVE_TEST_CLIENT
    _make_case_with_force_csv(runs_dir, "case0001", "wing", fx=12.0)
    _write_csauto_toml(
        runs_dir,
        """\
[[qoi]]
name = "Cd"
type = "force_coefficient"
boundary = "wing"
direction = [1.0, 0.0, 0.0]
ref_area = 1.0
ref_velocity = 1.0
ref_density = 1.0
aggregate = "final"
""",
    )
    monkeypatch.chdir(runs_dir)

    base_url, client = _start_server(runs_dir)
    try:
        status, body = _http_get(f"{base_url}/api/qoi/results")
        assert status == 200
        data = json.loads(body)
        assert len(data["recipes"]) == 1
        assert data["recipes"][0] == {"name": "Cd", "type": "force_coefficient"}
        assert "u" in data["doe_columns"]
        assert "Cd" in data["qoi_columns"]
        assert data["n_total"] == 1
        assert data["n_errors"] == 0
        assert data["rows"][0]["case_id"] == "case0001"
        assert data["rows"][0]["doe"]["u"] == "30.0"
        # q = 0.5 * 1 * 1^2 = 0.5; F = 12 -> Cd = 24
        assert data["rows"][0]["qois"]["Cd"] == pytest.approx(24.0)
        assert data["rows"][0]["status"] == "ok"
        assert data["rows"][0]["errors"] == []
    finally:
        client.close()
        _ACTIVE_TEST_CLIENT = None


def test_qoi_results_reports_per_recipe_errors(runs_dir: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Recipe wired but no force CSV produced → NaN value + error row."""
    global _ACTIVE_TEST_CLIENT
    case_dir = runs_dir / "case0001"
    (case_dir / "DATA").mkdir(parents=True)
    (case_dir / "DATA" / "setup.xml").write_text("<root/>", encoding="utf-8")
    (case_dir / "doe_row.csv").write_text("case_id,u\ncase0001,30\n", encoding="utf-8")
    _write_csauto_toml(
        runs_dir,
        """\
[[qoi]]
name = "Cd"
type = "force_coefficient"
boundary = "wing"
direction = [1.0, 0.0, 0.0]
ref_area = 1.0
ref_velocity = 1.0
ref_density = 1.0
""",
    )
    monkeypatch.chdir(runs_dir)

    base_url, client = _start_server(runs_dir)
    try:
        status, body = _http_get(f"{base_url}/api/qoi/results")
        assert status == 200
        data = json.loads(body)
        assert data["n_errors"] == 1
        assert data["rows"][0]["status"] == "error"
        assert data["rows"][0]["qois"]["Cd"] is None  # NaN -> null in JSON
        assert any("no RESU run found" in e for e in data["rows"][0]["errors"])
    finally:
        client.close()
        _ACTIVE_TEST_CLIENT = None
