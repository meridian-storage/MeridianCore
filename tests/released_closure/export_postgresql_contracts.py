# SPDX-License-Identifier: Apache-2.0
"""Export actual installed public contracts for the Constructs acceptance probe."""

import json
import sys
from importlib.metadata import distributions
from pathlib import Path

from meridian_storage.adapters.postgresql import PostgreSQLSettings, SchemaCompiler
from meridian_storage.adapters.postgresql.descriptor import manifest
from meridian_storage.adapters.postgresql.schema import ResourceLayout
from meridian_storage.evidence import EvidenceCatalogProvider
from meridian_storage.semantics import StructuredCatalogProvider

from meridian_storage import ResourceRef
from meridian_storage.registry import (
    CapabilityRequirement,
    NamespaceDefinition,
    ResourceBundle,
    ResourceDefinition,
    SchemaDefinition,
    SchemaRef,
)
from meridian_storage.spi import capability_violations


def main():
    providers = [StructuredCatalogProvider(), EvidenceCatalogProvider()]
    schemas, resources = [], []
    for catalog in ("structured", "evidence"):
        schema = SchemaDefinition(
            SchemaRef(catalog, "closure", "records", "1.0.0"),
            {
                "fields": [
                    {"name": "id", "logicalType": "string", "nullable": False, "mutable": False},
                    {"name": "value", "logicalType": "string", "nullable": False, "mutable": True},
                ],
                "identity": ["id"],
            },
        )
        schemas.append(schema)
        resources.append(
            ResourceDefinition(
                ResourceRef(catalog, "closure", "records"),
                "relational" if catalog == "structured" else "append-only-evidence",
                schema=schema.ref,
            )
        )
    bundle = ResourceBundle(
        "closure.schemas",
        "1.0.0",
        "1.0.0",
        namespaces=tuple(NamespaceDefinition(p.catalog_name, "closure") for p in providers),
        schemas=tuple(schemas),
        resources=tuple(resources),
    )
    cases = [
        ("read-control", "structured", "meridian.structured.get", "1.0.0", ()),
        ("put-v2", "structured", "meridian.structured.put", "2.0.0", ()),
        ("atomic-evidence", "evidence", "meridian.evidence.append", "1.0.0", ("atomic-evidence",)),
        ("unsupported-version-control", "structured", "meridian.structured.put", "99.0.0", ()),
    ]
    profiles = []
    for profile in ("postgresql-postgis-local-single-primary", "postgresql-postgis-cluster"):
        selected = manifest(profile, "17-postgis-3.5")
        layouts = [
            {
                "ref": resource.ref.canonical,
                "table": resource.ref.catalog + "_records",
                "profile": resource.profile,
                "schemaFingerprint": schema.fingerprint,
                "resourceFingerprint": resource.fingerprint,
                "fields": [
                    {**field, "column": field["name"]} for field in schema.definition["fields"]
                ],
                "identity": ["id"],
                "indexes": [],
                "relation": None,
            }
            for resource, schema in zip(resources, schemas, strict=True)
        ]
        physical = SchemaCompiler(
            PostgreSQLSettings(
                physical_schema="closure",
                engine_profile=profile,
                scope_keys=(),
                resources={
                    layout["ref"]: ResourceLayout.from_mapping(layout, "fixture")
                    for layout in layouts
                },
            )
        ).compile()
        profiles.append(
            {
                "id": profile,
                "engineVersion": selected.engine_version,
                "manifest": selected.to_dict(),
                "manifestFingerprint": selected.fingerprint,
                "physicalFingerprint": physical.physical_fingerprint,
                "layouts": layouts,
                "cases": [
                    {
                        "name": name,
                        "catalog": catalog,
                        "contract": contract,
                        "version": version,
                        "guarantees": list(guarantees),
                        "coreAccepts": not capability_violations(
                            selected,
                            (CapabilityRequirement(contract, version, guarantees=guarantees),),
                        ),
                    }
                    for name, catalog, contract, version, guarantees in cases
                ],
            }
        )
    record = {
        "classification": "released-artifact contract metadata; no real-engine claim",
        "packages": {
            d.metadata["Name"]: d.version
            for d in distributions()
            if d.metadata["Name"].startswith("meridian-")
        },
        "catalogs": [
            {
                "name": p.catalog_name,
                "package": p.manifest().package_name,
                "contract": p.manifest().catalog_contract_version,
                "requiredFingerprint": p.manifest().fingerprint,
            }
            for p in providers
        ],
        "schemaProviders": [
            {
                "id": bundle.provider_id,
                "package": "core-closure-conformance-fixture",
                "contract": "1.0.0",
                "requiredFingerprint": bundle.fingerprint,
            }
        ],
        "resources": {
            r.ref.catalog: {"selector": r.ref.to_dict(), "fingerprint": r.fingerprint}
            for r in resources
        },
        "profiles": profiles,
    }
    Path(sys.argv[1]).write_text(json.dumps(record, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
