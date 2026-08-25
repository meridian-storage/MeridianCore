# SPDX-License-Identifier: Apache-2.0
"""Stable Adapter, Catalog, Schema-provider, and Plugin SPI."""

from __future__ import annotations

from typing import Any

_ADAPTER_EXPORTS = {
    "AdapterCreateContext",
    "AdapterFactory",
    "AdapterProbe",
    "AdapterRuntime",
    "AdapterSession",
    "ExecutionRequest",
    "ExecutionResult",
    "PhysicalResource",
    "PhysicalVerification",
    "SecretResolver",
    "SecretValue",
}
_CAPABILITY_EXPORTS = {
    "CAPABILITY_FORMAT_VERSION",
    "AdapterDescriptor",
    "CapabilityManifest",
    "CapabilityRequirement",
    "CapabilityViolation",
    "OperationCapability",
    "adapter_capability_contract",
    "capability_violations",
}
_DISCOVERY_EXPORTS = {
    "ADAPTER_ENTRY_POINT_GROUP",
    "CATALOG_ENTRY_POINT_GROUP",
    "PLUGIN_ENTRY_POINT_GROUP",
    "SCHEMA_ENTRY_POINT_GROUP",
    "DiscoveryRecord",
    "DiscoveryResult",
    "discover_components",
}
_PROVIDER_EXPORTS = {
    "CatalogProvider",
    "CatalogSurface",
    "LiveSchemaProvider",
    "Observer",
    "PluginFactory",
    "PluginManifest",
    "SchemaProvider",
}


def __getattr__(name: str) -> Any:
    if name in _ADAPTER_EXPORTS:
        from . import adapters

        return getattr(adapters, name)
    if name in _CAPABILITY_EXPORTS:
        from . import capabilities

        return getattr(capabilities, name)
    if name in _DISCOVERY_EXPORTS:
        from . import discovery

        return getattr(discovery, name)
    if name in _PROVIDER_EXPORTS:
        from . import providers

        return getattr(providers, name)
    raise AttributeError(name)


__all__ = sorted(_ADAPTER_EXPORTS | _CAPABILITY_EXPORTS | _DISCOVERY_EXPORTS | _PROVIDER_EXPORTS)
