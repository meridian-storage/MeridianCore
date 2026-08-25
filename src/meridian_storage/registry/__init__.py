# SPDX-License-Identifier: Apache-2.0
"""Logical Resource model, read-only handles, and internal registry."""

from __future__ import annotations

from typing import Any

_RESOURCE_EXPORTS = {
    "REGISTERED_CATALOGS",
    "CapabilityRequirement",
    "NamespaceDefinition",
    "ResourceBundle",
    "ResourceDefinition",
    "ResourceRef",
    "SchemaDefinition",
    "SchemaRef",
}
_HANDLE_EXPORTS = {"NamespaceHandle", "ResourceHandle", "SchemaHandle"}
_REGISTRY_EXPORTS = {"BindingRecord", "Registry", "RegistrySnapshot", "build_registry"}


def __getattr__(name: str) -> Any:
    if name in _RESOURCE_EXPORTS:
        from . import resources

        return getattr(resources, name)
    if name in _HANDLE_EXPORTS:
        from . import handles

        return getattr(handles, name)
    if name in _REGISTRY_EXPORTS:
        from . import registry

        return getattr(registry, name)
    raise AttributeError(name)


__all__ = sorted(_RESOURCE_EXPORTS | _HANDLE_EXPORTS | _REGISTRY_EXPORTS)
