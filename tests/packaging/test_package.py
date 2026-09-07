# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.packaging
def test_repository_contains_one_distribution_and_one_python_package() -> None:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert project["project"]["name"] == "meridian-storage-core"
    assert project["project"]["version"] == "1.1.0"
    assert project["project"]["license"] == "Apache-2.0"
    assert project["project"]["dependencies"] == []
    assert project["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [
        "src/meridian_storage"
    ]
    packages = [path for path in (ROOT / "src").iterdir() if path.is_dir()]
    assert [path.name for path in packages] == ["meridian_storage"]


@pytest.mark.packaging
def test_license_notice_and_spdx_material_are_complete() -> None:
    license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert "Apache License" in license_text
    assert "Version 2.0" in license_text
    assert "Meridian" in notice
    for path in sorted((ROOT / "src").rglob("*.py")):
        assert path.read_text(encoding="utf-8").splitlines()[0] == (
            "# SPDX-License-Identifier: Apache-2.0"
        ), path


@pytest.mark.packaging
def test_compatibility_ledger_and_package_data_are_versioned() -> None:
    compatibility = json.loads((ROOT / "compatibility.json").read_text(encoding="utf-8"))
    assert compatibility["formatVersion"] == "meridian-compatibility.v1"
    assert compatibility["coreVersion"] == "1.1.0"
    assert compatibility["runtimeConfigFormat"] == "meridian-config.v1"
    force_include = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"][
        "hatch"
    ]["build"]["targets"]["wheel"]["force-include"]
    assert set(force_include) == {
        "contracts/adapter-capability/fixtures/release-provenance.v1.json",
        "compatibility.json",
        "contracts/adapter-capability/meridian-adapter-capabilities.v1.schema.json",
        "contracts/public-api/meridian-core.v1.json",
        "contracts/public-api/meridian-expression.v1.schema.json",
        "contracts/public-api/meridian-operation.v1.schema.json",
        "contracts/runtime-config/meridian-config.v1.schema.json",
    }


@pytest.mark.packaging
def test_ci_has_multi_python_quality_reproducibility_and_gated_publication() -> None:
    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    release = (ROOT / ".github/workflows/release.yml").read_text(encoding="utf-8")
    assert '["3.12", "3.13", "3.14"]' in ci
    assert "compare_artifacts.py" in ci
    assert "generate_sbom.py" in ci
    assert "include-hidden-files: true" in ci
    assert "attest-build-provenance" in release
    assert "PYPI_TRUSTED_PUBLISHING_ENABLED == 'true'" in release
    assert "gh-action-pypi-publish" in release
