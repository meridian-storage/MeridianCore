# SPDX-License-Identifier: Apache-2.0
"""Public V1 Adapter conformance runner owned by Meridian Core."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from meridian_storage._versions import contract_matches
from meridian_storage.context import OperationContext
from meridian_storage.registry import CapabilityRequirement
from meridian_storage.runtime.operations import Operation
from meridian_storage.runtime.runtime import (
    CORE_CONTRACT_VERSION,
    TRANSACTION_OPERATION_CONTRACT,
    TRANSACTION_OPERATION_VERSION,
)
from meridian_storage.spi.adapters import (
    AdapterCreateContext,
    AdapterFactory,
    AdapterProbe,
    AdapterRuntime,
    AdapterSession,
    ExecutionRequest,
    ExecutionResult,
    PhysicalResource,
    PhysicalVerification,
)
from meridian_storage.spi.capabilities import capability_violations


@dataclass(frozen=True, slots=True)
class AdapterConformanceTarget:
    """Real-engine fixtures supplied by an independently released Adapter."""

    factory: AdapterFactory
    create_context: AdapterCreateContext
    resources: tuple[PhysicalResource, ...]
    operation: Operation
    context: OperationContext
    assert_result: Callable[[ExecutionResult], None]


@dataclass(frozen=True, slots=True)
class AdapterConformanceReport:
    adapter_id: str
    adapter_contract_version: str
    engine_profile: str
    engine_version: str
    capability_fingerprint: str
    physical_fingerprint: str
    resource_count: int
    checks: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "adapterId": self.adapter_id,
            "adapterContractVersion": self.adapter_contract_version,
            "engineProfile": self.engine_profile,
            "engineVersion": self.engine_version,
            "capabilityFingerprint": self.capability_fingerprint,
            "physicalFingerprint": self.physical_fingerprint,
            "resourceCount": self.resource_count,
            "checks": list(self.checks),
        }


def run_adapter_conformance(target: AdapterConformanceTarget) -> AdapterConformanceReport:
    """Exercise required SPI behavior against the target's disposable Engine."""

    if target.factory.adapter_id != target.create_context.binding.adapter_id:
        raise AssertionError("factory and Binding Adapter identities differ")
    if not target.resources:
        raise AssertionError("conformance target requires at least one physical Resource")
    runtime = target.factory.create(target.create_context)
    if not isinstance(runtime, AdapterRuntime):
        raise AssertionError("factory did not return an AdapterRuntime")
    checks: list[str] = []
    try:
        # A factory-created runtime is owned by the runner even when ``open``
        # fails after partially allocating engine resources.
        opened = True
        runtime.open()
        checks.append("authenticated-open")
        first_probe = runtime.probe()
        second_probe = runtime.probe()
        if not isinstance(first_probe, AdapterProbe) or not isinstance(second_probe, AdapterProbe):
            raise AssertionError("Adapter did not return an AdapterProbe")
        if first_probe.manifest.to_dict() != second_probe.manifest.to_dict():
            raise AssertionError("Capability probe is not deterministic")
        manifest = first_probe.manifest
        if manifest.adapter_id != target.factory.adapter_id:
            raise AssertionError("Capability manifest Adapter identity differs")
        binding = target.create_context.binding
        if not contract_matches(manifest.adapter_contract_version, binding.adapter_contract):
            raise AssertionError("Adapter contract is incompatible with the Binding")
        if (manifest.engine_profile, manifest.engine_version) != (
            binding.engine_profile,
            binding.engine_version,
        ):
            raise AssertionError("Capability manifest Engine selection differs from the Binding")
        if manifest.fingerprint != target.create_context.binding.required_capability_fingerprint:
            raise AssertionError("Capability fingerprint differs from the Binding pin")
        known_pins = {
            "coreVersion": CORE_CONTRACT_VERSION,
            "driver": manifest.descriptor.driver,
            "adapterContract": manifest.adapter_contract_version,
            "engineProfile": manifest.engine_profile,
            "engineVersion": manifest.engine_version,
        }
        for name, expected_pin in binding.compatibility_pins.items():
            actual_pin = known_pins.get(name)
            if actual_pin is None:
                extension = manifest.extensions.get(name)
                actual_pin = extension if isinstance(extension, str) else None
            if actual_pin != expected_pin:
                raise AssertionError(f"compatibility pin {name!r} is unsatisfied")
        base_requirement = CapabilityRequirement(
            target.operation.operation_contract, target.operation.operation_version
        )
        if capability_violations(manifest, (base_requirement, *target.operation.requirements)):
            raise AssertionError("sample Operation contract is not advertised")
        checks.append("deterministic-capability-manifest")

        first_physical = runtime.verify_physical(target.resources)
        second_physical = runtime.verify_physical(target.resources)
        if not isinstance(first_physical, PhysicalVerification) or not isinstance(
            second_physical, PhysicalVerification
        ):
            raise AssertionError("Adapter did not return PhysicalVerification")
        if first_physical.fingerprint != second_physical.fingerprint:
            raise AssertionError("physical verification fingerprint is not deterministic")
        if first_physical.mappings != second_physical.mappings:
            raise AssertionError("physical verification mappings are not deterministic")
        expected = {str(item.resource_ref) for item in target.resources}
        if set(first_physical.mappings) != expected or any(
            not value for value in first_physical.mappings.values()
        ):
            raise AssertionError("physical verification mappings are incomplete")
        required_physical = target.create_context.binding.required_physical_fingerprint
        if required_physical is not None and first_physical.fingerprint != required_physical:
            raise AssertionError("physical fingerprint differs from the Binding pin")
        checks.append("deterministic-physical-verification")

        context = target.context.resolve_request_id()
        assert context.request_id is not None
        request = ExecutionRequest(
            operation=target.operation,
            context=context,
            request_id=context.request_id,
            execution_id="conformance-execution",
            binding_id=target.create_context.binding.id,
            registry_revision=1,
            registry_fingerprint="sha256:" + "0" * 64,
            attempt=1,
        )
        session = runtime.open_session(transactional=False)
        if not isinstance(session, AdapterSession):
            raise AssertionError("Adapter did not return an AdapterSession")
        try:
            result = session.execute(request)
            if not isinstance(result, ExecutionResult):
                raise AssertionError("Adapter did not return an ExecutionResult")
            target.assert_result(result)
        finally:
            session.close()
        checks.append("normalized-execution")

        transaction_requirement = CapabilityRequirement(
            TRANSACTION_OPERATION_CONTRACT,
            TRANSACTION_OPERATION_VERSION,
            guarantees=("atomic", "no-dirty-reads"),
        )
        if not capability_violations(manifest, (transaction_requirement,)):
            committed = runtime.open_session(transactional=True)
            if not isinstance(committed, AdapterSession):
                raise AssertionError("Adapter did not return a transactional AdapterSession")
            committed.begin()
            try:
                transaction_result = committed.execute(request)
                if not isinstance(transaction_result, ExecutionResult):
                    raise AssertionError("transaction did not return an ExecutionResult")
                target.assert_result(transaction_result)
                committed.commit()
            finally:
                committed.close()
            rolled_back = runtime.open_session(transactional=True)
            if not isinstance(rolled_back, AdapterSession):
                raise AssertionError("Adapter did not return a transactional AdapterSession")
            rolled_back.begin()
            try:
                transaction_result = rolled_back.execute(request)
                if not isinstance(transaction_result, ExecutionResult):
                    raise AssertionError("transaction did not return an ExecutionResult")
                target.assert_result(transaction_result)
                rolled_back.rollback()
            finally:
                rolled_back.close()
            checks.append("transaction-commit-rollback")
        return AdapterConformanceReport(
            adapter_id=manifest.adapter_id,
            adapter_contract_version=manifest.adapter_contract_version,
            engine_profile=manifest.engine_profile,
            engine_version=manifest.engine_version,
            capability_fingerprint=manifest.fingerprint,
            physical_fingerprint=first_physical.fingerprint,
            resource_count=len(target.resources),
            checks=tuple(checks),
        )
    finally:
        if opened:
            runtime.close()


__all__ = [
    "AdapterConformanceReport",
    "AdapterConformanceTarget",
    "run_adapter_conformance",
]
