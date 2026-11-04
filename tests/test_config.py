from pathlib import Path

import pytest

import p9
from p9.config import find_root, load_settings

SUBPACKAGES = ["ingest", "model", "generate", "checks", "llm", "api"]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackages_importables(name: str) -> None:
    __import__(f"p9.{name}")


def test_root_contient_pyproject() -> None:
    root = find_root(Path(p9.__file__).parent)
    assert (root / "pyproject.toml").is_file()


def test_settings_par_defaut_pointent_vers_l_export() -> None:
    s = load_settings(Path(p9.__file__).parent)
    assert s.data_dir == s.root / "data" / "usershaker"
    assert s.out_dir == s.root / "out"
    assert (s.data_dir / "meta.csv").is_file()


def test_surcharge_par_variable_d_environnement(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("P9_OUT_DIR", str(tmp_path))
    assert load_settings(Path(p9.__file__).parent).out_dir == tmp_path
