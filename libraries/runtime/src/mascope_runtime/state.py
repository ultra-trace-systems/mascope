"""
This file provides an API representing runtime state,

- `RuntimeJsonState` persisted to a `state.json` on disk
- `RuntimeTempState` persisted to an object in memory

The API uses setters and getters to provide a simple
API to the state:

Read the `mode` field from the json
 print(state.mode) # 'dev'

Update the `env` field in the json:
  state.env = "foo"

Overrides

Additionally, an 'override' API is exposed allowing
the CLI to temporarily override a variable. Each field
in the `state` object has two values: `active` and
`override`. The `active` value is persisted which the
`override` value is ephemeral, being cleared with
every time a CLI command is run.

When the CLI wants to temporarily override state, it can
use the `.override` method to do so. Since passing `None`
to this method will clear it, the CLI will thereby naturally
reset the override whenever no option is passed. This is
currently only used for overriding the `env` selected.

Production

In the production containers, a special `state.json`
is used. You can find this file committed in
`runtime/lib/state.prod.json`

Concurrency

The `state.json` is shared by every mascope process using the same
`MASCOPE_PATH`, and every CLI invocation writes it during startup
(the entrypoint clears the `env` override). Two commands starting the
same second - say a backup cron and a monitoring cron - therefore
read and write the same file concurrently. Writes go to a temp file
that is then `os.replace`d into place, so a reader observes either
the old file or the new one, never a truncated one. Reads retry
briefly and fall back to the defaults with a warning, so a file
corrupted by an older version or a crashed write cannot take down
the CLI.

Implementation note: both classes intercept every public attribute
access via `__setattr__`/`__getattr__`, so their own internals are
kept in underscore-prefixed attributes written with
`object.__setattr__` to avoid recursion. State is stored per
instance — two Runtime instances in one process (e.g. the CLI's own
plus a temporary one built for a compose invocation) must not share
or clobber each other's state.
"""

import copy
import json
import os
import time

from loguru import logger

from mascope_runtime.atomic import write_json


# Writes are atomic, so an unreadable file is corrupt rather than torn -
# but retry once in case some other writer is not going through this class.
_READ_RETRY_DELAY = 0.05

# How long to keep retrying a replace that loses a race with a concurrent
# reader. Only Windows fails this way (it refuses to replace a file another
# process holds open); POSIX renames straight over an open file. Readers hold
# the file for microseconds, so this budget is never close to exhausted.
_REPLACE_TIMEOUT = 5.0

default_state = {
    "env": {"active": "default", "override": None},
    "mode": {"active": "dev", "override": None},
    "modules": {
        "active": ["backend", "frontend"],
        "override": None,
    },
}


class RuntimeJsonState(object):
    """
    Persistent  runtime state

    This class represents the persistent runtime state,
    which is saved to `MASCOPE_PATH/runtime/state.json`.
    When instantiating the class, the JSON will be
    created if it doesn't exist. Every call to the
    attribute API updates the `state.json` file
    immediately.
    """

    def __init__(self, root_path):
        object.__setattr__(
            self, "_state_path", os.path.join(root_path, ".runtime", "state.json")
        )

    def __setattr__(self, attr: str, value: any):
        self._update(attr, "active", value)

    def __getattr__(self, attr: str) -> any:
        self.ensure_state_json()

        # Check environment variable override for 'env'
        if attr == "env":
            env_override = os.environ.get("MASCOPE_ENV")
            if env_override:
                return env_override

        state = self._read_state()
        return state[attr]["override"] or state[attr]["active"]

    def override(self, attr: str, value: any):
        self._update(attr, "override", value)

    def ensure_state_json(self):
        if not os.path.exists(self._state_path):
            os.makedirs(os.path.dirname(self._state_path), exist_ok=True)
            # Two processes racing on a fresh runtime home both write the
            # same defaults, so last-writer-wins is harmless here.
            self._write_state(copy.deepcopy(default_state))

    def _update(self, attr: str, slot: str, value: any):
        """Read-modify-write one slot ("active" or "override") of one field."""
        self.ensure_state_json()
        state = self._read_state()
        state[attr][slot] = value
        self._write_state(state)

    def _read_state(self) -> dict:
        """
        Load the state file, tolerating a concurrent writer or a corrupt file.

        Writes are atomic, so a torn read should be impossible - but a state
        file left corrupt by an older version, a full disk or a killed write
        must not crash the CLI with an unhandled JSONDecodeError. Fall back
        to the defaults loudly: the next write repairs the file.
        """
        last_error = None
        for attempt in range(2):
            try:
                # Read and close in one call, then parse: on Windows a writer
                # cannot replace a file this process holds open, so the
                # shorter the fd lives the less a reader blocks writers.
                with open(self._state_path, "rb") as f:
                    raw = f.read()
                return json.loads(raw)
            except (json.JSONDecodeError, OSError, UnicodeDecodeError) as error:
                last_error = error
            if attempt == 0:
                time.sleep(_READ_RETRY_DELAY)
        logger.warning(
            f"Runtime state {self._state_path} is unreadable ({last_error}); "
            "falling back to default state"
        )
        return copy.deepcopy(default_state)

    def _write_state(self, state: dict):
        """Write the state file atomically, so readers never see a partial file."""
        # mode=0600 keeps what this file has had since it started being
        # written through a temporary; the helper's default would loosen a
        # newly created one to the umask.
        write_json(
            self._state_path,
            state,
            indent=2,
            prefix=".",
            timeout=_REPLACE_TIMEOUT,
            mode=0o600,
        )


class RuntimeTempState(object):
    def __init__(
        self,
        env: str,
        mode: str,
    ):
        """
        Emphemeral  runtime state

        This class representates a temporary runtime state.
        State is persisted only in memory, in a regular
        Python dict object.
        """
        object.__setattr__(
            self,
            "_state",
            {
                "env": {"active": env or "default", "override": None},
                "mode": {"active": mode or "dev", "override": None},
            },
        )

    def __setattr__(self, attr: str, value: any):
        self._state[attr]["active"] = value

    def __getattr__(self, attr: str):
        return self._state[attr]["override"] or self._state[attr]["active"]

    def override(self, attr: str, value: any):
        self._state[attr]["override"] = value
