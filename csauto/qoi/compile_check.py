"""Pre-launch test-compile of a generated case.

Invokes ``code_saturne compile -t -s SRC`` inside a case directory to catch
broken C++ templates (typos, wrong field names, link errors) before the user
spends time and CPU launching the campaign. The compile is a dry test
(``-t``) — no object files are kept.

Supports docker, singularity and native runtimes via the existing runtime
selection in csauto.execution.
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..execution import (
    RUNTIME_DOCKER,
    RUNTIME_NATIVE,
    RUNTIME_SINGULARITY,
    RuntimeSelection,
)
from .errors import QoIError

DEFAULT_COMPILE_TIMEOUT_S = 300.0
_CONTAINER_ROOT = "/home/code_saturne"


@dataclass(frozen=True)
class CompileResult:
    """Outcome of a single test-compile invocation."""

    success: bool
    case_dir: Path
    runtime: str
    command: list[str]
    log: str
    exit_code: int
    timed_out: bool = False

    def summary(self) -> str:
        verdict = "OK" if self.success else ("TIMEOUT" if self.timed_out else "FAILED")
        return f"test-compile {verdict} ({self.runtime}) for {self.case_dir.name}"


def build_compile_command(case_dir: Path, selection: RuntimeSelection) -> list[str]:
    """Build the shell command that runs ``code_saturne compile -t -s SRC``.

    Bind-mounts the parent RUNS/ for docker and singularity so file paths
    resolve identically on host and inside the container.
    """
    if selection.runtime == RUNTIME_NATIVE:
        if not selection.saturne_bin:
            raise QoIError("native runtime requires saturne_bin to be configured.")
        return [selection.saturne_bin, "compile", "-t", "-s", str(case_dir / "SRC")]

    if selection.runtime == RUNTIME_DOCKER:
        if not shutil.which("docker"):
            raise QoIError("docker not found in PATH but runtime is docker.")
        runs_root = case_dir.parent.resolve()
        container_case = f"{_CONTAINER_ROOT}/{case_dir.name}"
        # Override entrypoint so the image's wrapper (which prepends 'code_saturne')
        # doesn't interfere with our shell composition.
        return [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "/bin/bash",
            "-v",
            f"{runs_root}:{_CONTAINER_ROOT}",
            "-w",
            container_case,
            selection.docker_image,
            "-c",
            "code_saturne compile -t -s SRC",
        ]

    if selection.runtime == RUNTIME_SINGULARITY:
        if not selection.singularity_bin or not selection.singularity_image:
            raise QoIError("Incomplete singularity configuration.")
        runs_root = case_dir.parent.resolve()
        container_case = f"{_CONTAINER_ROOT}/{case_dir.name}"
        return [
            selection.singularity_bin,
            "exec",
            "--bind",
            f"{runs_root}:{_CONTAINER_ROOT}",
            "--pwd",
            container_case,
            selection.singularity_image,
            "code_saturne",
            "compile",
            "-t",
            "-s",
            "SRC",
        ]

    raise QoIError(f"Unsupported runtime for test-compile: {selection.runtime}")


def check_compiles_in_case(
    case_dir: Path,
    selection: RuntimeSelection,
    *,
    timeout_s: float = DEFAULT_COMPILE_TIMEOUT_S,
) -> CompileResult:
    """Run ``code_saturne compile -t -s SRC`` once on the given case.

    Captures stdout+stderr and the exit code. Never raises on a compile
    failure — the caller inspects ``result.success``. Only raises for
    configuration errors (missing runtime, missing binary).
    """
    if not (case_dir / "SRC").is_dir():
        raise QoIError(f"test-compile: SRC/ not found in {case_dir}")
    cmd = build_compile_command(case_dir, selection)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        return CompileResult(
            success=False,
            case_dir=case_dir,
            runtime=selection.runtime,
            command=cmd,
            log=(exc.stdout or "") + (exc.stderr or ""),
            exit_code=-1,
            timed_out=True,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise QoIError(f"test-compile: failed to invoke {cmd[0]}: {exc}") from exc

    log = (proc.stdout or "") + (proc.stderr or "")
    return CompileResult(
        success=proc.returncode == 0,
        case_dir=case_dir,
        runtime=selection.runtime,
        command=cmd,
        log=log,
        exit_code=proc.returncode,
    )


def check_compiles_first_case(
    runs_dir: Path,
    selection: RuntimeSelection,
    *,
    timeout_s: float = DEFAULT_COMPILE_TIMEOUT_S,
) -> CompileResult:
    """Pick the alphabetically-first case under runs_dir and test-compile it."""
    cases = sorted(p for p in runs_dir.iterdir() if p.is_dir() and p.name.startswith("case"))
    if not cases:
        raise QoIError(f"test-compile: no case* directories found under {runs_dir}")
    return check_compiles_in_case(cases[0], selection, timeout_s=timeout_s)


__all__ = [
    "DEFAULT_COMPILE_TIMEOUT_S",
    "CompileResult",
    "build_compile_command",
    "check_compiles_first_case",
    "check_compiles_in_case",
]
