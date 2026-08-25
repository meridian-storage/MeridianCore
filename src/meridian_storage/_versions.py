# SPDX-License-Identifier: Apache-2.0
"""Small deterministic semantic-version/range implementation for SPI contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import total_ordering
from typing import Self

_VERSION_RE = re.compile(
    r"^(?P<major>0|[1-9][0-9]*)\."
    r"(?P<minor>0|[1-9][0-9]*)\."
    r"(?P<patch>0|[1-9][0-9]*)"
    r"(?:-(?P<pre>[0-9A-Za-z.-]+))?"
    r"(?:\+(?P<build>[0-9A-Za-z.-]+))?$"
)


@total_ordering
@dataclass(frozen=True, slots=True)
class ContractVersion:
    major: int
    minor: int
    patch: int
    prerelease: str = ""

    def __lt__(self, other: object) -> bool:
        if not isinstance(other, ContractVersion):
            return NotImplemented
        left_release = (self.major, self.minor, self.patch)
        right_release = (other.major, other.minor, other.patch)
        if left_release != right_release:
            return left_release < right_release
        if self.prerelease == other.prerelease:
            return False
        if not self.prerelease:
            return False
        if not other.prerelease:
            return True
        return _prerelease_less(self.prerelease, other.prerelease)

    @classmethod
    def parse(cls, value: str) -> Self:
        match = _VERSION_RE.fullmatch(value)
        if match is None:
            raise ValueError(f"invalid semantic version: {value!r}")
        return cls(
            major=int(match.group("major")),
            minor=int(match.group("minor")),
            patch=int(match.group("patch")),
            prerelease=match.group("pre") or "",
        )


def _prerelease_less(left: str, right: str) -> bool:
    left_parts = left.split(".")
    right_parts = right.split(".")
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part == right_part:
            continue
        left_numeric = left_part.isdigit()
        right_numeric = right_part.isdigit()
        if left_numeric and right_numeric:
            return int(left_part) < int(right_part)
        if left_numeric != right_numeric:
            return left_numeric
        return left_part < right_part
    return len(left_parts) < len(right_parts)


def contract_matches(version: str, expression: str) -> bool:
    """Evaluate the bounded range syntax accepted by Meridian V1.

    Supported forms are exact semantic versions, ``N.x``, comma-separated
    comparators, caret ranges and tilde ranges. Logical OR and arbitrary
    package-manager syntax are deliberately rejected.
    """

    candidate = ContractVersion.parse(version)
    spec = expression.strip()
    wildcard = re.fullmatch(r"(0|[1-9][0-9]*)\.(?:x|\*)", spec)
    if wildcard is not None:
        return candidate.major == int(wildcard.group(1))
    wildcard_minor = re.fullmatch(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(?:x|\*)", spec)
    if wildcard_minor is not None:
        return (candidate.major, candidate.minor) == (
            int(wildcard_minor.group(1)),
            int(wildcard_minor.group(2)),
        )
    if spec.startswith("^"):
        floor = ContractVersion.parse(_normalize_short(spec[1:]))
        if floor.major > 0:
            ceiling = ContractVersion(floor.major + 1, 0, 0)
        elif floor.minor > 0:
            ceiling = ContractVersion(0, floor.minor + 1, 0)
        else:
            ceiling = ContractVersion(0, 0, floor.patch + 1)
        return floor <= candidate < ceiling
    if spec.startswith("~"):
        floor = ContractVersion.parse(_normalize_short(spec[1:]))
        ceiling = ContractVersion(floor.major, floor.minor + 1, 0)
        return floor <= candidate < ceiling
    if any(spec.startswith(operator) for operator in (">", "<", "=")) or "," in spec:
        components = [component.strip() for component in spec.split(",")]
        if not components or any(not component for component in components):
            raise ValueError(f"invalid contract range: {expression!r}")
        return all(_compare(candidate, component) for component in components)
    return candidate == ContractVersion.parse(_normalize_short(spec))


def _normalize_short(value: str) -> str:
    parts = value.split(".")
    if len(parts) == 1:
        return f"{value}.0.0"
    if len(parts) == 2:
        return f"{value}.0"
    return value


def _compare(candidate: ContractVersion, component: str) -> bool:
    match = re.fullmatch(r"(>=|<=|>|<|==|=)?\s*(.+)", component)
    if match is None:
        raise ValueError(f"invalid contract comparator: {component!r}")
    operator = match.group(1) or "=="
    expected = ContractVersion.parse(_normalize_short(match.group(2)))
    if operator in {"=", "=="}:
        return candidate == expected
    if operator == ">=":
        return candidate >= expected
    if operator == "<=":
        return candidate <= expected
    if operator == ">":
        return candidate > expected
    return candidate < expected
