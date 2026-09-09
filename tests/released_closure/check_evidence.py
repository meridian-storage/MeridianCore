# SPDX-License-Identifier: Apache-2.0
"""Verify scoped, previously measured engine evidence and current Core round trips."""

import hashlib
import json
import posixpath
import sys
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

from meridian_storage.runtime.config import RuntimeConfig


def main():
    consumer = Path(sys.argv[1]).resolve()
    fixture = Path(__file__).parent / "evidence"
    index = json.loads((fixture / "index.json").read_text())
    archive = fixture / "upstream-records.zip"
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == index["snapshotSha256"]
    with zipfile.ZipFile(archive) as source:
        assert set(source.namelist()) == set(index["files"])
        for name, record in index["files"].items():
            data = source.read(name)
            assert len(data) == record["bytes"]
            assert hashlib.sha256(data).hexdigest() == record["sha256"], name

        def read(name):
            return json.loads(source.read(name))

        routes = read("attempt5/route-inventory.json")
        actual = json.loads((consumer / "evidence/surface-inventory.json").read_text())
        keys = ("profile", "mode", "topology")
        assert {tuple(r[k] for k in keys) for r in routes["routes"]} == {
            tuple(r[k] for k in keys) for r in actual["routes"]
        }
        assert routes["status"] == "passed" and not routes["blockers"]
        included = [r for r in routes["routes"] if r["profile"] != "aws-s3"]
        assert len(included) == 26
        for route in routes["routes"]:
            if route["profile"] == "aws-s3":
                assert route["status"] == "excluded-from-delivery"
                continue
            assert route["status"] == "passed-with-verified-reuse"
            assert route["currentValidation"]["runtimeConfigAndFullPlanUnchanged"]
            assert route["proofs"]
            for proof in route["proofs"]:
                name = posixpath.normpath("attempt5/" + proof["result"])
                assert name.startswith(("attempt3/", "attempt4/"))
                assert read(name)["status"] == "passed", name
        plans = read("attempt5/route-plans/results.json")
        assert len(plans) == 24
        for plan in plans:
            assert plan["identicalConfig"] and plan["identicalPlan"]
            assert plan["oldFingerprint"] == plan["currentFingerprint"]
            name = f"attempt5/route-plans/{plan['fixture']}-{plan['mode']}.json"
            config = RuntimeConfig.from_mapping(read(name)["runtimeConfig"])
            assert RuntimeConfig.from_mapping(config.to_dict()).fingerprint == config.fingerprint
        collector = read("attempt5/collector/full-mapping-probe.json")
        assert collector["outcome"].startswith("PASS:")
        assert collector["coreAppendSurvivedBackendRestarts"]
        assert {record["mode"] for record in collector["durability"]} == {"gateway", "sidecar"}
        for record in collector["durability"]:
            assert record["allAcknowledgedSurvivedSIGKILL"]
            assert set(record["acknowledged"]) == set(record["recoveredPublicNames"])
            assert not set(record["rejected503"]) & set(record["recoveredPublicNames"])
        for mode in ("gateway", "sidecar"):
            assert routes["collector"][mode] == "passed"
            pages = read(f"attempt5/collector/{mode}-core-one-row-pagination.json")
            assert len(pages) == 2
            assert all(page["terminated"] and page["sameIdentityDistinctContent"] for page in pages)
        # The original inventory assertion failure remains visible as historical
        # release-provenance evidence. It is never counted as a passed test.
        original = ET.fromstring(source.read("attempt5/released-contracts.xml"))
        assert len(list(original.iter("failure"))) == 1
        measured_plugins = {}
        for name in ("config-artifact", "cost", "observability", "usage-clickhouse-final"):
            root = ET.fromstring(source.read(f"shared-lock/results/{name}.xml"))
            cases = list(root.iter("testcase"))
            assert cases
            assert not any(list(root.iter(status)) for status in ("failure", "error", "skipped"))
            measured_plugins[name] = len(cases)
        usage = ET.fromstring(source.read("shared-lock/results/usage.xml"))
        usage_cases = list(usage.iter("testcase"))
        failed = [case for case in usage_cases if case.find("failure") is not None]
        assert len(usage_cases) == 45 and len(failed) == 1
        assert not any(list(usage.iter(status)) for status in ("error", "skipped"))
        assert "ACCESS_DENIED" in failed[0].find("failure").get("message")
        retry = ET.fromstring(source.read("shared-lock/results/usage-clickhouse-final.xml"))
        retry_cases = list(retry.iter("testcase"))
        assert len(retry_cases) == 1 and retry_cases[0].get("name") == failed[0].get("name")
        measured_plugins["usage-original-passed"] = len(usage_cases) - len(failed)
    result = {
        "status": "passed",
        "scope": index["scope"],
        "sources": index["sources"],
        "verifiedSnapshotFiles": len(index["files"]),
        "acceptedLocalRoutes": len(included),
        "excludedAWSRoutes": 2,
        "currentCoreRoundTrips": len(plans),
        "collectorModes": 2,
        "historicalPluginTests": measured_plugins,
        "historicalInventoryFailuresRetained": 1,
        "historicalUsageFixtureFailureRetained": 1,
        "usageRecovery": "Exact failed ClickHouse test passed after disposable fixture grants",
    }
    (consumer / "evidence/scoped-evidence-verification.json").write_text(
        json.dumps(result, indent=2) + "\n"
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
