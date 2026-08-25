#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Require two build directories to contain byte-identical artifacts."""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("first", type=Path)
    parser.add_argument("second", type=Path)
    arguments = parser.parse_args()
    first = {path.name: sha256(path) for path in arguments.first.iterdir() if path.is_file()}
    second = {path.name: sha256(path) for path in arguments.second.iterdir() if path.is_file()}
    assert first == second, {"first": first, "second": second}
    for name, checksum in sorted(first.items()):
        print(f"{checksum}  {name}")


if __name__ == "__main__":
    main()
