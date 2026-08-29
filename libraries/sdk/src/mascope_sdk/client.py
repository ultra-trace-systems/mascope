"""Mascope API Client.

This module provides the main client class for interacting with the Mascope API.
"""

import os
import re
import sys
from pathlib import Path
from typing import Any

import pandas as pd
from dotenv import dotenv_values, find_dotenv
from loguru import logger
from tqdm import tqdm

from .exceptions import ConfigurationError


# Track whether we've already configured the SDK's loguru handler
_log_handler_id: int | None = None


def _configure_logging(env_vars: dict[str, str | None]) -> None:
    """Configure the SDK's loguru handler (env var > .env > default INFO)."""
    global _log_handler_id

    level = (
        os.environ.get("MASCOPE_SDK_LOG_LEVEL")
        or env_vars.get("MASCOPE_SDK_LOG_LEVEL")
        or "INFO"
    ).upper()

    if _log_handler_id is not None:
        logger.remove(_log_handler_id)
    else:
        # First call: remove loguru's default handler to avoid double logging
        logger.remove()

    _log_handler_id = logger.add(
        lambda msg: tqdm.write(
            msg, end="", file=sys.stderr
        ),  # Use tqdm.write for clean logging alongside progress bars
        level=level,
        filter="mascope_sdk",
        colorize=True,
    )


class MascopeClient:
    """Client for interacting with the Mascope API.

    The client can be configured in several ways:

    1. **Environment variables** (recommended for production):
        Set `MASCOPE_URL` and `MASCOPE_ACCESS_TOKEN` environment variables.

    2. **`.env` file** (recommended for Jupyter notebooks):
        Create a `.env` file in your working directory or any parent directory::

            MASCOPE_URL=https://example.mascope.app
            MASCOPE_ACCESS_TOKEN=your-api-token

    3. **Explicit parameters**:
        Pass `url` and `access_token` directly to the constructor.

    Configuration priority (highest to lowest):
        1. Constructor parameters
        2. Environment variables
        3. `.env` file

    :ivar url: The base URL of the Mascope instance.
    :vartype url: str
    :ivar access_token: The API access token.
    :vartype access_token: str
    :ivar workspace_id: The resolved workspace ID.
    :vartype workspace_id: str
    :ivar workspace_name: The resolved workspace name.
    :vartype workspace_name: str
    :ivar workspaces: Resource for workspace operations.
    :vartype workspaces: WorkspacesResource
    :ivar datasets: Resource for dataset operations.
    :vartype datasets: DatasetsResource
    :ivar batches: Resource for sample batch operations.
    :vartype batches: BatchesResource
    :ivar samples: Resource for sample operations.
    :vartype samples: SamplesResource
    :ivar matching: Resource for compound matching operations.
    :vartype matching: MatchingResource
    :ivar cheminfo: Resource for chemical information queries.
    :vartype cheminfo: ChemInfoResource
    :ivar peak_assignments: Resource for reading peak-centric assignment results.
    :vartype peak_assignments: PeakAssignmentsResource

    Example::

        from mascope_sdk import MascopeClient

        # Auto-configure from .env, auto-selects workspace if only one
        mascope = MascopeClient()

        # Explicit workspace selection
        mascope = MascopeClient(workspace="My Workspace")

        # List all datasets of the selected workspace
        datasets = mascope.datasets.list()

        # Get samples from a batch
        samples = mascope.samples.list(batch="My Batch")

        # Get spectrum data
        spectrum = mascope.samples.get_spectrum(sample_id="sample-123")
    """

    def __init__(
        self,
        url: str | None = None,
        access_token: str | None = None,
        *,
        workspace: str | None = None,
        env_file: Path | str | None = None,
        verify_ssl: bool | None = None,
        timeout: float | tuple[int, int] | None = None,
        service_name: str = "mascope_sdk",
    ):
        """Initialize the Mascope client.

        :param url: The base URL of the Mascope instance.
                    Falls back to ``MASCOPE_URL`` environment variable.
        :type url: str, optional
        :param access_token: The API access token.
                            Falls back to ``MASCOPE_ACCESS_TOKEN`` environment variable.
        :type access_token: str, optional
        :param workspace: Workspace name, substring, or ID to select. If not
                          provided and the user belongs to exactly one workspace,
                          it is auto-selected. If the user belongs to multiple
                          workspaces, a ``ConfigurationError`` is raised listing
                          the available options.
        :type workspace: str, optional
        :param env_file: Optional path to a ``.env`` file. If not provided, searches
                        for ``.env`` in the current directory and parent directories.
        :type env_file: Path | str | None, optional
        :param verify_ssl: Whether to verify SSL certificates. Defaults to True.
        :type verify_ssl: bool, optional
        :param timeout: Request timeout in seconds. A single number sets the READ
                        timeout (connect stays at the default); a (connect, read)
                        tuple is used as-is. Falls back to the ``MASCOPE_SDK_TIMEOUT``
                        environment variable, else (30, 300). Raise it for slow
                        servers (e.g. heavy match_compounds scoring).
        :type timeout: float | tuple[int, int], optional
        :param service_name: Service name for request headers.
        :type service_name: str, optional
        :raises ConfigurationError: If URL or access token cannot be determined,
                                    or if workspace cannot be resolved.

        Example::

            # Using .env file (automatic), auto-selects workspace
            mascope = MascopeClient()

            # Explicit workspace selection
            mascope = MascopeClient(workspace="My Workspace")

            # Explicit configuration
            mascope = MascopeClient(
                url="https://example.mascope.app",
                access_token="your-token",
                workspace="My Workspace",
            )

            # Custom .env file location
            mascope = MascopeClient(env_file="/path/to/.env")
        """
        # Load .env file
        dotenv_path = (
            str(env_file) if env_file is not None else find_dotenv(usecwd=True)
        )
        env_vars = dotenv_values(dotenv_path) if dotenv_path else {}

        # Resolve URL (parameter > env var > .env file)
        self._url = url or os.environ.get("MASCOPE_URL") or env_vars.get("MASCOPE_URL")
        if not self._url:
            raise ConfigurationError(
                "Mascope URL not configured. "
                "Set MASCOPE_URL environment variable, create a .env file, "
                "or pass url parameter to MascopeClient()."
            )

        # Normalize URL (remove trailing slash)
        self._url = self._url.rstrip("/")

        # Resolve access token (parameter > env var > .env file)
        self._access_token = (
            access_token
            or os.environ.get("MASCOPE_ACCESS_TOKEN")
            or env_vars.get("MASCOPE_ACCESS_TOKEN")
        )
        if not self._access_token:
            raise ConfigurationError(
                "Mascope access token not configured. "
                "Set MASCOPE_ACCESS_TOKEN environment variable, create a .env file, "
                "or pass access_token parameter to MascopeClient()."
            )

        # Resolve verify_ssl (parameter > env var > .env > default True)
        if verify_ssl is None:
            env_val = os.environ.get("MASCOPE_SDK_VERIFY_SSL") or env_vars.get(
                "MASCOPE_SDK_VERIFY_SSL"
            )
            self._verify_ssl = (
                env_val.lower() not in ("0", "false", "no") if env_val else True
            )
        else:
            self._verify_ssl = verify_ssl

        # Resolve timeout (parameter > env var > default (30, 300)). A single number
        # is the READ timeout (connect stays at the default); a (connect, read) tuple
        # is used as-is. $MASCOPE_SDK_TIMEOUT raises it for slow servers without code
        # changes (mirrors MASCOPE_SDK_VERIFY_SSL).
        from ._http import DEFAULT_TIMEOUT

        if timeout is None:
            env_val = os.environ.get("MASCOPE_SDK_TIMEOUT") or env_vars.get(
                "MASCOPE_SDK_TIMEOUT"
            )
            try:
                self._timeout = (
                    (DEFAULT_TIMEOUT[0], float(env_val)) if env_val else DEFAULT_TIMEOUT
                )
            except (TypeError, ValueError):
                self._timeout = DEFAULT_TIMEOUT
        elif isinstance(timeout, (int, float)):
            self._timeout = (DEFAULT_TIMEOUT[0], float(timeout))
        else:
            self._timeout = tuple(timeout)

        self._service_name = service_name

        # Configure loguru log level (env var > .env > default INFO)
        _configure_logging(env_vars)

        # Metadata cache for dataset/batch/sample listings
        self._cache: dict[str, pd.DataFrame] = {}

        # Initialize resource objects (lazy imports to avoid circular dependencies)
        self._workspaces: Any = None
        self._datasets: Any = None
        self._batches: Any = None
        self._samples: Any = None
        self._matching: Any = None
        self._cheminfo: Any = None
        self._ionization: Any = None
        self._peak_assignments: Any = None

        # Resolve workspace (fetch list, then resolve or auto-select)
        self._workspace_id, self._workspace_name = self._resolve_workspace(workspace)

    @property
    def url(self) -> str:
        """The base URL of the Mascope instance."""
        return self._url  # type: ignore

    @property
    def access_token(self) -> str:
        """The API access token."""
        return self._access_token  # type: ignore

    @property
    def workspace_id(self) -> str:
        """The resolved workspace ID."""
        return self._workspace_id

    @property
    def workspace_name(self) -> str:
        """The resolved workspace name."""
        return self._workspace_name

    @property
    def workspaces(self) -> "WorkspacesResource":
        """Resource for workspace operations."""
        if self._workspaces is None:
            from .resources.workspaces import WorkspacesResource

            self._workspaces = WorkspacesResource(self)
        return self._workspaces

    @property
    def datasets(self) -> "DatasetsResource":
        """Resource for dataset operations."""
        if self._datasets is None:
            from .resources.datasets import DatasetsResource

            self._datasets = DatasetsResource(self)
        return self._datasets

    @property
    def batches(self) -> "BatchesResource":
        """Resource for sample batch operations."""
        if self._batches is None:
            from .resources.batches import BatchesResource

            self._batches = BatchesResource(self)
        return self._batches

    @property
    def samples(self) -> "SamplesResource":
        """Resource for sample operations."""
        if self._samples is None:
            from .resources.samples import SamplesResource

            self._samples = SamplesResource(self)
        return self._samples

    @property
    def matching(self) -> "MatchingResource":
        """Resource for compound matching operations."""
        if self._matching is None:
            from .resources.matching import MatchingResource

            self._matching = MatchingResource(self)
        return self._matching

    @property
    def cheminfo(self) -> "ChemInfoResource":
        """Resource for chemical information queries."""
        if self._cheminfo is None:
            from .resources.cheminfo import ChemInfoResource

            self._cheminfo = ChemInfoResource(self)
        return self._cheminfo

    @property
    def ionization(self) -> "IonizationResource":
        """Resource for ionization mechanism operations."""
        if self._ionization is None:
            from .resources.ionization import IonizationResource

            self._ionization = IonizationResource(self)
        return self._ionization

    @property
    def peak_assignments(self) -> "PeakAssignmentsResource":
        """Resource for reading peak-centric assignment results."""
        if self._peak_assignments is None:
            from .resources.peak_assignments import PeakAssignmentsResource

            self._peak_assignments = PeakAssignmentsResource(self)
        return self._peak_assignments

    def _resolve_workspace(self, workspace: str | None) -> tuple[str, str]:
        """Resolve workspace argument to a workspace ID and name.

        If *workspace* is provided, resolves it by exact ID or name match.
        If not provided, auto-selects when exactly one workspace is available.

        :param workspace: Workspace name or literal substring (or ID); a
            compiled ``re.Pattern`` matches by regex.
        :raises ConfigurationError: If resolution fails.
        :return: Tuple of (workspace_id, workspace_name).
        """
        from ._resolve import resolve_id

        ws_df = self.workspaces.list()

        if ws_df is None or ws_df.empty:
            raise ConfigurationError("No workspaces found for the current user.")

        if workspace is not None:
            try:
                ws_id = resolve_id(
                    workspace, ws_df, "workspace_id", "workspace_name", "workspace"
                )
            except ValueError as exc:
                raise ConfigurationError(str(exc)) from exc
        elif len(ws_df) == 1:
            ws_id = ws_df.iloc[0]["workspace_id"]
        else:
            available = ws_df["workspace_name"].tolist()
            raise ConfigurationError(
                f"Multiple workspaces available: {available}. "
                f"Please specify one with MascopeClient(workspace=...)."
            )

        ws_name = ws_df.loc[ws_df["workspace_id"] == ws_id, "workspace_name"].iloc[0]
        logger.info("Selected workspace '{}' ({})", ws_name, ws_id)
        return ws_id, ws_name

    def load_peaks(
        self,
        dataset: "str | re.Pattern",
        batches: "str | re.Pattern | None" = None,
        *,
        samples: "str | re.Pattern | None" = None,
        exact: bool = False,
        matches: bool = True,
        areas: bool = True,
        heights: bool = True,
        average: bool = True,
        confirm_above: int | None = 100,
        max_workers: int = 8,
    ) -> pd.DataFrame | None:
        """Load peaks for all samples across one or more batches.

        This is a high-level convenience method that handles the typical workflow
        of selecting a dataset, filtering batches by name, iterating all samples,
        and concatenating peak data into a single DataFrame enriched with batch and
        sample metadata.

        Requests are made concurrently for better performance. A progress bar is
        displayed during loading.

        :param dataset: Dataset name or literal substring (or ID); pass a
                        compiled ``re.Pattern`` to match by regex.
        :type dataset: str | re.Pattern
        :param batches: Optional case-insensitive filter on batch names. A string
                        is a literal substring and may select several batches at
                        once (e.g. ``"blank"`` matches every batch whose name
                        contains "blank"); pass a compiled ``re.Pattern`` to match
                        by regex. If not provided, all batches in the dataset are
                        loaded.
        :type batches: str | re.Pattern, optional
        :param samples: Optional case-insensitive filter on sample names, same
                        semantics as ``batches``.
        :type samples: str | re.Pattern, optional
        :param exact: Match a string ``batches`` / ``samples`` against the whole
                      name instead of as a substring. Use this to select a single
                      named batch. Not valid with a compiled pattern. Defaults to
                      False.
        :type exact: bool
        :param matches: Include matched compounds/ions/isotopes. Defaults to True.
        :type matches: bool
        :param areas: Include peak areas. Defaults to True.
        :type areas: bool
        :param heights: Include peak heights. Defaults to True.
        :type heights: bool
        :param average: Return averaged data across time. Defaults to True.
        :type average: bool
        :param confirm_above: If the number of samples exceeds this threshold,
                              an interactive confirmation prompt is shown before
                              loading starts. Set to ``None`` to disable.
                              Defaults to 100.
        :type confirm_above: int | None
        :param max_workers: Maximum number of concurrent requests. Defaults to 8.
        :type max_workers: int
        :return: A DataFrame containing all peaks enriched with columns:

            - ``sample_batch_name``: Name of the batch the sample belongs to
            - ``sample_item_name``: Name of the sample
            - ``datetime_utc``: Measurement start timestamp (UTC)

            Plus all columns from
            :meth:`~mascope_sdk.resources.samples.SamplesResource.get_peaks`.

            When a peak matches multiple isotopes it is expanded
            into one row per match.  Use ``target_ion_id`` /
            ``target_compound_id`` for grouping to avoid
            double-counting peaks whose matches share the same
            formula.

            Returns None if no peaks are found.
        :rtype: pd.DataFrame | None
        :raises ValueError: If the dataset or batches cannot be resolved.
        :raises KeyboardInterrupt: If the user declines the confirmation prompt.

        Example::

            mascope = MascopeClient()

            # Load all peaks from batches containing "Uronium"
            peaks = mascope.load_peaks(
                dataset="My Dataset",
                batches="Uronium",
            )

            # Filter samples by regex (compiled pattern)
            import re

            peaks = mascope.load_peaks(
                dataset="My Dataset",
                samples=re.compile("blank|control", re.IGNORECASE),
            )

            # Load all peaks, skip confirmation
            peaks = mascope.load_peaks(
                dataset="My Dataset",
                confirm_above=None,
            )
        """
        from ._loaders import load_peaks as _load_peaks

        return _load_peaks(
            self,
            dataset,
            batches,
            samples=samples,
            exact=exact,
            matches=matches,
            areas=areas,
            heights=heights,
            average=average,
            confirm_above=confirm_above,
            max_workers=max_workers,
        )

    def load_peak_timeseries(
        self,
        dataset: "str | re.Pattern",
        batches: "str | re.Pattern | None" = None,
        *,
        samples: "str | re.Pattern | None" = None,
        exact: bool = False,
        compound: str | list[str] | None = None,
        ion: str | list[str] | None = None,
        isotope: str | list[str] | None = None,
        confirm_above: int | None = 20,
        max_workers: int = 8,
    ) -> pd.DataFrame | None:
        """Load intra-sample peak timeseries for matched peaks across batches.

        Resolves a compound, ion, or isotope formula to the corresponding peak
        IDs via match data, then fetches the per-scan timeseries for each peak
        in each sample. The hierarchy is:
        compound -> ions -> isotopes -> peaks (1:1).

        Provide exactly one of ``compound``, ``ion``, or ``isotope``. Each
        accepts a single string or a list of strings to load timeseries for
        multiple targets in a single pass.

        Requests are made concurrently for better performance. Two progress bars
        are displayed: one for peak discovery and one for timeseries loading.

        :param dataset: Dataset name or literal substring (or ID); pass a
                        compiled ``re.Pattern`` to match by regex.
        :type dataset: str | re.Pattern
        :param batches: Optional case-insensitive filter on batch names. A string
                        is a literal substring; pass a compiled ``re.Pattern`` for
                        regex.
        :type batches: str | re.Pattern, optional
        :param samples: Optional case-insensitive filter on sample names, same
                        semantics as ``batches``.
        :type samples: str | re.Pattern, optional
        :param exact: Match a string ``batches`` / ``samples`` against the whole
                      name instead of as a substring. Not valid with a compiled
                      pattern. Defaults to False.
        :type exact: bool
        :param compound: Target compound name(s) or formula(s).
        :type compound: str | list[str], optional
        :param ion: Target ion formula(s).
        :type ion: str | list[str], optional
        :param isotope: Target isotope formula(s).
        :type isotope: str | list[str], optional
        :param confirm_above: If the number of samples exceeds this threshold,
                              an interactive confirmation prompt is shown before
                              loading starts. Set to ``None`` to disable.
                              Defaults to 20.
        :type confirm_above: int | None
        :param max_workers: Maximum number of concurrent requests. Defaults to 8.
        :type max_workers: int
        :return: A DataFrame with one row per time point per peak, containing:

                 - ``sample_batch_name``: Batch name
                 - ``sample_item_id``: Sample ID
                 - ``sample_item_name``: Sample name
                 - ``datetime_utc``: Absolute datetime per data point (UTC)
                 - ``peak_id``: Peak identifier
                 - ``mz``: Actual m/z of the peak
                 - ``target_compound_name``, ``target_compound_formula``,
                   ``target_ion_formula``, ``target_isotope_formula``: Match metadata
                 - ``time``: Relative time in seconds within the sample
                 - ``height``: Intensity at each time point

                 Returns None if no matching peaks are found.
        :rtype: pd.DataFrame | None
        :raises ValueError: If zero or more than one formula parameter is provided.
        :raises KeyboardInterrupt: If the user declines the confirmation prompt.

        Example::

            mascope = MascopeClient()

            # Single compound
            ts = mascope.load_peak_timeseries(
                dataset="My Dataset",
                batches="Uronium",
                compound="CH4N2O",
            )

            # Multiple compounds in one call
            ts = mascope.load_peak_timeseries(
                dataset="My Dataset",
                compound=["CH4N2O", "Lactic acid"],
            )
        """
        from ._loaders import load_peak_timeseries as _load_peak_timeseries

        return _load_peak_timeseries(
            self,
            dataset,
            batches,
            samples=samples,
            exact=exact,
            compound=compound,
            ion=ion,
            isotope=isotope,
            confirm_above=confirm_above,
            max_workers=max_workers,
        )

    def load_peaks_by_stage(
        self,
        sample: str,
        stages: list[tuple[float, float] | tuple[float, float, str]],
        *,
        matches: bool = True,
        areas: bool = True,
        heights: bool = True,
        max_workers: int = 8,
    ) -> pd.DataFrame | None:
        """Load averaged peaks for each time-range stage of a single sample.

        For each stage (time range), requests the averaged peak list and
        concatenates everything into a single DataFrame. Useful when a
        measurement has distinct phases (e.g. blank, sample introduction, wash).

        :param sample: Sample name or sample ID.
        :type sample: str
        :param stages: List of time-range tuples. Each element can be
                       ``(t_min, t_max)`` or ``(t_min, t_max, name)`` where
                       *name* is a human-readable label for the stage.
        :type stages: list[tuple[float, float] | tuple[float, float, str]]
        :param matches: Include matched compounds/ions/isotopes. Defaults to True.
        :type matches: bool
        :param areas: Include peak areas. Defaults to True.
        :type areas: bool
        :param heights: Include peak heights. Defaults to True.
        :type heights: bool
        :param max_workers: Maximum number of concurrent requests. Defaults to 8.
        :type max_workers: int
        :return: A DataFrame with stage-enriched peak data. Extra columns:

                 - ``stage``: 0-based stage index
                 - ``stage_name``: Stage label (or None)
                 - ``t_min`` / ``t_max``: Time range in seconds

                 Returns None if no peaks are found.
        :rtype: pd.DataFrame | None

        Example::

            mascope = MascopeClient()

            stages = [
                (0, 30, "blank"),
                (30, 120, "sample"),
                (120, 180, "wash"),
            ]

            peaks = mascope.load_peaks_by_stage(
                sample="my-sample-id",
                stages=stages,
            )

            peaks.groupby("stage_name")["area"].sum()
        """
        from ._loaders import load_peaks_by_stage as _load_peaks_by_stage

        return _load_peaks_by_stage(
            self,
            sample,
            stages,
            matches=matches,
            areas=areas,
            heights=heights,
            max_workers=max_workers,
        )

    def load_assignments(
        self,
        dataset: "str | re.Pattern",
        batches: "str | re.Pattern | None" = None,
        *,
        samples: "str | re.Pattern | None" = None,
        exact: bool = False,
        run: str = "latest",
        tier: str | None = None,
        source: str | None = None,
        confirm_above: int | None = 100,
        max_workers: int = 8,
    ) -> pd.DataFrame | None:
        """Load peak assignments for all samples across one or more batches.

        The peak-assignment counterpart of :meth:`load_peaks`: resolves the
        dataset/batch/sample selection, reads each sample's latest completed
        peak assignment run via
        :meth:`~mascope_sdk.resources.peak_assignments.PeakAssignmentsResource.get`,
        and concatenates everything into a single DataFrame enriched with batch
        and sample metadata.

        Read-only: it returns whatever runs already exist. Samples without a
        completed assignment run contribute nothing (and are logged) rather
        than being assigned on the fly - launch runs from the Mascope app.

        Requests are made concurrently for better performance. A progress bar
        is displayed during loading.

        :param dataset: Dataset name or literal substring (or ID); pass a
                        compiled ``re.Pattern`` to match by regex.
        :type dataset: str | re.Pattern
        :param batches: Optional case-insensitive filter on batch names. A
                        string is a literal substring and may select several
                        batches at once; pass a compiled ``re.Pattern`` to
                        match by regex. If not provided, all batches in the
                        dataset are loaded.
        :type batches: str | re.Pattern, optional
        :param samples: Optional case-insensitive filter on sample names, same
                        semantics as ``batches``.
        :type samples: str | re.Pattern, optional
        :param exact: Match a string ``batches`` / ``samples`` against the
                      whole name instead of as a substring. Not valid with a
                      compiled pattern. Defaults to False.
        :type exact: bool
        :param run: Which run to read per sample. Only ``"latest"`` (the
                    latest completed run of each sample) is supported; run IDs
                    are per-sample and cannot be given across samples.
        :type run: str
        :param tier: Filter by confidence tier: ``assigned`` | ``candidate``
                     | ``below_assignability`` | ``unassigned``. The server
                     still accepts the legacy spelling ``identified`` for
                     ``assigned``.
        :type tier: str, optional
        :param source: Filter by assignment source: ``database`` |
                       ``untargeted``.
        :type source: str, optional
        :param confirm_above: If the number of samples exceeds this threshold,
                              an interactive confirmation prompt is shown
                              before loading starts. Set to ``None`` to
                              disable. Defaults to 100.
        :type confirm_above: int | None
        :param max_workers: Maximum number of concurrent requests. Defaults to 8.
        :type max_workers: int
        :return: A DataFrame containing all assignments enriched with columns:

            - ``sample_batch_name``: Name of the batch the sample belongs to
            - ``sample_item_name``: Name of the sample
            - ``datetime_utc``: Measurement start timestamp (UTC)

            Plus all columns from
            :meth:`~mascope_sdk.resources.peak_assignments.PeakAssignmentsResource.get`
            (one row per observed peak; core rows only - fetch
            ``alternatives`` / ``provenance`` per assignment with
            :meth:`~mascope_sdk.resources.peak_assignments.PeakAssignmentsResource.detail`).

            Returns None if no assignments are found.
        :rtype: pd.DataFrame | None
        :raises ValueError: If the dataset or batches cannot be resolved, or
                            ``run`` is not ``"latest"``.
        :raises KeyboardInterrupt: If the user declines the confirmation prompt.

        Example::

            mascope = MascopeClient()

            # Assignments of every sample in matching batches
            assignments = mascope.load_assignments(
                dataset="My Dataset",
                batches="Uronium",
            )

            # Assigned, untargeted-sourced peaks only
            assignments = mascope.load_assignments(
                dataset="My Dataset",
                tier="assigned",
                source="untargeted",
            )
        """
        from ._loaders import load_assignments as _load_assignments

        return _load_assignments(
            self,
            dataset,
            batches,
            samples=samples,
            exact=exact,
            run=run,
            tier=tier,
            source=source,
            confirm_above=confirm_above,
            max_workers=max_workers,
        )

    def clear_cache(self) -> None:
        """Clear the metadata cache.

        Call this when datasets, batches, or samples have changed on the
        server and you want subsequent calls to fetch fresh data.

        Example::

            mascope.clear_cache()
        """
        self._cache.clear()

    def __repr__(self) -> str:
        return f"MascopeClient(url='{self._url}', workspace='{self._workspace_name}')"


# Type hints for lazy-loaded resources
from typing import TYPE_CHECKING  # noqa: E402


if TYPE_CHECKING:
    from .resources.batches import BatchesResource
    from .resources.cheminfo import ChemInfoResource
    from .resources.datasets import DatasetsResource
    from .resources.ionization import IonizationResource
    from .resources.matching import MatchingResource
    from .resources.peak_assignments import PeakAssignmentsResource
    from .resources.samples import SamplesResource
    from .resources.workspaces import WorkspacesResource
