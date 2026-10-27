"""The deployment's reference-licence gate, read where Stage A and the API need it.

One home for the gate, so the run that matches against the mirror and the route
that says which sources it may match read the same setting the same way.
"""

from mascope_backend.runtime import runtime


def reference_license_gate() -> list[str] | None:
    """The reference licences Stage A is allowed to match against, or None.

    The reference mirror carries a per-record licence from ingest through to
    results precisely so a deployment can decline to match against sources
    whose terms it has not accepted (HMDB's adapter, for instance, asks that
    commercial terms be verified first). Nothing wired that to a setting until
    now: the call site passed nothing, so every active source was matched.

    ``None`` - the default, and what an env toml that says nothing yields -
    keeps exactly that behaviour. Gating is opt-in because narrowing it shrinks
    what Stage A can find with no trace in the UI: an operator must choose it,
    not inherit it.

    Read per call rather than captured at import so a restart is enough to pick
    up a change, and so tests can set it.

    :return: The allowlist (sorted, non-empty) or None for no gating.
    """
    return getattr(runtime.config, "reference_licenses", None)
