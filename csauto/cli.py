from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from . import __version__
from .config import Config, load_config
from .doe import generate_cases, load_doe
from .execution import (
    RUNTIME_DOCKER,
    RUNTIME_SINGULARITY,
    resolve_runtime,
)
from .logs import (
    collect_performance,
    tail_log,
)
from .maintenance import cleanup_runs, run_doctor
from .residuals import (
    collect_residuals,
    plot_residuals,
)
from .runner import refresh_status, run_cases
from .serve_commands import add_serve_subcommands, dispatch_serve_command
from .warn import error, flush_warnings


def serve_fastapi(*args: Any, **kwargs: Any) -> Any:
    from .fastapi_app import serve_fastapi as _serve_fastapi

    return _serve_fastapi(*args, **kwargs)


def resolve_runs_dir(base: Path) -> Path:
    """Resolve runs dir, trying CWD first then repository root."""
    candidates: list[Path] = []
    if base.is_absolute():
        candidates.append(base)
    else:
        candidates.append((Path.cwd() / base).resolve())
        repo_root = Path(__file__).resolve().parent.parent
        candidates.append((repo_root / base).resolve())
    seen: set[str] = set()
    uniq_candidates: list[Path] = []
    for cand in candidates:
        key = str(cand)
        if key not in seen:
            seen.add(key)
            uniq_candidates.append(cand)
    for cand in uniq_candidates:
        if cand.is_dir():
            return cand
    raise FileNotFoundError(f"Runs directory not found. Tried: {', '.join(str(c) for c in uniq_candidates)}")


def print_status_table(rows: Sequence[Mapping[str, Any]]) -> None:
    """Display a simple text table with status information."""
    headers = (
        "case_id",
        "status",
        "nprocs",
        "nt",
        "last_iter",
        "duration",
        "last_mod",
        "resu_size_mb",
    )
    col_widths = {h: len(h) for h in headers}
    for row in rows:
        for h in headers:
            col_widths[h] = max(col_widths[h], len(str(row.get(h, ""))))

    def fmt(row: Mapping[str, Any]) -> str:
        return " | ".join(str(row.get(h, "")).ljust(col_widths[h]) for h in headers)

    print(fmt({h: h for h in headers}))
    print("-+-".join("-" * col_widths[h] for h in headers))
    for row in rows:
        print(fmt(row))


def parse_arguments(
    argv: Sequence[str], config: Config | None = None
) -> tuple[argparse.ArgumentParser, argparse.Namespace]:
    """Parse CLI arguments, supporting prepare/run/status commands."""
    config = config or Config()
    parser = argparse.ArgumentParser(
        description="Prepare and run Code_Saturne cases from a DOE CSV and a template case directory.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"csauto {__version__}",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to csauto.toml (otherwise searched automatically).",
    )
    subparsers = parser.add_subparsers(dest="command")

    prepare_parser = subparsers.add_parser("prepare", help="Prepare cases from the DOE CSV and template directory.")
    prepare_parser.add_argument("doe_csv", type=Path, help="Path to the doe.csv file")
    prepare_parser.add_argument("template_case", type=Path, help="Template case directory")
    prepare_parser.add_argument("output_root", type=Path, help="Root directory where cases will be generated")
    prepare_parser.add_argument(
        "--test-compile",
        dest="test_compile",
        action="store_true",
        help="After preparing, run 'code_saturne compile -t -s SRC' on the first case to "
        "validate the auto-generated QoI user file. Requires the configured runtime "
        "(docker/singularity/native) to be available.",
    )
    run_parser = subparsers.add_parser("run", help="Launch code_saturne on all cases.")
    run_parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")
    run_parser.add_argument("--n", dest="nprocs", type=int, required=True, help="Number of MPI processes")
    run_parser.add_argument("--nt", dest="nt", type=int, required=True, help="Number of OpenMP threads")
    run_parser.add_argument(
        "--max-parallel",
        dest="max_parallel",
        type=int,
        default=config.max_parallel,
        help="Maximum number of simultaneous launches (default 1).",
    )
    run_parser.add_argument(
        "--case",
        dest="cases",
        action="append",
        default=None,
        help="Specific case name to launch (repeatable for multiple cases).",
    )
    run_parser.add_argument(
        "--runtime",
        dest="runtime",
        choices=["auto", "docker", "singularity", "native"],
        default=config.runtime,
        help="Execution backend (auto, docker, singularity, native).",
    )
    run_parser.add_argument(
        "--docker-image",
        dest="docker_image",
        default=config.docker_image,
        help="Docker image to use (default: simvia/code_saturne).",
    )
    run_parser.add_argument(
        "--saturne-bin",
        dest="saturne_bin",
        default=config.saturne_bin,
        help="Path to the code_saturne executable (native runtime).",
    )
    run_parser.add_argument(
        "--singularity-image",
        dest="singularity_image",
        default=config.singularity_image,
        help="Apptainer/Singularity image (.sif or URI).",
    )
    run_parser.add_argument(
        "--singularity-bin",
        dest="singularity_bin",
        default=config.singularity_bin,
        help="apptainer/singularity executable to use (optional).",
    )
    run_parser.add_argument(
        "--resume",
        dest="resume",
        action="store_true",
        help="Re-launch only cases with FAILED status.",
    )
    run_parser.add_argument(
        "--no-doctor",
        dest="no_doctor",
        action="store_true",
        help="Skip the pre-check before run.",
    )

    status_parser = subparsers.add_parser("status", help="Display the status of all cases.")
    status_parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")

    def _add_export_args(parser: argparse.ArgumentParser) -> None:
        parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")
        parser.add_argument(
            "--case",
            dest="cases",
            action="append",
            required=True,
            help="Specific case name to export (repeatable).",
        )
        parser.add_argument(
            "--out",
            dest="output",
            type=Path,
            default=None,
            help="Path to the output CSV file (stdout by default).",
        )

    residuals_parser = subparsers.add_parser("residuals", help="Export residuals vs iteration for selected cases.")
    _add_export_args(residuals_parser)
    residuals_parser.add_argument(
        "--plot",
        dest="plot",
        type=Path,
        default=None,
        help="SVG output path for residuals plot (optional).",
    )
    residuals_parser.add_argument(
        "--columns",
        dest="columns",
        nargs="+",
        default=None,
        help="Residual columns to plot (e.g. density velocity).",
    )

    perf_parser = subparsers.add_parser("perf", help="Export performance info from performance.log for selected cases.")
    _add_export_args(perf_parser)

    tail_parser = subparsers.add_parser("tail", help="Follow a case log file (like tail -f).")
    tail_parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")
    tail_parser.add_argument("--case", required=True, help="Case name (caseXXXX)")
    tail_parser.add_argument(
        "--file",
        dest="file_name",
        default="listing",
        help="File to follow (listing, run_solver.log, run_status.running, csauto.stdout, ...).",
    )
    tail_parser.add_argument(
        "-n",
        dest="lines",
        type=int,
        default=20,
        help="Number of lines to display initially (default 20).",
    )
    tail_parser.add_argument(
        "--no-follow",
        dest="no_follow",
        action="store_true",
        help="Print the end of the file then exit (no real-time follow).",
    )

    add_serve_subcommands(subparsers, config)

    doctor_parser = subparsers.add_parser("doctor", help="Check configuration and cases.")
    doctor_parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")

    postprocess_parser = subparsers.add_parser(
        "postprocess",
        help="Run all configured [[qoi]] extractors and write a campaign-level table.",
    )
    postprocess_parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")
    postprocess_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output file path. Format inferred from the extension (.csv, .tsv, .json).",
    )
    postprocess_parser.add_argument(
        "--format",
        choices=("csv", "tsv", "json"),
        default=None,
        help="Override the output format detection.",
    )
    postprocess_parser.add_argument(
        "--cases",
        default=None,
        help="Comma-separated case IDs to include (e.g. case0001,case0002). Default: all.",
    )

    cleanup_parser = subparsers.add_parser("cleanup", help="Clean up runs (RESU/logs/cache).")
    cleanup_parser.add_argument("runs_dir", type=Path, help="Directory containing generated cases")
    cleanup_parser.add_argument(
        "--prune-resu",
        action="store_true",
        help="Delete old RESU directories (keep the most recent ones).",
    )
    cleanup_parser.add_argument(
        "--keep-last",
        type=int,
        default=1,
        help="Number of RESU directories to keep per case (default 1, 0 to delete all).",
    )
    cleanup_parser.add_argument(
        "--max-log-mb",
        type=float,
        default=0.0,
        help="Truncate log files exceeding this size (MB).",
    )
    cleanup_parser.add_argument(
        "--clear-cid",
        action="store_true",
        help="Delete .csauto.cid files.",
    )
    cleanup_parser.add_argument(
        "--clear-pyc",
        action="store_true",
        help="Delete __pycache__ directories under cases.",
    )
    cleanup_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate without deleting (show only).",
    )

    completion_parser = subparsers.add_parser("completion", help="Output shell completion script.")
    completion_parser.add_argument(
        "shell",
        choices=["bash", "zsh"],
        help="Shell type (bash or zsh).",
    )

    subparsers.add_parser("enable-telemetry", help="Enable anonymous usage telemetry.")
    subparsers.add_parser("disable-telemetry", help="Disable anonymous usage telemetry.")
    ping_parser = subparsers.add_parser("_telemetry-ping")
    ping_parser.add_argument("event_type", type=int)
    ping_parser.add_argument("id_docker")

    # Backward compatibility: allow legacy call without subcommand.
    commands = {
        "prepare",
        "run",
        "status",
        "residuals",
        "perf",
        "tail",
        "serve",
        "doctor",
        "cleanup",
        "completion",
        "enable-telemetry",
        "disable-telemetry",
        "_telemetry-ping",
    }
    argv_list = list(argv)
    if argv_list and argv_list[0] not in commands and len(argv_list) == 3:
        argv_list = ["prepare", *argv_list]

    return parser, parser.parse_args(argv_list)


def _preparse_config(argv: Sequence[str]) -> Path | None:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", type=Path, default=None)
    known, _ = parser.parse_known_args(list(argv))
    return known.config


def _print_doctor(items: Sequence[object]) -> bool:
    failed = False
    for item in items:
        level = getattr(item, "level", "ok")
        msg = getattr(item, "message", "")
        if str(level).lower() == "fail":
            failed = True
        print(f"[{str(level).upper()}] {msg}")
    return failed


def main(argv: Sequence[str] | None = None) -> int:
    argv_list = list(argv or sys.argv[1:])
    config_path = _preparse_config(argv_list)
    config = load_config(config_path)
    parser, args = parse_arguments(argv_list, config)

    try:
        if args.command is None:
            parser.print_help()
            return 0
        elif args.command == "prepare":
            headers, rows = load_doe(args.doe_csv)
            generate_cases(headers, rows, args.template_case, args.output_root)
            if config.qoi_recipes:
                from .qoi.injection import inject_user_files_into_runs
                from .qoi.setup_patcher import patch_setup_for_recipes
                from .warn import warn

                inj_reports = inject_user_files_into_runs(args.output_root, config.qoi_recipes, mode=config.qoi_mode)
                n_injected = sum(1 for r in inj_reports if r.files_written)
                if n_injected:
                    if config.qoi_mode == "managed":
                        print(f"Injected QoI user file in {n_injected} case(s)")
                    else:
                        print(
                            f"Wrote csauto QoI files (injected mode) in {n_injected} case(s): "
                            f"cs_user_csauto_qoi.cpp + .h"
                        )
                        needs_action = [r for r in inj_reports if r.needs_user_action]
                        if needs_action:
                            sample = needs_action[0]
                            target = (
                                sample.user_file_with_extra_ops.name
                                if sample.user_file_with_extra_ops is not None
                                else "cs_user_extra_operations.cpp (to create)"
                            )
                            warn(
                                f"Injected mode: add the dispatch call inside "
                                f"cs_user_extra_operations (e.g. {target} in case "
                                f"{sample.case_dir.name}). Insert this in your function:\n\n"
                                f'    #include "cs_user_csauto_qoi.h"\n'
                                f"    ...\n"
                                f"    csauto_qoi_dispatch(domain);   // csauto: required for QoI extraction\n\n"
                                f"{len(needs_action)} case(s) need this fix."
                            )
                reports = patch_setup_for_recipes(args.output_root, config.qoi_recipes)
                n_changed = sum(1 for r in reports if r.changed)
                if n_changed:
                    print(f"Enabled QoI boundary fields in setup.xml for {n_changed} case(s)")
                missing_by_case = {r.case_dir.name: r.missing for r in reports if r.missing}
                if missing_by_case:
                    sample_setup = next(iter(missing_by_case.items()))
                    warn(
                        f"setup.xml is missing <property> tags for: {', '.join(sample_setup[1])} "
                        f"(e.g. case {sample_setup[0]}). QoI extraction will fail at runtime. "
                        f"Activate these fields in your template setup before re-running prepare."
                    )
            if getattr(args, "test_compile", False):
                if not config.qoi_recipes:
                    print("--test-compile: no [[qoi]] recipes configured, skipping.")
                else:
                    from .qoi.compile_check import check_compiles_first_case

                    selection = resolve_runtime(
                        runtime=config.runtime,
                        docker_image=config.docker_image,
                        saturne_bin=config.saturne_bin,
                        singularity_image=config.singularity_image,
                        singularity_bin=config.singularity_bin,
                    )
                    print(
                        f"Test-compiling first case with runtime={selection.runtime}..."
                        + (f" image={config.docker_image}" if selection.runtime == "docker" else "")
                    )
                    result = check_compiles_first_case(args.output_root, selection)
                    if result.success:
                        print(f"✓ {result.summary()}")
                    else:
                        print(f"✗ {result.summary()}")
                        print("--- compile log -----------------------------")
                        print(result.log)
                        print("---------------------------------------------")
                        error(
                            f"Test-compile failed (exit code {result.exit_code}). "
                            f"Fix the issue in the auto-generated user file or your template, "
                            f"then re-run prepare. The cases on disk are left untouched."
                        )
                        return 1
        elif args.command == "run":
            runtime_selection = resolve_runtime(
                runtime=args.runtime,
                docker_image=args.docker_image,
                saturne_bin=args.saturne_bin,
                singularity_image=args.singularity_image,
                singularity_bin=args.singularity_bin,
            )
            if not args.no_doctor:
                items = run_doctor(
                    args.runs_dir,
                    require_docker=runtime_selection.runtime == RUNTIME_DOCKER,
                    require_singularity=runtime_selection.runtime == RUNTIME_SINGULARITY,
                    check_display=True,
                    require_write=True,
                    check_setup=True,
                    runtime=runtime_selection.runtime,
                    saturne_bin=runtime_selection.saturne_bin,
                    singularity_image=runtime_selection.singularity_image,
                    singularity_bin=runtime_selection.singularity_bin,
                    qoi_recipes=config.qoi_recipes,
                )
                if _print_doctor(items):
                    raise ValueError("Pre-check failed.")
            run_cases(
                runs_dir=args.runs_dir,
                nprocs=args.nprocs,
                nt=args.nt,
                max_parallel=args.max_parallel,
                case_filter=args.cases,
                docker_image=runtime_selection.docker_image,
                runtime=runtime_selection.runtime,
                saturne_bin=runtime_selection.saturne_bin,
                singularity_image=runtime_selection.singularity_image,
                singularity_bin=runtime_selection.singularity_bin,
                resume_only_failed=args.resume,
                use_slurm=config.use_slurm,
                mpi_exec_options=config.mpi_exec_options,
                source="cli",
            )
        elif args.command == "status":
            rows = refresh_status(args.runs_dir)
            print_status_table(rows)
        elif args.command == "residuals":
            collect_residuals(args.runs_dir, args.cases, args.output)
            if args.plot:
                plot_residuals(args.runs_dir, args.cases, args.columns, args.plot)
        elif args.command == "perf":
            collect_performance(args.runs_dir, args.cases, args.output)
        elif args.command == "tail":
            tail_log(
                args.runs_dir,
                args.case,
                args.file_name,
                lines=args.lines,
                follow=not args.no_follow,
            )
        elif dispatch_serve_command(
            args,
            config,
            run_doctor=run_doctor,
            print_doctor=_print_doctor,
            serve_fastapi=serve_fastapi,
        ):
            pass
        elif args.command == "doctor":
            items = run_doctor(
                args.runs_dir,
                check_display=True,
                require_write=True,
                check_setup=True,
                runtime=config.runtime,
                saturne_bin=config.saturne_bin,
                singularity_image=config.singularity_image,
                singularity_bin=config.singularity_bin,
                qoi_recipes=config.qoi_recipes,
            )
            if _print_doctor(items):
                return 1
        elif args.command == "postprocess":
            from .qoi.postprocess import extract_runs_qois, write_table

            if not config.qoi_recipes:
                print("No [[qoi]] recipes configured in csauto.toml — nothing to extract.")
                return 0
            cases_filter = [c.strip() for c in args.cases.split(",") if c.strip()] if args.cases else None
            results = extract_runs_qois(args.runs_dir, config.qoi_recipes, cases=cases_filter)
            if not results:
                print(f"No cases matched under {args.runs_dir}.")
                return 0
            write_table(results, args.out, format=args.format)
            n_total = len(results)
            n_errors = sum(1 for r in results if r.errors)
            print(f"Wrote {n_total} row(s) to {args.out}")
            if n_errors:
                print(f"  ({n_errors} case(s) had at least one extractor error — see _errors column)")
        elif args.command == "cleanup":
            if not (args.prune_resu or args.max_log_mb > 0 or args.clear_cid or args.clear_pyc):
                print("No action specified. Use --prune-resu/--max-log-mb/--clear-cid/--clear-pyc.")
                return 0
            report = cleanup_runs(
                args.runs_dir,
                prune_resu=args.prune_resu,
                keep_last=args.keep_last,
                max_log_mb=args.max_log_mb,
                clear_cid=args.clear_cid,
                clear_pyc=args.clear_pyc,
                dry_run=args.dry_run,
            )
            prefix = "DRY-RUN " if args.dry_run else ""
            print(f"{prefix}RESU removed: {report.resu_removed}")
            print(f"{prefix}Logs truncated: {report.logs_truncated}")
            if report.bytes_freed:
                print(f"{prefix}Bytes freed: {report.bytes_freed}")
            if args.clear_cid:
                print(f"{prefix}.csauto.cid removed: {report.cid_removed}")
            if args.clear_pyc:
                print(f"{prefix}__pycache__ removed: {report.pycache_removed}")
        elif args.command == "completion":
            from .completion import generate_bash, generate_zsh

            if args.shell == "bash":
                print(generate_bash(parser))
            else:
                print(generate_zsh(parser))
        elif args.command == "enable-telemetry":
            from .telemetry import set_enabled

            set_enabled(True)
            print("Telemetry enabled.")
        elif args.command == "disable-telemetry":
            from .telemetry import set_enabled

            set_enabled(False)
            print("Telemetry disabled.")
        elif args.command == "_telemetry-ping":
            from .telemetry import send_event

            send_event(args.event_type, block=True, id_docker=args.id_docker)
        else:
            raise ValueError("Unknown command.")
    except Exception as exc:
        error(str(exc))
        return 1
    finally:
        flush_warnings()

    return 0


if __name__ == "__main__":
    sys.exit(main())
