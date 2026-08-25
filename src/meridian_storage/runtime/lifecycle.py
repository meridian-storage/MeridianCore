# SPDX-License-Identifier: Apache-2.0
"""Runtime lifecycle states and structured, redacted startup evidence."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any


class RuntimeState(StrEnum):
    NEW = "NEW"
    STARTING = "STARTING"
    READY = "READY"
    DRAINING = "DRAINING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


class EvidenceStatus(StrEnum):
    PASSED = "PASSED"
    SKIPPED = "SKIPPED"


@dataclass(frozen=True, slots=True)
class StartupEvidence:
    stage: str
    status: EvidenceStatus
    subject: str
    fingerprint: str | None = None
    details: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "details", MappingProxyType(dict(self.details)))

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "status": self.status.value,
            "subject": self.subject,
            "fingerprint": self.fingerprint,
            "details": dict(self.details),
        }


@dataclass(frozen=True, slots=True)
class BindingStartupReport:
    binding_id: str
    adapter_id: str
    adapter_contract_version: str
    engine_profile: str
    engine_version: str
    capability_fingerprint: str
    physical_fingerprint: str

    def to_dict(self) -> dict[str, str]:
        return {
            "bindingId": self.binding_id,
            "adapterId": self.adapter_id,
            "adapterContractVersion": self.adapter_contract_version,
            "engineProfile": self.engine_profile,
            "engineVersion": self.engine_version,
            "capabilityFingerprint": self.capability_fingerprint,
            "physicalFingerprint": self.physical_fingerprint,
        }


@dataclass(frozen=True, slots=True)
class StartupReport:
    profile: str
    config_fingerprint: str
    registry_revision: int
    registry_fingerprint: str
    started_at: datetime
    ready_at: datetime
    bindings: tuple[BindingStartupReport, ...]
    adapters: tuple[str, ...]
    catalogs: tuple[str, ...]
    schema_providers: tuple[str, ...]
    resources: tuple[str, ...]
    plugins: tuple[str, ...]
    evidence: tuple[StartupEvidence, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile": self.profile,
            "configFingerprint": self.config_fingerprint,
            "registryRevision": self.registry_revision,
            "registryFingerprint": self.registry_fingerprint,
            "startedAt": self.started_at.isoformat(),
            "readyAt": self.ready_at.isoformat(),
            "bindings": [binding.to_dict() for binding in self.bindings],
            "adapters": list(self.adapters),
            "catalogs": list(self.catalogs),
            "schemaProviders": list(self.schema_providers),
            "resources": list(self.resources),
            "plugins": list(self.plugins),
            "evidence": [item.to_dict() for item in self.evidence],
        }


__all__ = [
    "BindingStartupReport",
    "EvidenceStatus",
    "RuntimeState",
    "StartupEvidence",
    "StartupReport",
]
