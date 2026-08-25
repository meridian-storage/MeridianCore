<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian V1 error model

Every public failure derives from `MeridianError` and carries:

- a stable `MERIDIAN_*` code and category;
- a safe message and retryability flag;
- optional Operation contract, logical Resource, request, and execution IDs;
- optional adapter ID and Capability fingerprint provenance; and
- an optional `SafeCause` containing only exception type and a safe code.

`to_dict()` returns the transport-neutral envelope. It never serializes a Python
traceback, arbitrary component exception text, endpoint, secret reference,
secret bytes, or physical mapping value. Adapter provenance is included only
after Core has resolved a logical Resource through a verified snapshot.

`CatalogNotFound` distinguishes an unregistered Catalog name from
`MERIDIAN_CATALOG_UNAVAILABLE`, which means a registered Catalog package is not
configured or installed. Configuration, discovery, contract, Capability,
placement, physical verification, lifecycle, scope, transaction, timeout,
result-limit, and idempotency failures likewise retain distinct stable codes.

Only `TransientError` is retryable by default. An adapter may mark another typed
error retryable when its contract permits, but Core still retries only a read or
an idempotent keyed mutation, within the effective deadline, and never inside a
transaction. Unknown component exceptions become non-retryable `InternalError`
values with a redacted `SafeCause`.

Applications should branch on `code` or `category`, not message text. Messages
are diagnostic and may become clearer in a compatible patch release.
