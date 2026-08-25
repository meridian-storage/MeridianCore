# SPDX-License-Identifier: Apache-2.0
"""Read-only Namespace, Schema, and Resource handles."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from meridian_storage._types import JsonValue

from .registry import RegistrySnapshot
from .resources import ResourceRef, SchemaRef


class _SnapshotOwner(Protocol):
    def _snapshot_for_handle(self) -> RegistrySnapshot: ...


class NamespaceHandle:
    __slots__ = ("_catalog", "_name", "_runtime")

    def __init__(self, runtime: _SnapshotOwner, catalog: str, name: str) -> None:
        self._runtime = runtime
        self._catalog = catalog
        self._name = name
        runtime._snapshot_for_handle().namespace(catalog, name)

    @property
    def catalog(self) -> str:
        return self._catalog

    @property
    def name(self) -> str:
        return self._name

    @property
    def labels(self) -> Mapping[str, str]:
        return self._runtime._snapshot_for_handle().namespace(self._catalog, self._name).labels

    def schema(self, name: str, version: str | None = None) -> SchemaHandle:
        return SchemaHandle(self._runtime, self._catalog, self._name, name, version)

    def resource(self, name: str) -> ResourceHandle:
        return ResourceHandle(self._runtime, ResourceRef(self._catalog, self._name, name))

    def schemas(self) -> tuple[SchemaHandle, ...]:
        snapshot = self._runtime._snapshot_for_handle()
        latest: dict[str, SchemaRef] = {}
        for ref in snapshot.schemas:
            if (ref.catalog, ref.namespace) != (self._catalog, self._name):
                continue
            current = latest.get(ref.name)
            if current is None or snapshot.schema(ref.catalog, ref.namespace, ref.name).ref == ref:
                latest[ref.name] = snapshot.schema(ref.catalog, ref.namespace, ref.name).ref
        return tuple(
            SchemaHandle(self._runtime, ref.catalog, ref.namespace, ref.name, ref.version)
            for ref in sorted(latest.values())
        )

    def resources(self) -> tuple[ResourceHandle, ...]:
        snapshot = self._runtime._snapshot_for_handle()
        return tuple(
            ResourceHandle(self._runtime, ref)
            for ref in sorted(snapshot.resources)
            if (ref.catalog, ref.namespace) == (self._catalog, self._name)
        )


class SchemaHandle:
    __slots__ = ("_catalog", "_name", "_namespace", "_runtime", "_version")

    def __init__(
        self,
        runtime: _SnapshotOwner,
        catalog: str,
        namespace: str,
        name: str,
        version: str | None = None,
    ) -> None:
        self._runtime = runtime
        definition = runtime._snapshot_for_handle().schema(catalog, namespace, name, version)
        self._catalog = catalog
        self._namespace = namespace
        self._name = name
        self._version = definition.ref.version

    @property
    def ref(self) -> SchemaRef:
        return SchemaRef(self._catalog, self._namespace, self._name, self._version)

    @property
    def fingerprint(self) -> str:
        return self._runtime._snapshot_for_handle().schemas[self.ref].fingerprint

    @property
    def definition(self) -> Mapping[str, JsonValue]:
        return self._runtime._snapshot_for_handle().schemas[self.ref].definition


class ResourceHandle:
    __slots__ = ("_ref", "_runtime")

    def __init__(self, runtime: _SnapshotOwner, ref: ResourceRef) -> None:
        self._runtime = runtime
        self._ref = ref
        runtime._snapshot_for_handle().resource(ref)

    @property
    def ref(self) -> ResourceRef:
        return self._ref

    @property
    def profile(self) -> str:
        return self._runtime._snapshot_for_handle().resource(self._ref).profile

    @property
    def labels(self) -> Mapping[str, str]:
        return self._runtime._snapshot_for_handle().resource(self._ref).labels

    @property
    def schema(self) -> SchemaHandle | None:
        ref = self._runtime._snapshot_for_handle().resource(self._ref).schema
        if ref is None:
            return None
        return SchemaHandle(self._runtime, ref.catalog, ref.namespace, ref.name, ref.version)

    @property
    def fingerprint(self) -> str:
        return self._runtime._snapshot_for_handle().resource(self._ref).fingerprint


__all__ = ["NamespaceHandle", "ResourceHandle", "SchemaHandle"]
