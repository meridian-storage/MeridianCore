# SPDX-License-Identifier: Apache-2.0
"""Deterministic local Python entry-point discovery."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from importlib import metadata
from types import MappingProxyType

from meridian_storage.errors import ConfigurationError, ErrorCode, SafeCause

ADAPTER_ENTRY_POINT_GROUP = "meridian_storage.adapters"
CATALOG_ENTRY_POINT_GROUP = "meridian_storage.catalogs"
SCHEMA_ENTRY_POINT_GROUP = "meridian_storage.schemas"
PLUGIN_ENTRY_POINT_GROUP = "meridian_storage.plugins"


@dataclass(frozen=True, slots=True)
class DiscoveryRecord:
    component_id: str
    entry_point_name: str
    entry_point_value: str
    distribution: str
    distribution_version: str
    injected: bool = False


@dataclass(frozen=True, slots=True)
class DiscoveryResult[T]:
    components: Mapping[str, T]
    records: tuple[DiscoveryRecord, ...]


def discover_components[T](
    group: str,
    injected: Iterable[T],
    *,
    component_id: Callable[[T], str],
) -> DiscoveryResult[T]:
    """Load trusted local entry points and reject every ambiguous identity."""

    try:
        entry_points = tuple(metadata.entry_points().select(group=group))
    except Exception as exc:
        raise ConfigurationError(
            ErrorCode.DISCOVERY_FAILED,
            f"entry-point metadata for {group!r} could not be read",
            cause=SafeCause.from_exception(exc),
        ) from exc
    names: set[str] = set()
    for point in entry_points:
        if point.name in names:
            raise ConfigurationError(
                ErrorCode.DISCOVERY_DUPLICATE,
                f"entry-point group {group!r} contains duplicate name {point.name!r}",
            )
        names.add(point.name)

    components: dict[str, T] = {}
    records: list[DiscoveryRecord] = []

    def add(component: T, record: DiscoveryRecord) -> None:
        identifier = component_id(component)
        if (
            not isinstance(identifier, str)
            or not identifier
            or len(identifier.encode("utf-8")) > 256
            or any(ord(character) < 32 or ord(character) == 127 for character in identifier)
        ):
            raise ConfigurationError(
                ErrorCode.DISCOVERY_FAILED,
                f"a component in {group!r} has no stable id",
            )
        if identifier in components:
            raise ConfigurationError(
                ErrorCode.DISCOVERY_DUPLICATE,
                f"entry-point group {group!r} contains duplicate component id {identifier!r}",
            )
        components[identifier] = component
        records.append(record)

    for index, component in enumerate(injected):
        identifier = component_id(component)
        add(
            component,
            DiscoveryRecord(
                component_id=identifier,
                entry_point_name=f"injected-{index}",
                entry_point_value=type(component).__qualname__,
                distribution="<injected>",
                distribution_version="0",
                injected=True,
            ),
        )

    for point in sorted(entry_points, key=lambda item: (item.name, item.value)):
        try:
            loaded = point.load()
            component = loaded() if isinstance(loaded, type) else loaded
            identifier = component_id(component)
        except Exception as exc:
            raise ConfigurationError(
                ErrorCode.DISCOVERY_FAILED,
                f"entry point {point.name!r} in {group!r} could not be loaded",
                cause=SafeCause.from_exception(exc),
            ) from exc
        distribution = point.dist
        add(
            component,
            DiscoveryRecord(
                component_id=identifier,
                entry_point_name=point.name,
                entry_point_value=point.value,
                distribution=distribution.name if distribution is not None else "<unknown>",
                distribution_version=(
                    distribution.version if distribution is not None else "<unknown>"
                ),
            ),
        )
    return DiscoveryResult(
        components=MappingProxyType(components),
        records=tuple(sorted(records, key=lambda item: (item.component_id, item.entry_point_name))),
    )


__all__ = [
    "ADAPTER_ENTRY_POINT_GROUP",
    "CATALOG_ENTRY_POINT_GROUP",
    "PLUGIN_ENTRY_POINT_GROUP",
    "SCHEMA_ENTRY_POINT_GROUP",
    "DiscoveryRecord",
    "DiscoveryResult",
    "discover_components",
]
