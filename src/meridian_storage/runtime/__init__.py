# SPDX-License-Identifier: Apache-2.0
"""Meridian Core runtime public API with cycle-safe lazy exports."""

from __future__ import annotations

from typing import Any

CORE_VERSION = "1.0.0"

_CONFIG_EXPORTS = {
    "CONFIG_ENVIRONMENT_VARIABLE",
    "CONFIG_FORMAT_VERSION",
    "BindingConfig",
    "CatalogConfig",
    "CatalogsConfig",
    "ClientPolicy",
    "LiveSchemaConfig",
    "PlacementRule",
    "PlacementSelector",
    "ResourcePin",
    "ResourcesConfig",
    "RetryPolicy",
    "RuntimeConfig",
    "SchemaConfig",
    "SchemaProviderConfig",
    "SecretReference",
    "TLSPolicy",
    "TelemetryConfig",
    "ValidationConfig",
    "load_runtime_config",
    "load_runtime_config_from_environment",
    "runtime_config_contract",
}
_LIFECYCLE_EXPORTS = {
    "BindingStartupReport",
    "EvidenceStatus",
    "RuntimeState",
    "StartupEvidence",
    "StartupReport",
}
_OPERATION_EXPORTS = {
    "EXPRESSION_FORMAT_VERSION",
    "OPERATION_FORMAT_VERSION",
    "REGISTERED_CATALOG_METHODS",
    "CatalogManifest",
    "Expression",
    "Operation",
    "OperationContract",
    "OperationResult",
    "expression_contract",
    "operation_contract",
}
_SECRET_EXPORTS = {
    "CompositeSecretResolver",
    "EnvironmentSecretResolver",
    "FileSecretResolver",
    "default_secret_resolver",
}


def __getattr__(name: str) -> Any:
    if name == "Meridian":
        from .runtime import Meridian

        return Meridian
    if name in _CONFIG_EXPORTS:
        from . import config

        return getattr(config, name)
    if name in _LIFECYCLE_EXPORTS:
        from . import lifecycle

        return getattr(lifecycle, name)
    if name in _OPERATION_EXPORTS:
        from . import operations

        return getattr(operations, name)
    if name in _SECRET_EXPORTS:
        from . import secrets

        return getattr(secrets, name)
    raise AttributeError(name)


__all__ = sorted(
    {"CORE_VERSION", "Meridian"}
    | _CONFIG_EXPORTS
    | _LIFECYCLE_EXPORTS
    | _OPERATION_EXPORTS
    | _SECRET_EXPORTS
)
