# SPDX-License-Identifier: Apache-2.0
"""Canonical JSON and immutable-value helpers used by public contracts."""

from __future__ import annotations

import hashlib
import json
import math
from collections.abc import Mapping, Sequence
from types import MappingProxyType
from typing import Any, cast

from ._types import JsonValue


def deep_freeze(value: JsonValue) -> JsonValue:
    """Return an immutable, JSON-compatible representation of *value*.

    Mapping keys must be strings and floating-point values must be finite so
    every accepted value has one deterministic JSON representation.
    """

    return cast(JsonValue, _freeze(value, seen=set()))


def _freeze(value: Any, *, seen: set[int]) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("non-finite numbers are not valid Meridian JSON values")
        return value
    if isinstance(value, Mapping):
        marker = id(value)
        if marker in seen:
            raise ValueError("cyclic mappings are not valid Meridian JSON values")
        seen.add(marker)
        frozen: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise TypeError("Meridian JSON object keys must be strings")
            frozen[key] = _freeze(item, seen=seen)
        seen.remove(marker)
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        marker = id(value)
        if marker in seen:
            raise ValueError("cyclic sequences are not valid Meridian JSON values")
        seen.add(marker)
        frozen_sequence = tuple(_freeze(item, seen=seen) for item in value)
        seen.remove(marker)
        return frozen_sequence
    raise TypeError(f"unsupported Meridian JSON value type: {type(value).__name__}")


def deep_thaw(value: JsonValue) -> JsonValue:
    """Return ordinary dictionaries/lists suitable for JSON serialization."""

    if isinstance(value, Mapping):
        return {key: deep_thaw(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [deep_thaw(item) for item in value]
    return value


def canonical_json_bytes(value: JsonValue) -> bytes:
    """Serialize JSON using Meridian's deterministic UTF-8 representation."""

    return json.dumps(
        deep_thaw(deep_freeze(value)),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def sha256_fingerprint(value: JsonValue) -> str:
    """Return a prefixed SHA-256 digest of canonical JSON content."""

    return f"sha256:{hashlib.sha256(canonical_json_bytes(value)).hexdigest()}"
