# SPDX-License-Identifier: Apache-2.0
"""Stable consumer-facing Meridian Core facade."""

from pkgutil import extend_path

from ._version import __version__
from .context import OperationContext, bind_context, current_context
from .errors import (
    AuthenticationError,
    AuthorizationError,
    CatalogNotFound,
    CompatibilityError,
    ConfigurationError,
    ConflictError,
    ConstraintError,
    CorruptionError,
    ErrorCategory,
    ErrorCode,
    InternalError,
    LifecycleError,
    MeridianError,
    MeridianTimeoutError,
    NotFoundError,
    RateLimitError,
    SafeCause,
    TransactionError,
    TransientError,
    UnavailableError,
    ValidationError,
)
from .registry import NamespaceHandle, ResourceHandle, ResourceRef, SchemaHandle, SchemaRef
from .runtime import (
    CORE_VERSION,
    CatalogManifest,
    Expression,
    Meridian,
    Operation,
    OperationContract,
    OperationResult,
    RuntimeConfig,
    RuntimeState,
    StartupReport,
)
from .transactions import Transaction

__path__ = extend_path(__path__, __name__)

__all__ = [
    "CORE_VERSION",
    "AuthenticationError",
    "AuthorizationError",
    "CatalogManifest",
    "CatalogNotFound",
    "CompatibilityError",
    "ConfigurationError",
    "ConflictError",
    "ConstraintError",
    "CorruptionError",
    "ErrorCategory",
    "ErrorCode",
    "Expression",
    "InternalError",
    "LifecycleError",
    "Meridian",
    "MeridianError",
    "MeridianTimeoutError",
    "NamespaceHandle",
    "NotFoundError",
    "Operation",
    "OperationContext",
    "OperationContract",
    "OperationResult",
    "RateLimitError",
    "ResourceHandle",
    "ResourceRef",
    "RuntimeConfig",
    "RuntimeState",
    "SafeCause",
    "SchemaHandle",
    "SchemaRef",
    "StartupReport",
    "Transaction",
    "TransactionError",
    "TransientError",
    "UnavailableError",
    "ValidationError",
    "__version__",
    "bind_context",
    "current_context",
]
