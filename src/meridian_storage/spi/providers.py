# SPDX-License-Identifier: Apache-2.0
"""Catalog, Schema, Plugin, and telemetry provider protocols."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from meridian_storage.registry.resources import ResourceBundle
from meridian_storage.runtime.operations import CatalogManifest, Expression, Operation

if TYPE_CHECKING:
    from meridian_storage.runtime.runtime import Meridian


@runtime_checkable
class CatalogSurface(Protocol):
    """Marker protocol for a mapping-first Catalog Expression surface."""

    @property
    def catalog_name(self) -> str: ...


@runtime_checkable
class CatalogProvider(Protocol):
    """Installed package that owns one registered Catalog's public syntax."""

    @property
    def catalog_name(self) -> str: ...

    def manifest(self) -> CatalogManifest: ...

    def create_surface(self) -> CatalogSurface: ...

    def normalize(self, expression: Expression) -> Operation: ...


@runtime_checkable
class SchemaProvider(Protocol):
    @property
    def provider_id(self) -> str: ...

    @property
    def provider_contract_version(self) -> str: ...

    def load(self) -> ResourceBundle: ...


@runtime_checkable
class LiveSchemaProvider(Protocol):
    """Optional extension used when deployment enables live metadata."""

    def load_live(self) -> ResourceBundle: ...


@dataclass(frozen=True, slots=True)
class PluginManifest:
    plugin_id: str
    plugin_version: str
    plugin_contract_version: str
    core_contract: str
    extensions: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name in (
            "plugin_id",
            "plugin_version",
            "plugin_contract_version",
            "core_contract",
        ):
            value = getattr(self, name)
            if (
                not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 256
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
            ):
                raise ValueError(f"{name} must be bounded and non-empty")
        extensions: dict[str, str] = {}
        for key, value in self.extensions.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key.encode("utf-8")) > 128
                or not isinstance(value, str)
                or not value
                or len(value.encode("utf-8")) > 512
                or any(ord(character) < 32 or ord(character) == 127 for character in key + value)
            ):
                raise ValueError("Plugin extensions must contain bounded safe strings")
            extensions[key] = value
        object.__setattr__(self, "extensions", MappingProxyType(dict(sorted(extensions.items()))))


@runtime_checkable
class PluginFactory(Protocol):
    @property
    def plugin_id(self) -> str: ...

    def manifest(self) -> PluginManifest: ...

    def create(self, meridian: Meridian) -> object: ...


@runtime_checkable
class Observer(Protocol):
    def emit(self, name: str, attributes: Mapping[str, str]) -> None: ...


__all__ = [
    "CatalogProvider",
    "CatalogSurface",
    "LiveSchemaProvider",
    "Observer",
    "PluginFactory",
    "PluginManifest",
    "SchemaProvider",
]
