# import type hint w/o circular import error
from __future__ import annotations

import typing


if typing.TYPE_CHECKING:
    from mascope_runtime import Runtime

import os
import re


#: The characters a runtime env name may contain. An env name travels into a
#: filesystem path, an SSH command line, a Postgres database name and - in dev -
#: an HTTP cookie name, so the alphabet is the intersection of what all of those
#: accept: ASCII letters, digits, underscore and hyphen. It lives here, beside
#: the env itself, so every consumer agrees on one rule instead of restating it
#: and drifting.
ENV_NAME_PATTERN = re.compile(r"[A-Za-z0-9_-]+")


def is_valid_env_name(name: str | None) -> bool:
    """
    Whether ``name`` is usable as a runtime env name.

    :param name: Proposed env name.
    :type name: str, Optional
    :return: ``True`` if the name is non-empty and matches
        :data:`ENV_NAME_PATTERN`.
    :rtype: bool
    """
    return bool(name) and ENV_NAME_PATTERN.fullmatch(name) is not None


class RuntimeEnv:
    """
    An interface to the runtime environment, providing
    the name and path of the environment as well as a
    method for resolving paths relative to the env.
    """

    name: str

    _runtime: Runtime

    def __init__(self, runtime: Runtime):
        """
        Initializes the runtime enviornment interface, retrieving
        the active environment from the parent runtime state.

        :param runtime: The main runtime class
        :type runtime: Runtime
        """
        # init attributes
        self._runtime = runtime
        self.name = self.runtime.state.env

    @property
    def runtime(self) -> Runtime:
        """
        The parent runtime instance.
        """
        return self._runtime

    @property
    def dir(self) -> str:
        """
        Path of the runtime env parent directory.
        """
        return self.runtime.path(".runtime", "env")

    @property
    def list(self) -> list[dict]:
        """
        List environments available in the runtime, as
        dicts with "name" and "path" fields
        """
        envdir = [
            {"name": name, "path": os.path.join(self.dir, name)}
            for name in os.listdir(self.dir)
        ]
        envs = [
            entry
            for entry in envdir
            if (os.path.isdir(entry["path"]) and not entry["name"].startswith("."))
        ]
        return envs

    # PUBLIC METHODS

    def path(self, *args: list[str]) -> str:
        """
        Resolves the path relative to the active env path.

        :param *args: A list of path segments or one string path in Unix notation
        :type arg: list[str], optional
        :return: Resolved path
        :rtype: str
        """
        if len(args) == 1 and "/" in args[0]:
            # resolve string paths like "./foo/bar"
            segments = args[0].replace("./", "").split("/")
        else:
            # treat arg list as-is
            segments = args
        return os.path.join(self.dir, self.name, *segments)

    def realpath(self, *args: list[str]) -> str:
        """
        Resolves the path relative to the active env path, resolving symlinks as well.

        :param *args: A list of path segments or one string path in Unix notation
        :type arg: list[str], optional
        :return: Resolved path
        :rtype: str
        """
        return os.path.realpath(self.path(*args))
