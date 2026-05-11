from __future__ import annotations

from pathlib import Path

import pytest

from csauto.config import load_config


def test_load_config_reads_runtime_fields(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text(
        """
runtime = "auto"
docker_image = "simvia/code_saturne:latest"
saturne_bin = "/opt/code_saturne/bin/code_saturne"
singularity_image = "/images/cs.sif"
singularity_bin = "/usr/bin/apptainer"
use_slurm = true
mpi_exec_options = "--mca btl vader,self,tcp --bind-to core"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(cfg)
    assert config.runtime == "auto"
    assert config.docker_image == "simvia/code_saturne:latest"
    assert config.saturne_bin == "/opt/code_saturne/bin/code_saturne"
    assert config.singularity_image == "/images/cs.sif"
    assert config.singularity_bin == "/usr/bin/apptainer"
    assert config.use_slurm is True
    assert config.mpi_exec_options == "--mca btl vader,self,tcp --bind-to core"


def test_load_config_invalid_toml_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text('runtime = "native"\n[api\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid TOML"):
        load_config(cfg)


def test_load_config_invalid_runtime_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text('runtime = "podman"\n', encoding="utf-8")

    with pytest.raises(ValueError, match="Invalid runtime"):
        load_config(cfg)


def test_load_config_invalid_max_parallel_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text("max_parallel = 0\n", encoding="utf-8")

    with pytest.raises(ValueError, match="max_parallel"):
        load_config(cfg)


def test_load_config_missing_explicit_path_raises(tmp_path: Path) -> None:
    missing = tmp_path / "missing.toml"

    with pytest.raises(FileNotFoundError, match="Config file not found"):
        load_config(missing)


# --- [[qoi]] section --------------------------------------------------------


def test_load_config_qoi_section_parses_recipes(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text(
        """
[[qoi]]
name = "Cd"
type = "force_coefficient"
boundary = "wing"
ref_area = 1.5

[[qoi]]
name = "dP"
type = "pressure_drop"
probe_in = "inlet"
probe_out = "outlet"
""".strip(),
        encoding="utf-8",
    )

    config = load_config(cfg)
    assert len(config.qoi_recipes) == 2
    cd, dp = config.qoi_recipes
    assert cd.name == "Cd"
    assert cd.type == "force_coefficient"
    assert cd.params == {"boundary": "wing", "ref_area": 1.5}
    assert dp.name == "dP"
    assert dp.type == "pressure_drop"
    assert dp.params == {"probe_in": "inlet", "probe_out": "outlet"}


def test_load_config_qoi_missing_section_yields_empty_list(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text('runtime = "native"\n', encoding="utf-8")

    config = load_config(cfg)
    assert config.qoi_recipes == []


def test_load_config_qoi_invalid_shape_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text('qoi = "not an array"\n', encoding="utf-8")

    with pytest.raises(ValueError, match=r"\[\[qoi\]\]"):
        load_config(cfg)


def test_load_config_qoi_missing_name_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text(
        """
[[qoi]]
type = "force_coefficient"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="missing or empty 'name'"):
        load_config(cfg)


def test_load_config_qoi_duplicate_name_raises(tmp_path: Path) -> None:
    cfg = tmp_path / "csauto.toml"
    cfg.write_text(
        """
[[qoi]]
name = "Cd"
type = "force_coefficient"

[[qoi]]
name = "Cd"
type = "pressure_drop"
""".strip(),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Duplicate QoI name 'Cd'"):
        load_config(cfg)
