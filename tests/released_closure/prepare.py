# SPDX-License-Identifier: Apache-2.0
"""Acquire immutable public test fixtures; never install a sibling source tree."""

import hashlib
import io
import json
import sys
import tarfile
import urllib.request
from pathlib import Path

SOURCE = "266eb37bb22ffc429225726aecbddade06115569"
SOURCE_SHA256 = "c47ddff2a25bca401ffb15d455de3771e66f8c716c035a233b7db6679ad63877"
SOURCE_URL = f"https://codeload.github.com/zephytiju/MeridianConstructs/tar.gz/{SOURCE}"


def main():
    consumer = Path(sys.argv[1]).resolve()
    evidence = consumer / "evidence"
    evidence.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(SOURCE_URL, timeout=60) as response:
        archive = response.read()
    assert hashlib.sha256(archive).hexdigest() == SOURCE_SHA256, "public fixture archive drift"
    records = {}
    with tarfile.open(fileobj=io.BytesIO(archive)) as source:
        for member in source.getmembers():
            if not member.isfile():
                continue
            relative = Path(*Path(member.name).parts[1:])
            if not relative.parts or relative.parts[0] not in {"tests", "contracts", "LICENSE"}:
                continue
            assert ".." not in relative.parts
            data = source.extractfile(member).read()
            target = consumer / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            records[str(relative)] = hashlib.sha256(data).hexdigest()
    # The upstream historical inventory predates ClickHouse 1.1.3. Update only
    # the two distribution coordinates, keeping every contract/fingerprint byte
    # semantically equal. The unchanged upstream test now checks the selected lock.
    inventory = consumer / "tests/fixtures/released-capabilities/inventory.json"
    recorded = json.loads(inventory.read_text())
    changes = []
    for profile in ("clickhouse-standalone", "clickhouse-replicated"):
        entry = recorded["profiles"][profile]
        assert entry["package"] == "meridian-storage-clickhouse"
        assert entry["version"] == "1.1.1"
        entry["version"] = "1.1.3"
        changes.append(
            {"profile": profile, "field": "version", "before": "1.1.1", "after": "1.1.3"}
        )
    inventory.write_text(json.dumps(recorded, indent=2, sort_keys=True) + "\n")
    (evidence / "fixture-provenance.json").write_text(
        json.dumps(
            {
                "source": SOURCE,
                "url": SOURCE_URL,
                "sha256": SOURCE_SHA256,
                "files": records,
                "adaptations": changes,
                "selectedInventorySha256": hashlib.sha256(inventory.read_bytes()).hexdigest(),
                "scope": "Public test fixtures only; product imports use installed artifacts.",
            },
            indent=2,
        )
        + "\n"
    )
    print(f"Verified {len(records)} public fixture files; exactly two provenance fields updated")


if __name__ == "__main__":
    main()
