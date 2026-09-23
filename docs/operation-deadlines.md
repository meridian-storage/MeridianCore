# Operation deadline and commit outcome contract (M4 offline repair)

`OperationContext` accepts the existing timezone-aware `deadline` and the additive
`monotonic_deadline` (absolute local monotonic seconds) and `cancellation`
(`threading.Event`) fields. Convert an external remaining duration once at host
admission. Pass the inherited absolute monotonic deadline, including earlier IAM
read time, through the destination operation. Never send a monotonic timestamp
between machines. The M4 caller owns its 2-second default / 5-second maximum;
Meridian does not reset that outer budget or create a fresh authorization.

Wall deadlines are converted once on context construction. Generated request IDs
and Core's narrower per-operation context preserve the admitted absolute
monotonic limit, including a limit originally converted from UTC. Core captures
the client timeout cap before constructing the effective context, intersects it
with the inherited absolute bound, and checks the resulting budget again before
returning. Time spent constructing a context or paused by the scheduler is never
added back by converting a stale remaining duration into a new deadline.
An explicit dataclass replacement with a different wall deadline creates a new
context; callers must preserve the inherited monotonic deadline when narrowing.
Cancellation and expiry close Core transaction and operation admission. Core
rechecks the captured transaction context before commit. Nested same-Binding
transactions still join the same session, and cross-Binding work remains rejected.

The AdapterSession SPI retains begin/execute/commit/rollback/close signatures.
Deadline-capable adapters capture current_context at begin and narrow with each
ExecutionRequest.context, never extend it. Merely implementing the SPI does not
advertise bounded I/O. Consumers require the selected Binding's transaction
capabilities `inherited-operation-deadline`, `bounded-client-io` and
`typed-commit-outcome`; this repair declares them for the owner PostgreSQL adapter.
These are implementation declarations, not a native test certificate.

`Transaction.commit_state` and `CommitOutcomeError.commit_state` use `CommitState`:

- `KNOWN_NOT_COMMITTED`: no commit was dispatched (or an aborted transaction was
  discarded). This is not a claim that a rollback acknowledgement was observed.
- `KNOWN_COMMITTED`: a successful commit acknowledgement was received. A late
  acknowledgement or subsequent cleanup failure still raises and denies success.
- `UNKNOWN_COMMIT`: commit was attempted but acknowledgement is unavailable.
  Transport loss, cancellation, deadline expiry and interrupts can cause it.

CommitOutcomeError is a nonretryable MeridianTimeoutError subtype for compatible
budget handling, with a `commitState` field in its redacted error envelope. Never
classify it by timeout category alone or automatically retry it. After unknown or
late commit, stop effects and reconcile the domain operation ID/request digest
under fresh authorization using the domain's durable same-Binding ledger. Missing
or pending ledger state is not proof of non-occurrence. Core does not synthesize
cross-system atomicity or domain reconciliation results. Cleanup cannot replace
an existing body failure. The transaction outcome remains inspectable separately.
