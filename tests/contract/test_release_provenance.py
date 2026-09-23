# SPDX-License-Identifier: Apache-2.0
"""Metadata gate behavior only; real-engine support is established separately."""

import json
from dataclasses import replace
from pathlib import Path

import jsonschema
import pytest

from meridian_storage import CompatibilityError, Meridian, RuntimeConfig
from meridian_storage._canonical import sha256_fingerprint
from meridian_storage.registry import CapabilityRequirement
from meridian_storage.spi import (
    AdapterDescriptor,
    AdapterProbe,
    CapabilityManifest,
    OperationCapability,
    adapter_capability_contract,
    capability_violations,
)
from meridian_storage.spi._validation import validate_binding_probe
from meridian_storage.testing import run_adapter_conformance
from tests.contract.test_adapter_conformance import target
from tests.support import (
    FakeAdapterFactory,
    FakeCatalogProvider,
    StaticSchemaProvider,
    StaticSecretResolver,
    bundle_for,
    capability_manifest,
    config_mapping,
)

FIXTURES = (
    Path(__file__).resolve().parents[2]
    / "contracts/adapter-capability/fixtures/release-provenance.v1.json"
)


def from_wire(wire):
    raw = wire["descriptor"]
    capabilities = tuple(
        OperationCapability(
            c["operationContract"],
            tuple(c["operationVersions"]),
            tuple(c["guarantees"]),
            c["limits"],
            c["cursorBehavior"],
            c["migrationBehavior"],
            tuple(c["healthProbes"]),
            c["extensions"],
        )
        for c in raw["capabilities"]
    )
    descriptor = AdapterDescriptor(
        raw["adapterId"],
        raw["adapterContractVersion"],
        raw["driver"],
        raw["supportedEngineVersions"],
        capabilities,
    )
    return CapabilityManifest(
        descriptor,
        wire["engineProfile"],
        wire["engineVersion"],
        tuple(wire["availableOperationContracts"]),
        wire["extensions"],
        wire["formatVersion"],
    )


def test_released_golden_round_trips_keep_v1_bytes_and_hashes():
    fixture = json.loads(FIXTURES.read_text())
    config = RuntimeConfig.from_mapping(fixture["runtimeConfig"])
    assert config.to_dict() == fixture["runtimeConfig"]
    assert config.fingerprint == fixture["runtimeConfigFingerprint"]
    for case in fixture["manifests"]:
        manifest = from_wire(case["document"])
        jsonschema.Draft202012Validator(adapter_capability_contract()).validate(manifest.to_dict())
        assert manifest.to_dict() == case["document"]
        assert manifest.fingerprint == case["fingerprint"]
        assert manifest.descriptor.fingerprint == case["descriptorFingerprint"]
        reordered = json.loads(json.dumps(case["document"], sort_keys=True))
        assert from_wire(reordered).fingerprint == case["fingerprint"]
        assert sha256_fingerprint(manifest.to_dict()) == case["fingerprint"]


@pytest.mark.parametrize("version", ["1.0.0", "99.12.4", "unlisted-build-20260907"])
@pytest.mark.parametrize("driver", ["test-driver-1.0.0", "test-driver-23.7.1"])
def test_independent_release_metadata_passes_runtime_and_public_helper(version, driver):
    factory = FakeAdapterFactory()
    factory.manifest = replace(
        factory.manifest,
        descriptor=replace(factory.manifest.descriptor, driver=driver),
        engine_version=version,
    )
    catalog = FakeCatalogProvider()
    bundle = bundle_for()
    mapping = config_mapping(factory, bundle, catalog)
    binding = mapping["bindings"][0]
    binding["engineVersion"] = version
    binding["compatibilityPins"]["driver"] = driver
    runtime = Meridian.from_config(
        RuntimeConfig.from_mapping(mapping),
        adapter_factories=(factory,),
        catalog_providers=(catalog,),
        schema_providers=(StaticSchemaProvider(bundle),),
        secret_resolver=StaticSecretResolver(),
    )
    try:
        report = runtime.start()
        details = next(e.details for e in report.evidence if e.stage == "adapter-probe")
        assert details["selectedEngineVersion"] == version
        assert details["manifestEngineVersion"] == version
        # A declaration, even one matching config, cannot manufacture an observation.
        assert details["observedEngineVersion"] == "unavailable"
        assert details["coreContractVersion"] == "1.0.0"
        assert details["coreDistributionVersion"] == __import__("meridian_storage").__version__
    finally:
        runtime.close()
    selected = target()
    selected.factory.manifest = factory.manifest
    old = selected.create_context.binding
    selected = replace(
        selected,
        create_context=replace(
            selected.create_context,
            binding=replace(
                old,
                engine_version=version,
                required_capability_fingerprint=factory.manifest.fingerprint,
                compatibility_pins={**old.compatibility_pins, "driver": driver},
            ),
        ),
    )
    assert run_adapter_conformance(selected).engine_version == version


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"engine_version": "different-selection"}, "deployment lock"),
        ({"engine_profile": "unknown"}, "deployment lock"),
        ({"required_capability_fingerprint": "sha256:" + "0" * 64}, "fingerprint"),
        ({"adapter_contract": "2.x"}, "incompatible"),
        ({"compatibility_pins": {"coreVersion": "99.0.0"}}, "coreVersion"),
        ({"compatibility_pins": {"coreContractVersion": "99.0.0"}}, "coreContractVersion"),
        ({"compatibility_pins": {"coreDistributionVersion": "99.0.0"}}, "coreDistributionVersion"),
        ({"compatibility_pins": {"observedEngineVersion": "99.0.0"}}, "observedEngineVersion"),
        ({"compatibility_pins": {"missing": "required"}}, "missing"),
    ],
)
def test_runtime_and_helper_retain_contract_and_deployment_integrity(changes, reason):
    selected = target()
    binding = replace(selected.create_context.binding, **changes)
    with pytest.raises(CompatibilityError, match=reason):
        validate_binding_probe(binding, AdapterProbe(selected.factory.manifest))
    with pytest.raises(AssertionError, match=reason):
        run_adapter_conformance(
            replace(selected, create_context=replace(selected.create_context, binding=binding))
        )
    assert selected.factory.runtimes[-1].closed


@pytest.mark.parametrize("protocol", ["2006-03-01", "1.1.1"])
def test_legacy_protocol_is_not_relabelled_as_observed_server_release(protocol):
    manifest = capability_manifest()
    manifest = replace(
        manifest,
        engine_version=protocol,
        descriptor=replace(
            manifest.descriptor, supported_engine_versions={"test-engine": (protocol,)}
        ),
    )
    selected = target()
    binding = replace(
        selected.create_context.binding,
        engine_version=protocol,
        required_capability_fingerprint=manifest.fingerprint,
        compatibility_pins={
            "coreDistributionVersion": __import__("meridian_storage").__version__,
            "observedEngineVersion": "server-build-2030",
        },
    )
    # A manifest extension cannot substitute for a missing authenticated observation.
    spoofed = replace(manifest, extensions={"observedEngineVersion": "server-build-2030"})
    with pytest.raises(CompatibilityError, match="observedEngineVersion"):
        validate_binding_probe(
            replace(binding, required_capability_fingerprint=spoofed.fingerprint),
            AdapterProbe(spoofed),
        )
    probe = AdapterProbe(manifest, observed_engine_version="server-build-2030")
    validate_binding_probe(binding, probe)
    assert probe.manifest.engine_version == protocol
    assert probe.observed_engine_version == "server-build-2030"


@pytest.mark.parametrize("invalid", ["", "\n", "x" * 257, 1])
def test_observed_release_requires_bounded_safe_metadata(invalid):
    with pytest.raises(ValueError, match="bounded"):
        AdapterProbe(capability_manifest(), observed_engine_version=invalid)


def test_unlisted_metadata_never_supplies_missing_operations_guarantees_or_limits():
    manifest = replace(capability_manifest(), engine_version="unlisted-release")
    for requirement in [
        CapabilityRequirement("missing", "1.0.0"),
        CapabilityRequirement("meridian.structured.get", "99.0.0"),
        CapabilityRequirement("meridian.structured.get", "1.0.0", guarantees=("missing",)),
        CapabilityRequirement(
            "meridian.structured.get", "1.0.0", minimum_limits={"maxResultBytes": 10**12}
        ),
    ]:
        assert capability_violations(manifest, (requirement,))
    with pytest.raises(ValueError, match="profile"):
        replace(manifest, engine_profile="not-advertised")
    with pytest.raises(ValueError, match="absent"):
        replace(manifest, available_operation_contracts=("invented",))


def test_deployment_distribution_lock_uses_installed_release_not_compiled_recipe(monkeypatch):
    from meridian_storage import _version

    selected = target()
    monkeypatch.setattr(_version, "__version__", "99.7.2")
    binding = replace(
        selected.create_context.binding,
        compatibility_pins={"coreDistributionVersion": "99.7.2", "coreVersion": "1.0.0"},
    )
    validate_binding_probe(binding, AdapterProbe(selected.factory.manifest))
    assert run_adapter_conformance(
        replace(selected, create_context=replace(selected.create_context, binding=binding))
    )


@pytest.mark.parametrize(
    "field,value",
    [
        ("engineVersion", ""),
        ("engineVersion", None),
        ("requiredCapabilityFingerprint", "malformed"),
        ("compatibilityPins", {"coreDistributionVersion": ""}),
        ("compatibilityPins", {"coreDistributionVersion": 1}),
        ("newLockField", {}),
    ],
)
def test_unlisted_release_does_not_open_configuration_or_accept_malformed_locks(field, value):
    from meridian_storage import ConfigurationError

    fixture = json.loads(FIXTURES.read_text())["runtimeConfig"]
    fixture["bindings"][0]["engineVersion"] = "unlisted-release"
    fixture["bindings"][0][field] = value
    with pytest.raises(ConfigurationError):
        RuntimeConfig.from_mapping(fixture)
