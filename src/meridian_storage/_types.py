# SPDX-License-Identifier: Apache-2.0
"""Shared public typing primitives."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | Mapping[str, JsonValue] | Sequence[JsonValue]
