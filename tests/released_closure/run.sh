#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
set -euo pipefail

fixture="$(cd "$(dirname "$0")" && pwd)"
consumer="$(mkdir -p "$1" && cd "$1" && pwd)"
: "${MERIDIAN_POSTGRESQL_TEST_DSN:?A disposable PostgreSQL DSN is required}"
: "${MERIDIAN_POSTGRESQL_ENGINE_VERSION:?The selected engine provenance is required}"
: "${ENGINE_IMAGE:?The exact digest-pinned PostgreSQL image is required}"

mkdir -p "$consumer/evidence" "$consumer/legacy"
cp "$fixture/package.json" "$fixture/package-lock.json" "$consumer/"
cp "$fixture/legacy/package.json" "$fixture/legacy/package-lock.json" "$consumer/legacy/"
"${PYTHON:-python3}" -m venv "$consumer/runtime"
python="$consumer/runtime/bin/python"
"$python" -m pip install --disable-pip-version-check --require-hashes \
  -r "$fixture/public-requirements.lock" --report "$consumer/evidence/python-install.json"
"$python" -m pip check > "$consumer/evidence/pip-check.txt"
# The Python.org macOS runtime may have no system CA bundle. Use the CA bundle
# in the selected public dependency lock and keep certificate verification on.
export SSL_CERT_FILE="$("$python" -c 'import certifi; print(certifi.where())')"
npm ci --ignore-scripts --prefix "$consumer"
npm ci --ignore-scripts --prefix "$consumer/legacy"
"$python" "$fixture/prepare.py" "$consumer"
"$python" "$fixture/verify_artifacts.py" "$consumer"

export CONSTRUCTS_MODULE="$consumer/node_modules/@zephytiju/meridian-storage-constructs/dist/index.js"
export LEGACY_CONSTRUCTS_MODULE="$consumer/legacy/node_modules/@zephytiju/meridian-storage-constructs/dist/index.js"
export MERIDIAN_RUNTIME_REQUIREMENTS="$fixture/requirements.txt"
export MERIDIAN_POSTGRESQL_PACKAGE_VERSION=2.3.1
export MERIDIAN_ACCEPTANCE_EVIDENCE_DIR="$consumer/evidence/contracts"
mkdir -p "$MERIDIAN_ACCEPTANCE_EVIDENCE_DIR"
cp "$fixture/constructs_contracts.mjs" "$consumer/"
"$python" "$fixture/export_postgresql_contracts.py" "$consumer/evidence/postgresql-contracts.json"
cd "$consumer/evidence"
node "$consumer/constructs_contracts.mjs" postgresql-contracts.json original-sixteen.json
cd "$consumer"
node "$fixture/inventory.mjs" "$fixture/gate_inventory.json" "$consumer/evidence/surface-inventory.json"
"$python" "$fixture/check_evidence.py" "$consumer"
"$python" -m pytest tests/integration/released-capabilities -q \
  --junitxml="$consumer/evidence/contracts.xml"
"$python" "$fixture/selection_contracts.py" "$consumer"
"$python" -m pytest tests/integration/clickhouse/test_layout_contract.py \
  tests/integration/clickhouse/test_configured_append.py -q \
  --junitxml="$consumer/evidence/append-layouts.xml"
"$python" -m pytest tests/integration/jobs -q \
  --junitxml="$consumer/evidence/projection-jobs.xml"
docker image inspect "$ENGINE_IMAGE" --format '{{json .}}' > "$consumer/evidence/engine-image.json"
"$python" -m pip freeze > "$consumer/evidence/installed-requirements.txt"
cp "$fixture/public-requirements.lock" "$fixture/package-lock.json" "$consumer/evidence/"
cp -R "$fixture/evidence" "$consumer/evidence/upstream"
"$python" "$fixture/check_results.py" "$consumer/evidence"
