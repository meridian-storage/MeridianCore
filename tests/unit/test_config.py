# SPDX-License-Identifier: Apache-2.0

from __future__ import annotations

import json
from copy import deepcopy

import pytest

from meridian_storage import ConfigurationError, ErrorCode
from meridian_storage.runtime import (
    CONFIG_ENVIRONMENT_VARIABLE,
    RuntimeConfig,
    load_runtime_config,
    load_runtime_config_from_environment,
)
from tests.support import FakeAdapterFactory, FakeCatalogProvider, bundle_for, config_mapping


def mapping() -> dict[str, object]:
    factory = FakeAdapterFactory()
    catalog = FakeCatalogProvider()
    return config_mapping(factory, bundle_for(), catalog)


def test_runtime_config_round_trip_is_canonical() -> None:
    config = RuntimeConfig.from_mapping(mapping())
    assert config.format_version == "meridian-config.v1"
    assert config.profile == "test"
    assert config.catalogs.providers[0].name == "structured"
    assert config.resources.pins[0].ref.canonical == "structured:investigation.cases"
    assert config.bindings[0].endpoint == "memory://logical-endpoint"
    assert config.binding("primary") is config.bindings[0]
    assert RuntimeConfig.from_mapping(config.to_dict()).fingerprint == config.fingerprint
    with pytest.raises(KeyError):
        config.binding("missing")


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: value.update({"unexpected": True}), ErrorCode.CONFIG_UNKNOWN_FIELD),
        (lambda value: value.pop("resources"), ErrorCode.CONFIG_INVALID),
        (lambda value: value.update({"formatVersion": "v2"}), ErrorCode.CONFIG_INVALID),
        (lambda value: value.update({"bindings": []}), ErrorCode.CONFIG_INVALID),
    ],
)
def test_runtime_config_rejects_closed_schema_violations(
    mutate: object,
    code: ErrorCode,
) -> None:
    value = mapping()
    mutate(value)  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as failure:
        RuntimeConfig.from_mapping(value)
    assert failure.value.code == code


def test_runtime_config_rejects_duplicate_and_missing_references() -> None:
    duplicate = mapping()
    duplicate["bindings"] = [
        *duplicate["bindings"],  # type: ignore[misc]
        deepcopy(duplicate["bindings"][0]),  # type: ignore[index]
    ]
    with pytest.raises(ConfigurationError) as failure:
        RuntimeConfig.from_mapping(duplicate)
    assert failure.value.code == ErrorCode.CONFIG_DUPLICATE_ID

    missing_binding = mapping()
    missing_binding["placements"][0]["bindingId"] = "absent"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="unknown binding"):
        RuntimeConfig.from_mapping(missing_binding)

    missing_provider = mapping()
    missing_provider["resources"]["pins"][0]["providerId"] = "absent"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="unconfigured Schema provider"):
        RuntimeConfig.from_mapping(missing_provider)


def test_runtime_config_rejects_unregistered_catalogs() -> None:
    value = mapping()
    value["catalogs"]["providers"][0]["name"] = "query"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="not a registered"):
        RuntimeConfig.from_mapping(value)
    value = mapping()
    value["resources"]["pins"][0]["ref"]["catalog"] = "object"  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="unconfigured Catalog"):
        RuntimeConfig.from_mapping(value)


def test_placement_selector_accepts_catalog_and_rejects_an_empty_selector() -> None:
    value = mapping()
    selector = value["placements"][0]["selector"]  # type: ignore[index]
    selector["resources"] = []
    selector["catalog"] = "structured"
    assert RuntimeConfig.from_mapping(value).placements[0].selector.catalog == "structured"

    selector["catalog"] = None
    with pytest.raises(ConfigurationError, match="must select"):
        RuntimeConfig.from_mapping(value)


@pytest.mark.parametrize(
    "endpoint,service_ref",
    [(None, None), ("memory://x", "service.x")],
)
def test_binding_requires_one_locator(endpoint: str | None, service_ref: str | None) -> None:
    value = mapping()
    binding = value["bindings"][0]  # type: ignore[index]
    binding["endpoint"] = endpoint
    binding["serviceRef"] = service_ref
    with pytest.raises(ConfigurationError, match="exactly one"):
        RuntimeConfig.from_mapping(value)


def test_tls_policies_fail_closed() -> None:
    value = mapping()
    tls = value["bindings"][0]["tls"]  # type: ignore[index]
    tls["mode"] = "server"
    with pytest.raises(ConfigurationError, match="requires"):
        RuntimeConfig.from_mapping(value)

    value = mapping()
    tls = value["bindings"][0]["tls"]  # type: ignore[index]
    tls.update(
        {
            "mode": "mutual",
            "serverName": "engine.internal",
            "caRef": {"provider": "test", "reference": "ca"},
            "clientCertificateRef": None,
        }
    )
    with pytest.raises(ConfigurationError, match="clientCertificateRef"):
        RuntimeConfig.from_mapping(value)

    value = mapping()
    tls = value["bindings"][0]["tls"]  # type: ignore[index]
    tls.update(
        {
            "mode": "mutual",
            "serverName": "engine.internal",
            "caRef": {"provider": "test", "reference": "ca"},
            "clientCertificateRef": {"provider": "test", "reference": "client"},
        }
    )
    assert RuntimeConfig.from_mapping(value).bindings[0].tls.mode == "mutual"


def test_client_retry_and_validation_bounds() -> None:
    value = mapping()
    value["bindings"][0]["client"]["minSize"] = 5  # type: ignore[index]
    value["bindings"][0]["client"]["maxSize"] = 4  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="cannot exceed"):
        RuntimeConfig.from_mapping(value)

    value = mapping()
    value["validation"]["retry"]["baseDelayMs"] = 2  # type: ignore[index]
    value["validation"]["retry"]["maxDelayMs"] = 1  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="cannot exceed"):
        RuntimeConfig.from_mapping(value)

    value = mapping()
    value["validation"]["strict"] = False  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="must be true"):
        RuntimeConfig.from_mapping(value)


def test_live_schema_and_telemetry_invariants() -> None:
    value = mapping()
    value["schemas"]["live"] = {  # type: ignore[index]
        "enabled": False,
        "required": True,
        "providerId": None,
    }
    with pytest.raises(ConfigurationError, match="required"):
        RuntimeConfig.from_mapping(value)

    value = mapping()
    value["schemas"]["live"] = {  # type: ignore[index]
        "enabled": True,
        "required": True,
        "providerId": None,
    }
    with pytest.raises(ConfigurationError, match="providerId"):
        RuntimeConfig.from_mapping(value)

    value = mapping()
    value["telemetry"]["enabled"] = True  # type: ignore[index]
    value["telemetry"]["serviceName"] = None  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="serviceName"):
        RuntimeConfig.from_mapping(value)


def test_required_physical_fingerprint_policy() -> None:
    value = mapping()
    value["bindings"][0]["requiredPhysicalFingerprint"] = None  # type: ignore[index]
    with pytest.raises(ConfigurationError, match="physical fingerprint"):
        RuntimeConfig.from_mapping(value)
    value["validation"]["requirePhysicalFingerprints"] = False  # type: ignore[index]
    assert RuntimeConfig.from_mapping(value).bindings[0].required_physical_fingerprint is None


def test_every_deployment_fingerprint_requires_canonical_sha256() -> None:
    mutations = (
        lambda value: value["catalogs"]["providers"][0].update(  # type: ignore[index]
            {"requiredFingerprint": "not-a-fingerprint"}
        ),
        lambda value: value["resources"]["pins"][0].update(  # type: ignore[index]
            {"requiredFingerprint": "sha256:ABC"}
        ),
        lambda value: value["schemas"]["providers"][0].update(  # type: ignore[index]
            {"requiredFingerprint": "sha256:" + "0" * 63}
        ),
        lambda value: value["bindings"][0].update(  # type: ignore[index]
            {"requiredCapabilityFingerprint": "md5:" + "0" * 32}
        ),
        lambda value: value["bindings"][0].update(  # type: ignore[index]
            {"requiredPhysicalFingerprint": "sha256:" + "g" * 64}
        ),
    )
    for mutate in mutations:
        value = mapping()
        mutate(value)
        with pytest.raises(ConfigurationError, match="sha256 fingerprint"):
            RuntimeConfig.from_mapping(value)


def test_load_runtime_config_and_environment(tmp_path: object) -> None:
    path = tmp_path / "meridian.json"  # type: ignore[operator]
    path.write_text(json.dumps(mapping()), encoding="utf-8")
    loaded = load_runtime_config(path)
    assert loaded.profile == "test"
    assert load_runtime_config_from_environment({CONFIG_ENVIRONMENT_VARIABLE: str(path)}) == loaded
    with pytest.raises(ConfigurationError) as missing_env:
        load_runtime_config_from_environment({})
    assert missing_env.value.code == ErrorCode.CONFIG_NOT_FOUND


def test_load_runtime_config_redacts_io_and_decode_failures(tmp_path: object) -> None:
    missing = tmp_path / "missing.json"  # type: ignore[operator]
    with pytest.raises(ConfigurationError) as not_found:
        load_runtime_config(missing)
    assert not_found.value.code == ErrorCode.CONFIG_NOT_FOUND
    invalid = tmp_path / "invalid.json"  # type: ignore[operator]
    invalid.write_bytes(b"\xff")
    with pytest.raises(ConfigurationError) as decode:
        load_runtime_config(invalid)
    assert decode.value.cause is not None
    assert str(invalid) not in decode.value.message
    oversized = tmp_path / "large.json"  # type: ignore[operator]
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ConfigurationError, match="1 MiB"):
        load_runtime_config(oversized)
