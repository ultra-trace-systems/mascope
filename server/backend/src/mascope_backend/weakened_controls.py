"""
Test-only switches that turn two security controls off, for a weakened demo stack.

The security suite's weakened-target guard (``security/pentest/mutations/``)
proves a control can fail by switching off what it checks and requiring it to
report. Two controls have nothing a configuration could switch off: the
backend's rate limiter (:mod:`mascope_backend.api.lib.rate_limit`, pentest
``RL-01`` to ``RL-03``) and the write-origin guard (``origin_guard`` in
:mod:`mascope_backend.app.fast`, ``CSRF-01``). These switches are that lever,
set through the environment by the guard's compose override.

A switch takes effect only on a demo stack: beside it, the environment must
carry the demo stack's marker, the variable the public demo seed refuses to
run without (:mod:`mascope_backend.db.scripts.seed_demo`). A real deployment
never sets the marker, so a switch copied into one is ignored, and the start-up
log says so. On a demo stack, the start-up log says which controls are off.

Read from the environment on every check rather than once, so the process can
never act on a value it did not log: the environment does not change under a
running process.
"""

import os

from mascope_backend.runtime import runtime


#: The demo stack's marker (docker-compose.demo.yaml, ``mascope demo``), read
#: as the demo seed reads it.
DEMO_STACK_MARKER = "MASCOPE_ALLOW_DEMO_SEED"

#: Switches the backend's per-address, per-account and per-user rate limits off.
RATE_LIMIT_OFF = "MASCOPE_PENTEST_RATE_LIMIT_OFF"

#: Switches the refusal of a state-changing request from a foreign origin off.
ORIGIN_GUARD_OFF = "MASCOPE_PENTEST_ORIGIN_GUARD_OFF"

#: Each switch and the control it turns off, as the start-up log names it.
SWITCHES = {
    RATE_LIMIT_OFF: "the rate limiter",
    ORIGIN_GUARD_OFF: "the write-origin guard",
}

#: The values the demo seed reads as set (``_demo_seed_allowed``).
_TRUTHY = frozenset({"1", "true", "yes", "on"})


def _is_set(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in _TRUTHY


def on_a_demo_stack() -> bool:
    """Whether the environment carries the demo stack's marker."""
    return _is_set(DEMO_STACK_MARKER)


def switched_off(switch: str) -> bool:
    """
    Whether ``switch`` turns its control off in this process.

    :param switch: :data:`RATE_LIMIT_OFF` or :data:`ORIGIN_GUARD_OFF`.
    :return: True only when the switch is set on a demo stack; a switch set
        anywhere else leaves its control on.
    """
    return _is_set(switch) and on_a_demo_stack()


def announce() -> None:
    """
    Log, at start, each switch that is set, and whether it took effect.

    A warning for each control a switch turned off, so the stack's log says it
    is weakened; an error for a switch set without the marker, which is a
    real deployment's configuration mistake and changes nothing.
    """
    for switch, control in SWITCHES.items():
        if not _is_set(switch):
            continue
        if on_a_demo_stack():
            runtime.logger.warning(
                f"WEAKENED STACK: {control} is off ({switch} on a demo stack). "
                "For the security suite's weakened target only."
            )
        else:
            runtime.logger.error(
                f"{switch} is set but this is not a demo stack "
                f"({DEMO_STACK_MARKER} is not set): ignored, {control} stays on"
            )
