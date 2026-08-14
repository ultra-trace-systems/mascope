import os
import re
import shlex
import subprocess

from loguru import logger

from .config import MetaConfig, ModuleConfig, RuntimeConfig, load_config
from .env import RuntimeEnv
from .exceptions import MissingMascopePathException
from .logging import RuntimeLogging
from .mode import RuntimeMode
from .module import RuntimeModule
from .state import RuntimeJsonState, RuntimeTempState


class Runtime:
    """
    The main runtime instance, providing a localized interface
    to the Mascope runtime.

    Each service, library and tool - known collectively as
    'modules' - have their own runtime instance; they are
    distinguished by the 'module' string identifier passed
    to this class.

    The class provides access to global runtime interfaces like
    state, environment and meta configuration, as well as local
    runtime interfaces such as the module's own config.
    """

    context: str | None
    state: RuntimeJsonState | RuntimeTempState
    env: RuntimeEnv
    module: RuntimeModule
    logging: RuntimeLogging

    _path: str
    _version: str
    _full_config: RuntimeConfig

    def __init__(
        self,
        module: str,
        env: str | None = None,
        mode: RuntimeMode | None = None,
        path: str | None = None,
        context: str | None = None,
        log: bool = True,
    ):
        """
        Initializes the primary runtime interface. Resolves the
        Mascope path and version, and instantiates the runtime
        state, environment, module, config and logging interfaces.

        :param module: The module in which the runtime is initialized.
        :type module: str
        :param env: The env to initialize the runtime with.
        :type env: str, Optional
        :param mode: The runtime mode ("dev" or "prod").
        :type mode: RuntimeMode, Optional
        :param path: A runtime path, overriding of MASCOPE_PATH
        :type path: str, Optional
        """
        # initialize attributes
        self._init_path(path=path)
        self._init_version()
        self._init_state(env=env, mode=mode)

        # initalize runtime
        self.env = RuntimeEnv(self)
        self.module = RuntimeModule(module, self)
        self.context = context

        # load config
        self._full_config = load_config(self)

        # configure loguru global logger
        if log and not self.context:
            self.logging = RuntimeLogging(self)
            self.logging.configure()
            self.logger.debug(f"Initialized runtime for module '{module}'")

    @property
    def mode(self) -> RuntimeMode:
        """
        The runtime mode ("dev" or "prod").
        """
        return self.state.mode

    @property
    def version(self) -> str:
        """
        The Mascope version string
        """
        return self._version

    @property
    def meta(self) -> MetaConfig:
        """
        The runtime's global configuration
        """
        return self._full_config.meta

    @property
    def config(self) -> ModuleConfig:
        """
        The runtime's local (module specific) configuration
        """
        return self.module.config

    @property
    def full_config(self) -> RuntimeConfig:
        """
        The complete runtime configuration including all modules and infrastructure.

        Most modules should use `runtime.config` to access their module-specific
        configuration and `runtime.meta` for global settings. However, management
        tools like the CLI may need access to the full configuration to orchestrate
        infrastructure and coordinate between modules.

        Returns:
            RuntimeConfig: The complete runtime configuration
        """
        return self._full_config

    def reload_config(self) -> None:
        """
        Reload full configuration from disk using current runtime state.

        Call after changing mode via state.override() to ensure config
        reflects the correct mode-specific TOML layer.
        """
        self._full_config = load_config(self)

    def use_env(self, name: str) -> None:
        """
        Switch the active env for this runtime to ``name``.

        Sets ``MASCOPE_ENV`` (so subprocesses and any later re-resolution
        agree), rebuilds the env interface — whose ``name`` is captured once
        at construction — and reloads config so env-derived values (database
        name, filestore path, per-env overlays) reflect the new env. Uses the
        ``MASCOPE_ENV`` env var rather than persisted state so concurrent
        runtimes sharing one ``MASCOPE_PATH`` never clobber each other.

        :param name: Env name to activate.
        :type name: str
        """
        os.environ["MASCOPE_ENV"] = name
        self.env = RuntimeEnv(self)
        self.reload_config()

    @property
    def logger(self):
        """
        The runtime's local (module specific) configuration
        """
        if self.context:
            return logger.bind(context=self.context)
        return logger

    @property
    def modules(self):
        """
        List of available runtime modules, as dicts with
        various fields (documented below)
        """
        return [
            {
                key: mod.get(key)
                for key in (
                    "name",  # name of the module (e.g. 'backend')
                    "tags",  # tags for running as part of a group (e.g. 'file')
                    "color",  # color of the logging key
                    "run",  # command to run the module (optional)
                )
            }
            for mod in self._full_config.model_dump().values()
            if (mod is not None and "name" in mod and mod["name"])
        ]

    @property
    def tags(self):
        """
        The set of tags tag can be applied to runtime modules.
        Tags define runtime module groups.
        """
        return {tag for mod in self.modules for tag in mod["tags"]}

    @property
    def groups(self):
        """
        List of available runtime groups. These are groups of
        modules that can be launched together with `mascope dev
        run`.
        """
        return [
            {
                "name": tag,
                "modules": [mod for mod in self.modules if tag in mod["tags"]],
            }
            for tag in self.tags
        ]

    # PRIVATE METHODS

    def _init_path(self, path: str):
        """
        Resolve the mascope runtime path from the MASCOPE_PATH envvar
        and the provided 'path' argument. The path is persisted
        to self._path.

        :param path: A runtime path, overriding of MASCOPE_PATH
        :type path: str, Optional
        """
        resolved_path = path or os.environ.get("MASCOPE_PATH")
        if not resolved_path:
            raise MissingMascopePathException()
        else:
            self._path = resolved_path

    def _init_version(self):
        """
        Resolve the mascope version from the MASCOPE_VERSION envvar.
        The version is persisted to self._version.
        """
        self._version = os.environ.get("MASCOPE_VERSION")

    def _init_state(self, env: str | None = None, mode: RuntimeMode | None = None):
        """
        Initializes the mascope runtime state, which describes
        the currently active environment and runtime mode. If
        the runtime is provided with `env` or `mode` arguments,
        these are propagated, forcing a temporary state to be
        initialized rather than the persisted JSON state.

        :param env: The env to initialize the runtime with.
        :type env: str, Optional
        :param mode: The runtime mode ("dev" or "prod").
        :type mode: RuntimeMode, Optional
        """
        if env or mode:
            self.state = RuntimeTempState(env, mode)
        else:
            self.state = RuntimeJsonState(self._path)

    # PUBLIC METHODS

    def path(self, *args: list[str]) -> str:
        """
        Resolves the path relative to the runtime path.

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
        return os.path.join(self._path, *segments)

    def realpath(self, *args: list[str]) -> str:
        """
        Resolves the path relative to the runtime path, resolving symlinks as well.

        :param *args: A list of path segments or one string path in Unix notation
        :type arg: list[str], optional
        :return: Resolved path
        :rtype: str
        """
        return os.path.realpath(self.path(*args))

    def filestore(self, *args: list[str]) -> str:
        """
        Resolves the path relative to the filestore.

        The filestore path (config `meta.filestore`) is resolved by the config loader during initialization:
        - Relative paths like './filestore' are resolved against the active runtime env
        - Dev: './filestore' -> 'C:/path/to/.runtime/env/{MASCOPE_ENV}/filestore'
        - Prod: './filestore' -> '/app/.runtime/env/{MASCOPE_ENV}/filestore'

        :param *args: Optional path segments to append to filestore base path
        :return: Filestore path with optional segments appended
        :rtype: str
        """
        return os.path.join(self.meta.filestore, *args)

    def secret(self, envvar: str, path: str, all_lines: bool = False) -> str:
        """
        Reads a secret, attempting to resolve the path from an `envvar`
        before falling back on a `path` argument. The contents of the
        file in the path are read and returned.

        If `all_lines` is True, it returns the full contents of the file;
        otherwise only the first line is returned.

        :param envvar: an environment variable containing the path of the secret file
        :type envvar: str
        :param path: a fallback path of the secret file
        :type path: str
        :param all_lines: Whether to return all lines or only the first.
        :type all_lines: bool
        :return: Secret file contents
        :rtype: str
        """
        # construct paths to look in
        envvar_path = os.environ.get(envvar)
        root_path = self.path(".runtime", "secrets", path)
        # try to open each path
        for file_path in [envvar_path, root_path]:
            # ensure the path and file exist
            if file_path and os.path.exists(file_path):
                with open(file_path, "r", encoding="utf-8") as f:
                    # if succesful, return the result
                    return (
                        "\n".join(f.readlines())  # all lines
                        if all_lines
                        else f.readlines()[0].replace("\n", "")  # first line
                    )
        # if no file was found, raise an error
        raise FileNotFoundError(
            f"Secret could not be found using env var {envvar} and path {path}"
        )

    def parse_version(self, cwd: str | None = None):
        """
        Construct a version string for the app from git.

        Git is consulted in ``cwd`` when given, otherwise in the process's
        working directory. Callers that must resolve a specific checkout
        (rather than wherever the process happens to have been started) pass
        it explicitly - a deploy launched from a directory outside the
        checkout otherwise resolves no version at all.

        If HEAD is exactly at a release tag (``v*``), that tag IS the version, so
        a pinned release reports its release version:
          example: v1.0.0

        Otherwise a build identifier from the latest commit is used (no ``v`` -
        the leading ``v`` is what distinguishes a release from a build):

        In `master` (or a detached checkout, e.g. CI):
          format: {iso_date}-{short_commit_hash}
          example: 2024.09.03-d06bfef9

        In other branches ("/" in the branch name becomes "-" so the version
        stays a valid Docker image tag):
          format: {branch}-{iso_date}-{short_commit_hash}
          example: feature-new-stuff-2024.09.03-d06bfef9

        The build identifier matches the image tags the release pipeline pushes,
        so it doubles as a deployable/pinnable tag.
        """

        def exec(cmd: str):
            """
            Run a command in a subprocess and return the output
            """
            try:
                return (
                    subprocess.check_output(
                        shlex.split(cmd), stderr=subprocess.DEVNULL, cwd=cwd
                    )
                    .decode("utf-8")
                    # strip(), not replace("\n", ""): callers such as
                    # `git tag --points-at HEAD` return one item per line, so
                    # collapsing every newline would glue them into a single
                    # unmatchable token (e.g. "v1.1.0v2026.07.03-abc1234") and
                    # break the semver-tag detection below. Only trim the ends.
                    .strip()
                )
            except Exception:
                return None

        # A semver release tag at HEAD wins (e.g. v1.0.0) - report it as the
        # version. Dated build tags (v{date}-{hash}) are intentionally excluded
        # by the strict `vMAJOR.MINOR.PATCH` match.
        tags = exec("git tag --points-at HEAD") or ""
        for tag in tags.splitlines():
            if re.fullmatch(r"v\d+\.\d+\.\d+", tag.strip()):
                return tag.strip()

        # Otherwise a build identifier from the latest commit's date + short hash.
        # Use a fixed 7-char hash, not git's %h: %h auto-scales its abbreviation
        # length with the local object count, so a shallow CI clone (short) and a
        # full deploy clone (longer) derive different tags - and the image tag the
        # deploy pulls stops matching the one CI pushed. Slicing the full hash
        # keeps the length stable on every machine.
        date = exec('git log -1 --date=format:"%Y.%m.%d" --format="%ad"')
        commit = exec("git rev-parse HEAD")
        if not date or not commit:
            return "unknown-version"
        date_and_commit_hash = f"{date}-{commit[:7]}"
        # No branch prefix on master or a detached checkout (HEAD).
        branch = exec("git rev-parse --abbrev-ref HEAD")
        if branch in (None, "master", "HEAD"):
            return date_and_commit_hash
        # The version doubles as a Docker image tag, which only permits
        # [A-Za-z0-9_.-]; branch names routinely contain "/" (e.g. feat/x), so
        # sanitize before prefixing or compose rejects the tag.
        safe_branch = re.sub(r"[^A-Za-z0-9_.-]", "-", branch)
        return f"{safe_branch}-{date_and_commit_hash}"
