#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Verify Meridian wheel/sdist contents and emit deterministic JSON evidence."""

from __future__ import annotations

import argparse
import hashlib
import json
import tarfile
import zipfile
from email.parser import Parser
from pathlib import Path, PurePosixPath

PACKAGE = "meridian_storage"
VERSION = "1.0.0"


def digest(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def reject_generated(names: list[str]) -> None:
    assert not any("__pycache__" in name for name in names)
    assert not any(name.endswith((".pyc", ".pyo")) for name in names)
    assert not any("/.coverage" in name for name in names)


def verify_wheel(path: Path) -> dict[str, object]:
    with zipfile.ZipFile(path) as archive:
        names = sorted(archive.namelist())
        reject_generated(names)
        required = {
            f"{PACKAGE}/__init__.py",
            f"{PACKAGE}/py.typed",
            f"{PACKAGE}/compatibility.json",
            f"{PACKAGE}/contracts/meridian-core.v1.json",
            f"{PACKAGE}/contracts/meridian-expression.v1.schema.json",
            f"{PACKAGE}/contracts/meridian-operation.v1.schema.json",
            f"{PACKAGE}/runtime/contracts/meridian-config.v1.schema.json",
            f"{PACKAGE}/spi/contracts/meridian-adapter-capabilities.v1.schema.json",
            f"{PACKAGE}/testing/adapter_conformance/__init__.py",
        }
        assert required <= set(names), sorted(required - set(names))
        assert not any(name.startswith("tests/") for name in names)
        python_roots = {PurePosixPath(name).parts[0] for name in names if name.endswith(".py")}
        assert python_roots == {PACKAGE}, python_roots

        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(metadata_name).decode("utf-8"))
        assert metadata["Name"] == "meridian-storage-core"
        assert metadata["Version"] == VERSION
        assert metadata["Requires-Python"] == ">=3.12"
        assert metadata["License-Expression"] == "Apache-2.0"
        license_files = [name for name in names if ".dist-info/licenses/" in name]
        assert any(name.endswith("/LICENSE") for name in license_files)
        assert any(name.endswith("/NOTICE") for name in license_files)
    return {"kind": "wheel", "entries": len(names)}


def verify_sdist(path: Path) -> dict[str, object]:
    with tarfile.open(path, "r:gz") as archive:
        names = sorted(member.name for member in archive.getmembers())
        reject_generated(names)
        roots = {PurePosixPath(name).parts[0] for name in names}
        assert len(roots) == 1
        root = next(iter(roots))
        required = {
            f"{root}/LICENSE",
            f"{root}/NOTICE",
            f"{root}/README.md",
            f"{root}/pyproject.toml",
            f"{root}/compatibility.json",
            f"{root}/contracts/public-api/meridian-core.v1.json",
            f"{root}/src/{PACKAGE}/__init__.py",
            f"{root}/tests/contract/test_adapter_conformance.py",
            f"{root}/scripts/verify_artifacts.py",
        }
        assert required <= set(names), sorted(required - set(names))
    return {"kind": "sdist", "entries": len(names)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+", type=Path)
    arguments = parser.parse_args()
    evidence: list[dict[str, object]] = []
    for path in sorted(arguments.artifacts):
        assert path.is_file(), path
        if path.suffix == ".whl":
            detail = verify_wheel(path)
        elif path.name.endswith(".tar.gz"):
            detail = verify_sdist(path)
        else:
            raise AssertionError(f"unsupported artifact: {path}")
        evidence.append(
            {
                "file": path.name,
                "sha256": digest(path),
                "size": path.stat().st_size,
                **detail,
            }
        )
    kinds = {item["kind"] for item in evidence}
    assert kinds == {"wheel", "sdist"}, kinds
    print(json.dumps({"artifacts": evidence}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
