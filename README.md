<!-- SPDX-License-Identifier: Apache-2.0 -->

# Meridian Storage Core

Meridian Storage Core is the Python 3.12+ in-process runtime, binding resolver,
operation-context implementation, transaction boundary, normalized error model,
and adapter SPI for Meridian V1.

This repository owns exactly one distribution: `meridian-storage-core`. It does
not provision storage engines, expose a network service, own application domain
models, or contain a concrete adapter. NativeQuery is intentionally outside the
V1 implementation.

The project is under active initial implementation. See [LICENSE](LICENSE) and
[NOTICE](NOTICE) for licensing information.

