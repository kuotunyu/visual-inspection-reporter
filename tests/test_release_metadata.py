from __future__ import annotations

import pathlib
import re
import tomllib

import inspector


ROOT = pathlib.Path(__file__).resolve().parents[1]


def test_public_version_metadata_is_consistent() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project_version = pyproject["project"]["version"]

    assert project_version == "0.1.1"
    assert inspector.__version__ == project_version
    assert pyproject["project"]["urls"]["Release"].endswith("/releases/tag/v0.1.1")

    lock_text = (ROOT / "uv.lock").read_text(encoding="utf-8")
    root_package = re.search(
        r'\[\[package\]\]\nname = "visual-inspection-reporter"\nversion = "([^"]+)"',
        lock_text,
    )
    assert root_package is not None
    assert root_package.group(1) == project_version
