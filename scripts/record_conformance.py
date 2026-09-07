#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Record installed artifacts and independently queried disposable Engine facts."""

from __future__ import annotations

import json
import os
import platform
from importlib import metadata
from pathlib import Path


def main() -> None:
    import psycopg

    with psycopg.connect(os.environ["MERIDIAN_CORE_TEST_DSN"]) as connection:
        server_version = connection.execute("SHOW server_version").fetchone()[0]
        postgis = connection.execute("SELECT postgis_full_version()").fetchone()[0]
        standbys = connection.execute(
            "SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming'"
        ).fetchone()[0]
    record = {
        "profile": os.environ["MERIDIAN_CORE_TEST_PROFILE"],
        "selectedImage": os.environ["MERIDIAN_CORE_SELECTED_IMAGE"],
        "imageRepoDigests": json.loads(os.environ["MERIDIAN_CORE_IMAGE_DIGESTS"]),
        "observedServerVersion": server_version,
        "observedPostGIS": postgis,
        "observedStreamingStandbys": standbys,
        "python": platform.python_version(),
        "installedDistributions": {
            d.metadata["Name"]: d.version
            for d in sorted(metadata.distributions(), key=lambda d: d.metadata["Name"])
        },
    }
    output = Path("conformance-evidence")
    output.mkdir(exist_ok=True)
    (output / "environment.json").write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")
    print(json.dumps(record, sort_keys=True))


if __name__ == "__main__":
    main()
