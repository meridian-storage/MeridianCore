# SPDX-License-Identifier: Apache-2.0
"""Check installed public artifact bytes and record exact host/closure provenance."""

import hashlib
import io
import json
import sys
import tarfile
import urllib.request
import zipfile
from importlib.metadata import distribution
from pathlib import Path


def download(url, expected):
    assert url.startswith(("https://files.pythonhosted.org/", "https://registry.npmjs.org/"))
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    assert hashlib.sha256(data).hexdigest() == expected, f"public archive drift: {url}"
    return data


def main():
    consumer = Path(sys.argv[1]).resolve()
    fixture = Path(__file__).parent
    pins = dict(
        line.split("==")
        for line in (fixture / "requirements.txt").read_text().splitlines()
        if line and not line.startswith("#")
    )
    artifacts = json.loads((fixture / "public-artifacts.json").read_text())
    results = []
    for artifact in artifacts:
        data = download(artifact["url"], artifact["sha256"])
        checked = 0
        if artifact["package"].startswith("meridian-"):
            package = distribution(artifact["package"])
            assert package.version == pins[artifact["package"]] == artifact["version"]
            assert "site-packages" in package.locate_file("").parts
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                for name in archive.namelist():
                    if name.endswith("/") or name.endswith(".dist-info/RECORD"):
                        continue
                    assert package.locate_file(name).read_bytes() == archive.read(name), name
                    checked += 1
        else:
            installed = consumer / "node_modules/@zephytiju/meridian-storage-constructs"
            with tarfile.open(fileobj=io.BytesIO(data)) as archive:
                for member in archive.getmembers():
                    if not member.isfile():
                        continue
                    name = Path(*Path(member.name).parts[1:])
                    assert ".." not in name.parts
                    assert (installed / name).read_bytes() == archive.extractfile(member).read(), (
                        name
                    )
                    checked += 1
        results.append({**artifact, "installedFilesMatch": checked})
    assert len(results) == 19, "all 18 Python distributions and npm package are required"
    (consumer / "evidence/public-artifacts.json").write_text(json.dumps(results, indent=2) + "\n")
    print(f"Verified registry bytes for {len(results)} public packages")


if __name__ == "__main__":
    main()
