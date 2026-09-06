# SPDX-License-Identifier: Apache-2.0
"""Current published projection/append descriptors cannot satisfy authoritative v2 put."""

import importlib
from importlib.metadata import version

import pytest

from meridian_storage.registry import CapabilityRequirement
from meridian_storage.spi import CapabilityManifest, capability_violations


def test_released_projection_and_append_descriptors_fail_authoritative_put():
    pytest.importorskip("meridian_storage.adapters.clickhouse")
    pytest.importorskip("meridian_storage.adapters.opensearch")
    from meridian_storage.adapters.clickhouse.configuration import ClickHouseSettings, Endpoint
    from meridian_storage.adapters.clickhouse.descriptor import adapter_descriptor
    from meridian_storage.adapters.clickhouse.schema import Topology
    from meridian_storage.adapters.opensearch.descriptor import capability_manifest

    settings = ClickHouseSettings(
        database="descriptor_fixture",
        topology=Topology.STANDALONE,
        endpoint=Endpoint("localhost", 8123, False),
        layouts={},
        max_batch_rows=100,
        max_batch_bytes=1048576,
        max_time_range_seconds=86400,
        retry_window_seconds=3600,
        cursor_ttl_seconds=900,
        insert_quorum=1,
        required_functions=(),
        operation_timeout_ms=10000,
        max_result_bytes=1048576,
    )
    clickhouse = CapabilityManifest(
        adapter_descriptor(settings, "25.3"), settings.topology.value, "25.3"
    )
    opensearch = capability_manifest("2.19.2", pit_enabled=False)
    requirement = CapabilityRequirement("meridian.structured.put", "2.0.0")
    for manifest in (clickhouse, opensearch):
        assert capability_violations(manifest, (requirement,))
        assert manifest.descriptor.capability_for("meridian.transaction") is None
    assert opensearch.descriptor.capability_for("meridian.structured.put") is None
    search = opensearch.descriptor.capability_for("meridian.structured.search")
    assert search.extensions["projection"]["writes"] == (
        "upsert",
        "tombstone-delete",
        "bounded-bulk",
    )
    put = clickhouse.descriptor.capability_for("meridian.structured.put")
    assert put.operation_versions == ("1.0.0",)
    assert put.extensions["writeModel"] == "append-version"
    assert clickhouse.descriptor.capability_for("meridian.evidence.append") is not None
    for distribution, module in (
        ("meridian-storage-opensearch", "meridian_storage.adapters.opensearch"),
        ("meridian-storage-clickhouse", "meridian_storage.adapters.clickhouse"),
    ):
        assert version(distribution) == "1.0.0"
        assert "site-packages" in importlib.import_module(module).__file__
