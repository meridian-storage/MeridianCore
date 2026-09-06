# SPDX-License-Identifier: Apache-2.0
"""Disposable real PostgreSQL fixtures, using installed release artifacts only."""

import os
import uuid
from copy import deepcopy
from types import SimpleNamespace

import pytest

from meridian_storage import Meridian, ResourceRef, RuntimeConfig
from meridian_storage.registry import (
    NamespaceDefinition,
    ResourceBundle,
    ResourceDefinition,
    SchemaDefinition,
    SchemaRef,
)
from meridian_storage.spi import SecretValue


@pytest.fixture
def database():
    dsn = os.environ.get("MERIDIAN_CORE_TEST_DSN")
    if not dsn:
        pytest.skip("MERIDIAN_CORE_TEST_DSN is required for real-engine conformance")
    import psycopg
    from meridian_storage.adapters.postgresql._settings import PostgreSQLSettings
    from meridian_storage.adapters.postgresql.descriptor import manifest
    from meridian_storage.adapters.postgresql.migration import MigrationExecutor
    from meridian_storage.adapters.postgresql.schema import SchemaCompiler
    from meridian_storage.evidence import EvidenceCatalogProvider
    from meridian_storage.semantics import StructuredCatalogProvider
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    namespace = "core_" + uuid.uuid4().hex[:16]
    profile = os.environ.get(
        "MERIDIAN_CORE_TEST_PROFILE", "postgresql-postgis-local-single-primary"
    )
    engine_version = "16-postgis-3.4"
    fields = [
        {"name": "id", "logicalType": "string", "nullable": False, "mutable": False},
        {"name": "value", "logicalType": "string", "nullable": False, "mutable": True},
    ]
    schemas, resources, layouts = [], [], []
    for catalog, name in (
        ("structured", "records"),
        ("evidence", "events"),
        ("evidence", "remote"),
    ):
        schema = SchemaDefinition(
            SchemaRef(catalog, "coretest", name, "1.0.0"), {"fields": fields, "identity": ["id"]}
        )
        resource = ResourceDefinition(
            ResourceRef(catalog, "coretest", name),
            "relational" if catalog == "structured" else "append-only-evidence",
            schema=schema.ref,
            required_scope=("workspace",),
        )
        schemas.append(schema)
        resources.append(resource)
        layouts.append(
            {
                "ref": resource.ref.canonical,
                "table": name,
                "profile": resource.profile,
                "schemaFingerprint": schema.fingerprint,
                "resourceFingerprint": resource.fingerprint,
                "fields": [{**field, "column": field["name"]} for field in fields],
                "identity": ["id"],
                "indexes": [],
                "relation": None,
            }
        )
    bundle = ResourceBundle(
        "coretest.schemas",
        "1.0.0",
        "1.0.0",
        namespaces=tuple(NamespaceDefinition(c, "coretest") for c in ("structured", "evidence")),
        schemas=tuple(schemas),
        resources=tuple(resources),
    )

    class Provider:
        provider_id = bundle.provider_id
        provider_contract_version = "1.0.0"

        def load(self):
            return bundle

    parts = conninfo_to_dict(dsn)
    user = parts.pop("user", "meridian")
    password = parts.pop("password", "meridian")

    class Secrets:
        def resolve(self, reference):
            return SecretValue((user if reference.reference == "identity" else password).encode())

    catalogs = (StructuredCatalogProvider(), EvidenceCatalogProvider())
    binding = {
        "id": "primary",
        "adapterId": "postgresql",
        "adapterContract": "1.0.0",
        "engineProfile": profile,
        "engineVersion": engine_version,
        "endpoint": make_conninfo("", **parts),
        "serviceRef": None,
        "physicalNamespace": namespace,
        "tls": {
            "mode": "disabled",
            "serverName": None,
            "caRef": None,
            "clientCertificateRef": None,
        },
        "identityRef": {"provider": "coretest", "reference": "identity"},
        "secretRef": {"provider": "coretest", "reference": "credential"},
        "client": {
            "minSize": 1,
            "maxSize": 4,
            "acquireTimeoutMs": 10000,
            "idleTimeoutMs": 30000,
            "operationTimeoutMs": 10000,
            "maxResultBytes": 1048576,
            "iteratorLifetimeMs": 10000,
        },
        "requiredCapabilityFingerprint": manifest(profile, engine_version).fingerprint,
        "requiredPhysicalFingerprint": None,
        "compatibilityPins": {},
        "settings": {
            "formatVersion": "meridian.postgresql.settings.v1",
            "scopeKeys": ["workspace"],
            "topology": {"expectedStandbys": 2 if profile.endswith("cluster") else 0},
            "resources": layouts[:2],
        },
        "extensions": {},
    }
    secondary = deepcopy(binding)
    secondary.update(id="secondary", physicalNamespace=namespace + "_other")
    secondary["settings"]["resources"] = layouts[2:]
    bindings = [binding, secondary]
    config = {
        "formatVersion": "meridian-config.v1",
        "profile": "core-conformance",
        "catalogs": {
            "providers": [
                {
                    "name": p.catalog_name,
                    "package": p.manifest().package_name,
                    "contract": "1.x",
                    "requiredFingerprint": p.manifest().fingerprint,
                }
                for p in catalogs
            ],
            "extensions": {},
        },
        "schemas": {
            "providers": [
                {
                    "id": bundle.provider_id,
                    "package": "coretest-fixture",
                    "contract": "1.x",
                    "requiredFingerprint": bundle.fingerprint,
                }
            ],
            "live": {"enabled": False, "required": False, "providerId": None},
            "extensions": {},
        },
        "resources": {
            "pins": [
                {
                    "ref": r.ref.to_dict(),
                    "providerId": bundle.provider_id,
                    "requiredFingerprint": r.fingerprint,
                }
                for r in resources
            ],
            "extensions": {},
        },
        "bindings": bindings,
        "placements": [
            {
                "id": b["id"],
                "bindingId": b["id"],
                "extensions": {},
                "selector": {
                    "resources": [
                        ResourceRef.parse(r["ref"]).to_dict() for r in b["settings"]["resources"]
                    ],
                    "catalog": None,
                    "labels": {},
                },
            }
            for b in bindings
        ],
        "validation": {
            "strict": True,
            "requirePhysicalFingerprints": True,
            "defaultOperationTimeoutMs": 10000,
            "idempotencyCacheEntries": 64,
            "retry": {"maxAttempts": 1, "baseDelayMs": 0, "maxDelayMs": 0, "jitterRatio": 0},
        },
        "extensions": {},
    }
    settings_and_plans = []
    for raw in bindings:
        from meridian_storage.runtime import BindingConfig

        settings = PostgreSQLSettings.from_binding(
            BindingConfig.from_mapping(raw, "fixture.binding")
        )
        plan = SchemaCompiler(settings).compile()
        raw["requiredPhysicalFingerprint"] = plan.physical_fingerprint
        settings_and_plans.append((settings, plan))
    runtime = None
    try:
        # Schema migration belongs to the disposable fixture, never Core startup.
        with psycopg.connect(dsn) as connection:
            for settings, plan in settings_and_plans:
                MigrationExecutor(settings).apply(connection, plan)
        runtime = Meridian.from_config(
            RuntimeConfig.from_mapping(config),
            schema_providers=(Provider(),),
            secret_resolver=Secrets(),
        )
        runtime.start()
        calls, sessions = [], []
        for adapter in runtime._adapter_runtimes.values():
            original_open = adapter.open_session

            def observed_open(*, transactional, original_open=original_open):
                session = original_open(transactional=transactional)
                sessions.append(session)
                original_execute = session.execute

                def observed_execute(request):
                    calls.append((id(session), request))
                    return original_execute(request)

                session.execute = observed_execute
                return session

            adapter.open_session = observed_open

        def rows(table):
            with psycopg.connect(dsn) as connection:
                return connection.execute(
                    sql.SQL("SELECT id, value FROM {}.{} ORDER BY id").format(
                        sql.Identifier(namespace), sql.Identifier(table)
                    )
                ).fetchall()

        yield SimpleNamespace(
            runtime=runtime, rows=rows, calls=calls, sessions=sessions, profile=profile
        )
    finally:
        if runtime is not None:
            runtime.close()
        with psycopg.connect(dsn, autocommit=True) as connection:
            for raw in bindings:
                connection.execute(
                    sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(
                        sql.Identifier(raw["physicalNamespace"])
                    )
                )
