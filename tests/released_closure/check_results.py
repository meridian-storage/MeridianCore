# SPDX-License-Identifier: Apache-2.0
"""Fail the closure on missing, empty, failed, errored or skipped required suites."""

import hashlib
import json
import os
import platform
import sys
import xml.etree.ElementTree as ET
from pathlib import Path


def main():
    evidence = Path(sys.argv[1]).resolve()
    expected = {"contracts.xml": 4, "append-layouts.xml": 10, "projection-jobs.xml": 39}
    suites = []
    for name, count in expected.items():
        root = ET.parse(evidence / name).getroot()
        cases = list(root.iter("testcase"))
        assert len(cases) == count, (name, "missing or unexpected tests", len(cases), count)
        for case in cases:
            assert not any(
                case.find(status) is not None for status in ("failure", "error", "skipped")
            ), (
                name,
                case.attrib,
            )
        suites.append({"suite": name, "passed": len(cases), "failures": 0, "errors": 0, "skips": 0})
    for name, count in (("all-family-contracts.json", 1014), ("configured-limits.json", 1688)):
        result = json.loads((evidence / "contracts" / name).read_text())
        assert result["cases"] == count and result["mismatches"] == 0, name
    original = json.loads((evidence / "original-sixteen.json").read_text())
    assert len(original["results"]) == 16
    assert all(r["matchesReleasedContract"] for r in original["results"])
    selection = json.loads((evidence / "independent-selection.json").read_text())
    assert selection["cases"] == 252 and not selection["mismatches"]
    append = json.loads((evidence / "contracts/configured-append-parity.json").read_text())
    assert append["cases"] == 2988 and not append["mismatches"]
    configs = list(evidence.glob("*.config.json"))
    assert len(configs) == 12
    from meridian_storage.runtime.config import RuntimeConfig

    for path in configs:
        config = RuntimeConfig.from_mapping(json.loads(path.read_text()))
        assert RuntimeConfig.from_mapping(config.to_dict()).fingerprint == config.fingerprint
    hashes = {
        str(path.relative_to(evidence)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(evidence.rglob("*"))
        if path.is_file() and path.name != "acceptance.json"
    }
    result = {
        "status": "passed",
        "scope": "Fresh public contracts and actual PostgreSQL packaged-host processes",
        "ci": {
            name: os.environ.get(name)
            for name in (
                "GITHUB_REPOSITORY",
                "GITHUB_SHA",
                "GITHUB_WORKFLOW",
                "GITHUB_RUN_ID",
                "GITHUB_RUN_ATTEMPT",
                "GITHUB_REF",
            )
        },
        "suites": suites,
        "originalContractCases": 16,
        "originalCoreRoundTrips": len(configs),
        "independentSelectionCases": selection["cases"],
        "configuredAppendCases": append["cases"],
        "host": {
            "python": platform.python_version(),
            "platform": platform.platform(),
            "executableSha256": hashlib.sha256(Path(sys.executable).read_bytes()).hexdigest(),
            "packagedFiles": "surface-inventory.json",
        },
        "files": hashes,
        "operationalEvidence": "tests/released_closure/evidence/index.json",
    }
    (evidence / "acceptance.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"status": "passed", "suites": suites}))


if __name__ == "__main__":
    main()
