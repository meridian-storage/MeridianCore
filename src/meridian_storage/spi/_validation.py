# SPDX-License-Identifier: Apache-2.0
"""Shared contract and deployment-integrity checks; no release allowlists."""

from __future__ import annotations

from meridian_storage import _version
from meridian_storage._versions import contract_matches
from meridian_storage.errors import CompatibilityError, ErrorCode, SafeCause
from meridian_storage.runtime.config import BindingConfig
from meridian_storage.spi.adapters import AdapterProbe

# Legacy compatibilityPins.coreVersion denotes this SPI contract since 1.0.1.
# It is not the installed Python distribution release.
CORE_CONTRACT_VERSION = "1.0.0"


def validate_binding_probe(binding: BindingConfig, probe: AdapterProbe) -> None:
    """Compare observed contracts/content with the deployment's own expectations."""

    manifest = probe.manifest
    if manifest.adapter_id != binding.adapter_id:
        raise CompatibilityError(
            ErrorCode.ADAPTER_CONTRACT,
            f"Binding {binding.id!r} probed an unexpected Adapter identity",
        )
    try:
        compatible = contract_matches(manifest.adapter_contract_version, binding.adapter_contract)
    except ValueError as exc:
        raise CompatibilityError(
            ErrorCode.ADAPTER_CONTRACT,
            f"Binding {binding.id!r} contains an invalid Adapter contract range",
            cause=SafeCause.from_exception(exc),
        ) from exc
    if not compatible:
        raise CompatibilityError(
            ErrorCode.ADAPTER_CONTRACT,
            f"Binding {binding.id!r} Adapter contract is incompatible",
        )
    if (manifest.engine_profile, manifest.engine_version) != (
        binding.engine_profile,
        binding.engine_version,
    ):
        raise CompatibilityError(
            ErrorCode.ADAPTER_CONTRACT,
            f"Binding {binding.id!r} Engine selection differs from the deployment lock",
        )
    if manifest.fingerprint != binding.required_capability_fingerprint:
        raise CompatibilityError(
            ErrorCode.CAPABILITY_FINGERPRINT,
            f"Binding {binding.id!r} Capability fingerprint does not match its pin",
        )
    known_pins = {
        "coreVersion": CORE_CONTRACT_VERSION,
        "coreContractVersion": CORE_CONTRACT_VERSION,
        "coreDistributionVersion": _version.__version__,
        "observedEngineVersion": probe.observed_engine_version,
        "driver": manifest.descriptor.driver,
        "adapterContract": manifest.adapter_contract_version,
        "engineProfile": manifest.engine_profile,
        "engineVersion": manifest.engine_version,
    }
    for name, expected in binding.compatibility_pins.items():
        actual = known_pins.get(name)
        if name not in known_pins:
            extension = manifest.extensions.get(name)
            actual = extension if isinstance(extension, str) else None
        if actual is None or actual != expected:
            raise CompatibilityError(
                ErrorCode.ADAPTER_CONTRACT,
                f"Binding {binding.id!r} compatibility pin {name!r} is unsatisfied",
            )
