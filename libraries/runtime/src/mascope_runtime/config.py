"""
Runtime configuration models and loaders.

Defines Pydantic models for Mascope runtime configuration, including
global settings, module-specific options, and infrastructure dependencies.
Handles loading and validation of .mascope.toml configuration files with
three-layer overlay system.
"""

from __future__ import annotations

import os
import re
import tomllib
import typing
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


if typing.TYPE_CHECKING:
    from mascope_runtime import Runtime


# PYDANTIC MODELS

# Note: all relative paths like `./foo/bar` are resolved relative
# to the runtime environment active, which defaults to
#    $MASCOPE_PATH/runtime/env/default

type LogLevel = Literal[
    "trace", "debug", "info", "success", "warning", "error", "critical"
]


class MetaConfig(BaseModel):
    """
    Global configuration options shared across all  modules.
    """

    log_level: LogLevel | None = None  # global log level to print to terminal at
    description: str = "Mascope configuration"  # Description for `mascope env list`
    api_port: int = 8090  # API port
    filestore: str = r"./filestore"  # filestore path
    # Peak-centric assignment (docs/dev/peak_assignment_paradigm.md). On by
    # default: the feature is generally available, and targeted matching is
    # unaffected either way - it coexists rather than being replaced. The
    # backend reads it via `peak_assignment_enabled()` and the frontend via
    # `runtime.meta`, so one switch gates both sides. Set it to false to keep
    # a deployment on the pre-assignment behaviour: no assignment at sample
    # ingest, no assignment views, and the API write routes answering 403.
    peak_assignment: bool = True
    # Whether a newly processed sample is assigned as it arrives (the database
    # stage only). Subordinate to `peak_assignment`: with the feature off nothing
    # assigns at ingest whatever this says. Off, the feature stays available -
    # views, on-demand runs, imports - without the per-sample cost of assigning
    # everything the instrument acquires: one ledger row and one batch-peak
    # occurrence per detected peak, about 1 KB per peak, which the retention
    # pass never reclaims. Read by the backend via `peak_assignment_on_ingest()`.
    peak_assignment_on_ingest: bool = True
    # Ceiling on the detected-peak count of a sample assigned at ingest. A
    # denser sample is logged and left for an explicit run: at about 1 KB per
    # peak, one very dense acquisition is hundreds of megabytes of ledger
    # written unasked. 0 disables the ceiling. Read by the backend via
    # `peak_assignment_ingest_max_peaks()`.
    peak_assignment_ingest_max_peaks: int = Field(default=100_000, ge=0)
    # Which ledger an ingest-time assignment writes. "batch" (the default) folds
    # the sample into the batch peaks and writes no per-sample run: one member
    # row of some 200 bytes per detected peak, and the Sample view is served
    # from the batch ledger. "sample" writes a per-sample run as well and folds
    # it - about a kilobyte per detected peak, most of it placeholders for
    # peaks nothing assigned - which is what an explicit run on a sample
    # writes anyway. Read by the backend via `peak_assignment_ingest_ledger()`.
    peak_assignment_ingest_ledger: Literal["sample", "batch"] = "batch"
    # Size cap (in gigabytes) for a single resumable (tus) upload, advertised
    # to clients as Tus-Max-Size and enforced at upload creation. Applies per
    # upload: it does not limit how many files a client may transfer, only how
    # large each one may be. Lives here rather than in [backend] because both
    # sides need it - the backend enforces it, and the web uploader sizes its
    # own client-side restriction from it via `runtime.meta` (a cap only the
    # backend knew about meant a browser refusing files the server would have
    # accepted). Must be at least 1: a zero or negative cap rejects every
    # upload. A value still under [backend] is promoted by
    # `migrate_legacy_options()`.
    tus_max_upload_gb: int = Field(default=5, ge=1)


class DatabaseConfig(BaseModel):
    """
    PostgreSQL database configuration — connections, pool, engine and server tuning.

    NOTE: Connection pool settings are per-worker: total possible connections
    across all workers must stay under PostgreSQL max_connections (default 100).
    See postgres README.md for tuning parameter explanations.

    """

    # --- Connection ---
    host: str = "localhost"  # dev default, in prod the host is postgres container name
    port: int = 5432
    database: str = "mascope"  # Base name - actual will be mascope_{env}
    user: str = "mascope_user"  # password loaded via secret

    # --- Docker ---
    container_name: str = (
        "postgres"  # base name, actual will be mascope_{mode}_postgres
    )
    # Mount base names — must match compose bind mount targets
    backups_mount: str = "backups"
    transfer_mount: str = "transfer"
    # /dev/shm tmpfs size. NOT a postgres flag — applied via compose shm_size field.
    # Must be >= shared_buffers + ~2GB. Uses Docker format: m/g (not MB/GB).
    shm_size: str = "1g"

    # --- SQLAlchemy pool (per worker) ---
    pool_size: int = 3  # Base pool size - persistent connections kept open per worker
    max_overflow: int = (
        2  # Additional overflow connections allowed per worker under load
    )
    pool_timeout: int = (
        30  # Seconds to wait for available connection before raising timeout error
    )
    pool_pre_ping: bool = (
        True  # Health check connection before use (prevents stale connections)
    )
    expire_on_commit: bool = False  # Keep loaded objects accessible after commit

    # --- PostgreSQL: connections ---
    # Server-side connection cap. Must exceed
    # workers x (pool_size + max_overflow) + ~10 headroom for
    # superuser_reserved_connections (3), db_init, backups and psql sessions.
    max_connections: int = 100

    # --- PostgreSQL: memory ---
    shared_buffers: str = "512MB"  # primary data cache; 25% RAM on prod
    effective_cache_size: str = (
        "4GB"  # planner hint only, no allocation; 75% RAM on prod
    )
    work_mem: str = "32MB"  # per sort/hash-join op per connection
    maintenance_work_mem: str = "512MB"  # VACUUM, CREATE INDEX, pg_restore
    autovacuum_work_mem: str = "-1"  # -1 = inherit maintenance_work_mem
    wal_buffers: str = "16MB"  # WAL shared memory buffer; 16MB is practical ceiling

    # --- PostgreSQL: checkpoints and WAL ---
    min_wal_size: str = "512MB"  # minimum WAL retained on disk
    max_wal_size: str = "2GB"  # WAL ceiling before forced checkpoint
    checkpoint_completion_target: float = (
        0.9  # spread checkpoint writes over 90% of interval
    )
    wal_compression: str = "on"  # zlib WAL compression; lz4 requires build flag

    # --- PostgreSQL: planner ---
    effective_io_concurrency: int = (
        200  # concurrent I/O requests; 200 for SSD/NVMe, 1 for HDD
    )
    random_page_cost: float = (
        1.1  # relative cost of random vs sequential read; 1.1 for SSD
    )
    default_statistics_target: int = (
        100  # planner histogram depth; raise for skewed distributions
    )
    jit: str = "off"  # JIT compilation; off for mixed/short-query workloads

    # --- PostgreSQL: autovacuum ---
    autovacuum_max_workers: int = 3  # parallel autovacuum workers

    # --- Dumps (pg_dump) ---
    # Value for pg_dump --compress: a level ("0".."9") or method:level
    # ("zstd:1", "lz4"; PG16+ client). Empty = pg_dump's default (moderate
    # gzip). "0" (uncompressed) dumps considerably faster — the gzip stage is
    # the usual single-core bottleneck — and lets restic deduplicate
    # consecutive dumps in the off-site backup repository; dumps take more
    # local disk, so pair with a short local retention on large databases.
    dump_compression: str = ""

    @field_validator("shm_size")
    @classmethod
    def validate_shm_size_format(cls, v: str) -> str:
        """Reject PostgreSQL-format values (MB/GB) — Docker only accepts m/g."""
        if not re.fullmatch(r"\d+[bkmg]", v.lower()):
            raise ValueError(
                f"shm_size must use Docker format (e.g. '1g', '256m'), got '{v}'. "
                "See postgres README.md."
            )
        return v.lower()

    def get_postgres_container_name(self, mode: str) -> str:
        """
        Get mode-qualified postgres container name.

        :param mode: Runtime mode ('dev'/'prod')
        :return: e.g. 'mascope_dev_postgres', 'mascope_prod_postgres'
        """
        return f"mascope_{mode}_{self.container_name}"

    def get_postgres_database_name(self, env_name: str) -> str:
        """
        Get environment-specific database name.

        :param env_name: Runtime environment name (e.g., 'default', 'test-env')
        :return: Database name like 'mascope_default' or 'mascope_test_env'
        """
        # Sanitize env name for PostgreSQL (replace hyphens with underscores)
        safe_env = env_name.replace("-", "_").replace(" ", "_")
        return f"{self.database}_{safe_env}"

    def get_postgres_url(self, password: str, env_name: str) -> str:
        """
        Build PostgreSQL async URL (asyncpg driver).

        :param password: Database password
        :param env_name: Runtime environment name
        :return: PostgreSQL async connection URL
        """
        db_name = self.get_postgres_database_name(env_name)
        return f"postgresql+asyncpg://{self.user}:{password}@{self.host}:{self.port}/{db_name}"

    def get_postgres_url_sync(self, password: str, env_name: str) -> str:
        """
        Build PostgreSQL sync URL (psycopg2 driver) - used by Alembic.

        :param password: Database password
        :param env_name: Runtime environment name
        :return: PostgreSQL sync connection URL
        """
        db_name = self.get_postgres_database_name(env_name)
        return f"postgresql+psycopg2://{self.user}:{password}@{self.host}:{self.port}/{db_name}"

    def get_backups_dir(self, mode: str) -> Path:
        """
        Resolve the host-side backups directory for the given mode.

        Matches the compose bind mount:
            ${MASCOPE_PATH}/.runtime/database/backups/{mode}:/{backups_mount}

        :param mode: Runtime mode ('dev' or 'prod').
        :type mode: str
        :return: Absolute path to .runtime/database/backups/{mode}/.
        :rtype: Path
        """
        return (
            Path(os.environ["MASCOPE_PATH"])
            / ".runtime"
            / "database"
            / "backups"
            / mode
        )

    def get_transfer_dir(self, mascope_path: str | None = None) -> Path:
        """
        Resolve the host-side transfer directory.

        Shared between dev and prod postgres containers. Matches the compose
        bind mount:
            ${MASCOPE_PATH}/.runtime/database/transfer:/{transfer_mount}

        :param mascope_path: Override for `MASCOPE_PATH`. When `None`, reads
                            from the environment variable. Pass a remote
                            machine's path (queried via SSH) to construct
                            the equivalent path on that machine.
        :type mascope_path: str | None
        :return: Absolute path to `.runtime/database/transfer/`.
        :rtype: Path
        """
        base = mascope_path or os.environ["MASCOPE_PATH"]
        return Path(base) / ".runtime" / "database" / "transfer"

    def get_backups_mount(self) -> str:
        """
        Container mount point for the backups directory (e.g. '/backups').

        :rtype: str
        """
        return f"/{self.backups_mount}"

    def get_transfer_mount(self) -> str:
        """
        Container mount point for the transfer directory (e.g. '/transfer').

        :rtype: str
        """
        return f"/{self.transfer_mount}"


class ModuleConfig(BaseModel):
    """
    Base class for module-specific configuration; every  module
    shares these configuration options.
    """

    name: str  # name of the module, e.g. 'backend'
    color: str | None = "white"  # color for logging tag
    tags: list[str] | None = []  # module groups to which the module should belong *
    log_path: str | None = "./logs"  # path where to print log files
    log_level: LogLevel | None = None  # module log level to print to terminal at
    run: str | None = None  # command to run the module, if any

    # * module groups allow you to easily run multiple modules.
    # For example, a common scenario is testing Orbitrap acquisition
    # workflows. For example, with the default `base.mascope.toml`
    # configuration, running `mascope dev run orbi` will spin up
    # the backend, frontend, file-converter and file-agent modules.


class RedisConfig(BaseModel):
    """
    Redis infrastructure configuration.
    """

    host: str = "localhost"
    port: int = 6379
    container_name: str = "redis"  # base name

    def get_redis_container_name(self, mode: str) -> str:
        """
        Get mode-qualified redis container name.

        :param mode: Runtime mode ('dev'/'prod')
        :return: e.g. 'mascope_dev_redis', 'mascope_prod_redis'
        """
        return f"mascope_{mode}_{self.container_name}"

    def get_redis_url(self) -> str:
        """Build Redis URL from host and port."""
        return f"redis://{self.host}:{self.port}"


class BackendConfig(ModuleConfig):
    """
    Backend module specific configuration options
    """

    container_name: str = "backend"  # base name
    database: DatabaseConfig = DatabaseConfig()
    filestreams: str = r"./filestreams"  # path to the file streams folder
    redis: RedisConfig = RedisConfig()
    workers: Literal["auto"] | int = "auto"  # uvicorn workers, auto -  half cpu cores
    # Free space (in gigabytes) that must remain on the tus spool's filesystem
    # once a resumable upload is admitted. `meta.tus_max_upload_gb` bounds one
    # transfer; it does not bound N concurrent ones, so admission also refuses
    # a creation that would eat into this reserve. Backend-only (the browser
    # never needs it), and the default matches MIN_FREE_GB in
    # tooling/disk-check.sh so uploads start being refused around the point the
    # disk monitor already alerts. 0 disables the check.
    tus_min_free_disk_gb: int = Field(default=10, ge=0)
    # Lowest role required to hold a second authentication factor, by name
    # ("guest" covers everyone, "admin" only admins and owners). Unset means no
    # account is required to enrol. Validated by the backend at startup, which
    # refuses to start on a name that is not a role - see
    # api/new/auth/mfa/policy.py. Typed loosely here because this library must
    # not depend on the backend's role table.
    mfa_required_min_role: Optional[str] = None
    # Refuse bearer tokens for the pairable agent services (file-agent,
    # tof-agent, export-agent) unless the token is bound to a registered
    # device. Off by default: tokens issued before the device registry keep
    # working until the deployment has (re-)paired every agent machine and
    # turns this on. Pairing binds new tokens to a device automatically.
    require_device_tokens: bool = False
    # Allowlist of per-record reference licences the peak-assignment database
    # stage (Stage A) may match against. The reference mirror carries a
    # licence per record from ingest through to results, and some sources
    # (HMDB) permit academic use only - this is what stops a commercial
    # deployment matching against them.
    #
    # MATCHED AS AN EXACT STRING, so a tag left out of the list is dropped
    # with nothing in a result to say why. All six, with the sources carrying
    # each: public-domain (comptox, pubchem), CC-BY-4.0 (chebi, lipidmaps),
    # CC0 (coconut), open (norman), hmdb-attribution (hmdb), custom
    # (hand-authored rows with no licence of their own). Write the allowlist
    # by starting from that whole vocabulary and deleting what you decline,
    # e.g. to decline HMDB only:
    #     reference_licenses = ["public-domain", "CC-BY-4.0", "CC0", "open", "custom"]
    # base.mascope.toml and docs/maintaining.md say the same, and `mascope
    # reference status` prints it from the adapter registry on a monorepo
    # checkout. A written-out list also freezes the vocabulary: a source added
    # by a later release brings a tag the list does not name, so re-check it
    # after an upgrade.
    #
    # UNSET IS THE DEFAULT AND MEANS NO GATING: every active source is matched,
    # exactly as before this setting existed. That is deliberate. Narrowing the
    # gate shrinks what Stage A can find without saying so anywhere in the UI,
    # so it must be an operator's explicit decision, never a default they
    # inherit on upgrade. `mascope reference status` reports the effective set,
    # and every run records it on `PeakAssignmentRun.config`.
    #
    # Backend-only: it bounds a server-side query, and putting it in [meta]
    # would ship it to the browser (where it is unused) and bake it into the
    # frontend image at build time. It is deliberately NOT a field on the
    # request's PeakAssignmentConfig either - a client must not be able to
    # widen it.
    reference_licenses: Optional[list[str]] = None

    @field_validator("reference_licenses")
    @classmethod
    def _clean_reference_licenses(cls, value: list[str] | None) -> list[str] | None:
        """Normalize the allowlist, and refuse an empty one.

        Sorted and deduplicated so the set recorded on a run and folded into
        the reference-isotope cache key does not depend on the order the
        operator happened to type. An empty list is rejected rather than
        honoured: `reference_licenses = []` reads as "no restriction" but would
        gate out every record, silently emptying the known set - delete the
        line to disable gating.
        """
        if value is None:
            return None
        cleaned = sorted({entry.strip() for entry in value if entry.strip()})
        if not cleaned:
            raise ValueError(
                "backend.reference_licenses is empty, which would stop peak "
                "assignment matching against any reference record. Remove the "
                "line entirely to allow every licence."
            )
        return cleaned

    def get_worker_count(self) -> int:
        """
        Resolve worker count, calculating from CPU cores if set to "auto".

        Rule of thumb for Mascope (mixed I/O + CPU workload):
        - auto: half CPU cores
        - explicit integer: use as-is

        Returns:
            int: Number of workers to use
        """
        if self.workers == "auto":
            cpu_cores = os.cpu_count() or 1
            return max(1, cpu_cores // 2)
        return self.workers

    def get_backend_container_name(self, mode: str) -> str:
        """
        Get mode-qualified backend container name.

        :param mode: Runtime mode ('dev'/'prod')
        :return: e.g. 'mascope_dev_backend', 'mascope_prod_backend'
        """
        return f"mascope_{mode}_{self.container_name}"


class FileConverterConfig(ModuleConfig):
    """
    File converter module specific configuration options
    """

    container_name: str = "file_converter"  # base name
    server: str = r"backend"  # production host URL; the default works in our docker compose network
    source: str = r"./filestreams"  # folder to monitor for files to convert
    raw_threads: int = 2  # number of threads for converting Orbitrap files
    h5_threads: int = 2  # number of threads for converting Tof files
    interval: int = 3  # polling interval (s) when checking the file system

    def get_file_converter_container_name(self, mode: str) -> str:
        """
        Get mode-qualified file converter container name.

        :param mode: Runtime mode ('dev'/'prod')
        :return: e.g. 'mascope_dev_file_converter', 'mascope_prod_file_converter'
        """
        return f"mascope_{mode}_{self.container_name}"


class TofAgentConfig(ModuleConfig):
    """
    Tof Agent module specific configuration options
    """

    host: str  # URL of the backend
    access_token: str  # API access token
    filename_prefix: str | None = (
        None  # optional prefix prepended to filename on upload
    )
    filename_suffix: str | None = None  # optional suffix appended to filename on upload


class FileAgentConfig(ModuleConfig):
    """
    File Agent module specific configuration options
    """

    mask: str = "*.raw"  # file pattern to look for
    timeout: int = 10  # timeout (s) for a file transfer operation
    source: str  # folder to monitor in the instrument machine
    recursive: bool = False  # also watch subfolders of source
    # Verify the server's TLS certificate. On by default; a self-signed or
    # plain-HTTP dev deployment turns it off.
    verify_tls: bool = True
    # IANA timezone of this instrument PC, reported with each upload so the
    # converter can turn the instrument-local acquisition time into UTC.
    # Empty means auto-detect from the operating system. Set it explicitly when
    # detection picks the wrong zone: Windows names a group of zones rather
    # than a city, so a machine can resolve to a neighbouring city whose
    # historical DST rules differ.
    timezone: str = ""
    host: str  # URL of the backend
    access_token: str  # API access token
    filename_prefix: str | None = (
        None  # optional prefix prepended to filename on upload
    )
    filename_suffix: str | None = None  # optional suffix appended to filename on upload


class DatetimeRange(BaseModel):
    min: str | None = None
    max: str | None = None


class SampleTableDefaults(BaseModel):
    columns: list[str] = ["sample_item_name", "index"]
    sort_field: str = "index"
    sort_order: Literal[1, -1] = 1


class FrontendConfig(ModuleConfig):
    """
    Frontend module specific configuration options
    """

    acquisition_filter: DatetimeRange | str | None = None
    container_name: str = "frontend"  # base name
    port: int = 5173  # Vite dev-server listen port (dev mode only)
    sample_table_defaults: SampleTableDefaults = SampleTableDefaults()

    def get_frontend_container_name(self, mode: str) -> str:
        """
        Get mode-qualified frontend container name.

        :param mode: Runtime mode ('dev'/'prod')
        :return: e.g. 'mascope_dev_frontend', 'mascope_prod_frontend'
        """
        return f"mascope_{mode}_{self.container_name}"


class CliConfig(ModuleConfig):
    """
    Cli module specific configuration options
    """

    pass


class ChemistryLibConfig(ModuleConfig):
    """
    Standard Library module specific configuration options
    """

    pass


class FileLibConfig(ModuleConfig):
    """
    Standard Library module specific configuration options
    """

    pass


class SignalLibConfig(ModuleConfig):
    """
    Standard Library module specific configuration options
    """

    pass


class MatchLibConfig(ModuleConfig):
    """
    Standard Library module specific configuration options
    """

    pass


class TofwerkLibConfig(ModuleConfig):
    """
    Hardware Library module specific configuration options
    """

    tofwerk_dll: Literal["Auto", "Linux", "Windows", "Darwin"] = "Auto"  # *
    # * Which TofWerk DLLs to use in the hardware library
    # Defaults to automatically resolving the platform.


class ThermoLibConfig(ModuleConfig):
    """
    Hardware Library module specific configuration options
    """

    pass


class SdkLibConfig(ModuleConfig):
    """
    API Library module specific configuration options
    """

    pass


class RuntimeConfig(BaseModel):
    """
    The  runtime configuration

    Includes the meta configuration, as well as all module
    configuration objects.
    """

    # global
    meta: MetaConfig
    # services
    backend: BackendConfig | None = None
    file_converter: FileConverterConfig | None = None
    tof_agent: TofAgentConfig | None = None
    file_agent: FileAgentConfig | None = None
    # clients
    frontend: FrontendConfig | None = None
    cli: CliConfig | None = None
    # libraries
    chemistry_lib: ChemistryLibConfig | None = None
    signal_lib: SignalLibConfig | None = None
    match_lib: MatchLibConfig | None = None
    file_lib: FileLibConfig | None = None
    tofwerk_lib: TofwerkLibConfig | None = None
    thermo_lib: ThermoLibConfig | None = None
    sdk_lib: SdkLibConfig | None = None


def migrate_legacy_options(raw: dict, logger=None) -> dict:
    """
    Lift settings that moved between config sections in an earlier release.

    An env toml written for an older Mascope keeps working: the value is moved
    into its new home before validation, with a warning naming the move. The
    models ignore unknown keys, so without this an operator's override would
    silently revert to the default - a deployment that raised
    `tus_max_upload_gb` for a large-file instrument would drop back to 5 GB on
    upgrade without saying so.

    :param raw: The merged but unvalidated config dictionary, modified in place
    :type raw: dict
    :param logger: Optional logger for the migration warnings
    :return: The same dictionary, with legacy settings moved
    :rtype: dict
    """
    backend = raw.get("backend")
    if isinstance(backend, dict) and "tus_max_upload_gb" in backend:
        legacy = backend.pop("tus_max_upload_gb")
        meta = raw.setdefault("meta", {})
        if "tus_max_upload_gb" in meta:
            if logger:
                logger.warning(
                    "Ignoring [backend] tus_max_upload_gb: the setting moved to "
                    "[meta], which already sets it. Remove the [backend] line."
                )
        else:
            meta["tus_max_upload_gb"] = legacy
            if logger:
                logger.warning(
                    "[backend] tus_max_upload_gb has moved to [meta]; using the "
                    f"legacy value ({legacy}). Move it to the [meta] section of "
                    "your env config toml - the web uploader reads it from there."
                )
    return raw


class RuntimeConfigLoader:
    """
    Helper class to facilitate loading the configuration of
    the runtime.

    During initialization, the class loads mascope.toml files,
    combines them, resolves paths and log levels and validates
    all fields using a Pydantic model.

    The resulting validated configuration is exposed with
    the `config` property.

    This class is to be used with the `load_config` below.
    """

    _runtime: Runtime
    _raw: dict
    _resolved: RuntimeConfig

    def __init__(self, runtime: Runtime):
        """
        Initializes the runtime configuration:

        1. Load config with 3-layer overlay:
            - base.mascope.toml - Shared defaults for all modes
            - {mode}.mascope.toml (runtime lib) - Mode-specific defaults
            - {mode}.mascope.toml (env dir, optional) - Env-specific overrides
        2. Move settings that changed section in an earlier release into
           their new home, so an older env toml keeps working (see
           `migrate_legacy_options`).
        3. Resolve relative paths into absolute paths, using the
           runtime environment path (except for package paths,
           which resolve relative to the Mascope root path).
        4. Resolve log level for each module, using CLI arguments,
           toml settings and defaults.
        5. Validate the resulting dictionary using the Pydantic
            model for the configuration.

        :param runtime: The parent runtime
        :type runtime: Runtime
        """
        self._runtime = runtime

        config = self._load_tomls()
        config = migrate_legacy_options(config, self.runtime.logger)
        config = self._resolve_paths(config)
        config = self._resolve_loglevels(config)
        config = self._resolve_env_ports(config)
        config = self._validate_options(config)
        self._resolved = config

    @property
    def runtime(self):
        """
        The main runtime context
        """
        return self._runtime

    @property
    def config(self):
        """
        The loaded and resolved config
        """
        return self._resolved

    def _deep_merge(self, base: dict, overlay: dict) -> dict:
        """
        Deep merge overlay into base, preserving nested dicts.

        :param base: Base dictionary
        :param overlay: Overlay dictionary to merge
        :return: Merged dictionary
        """
        result = base.copy()
        for key, value in overlay.items():
            if (
                key in result
                and isinstance(result[key], dict)
                and isinstance(value, dict)
            ):
                # Recursively merge nested dicts
                result[key] = self._deep_merge(result[key], value)
            else:
                # Override with overlay value
                result[key] = value
        return result

    def _load_tomls(self) -> dict:
        """
        Load configuration with three-layer overlay system:

        1. base.mascope.toml (runtime library, git tracked)
           - Shared defaults for all modes
        2. {mode}.mascope.toml (runtime library, git tracked)
           - Mode-specific defaults (dev vs prod)
           - Overrides base settings
        3. {mode}.mascope.toml (env directory, not tracked)
           - Environment-specific overrides
           - Optional, for special cases only

        :return: Raw config dictionary
        :rtype: dict
        """
        # Layer 1: Base defaults (git tracked)
        base_path = self.runtime.path("base.mascope.toml")

        # Layer 2: Mode defaults from runtime library (git tracked)
        mode_base_path = self.runtime.path(f"{self.runtime.mode}.mascope.toml")

        # Layer 3: Env-specific overrides (not tracked, optional)
        mode_env_path = self.runtime.env.path(f"./{self.runtime.mode}.mascope.toml")

        # Debug output
        self.runtime.logger.trace("Config loading:")
        self.runtime.logger.trace(f"  Runtime env.name: {self.runtime.env.name}")
        self.runtime.logger.trace(f"  Runtime mode: {self.runtime.mode}")
        self.runtime.logger.trace(f"  Layer 1 (base): {base_path}")
        self.runtime.logger.trace(f"  Layer 2 (mode): {mode_base_path}")
        self.runtime.logger.trace(f"  Layer 3 (env):  {mode_env_path}")

        raw_config = {}
        # Apply layers in order: base → mode (lib) → mode (env)
        for path in [base_path, mode_base_path, mode_env_path]:
            if os.path.exists(path):
                self.runtime.logger.trace(f"  ✅ Loading: {path}")
                with open(path, "rb") as f:
                    # apply overlay
                    overlay = tomllib.load(f)
                    for module, module_overlay in overlay.items():
                        module_key = module.replace("-", "_")
                        if module_key not in raw_config:
                            raw_config[module_key] = {}

                        base_config = {"name": module, **raw_config[module_key]}
                        raw_config[module_key] = self._deep_merge(
                            base_config, module_overlay
                        )
            else:
                self.runtime.logger.trace(f"  ❌ Not found: {path}")

        return raw_config

    def _resolve_paths(self, unresolved: dict) -> dict:
        """
        Iterates through an unresolved config or - when recursing -
        a subdict thereof. When encountering a path-like string value,
        it replaces relative paths with absolute paths. Resolution
        uses the runtime env path by default, except for package
        paths which are resolved relative to the runtime root path.

        :param unresolved: Raw config dictionary with unresolved paths
        :type unresolved: dict
        :return: Resolved config dictionary
        :rtype: dict
        """
        resolved = {}
        for key, value in unresolved.items():
            if isinstance(value, dict):
                # recurse for subconfigs
                resolved[key] = self._resolve_paths(value)
            elif isinstance(value, str):
                # resolve relative paths
                if value.startswith("./"):
                    # resolve against env
                    resolved_path = self.runtime.env.realpath(value)
                    resolved[key] = resolved_path

                    # Debug logging for important paths
                    if key in [
                        "filestore",
                        "database",
                        "log_path",
                        "source",
                    ]:
                        self.runtime.logger.trace(
                            f"Path resolved: {key}: '{value}' → '{resolved_path}'"
                        )

                else:
                    # keep non-relative paths as-is
                    resolved[key] = value
            else:
                resolved[key] = value
        return resolved

    def _resolve_loglevels(self, unresolved: dict, fallback: LogLevel = "info") -> dict:
        """
        Iterates through the root level of the unresolved config,
        resolving log levels based on various inputs.

        :param unresolved: Config dictionary with unresolved log levels
        :type unresolved: dict
        :param fallback: Fallback log level if none specified (default: "info")
        :type fallback: LogLevel
        :return: Resolved config dictionary
        :rtype: dict
        """

        resolved = {}
        meta = unresolved.get("meta")
        meta_log_level = meta.get("log_level") if meta else None
        cli_env_var = os.environ.get("MASCOPE_LOGLEVEL")
        cli_log_level = cli_env_var.lower() if cli_env_var else None
        for sub_key, sub_config in unresolved.items():
            # init subconfig
            resolved[sub_key] = unresolved[sub_key]
            # resolve log levels
            config_log_level = sub_config.get("log_level")
            resolved[sub_key]["log_level"] = (
                cli_log_level  # cli overrides all
                or config_log_level  # otherwise use module level
                or meta_log_level  # otherwise use the meta level
                or fallback  # and worst case fall back to info
            )
        return resolved

    def _resolve_env_ports(self, unresolved: dict) -> dict:
        """
        Apply per-process port overrides from the environment.

        ``MASCOPE_API_PORT`` overrides ``meta.api_port`` (the backend bind
        port, which the frontend and file-converter also target) and
        ``MASCOPE_FRONTEND_PORT`` overrides ``frontend.port`` (the Vite
        dev-server listen port). This mirrors the ``MASCOPE_ENV`` /
        ``MASCOPE_LOGLEVEL`` overrides: it lets several checkouts on one
        machine run their own stack on distinct ports without editing config
        files or clobbering shared state. Absent or non-integer values are
        ignored, falling back to the config defaults.

        :param unresolved: Config dictionary after log-level resolution.
        :type unresolved: dict
        :return: Config dictionary with env port overrides applied.
        :rtype: dict
        """
        overrides = (
            ("MASCOPE_API_PORT", "meta", "api_port"),
            ("MASCOPE_FRONTEND_PORT", "frontend", "port"),
        )
        for env_var, section, key in overrides:
            raw = os.environ.get(env_var)
            if not raw:
                continue
            try:
                port = int(raw)
            except ValueError:
                self.runtime.logger.warning(
                    f"Ignoring {env_var}={raw!r}: not a valid port number"
                )
                continue
            if section in unresolved and isinstance(unresolved[section], dict):
                unresolved[section][key] = port
        return unresolved

    def _validate_options(self, unvalidated: dict) -> RuntimeConfig:
        """
        Validates the resolved but unvalidated config dict using
        the Pydantic model.

        :param unvalidated: Resolved config dictionary without validation
        :type unvalidated: dict
        :return: Validated configuration model
        :rtype: RuntimeConfig
        """
        return RuntimeConfig(**unvalidated)


def load_config(runtime: Runtime) -> RuntimeConfig:
    """
    Init a runtime config loader using the runtime,
    and return the resolved and validated config.

    :param runtime: The runtime context
    :type runtime: Runtime
    :return: The runtime configuration
    :rtype: RuntimeConfig
    """
    loader = RuntimeConfigLoader(runtime)
    return loader.config
