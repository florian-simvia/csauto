from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from csauto.execution import (
    RUNTIME_DOCKER,
    RUNTIME_NATIVE,
    RUNTIME_SINGULARITY,
    RuntimeSelection,
)
from csauto.qoi.compile_check import (
    CompileResult,
    build_compile_command,
    check_compiles_first_case,
    check_compiles_in_case,
)
from csauto.qoi.errors import QoIError

# --- Test helpers -----------------------------------------------------------


def _make_case_with_src(tmp_path: Path, name: str = "case0001") -> Path:
    case_dir = tmp_path / name
    (case_dir / "SRC").mkdir(parents=True)
    (case_dir / "SRC" / "cs_user_extra_operations.cpp").write_text(
        "void cs_user_extra_operations(cs_domain_t *domain) {}\n",
        encoding="utf-8",
    )
    return case_dir


def _docker_selection() -> RuntimeSelection:
    return RuntimeSelection(runtime=RUNTIME_DOCKER, docker_image="simvia/code_saturne:9.1.0")


def _native_selection(bin_path: str) -> RuntimeSelection:
    return RuntimeSelection(
        runtime=RUNTIME_NATIVE,
        docker_image="unused",
        saturne_bin=bin_path,
    )


def _singularity_selection(tmp_path: Path) -> RuntimeSelection:
    return RuntimeSelection(
        runtime=RUNTIME_SINGULARITY,
        docker_image="unused",
        singularity_bin="/usr/bin/apptainer",
        singularity_image=str(tmp_path / "cs.sif"),
    )


# --- build_compile_command --------------------------------------------------


def test_build_compile_command_docker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("csauto.qoi.compile_check.shutil.which", lambda _name: "/usr/bin/docker")
    case_dir = _make_case_with_src(tmp_path)
    cmd = build_compile_command(case_dir, _docker_selection())

    assert cmd[0] == "docker"
    assert "run" in cmd and "--rm" in cmd
    # Mount and workdir derived from the parent RUNS-like dir
    assert f"{tmp_path.resolve()}:/home/code_saturne" in cmd
    assert f"/home/code_saturne/{case_dir.name}" in cmd
    # The shell invocation overrides the image entrypoint
    assert "--entrypoint" in cmd
    assert "/bin/bash" in cmd
    # The shell command is the last argument
    assert cmd[-2] == "-c"
    assert cmd[-1] == "code_saturne compile -t -s SRC"


def test_build_compile_command_native(tmp_path: Path) -> None:
    case_dir = _make_case_with_src(tmp_path)
    cmd = build_compile_command(case_dir, _native_selection("/opt/cs/bin/code_saturne"))

    assert cmd == [
        "/opt/cs/bin/code_saturne",
        "compile",
        "-t",
        "-s",
        str(case_dir / "SRC"),
    ]


def test_build_compile_command_singularity(tmp_path: Path) -> None:
    case_dir = _make_case_with_src(tmp_path)
    cmd = build_compile_command(case_dir, _singularity_selection(tmp_path))

    assert cmd[0] == "/usr/bin/apptainer"
    assert "exec" in cmd
    assert "--bind" in cmd
    assert "--pwd" in cmd
    # Trailing positional args for the in-container code_saturne invocation
    assert cmd[-5:] == ["code_saturne", "compile", "-t", "-s", "SRC"]


def test_build_compile_command_native_missing_bin_raises(tmp_path: Path) -> None:
    case_dir = _make_case_with_src(tmp_path)
    selection = RuntimeSelection(runtime=RUNTIME_NATIVE, docker_image="unused", saturne_bin=None)
    with pytest.raises(QoIError, match="requires saturne_bin"):
        build_compile_command(case_dir, selection)


def test_build_compile_command_docker_missing_runtime_raises(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("csauto.qoi.compile_check.shutil.which", lambda _name: None)
    case_dir = _make_case_with_src(tmp_path)
    with pytest.raises(QoIError, match="docker not found"):
        build_compile_command(case_dir, _docker_selection())


def test_build_compile_command_unsupported_runtime_raises(tmp_path: Path) -> None:
    case_dir = _make_case_with_src(tmp_path)
    selection = RuntimeSelection(runtime="auto", docker_image="x")
    with pytest.raises(QoIError, match="Unsupported runtime"):
        build_compile_command(case_dir, selection)


# --- check_compiles_in_case ---------------------------------------------------


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, *, returncode: int, stdout: str = "", stderr: str = "") -> None:
    class _Completed:
        def __init__(self) -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = stderr

    monkeypatch.setattr(
        "csauto.qoi.compile_check.subprocess.run",
        lambda *args, **kwargs: _Completed(),
    )


def test_check_compiles_in_case_success(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = _make_case_with_src(tmp_path)
    _patch_subprocess(monkeypatch, returncode=0, stdout="ok\n")
    result = check_compiles_in_case(case_dir, _native_selection("/opt/cs/bin/code_saturne"))

    assert result.success is True
    assert result.exit_code == 0
    assert result.timed_out is False
    assert "ok" in result.log
    assert "OK" in result.summary()


def test_check_compiles_in_case_failure(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = _make_case_with_src(tmp_path)
    _patch_subprocess(monkeypatch, returncode=1, stderr="undefined reference to ...\n")
    result = check_compiles_in_case(case_dir, _native_selection("/opt/cs/bin/code_saturne"))

    assert result.success is False
    assert result.exit_code == 1
    assert "undefined reference" in result.log
    assert "FAILED" in result.summary()


def test_check_compiles_in_case_timeout(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    case_dir = _make_case_with_src(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise subprocess.TimeoutExpired(cmd=["x"], timeout=1.0, output="partial-out", stderr="partial-err")

    monkeypatch.setattr("csauto.qoi.compile_check.subprocess.run", _boom)
    result = check_compiles_in_case(case_dir, _native_selection("/opt/cs/bin/code_saturne"), timeout_s=1.0)

    assert result.success is False
    assert result.timed_out is True
    assert result.exit_code == -1
    assert "TIMEOUT" in result.summary()


def test_check_compiles_in_case_missing_src_raises(tmp_path: Path) -> None:
    case_dir = tmp_path / "case0001"
    case_dir.mkdir()  # no SRC subdir
    with pytest.raises(QoIError, match="SRC/ not found"):
        check_compiles_in_case(case_dir, _native_selection("/opt/cs/bin/code_saturne"))


def test_check_compiles_in_case_subprocess_invocation_error_raises(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case_dir = _make_case_with_src(tmp_path)

    def _boom(*_args: object, **_kwargs: object) -> None:
        raise OSError("no such binary")

    monkeypatch.setattr("csauto.qoi.compile_check.subprocess.run", _boom)
    with pytest.raises(QoIError, match="failed to invoke"):
        check_compiles_in_case(case_dir, _native_selection("/opt/cs/bin/code_saturne"))


# --- check_compiles_first_case ------------------------------------------------


def test_check_compiles_first_case_picks_first(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runs = tmp_path / "RUNS"
    runs.mkdir()
    _make_case_with_src(runs, "case0002")
    _make_case_with_src(runs, "case0001")
    (runs / "MESH").mkdir()  # non-case dir, must be ignored

    captured: dict[str, Path] = {}

    def _fake_run(cmd: list[str], **_kwargs: object) -> object:
        # Locate which case the command targets so we can verify alphabetical pick.
        for arg in cmd:
            if "case0001" in str(arg):
                captured["chosen"] = Path("case0001")
                break
            if "case0002" in str(arg):
                captured["chosen"] = Path("case0002")
                break

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    monkeypatch.setattr("csauto.qoi.compile_check.subprocess.run", _fake_run)
    result = check_compiles_first_case(runs, _native_selection("/opt/cs/bin/code_saturne"))

    assert isinstance(result, CompileResult)
    assert result.case_dir.name == "case0001"
    assert captured["chosen"].name == "case0001"


def test_check_compiles_first_case_no_cases_raises(tmp_path: Path) -> None:
    runs = tmp_path / "RUNS"
    runs.mkdir()
    with pytest.raises(QoIError, match="no case\\* directories found"):
        check_compiles_first_case(runs, _native_selection("/opt/cs/bin/code_saturne"))
