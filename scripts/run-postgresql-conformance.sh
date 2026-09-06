#!/usr/bin/env bash
# SPDX-License-Identifier: Apache-2.0
# Create only disposable loopback-bound databases, then run the supplied test command.
set -euo pipefail

profile="${1:?usage: run-postgresql-conformance.sh local|cluster command...}"
shift
case "$profile" in local|cluster) ;; *) exit 2 ;; esac
image="${MERIDIAN_CORE_POSTGRES_IMAGE:-postgis/postgis:16-3.4}"
prefix="meridian-core-${GITHUB_RUN_ID:-local}-$$"
network="${prefix}-network"
primary="${prefix}-primary"

cleanup() {
  for suffix in standby1 standby2 primary; do
    docker rm -fv "${prefix}-${suffix}" >/dev/null 2>&1 || true
  done
  for suffix in standby1 standby2; do
    docker volume rm "${prefix}-${suffix}" >/dev/null 2>&1 || true
  done
  docker network rm "$network" >/dev/null 2>&1 || true
}
trap cleanup EXIT

ready() {
  for ((attempt=0; attempt<60; attempt++)); do
    if docker exec "$1" pg_isready -h 127.0.0.1 -U meridian -d meridian >/dev/null 2>&1; then
      return
    fi
    sleep 1
  done
  echo "Disposable PostgreSQL did not become ready: $1" >&2
  exit 1
}

docker network create "$network" >/dev/null
docker run --detach --name "$primary" --network "$network" \
  --publish 127.0.0.1::5432 --env POSTGRES_USER=meridian \
  --env POSTGRES_PASSWORD=meridian --env POSTGRES_DB=meridian \
  "$image" -c 'listen_addresses=*' -c wal_level=replica \
  -c max_wal_senders=10 -c max_replication_slots=10 -c hot_standby=on >/dev/null
ready "$primary"

if [[ "$profile" == cluster ]]; then
  docker exec "$primary" sh -c \
    'echo "host replication meridian 0.0.0.0/0 scram-sha-256" >> "$PGDATA/pg_hba.conf"'
  docker exec "$primary" psql -U meridian -d meridian -c 'SELECT pg_reload_conf()' >/dev/null
  for suffix in standby1 standby2; do
    volume="${prefix}-${suffix}"
    docker volume create "$volume" >/dev/null
    docker run --rm --user postgres --network "$network" --env PGPASSWORD=meridian \
      --volume "$volume:/var/lib/postgresql/data" --entrypoint pg_basebackup "$image" \
      -h "$primary" -U meridian -D /var/lib/postgresql/data -Fp -Xs -R -C -S "$suffix" >/dev/null
    docker run --detach --name "${prefix}-${suffix}" --network "$network" \
      --volume "$volume:/var/lib/postgresql/data" "$image" \
      -c 'listen_addresses=*' -c hot_standby=on >/dev/null
    ready "${prefix}-${suffix}"
  done
  for ((attempt=0; attempt<60; attempt++)); do
    replicas="$(docker exec "$primary" psql -U meridian -d meridian -Atc \
      "SELECT count(*) FROM pg_stat_replication WHERE state = 'streaming'")"
    [[ "$replicas" == 2 ]] && break
    sleep 1
  done
  [[ "$replicas" == 2 ]]
  export MERIDIAN_CORE_TEST_PROFILE=postgresql-postgis-cluster
else
  export MERIDIAN_CORE_TEST_PROFILE=postgresql-postgis-local-single-primary
fi

port="$(docker port "$primary" 5432/tcp | awk -F: 'NR == 1 {print $NF}')"
export MERIDIAN_CORE_TEST_DSN="postgresql://meridian:meridian@127.0.0.1:${port}/meridian"
echo "Running Core conformance on ${MERIDIAN_CORE_TEST_PROFILE}"
"$@"
