"""Peak assignments resource for the Mascope SDK.

Reads persisted peak-centric assignment results: every observed peak of a
sample with its committed formula, adduct, evidence, and confidence tier, as
produced by a server-side assignment run. Launches such a run on a sample,
records a verdict on one of its assignments, and scores an arbitrary
composition against a sample (see ``docs/dev/sdk_peak_assignment.md``).
Reads the verification verdicts of one sample, or of a whole batch or
dataset in one request.

Publishes a run an engine outside Mascope computed, with
:meth:`PeakAssignmentsResource.import_run`.

The two writes, :meth:`PeakAssignmentsResource.assign` and
:meth:`PeakAssignmentsResource.verify`, are sent once and never again on a
lost answer: a verdict sent twice is two labels in the confidence
calibration, and a launch sent twice is refused as already in flight. So are
the first and the last request of an import.
"""

import time
import uuid
from collections.abc import Callable, Mapping
from datetime import datetime
from typing import Any, TypedDict

import pandas as pd
from loguru import logger

from .._http import http_get
from ..exceptions import (
    AssignmentInFlightError,
    AssignmentRunError,
    AssignmentTimeoutError,
    ConflictError,
    ImportValidationError,
    MascopeAPIError,
    MascopeConnectionError,
    MascopeError,
    SampleIneligibleError,
    ServerError,
    ValidationError,
)
from . import _assignment_import as _import
from ._base import (
    BaseResource,
    Needs,
    _coerce_datetime_columns,
    _coerce_utc_columns,
)


#: Rows requested per page when reading a run. The list endpoint caps a page
#: at 5000 rows (its own default is 1000); the SDK asks for the cap to
#: minimise round trips and pages until it has ``total`` rows.
PAGE_LIMIT = 5000

#: The run statuses nothing moves on from. ``completed`` has a ledger;
#: ``failed`` and ``cancelled`` have none.
TERMINAL_STATUSES = ("completed", "failed", "cancelled")

#: The code the server gives a 422 for a sample it will not assign, which
#: tells it apart from a request body it could not read (also 422).
SAMPLE_INELIGIBLE_CODE = "sample_ineligible"

#: The code the server gives a 409 that refused a request only because another
#: request on the sample was still running: nothing was applied, and the same
#: request goes through once that one finishes.
CLAIM_HELD_CODE = "assignment_claim_held"

#: How a server from before :data:`CLAIM_HELD_CODE` ends that 409's message,
#: and no other.
_CLAIM_HELD_PROSE = "Retry when it has finished."

#: Times a request the claim refuses is sent in all, and the seconds between
#: two sends: doubling from the first, up to the last. About five minutes in
#: all, which outlasts the slowest chunk a server applies.
CLAIM_ATTEMPTS = 20
CLAIM_PAUSES = (2.0, 15.0)

#: The role the two writes need, and the switch they need on the server.
_EDITOR = "editor on the sample, on a server with peak assignment enabled"

#: Default seconds between two looks at a launched run.
DEFAULT_POLL_INTERVAL = 5.0

#: Default seconds to wait for a launched run before handing back its id.
DEFAULT_WAIT_TIMEOUT = 1800.0

#: An import whose frame holds fewer rows than this share of the sample's
#: latest completed run is warned about: the batch ledger takes the sample's
#: whole contribution from the newest run, so the peaks left out leave it.
COVERAGE_WARNING_SHARE = 0.9


class AssignmentConfig(TypedDict, total=False):
    """The settings of one run, as the server's ``PeakAssignmentConfig``.

    Every key is optional: one left out takes the server's default, which
    for ``mz_precision_ppm``, ``formula_ranges`` and ``max_untargeted_peaks``
    means what the resolved ``profile`` gives. The server checks the values
    and answers a bad one with a 422; a key it does not know, it ignores.
    """

    #: Search the peaks the curated library left unassigned (Stage B).
    run_untargeted: bool
    #: Reagent chemistry preset: ``auto`` (read off the sample's mechanisms),
    #: ``none``, or a preset name.
    profile: str
    #: Sampled-matrix preset: ``auto`` (the profile's), ``none``, or a name.
    context: str
    #: m/z tolerance of the untargeted search, in ppm.
    mz_precision_ppm: float
    #: Element ranges of the untargeted search, e.g. ``"C0-80 H0-160 O0-50"``.
    formula_ranges: str
    #: How many of the most intense unassigned peaks the search takes.
    max_untargeted_peaks: int
    #: The least intensity a peak needs to enter the untargeted search.
    peak_intensity_threshold: float
    #: How many runner-up candidates are kept per peak.
    max_alternatives: int
    #: Evidence at or above which a row is tiered ``assigned``. The server
    #: also reads the old name ``identified_threshold``.
    assigned_threshold: float
    #: Evidence at or above which a row is tiered ``candidate``.
    candidate_threshold: float


def _is_lost(error: Exception) -> bool:
    """Whether a write's answer was lost, rather than the write refused."""
    if isinstance(error, MascopeConnectionError):
        return True
    return isinstance(error, ServerError) and error.status_code in (502, 503, 504)


def _latest_completed(runs: pd.DataFrame | None) -> pd.Series | None:
    """The newest completed run of a :meth:`~PeakAssignmentsResource.list_runs`
    frame, which is the one a ledger read without a run id reads."""
    if runs is None:
        return None
    completed = runs[runs["status"] == "completed"]
    if completed.empty:
        return None
    return completed.sort_values(
        "peak_assignment_run_utc_created", ascending=False
    ).iloc[0]


def _is_claim_held(error: ConflictError) -> bool:
    """Whether a 409 refused a request only because another one was running.

    By the code; a server from before the code says it in its message alone.
    """
    if error.code is not None:
        return error.code == CLAIM_HELD_CODE
    return error.message.endswith(_CLAIM_HELD_PROSE)


def _extended(error: Exception, extra: str) -> Exception:
    """The same kind of error as *error*, *extra* added to its message."""
    if isinstance(error, MascopeConnectionError):
        return type(error)(f"{error.message} {extra}", url=error.url)
    return type(error)(
        f"{error.message} {extra}",
        status_code=error.status_code,
        url=error.url,
        body=error.body,
    )


def _answer_lost(error: Exception, what: str) -> Exception:
    """The error of a write whose answer did not arrive, saying so.

    The server may have carried the write out: the request can reach it and
    the answer be lost on the way back, or a proxy give up on a request the
    server goes on to finish. Such a write is not sent again, so the caller
    is told what to look at before sending it themselves.

    :param error: What the HTTP layer raised.
    :param what: Where the write would show if it was carried out.
    :return: The same kind of error, its message extended; *error* itself
        when it is not a lost answer.
    """
    if not _is_lost(error):
        return error
    return _extended(
        error, f"The server may have carried it out: {what} before sending it again."
    )


class PeakAssignmentsResource(BaseResource):
    """Resource for reading peak-centric assignment results.

    A peak assignment run assigns a composition to *every* observed peak of a
    sample - database-known targets first (Stage A), then untargeted search
    (Stage B) - arbitrates a single owner per peak, and files each assignment
    into a confidence tier. A run is launched from the Mascope app, on
    ingest, or with :meth:`assign`; this resource reads the persisted
    results, and records a person's verdict on one of them with
    :meth:`verify`.

    A person can also overrule the engine on a single row from the app's peak
    inspector - promoting a close alternative, or committing a re-search hit.
    Such a row reads back with ``source`` ``manual`` (see :meth:`get`), which
    is why a run does not partition into ``database`` plus ``untargeted``.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # Run history of a sample, newest first
        runs = mascope.peak_assignments.list_runs("sample-123")

        # The full ledger of the latest completed run (one row per peak)
        assignments = mascope.peak_assignments.get("sample-123")
        assignments.attrs["run"]  # run metadata (engine_version, config, ...)

        # Only the confidently assigned peaks
        assigned = mascope.peak_assignments.get("sample-123", tier="assigned")

        # Run the engine on the sample and read what it assigned
        assignments = mascope.peak_assignments.assign("sample-123")
    """

    def _get_envelope(
        self, path: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """GET *path* and return the full response envelope.

        The shared ``_get`` unwraps responses to their ``data`` field, which
        drops the ``total`` the paged ledger read needs to know when it has
        the whole run - so the paging loop reads the envelope itself.
        """
        response = http_get(
            url=self._client.url,
            path=path,
            access_token=self._client.access_token,
            params=params,
            timeout=self._client._timeout,
            verify_ssl=self._client._verify_ssl,
            service_name=self._client._service_name,
        )
        return response.json()

    def list_runs(self, sample_id: str) -> pd.DataFrame | None:
        """List the peak assignment runs of a sample, newest first.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :return: A DataFrame with one row per run, containing:

                 - ``peak_assignment_run_id``: Run identifier
                 - ``engine``: Which engine produced the run - ``mascope`` for
                   a run this deployment computed, otherwise the name of the
                   external engine that published it (see
                   :meth:`~mascope_sdk.MascopeClient.peak_assignments`). Never
                   null: rows predating the column were backfilled to
                   ``mascope``, and the name is reserved server-side so an
                   import cannot claim it. ``batch`` is the ledger derived
                   from the batch peaks for a sample that has no run of its
                   own - listed after the real runs, always ``completed``,
                   and what :meth:`get` falls back to for such a sample.
                 - ``engine_version``: Assignment engine version
                 - ``status``: ``pending`` -> ``running`` -> ``completed`` |
                   ``failed`` | ``cancelled``; ``importing`` while an external
                   run is still being uploaded (its rows are not servable yet)
                 - ``config``: Run configuration (dict)
                 - ``tier_bands``: The ``assigned`` / ``candidate`` evidence
                   thresholds this run tiered with (dict) - evidence being a
                   row's ``fit_score`` weighted by the chemical plausibility
                   of the formula it commits. A tier is only comparable across
                   engines under the bands that produced it. Null for runs
                   predating the column.
                 - ``calibration``: What an external engine disclosed about
                   its calibration at import (dict). Null for ``mascope``
                   runs, whose calibration state is the sample's own.
                 - ``sigma_ppm`` / ``sigma_source``: The mass-error width
                   the run scored at [ppm], and where it came from:
                   ``fitted`` or ``instrument_class`` for a ``mascope`` run,
                   the engine's own word for an imported one.
                 - ``mu_ppm`` / ``mu_source``: The mass-error centre the
                   run corrected by [ppm], and where it came from. All four
                   are null for a run that never reached scoring, an import
                   that disclosed no width, and the ``batch`` run.
                 - ``error``: Failure reason, if any
                 - ``peak_assignment_run_utc_created`` /
                   ``peak_assignment_run_utc_completed``: Timestamps (UTC)

                 Returns None if the sample has no runs.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            runs = mascope.peak_assignments.list_runs("sample-123")
            completed = runs[runs["status"] == "completed"]
        """
        data = self._get(f"peak-assignments/sample/{sample_id}/runs")
        if not data:
            return None
        return _coerce_utc_columns(_coerce_datetime_columns(pd.DataFrame(data)))

    def get(
        self,
        sample_id: str,
        *,
        run_id: str | None = None,
        tier: str | None = None,
        engine_tier: str | None = None,
        tier_disagrees: bool | None = None,
        role: str | None = None,
        source: str | None = None,
    ) -> pd.DataFrame | None:
        """Get the peaks-with-assignments ledger of a sample, one row per peak.

        Reads the requested run (or the latest completed run) in full: the
        endpoint is paginated, and this method pages internally until it has
        every matching row, so the caller never sees a page boundary. The run
        metadata (engine, engine version, status, config, tier bands,
        calibration disclosure, timestamps - the full run record listed under
        :meth:`list_runs`) is attached on ``df.attrs["run"]`` as a dict.

        Rows are the slim ledger projection: per-peak scalars plus the
        flattened provenance scalars (``evidence``, ``p_correct``,
        ``p_correct_provisional``, ``corroboration_adducts``,
        ``corroboration_channels``, ``candidate_density``, ``mass_z``,
        ``reference_listing``). The
        inspector-detail JSON (``alternatives``, ``provenance``) of a single
        assignment is served by :meth:`detail`.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param run_id: Specific run to read. Defaults to the latest completed
                       run, resolved via :meth:`list_runs`.
        :type run_id: str, optional
        :param tier: Filter by confidence tier: ``assigned`` | ``candidate``
                     | ``below_assignability`` | ``unassigned``. The server
                     still accepts the legacy spelling ``identified`` for
                     ``assigned``, so older scripts keep working.
        :type tier: str, optional
        :param engine_tier: Filter by the tier the producing engine itself
                            concluded. Only an imported run carries one - an
                            in-app row's engine tier IS its ``tier`` - so this
                            matches nothing on an in-app run.
        :type engine_tier: str, optional
        :param tier_disagrees: With ``True``, keep only the rows where the
                               engine's own tier differs from this server's;
                               with ``False``, only those where they agree.
                               Rows carrying no engine tier are excluded either
                               way, since absence is not agreement. Done
                               server-side rather than over the returned frame
                               because the endpoint is paged: the disagreements
                               on a dense run are a handful of rows out of tens
                               of thousands.
        :type tier_disagrees: bool, optional
        :param role: Filter by peak role: ``M0`` | ``iso_child`` | ``reagent``
                     | ``artifact`` | ``unassigned``.
        :type role: str, optional
        :param source: Filter by assignment source: ``database`` (Stage A, the
                       curated target library) | ``untargeted`` (Stage B, the
                       composition search) | ``manual`` (a person assigned this
                       row by hand, in the app's peak inspector). Peaks nothing
                       explained carry no source at all, so the three values
                       plus null cover the run. Note that ``manual`` is not a
                       subset of the other two: a curated row *leaves*
                       ``database``/``untargeted``, so partitioning a run into
                       those two frames alone silently drops every hand-assigned
                       peak.
        :type source: str, optional
        :return: A DataFrame with one row per observed peak, containing:

                 - ``peak_assignment_id``, ``peak_assignment_run_id``
                 - ``sample_item_id``, ``sample_peak_id``
                 - ``sample_peak_mz``, ``sample_peak_intensity``,
                   ``sample_peak_tof``
                 - ``role``, ``tier``, ``source``
                 - ``engine_tier``: the tier the engine that produced the row
                   reached on its own terms, when it stated one. Only an
                   imported run carries it - an in-app row's engine tier IS its
                   ``tier`` - so it is null on most frames. Where it differs
                   from ``tier`` the two engines disagree about how much
                   confidence the evidence supports, which is what makes an
                   imported run worth reading beside an in-app one. Unlike
                   ``tier`` it is checked against no bands and feeds no
                   cross-sample roll-up.
                 - ``assigned_formula``, ``ion_formula``,
                   ``ionization_mechanism_id``
                 - ``isotope_label``, ``isotope_formula``
                 - ``fit_score``, ``mz_error_ppm``, ``abundance_error``
                 - ``evidence``: ``fit_score`` weighted by the chemical
                   plausibility of ``assigned_formula``, and the quantity the
                   row's ``tier`` was read off under the run's ``tier_bands``.
                   ``fit_score`` is unchanged beside it as the pure fit
                   measurement.
                 - ``p_correct``, ``p_correct_provisional``,
                   ``corroboration_adducts``
                 - ``corroboration_channels``: how many of the run's ionization
                   channels committed this row's neutral. Independent evidence
                   for the formula rather than for the peak, and unlike
                   ``corroboration_adducts`` it reaches untargeted rows too -
                   that one counts the adducts a CURATED compound matched
                   through. Not folded into ``p_correct``. The run's
                   ``config.cross_channel`` holds the channels it searched.
                 - ``candidate_density``: how many formulas the peak's own
                   evidence could not tell apart from the one this row commits,
                   1 meaning nothing ties it. Not recoverable from
                   ``alternatives``, which the run caps at ``max_alternatives``.
                   Null on an isotopologue and on an imported row - an external
                   engine's own count, where it publishes one, stays in
                   ``provenance.engine_provenance`` and is not read into this
                   column, because it is a different measurement.
                 - ``mass_z``: how far the row's mass error sits from the
                   centre the run fitted over its own corroborated
                   assignments and the source's own lines its reagent
                   pre-pass claimed, in the widths it fitted there. Where the
                   run's commits follow the mass range the centre does too,
                   and is read at the row's own m/z. The run's
                   ``config.mass_calibration`` holds that fit, its ``centre``
                   and any ``trend``; a row beyond three widths with nothing
                   but the mass fit behind it is capped, and says so in its
                   detail provenance.
                 - ``reference_listing``: what a reference list calls the
                   formula, where the run matched it from one - a dict with
                   the first compound's ``name``, the list it comes from
                   (``source``), the ``tags`` the lists carry (``background``)
                   and the ``total`` of names matched; null on a row no list
                   names. The full list is ``provenance.reference_identities``
                   in :meth:`detail`.
                 - ``target_compound_id``, ``target_ion_id`` (set for
                   database-sourced assignments)
                 - ``owner_peak_assignment_id`` (for isotope children, the
                   M0 assignment that owns them)

                 Rows are ordered by m/z. The run metadata dict is on
                 ``df.attrs["run"]``. Returns None if the sample has no
                 completed run (and ``run_id`` was not given), or if no rows
                 match the filters.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample (or the given run) is not found.
        :raises ValidationError: If a filter value is not one of the accepted
                                 enum values (server-side 422).
        :raises MascopeAPIError: If the API request fails.

        Example::

            assignments = mascope.peak_assignments.get("sample-123")
            print(assignments.attrs["run"]["engine_version"])
            assignments["tier"].value_counts()

            # Untargeted winners only
            stage_b = mascope.peak_assignments.get(
                "sample-123", source="untargeted"
            )

            # Hand-curated rows only
            curated = mascope.peak_assignments.get(
                "sample-123", source="manual"
            )

            # Where an imported run's engine and this server disagree
            disputed = mascope.peak_assignments.get(
                "sample-123", tier_disagrees=True
            )
            disputed[["sample_peak_mz", "assigned_formula", "tier", "engine_tier"]]
        """
        # Resolve the run client-side (the ledger response carries no run
        # object), so the call is deterministic and the run metadata can be
        # attached to the result.
        runs = self.list_runs(sample_id)
        run_meta: dict[str, Any] | None = None
        if run_id is None:
            if runs is None:
                logger.info("Sample '{}' has no peak assignment runs", sample_id)
                return None
            completed = runs[runs["status"] == "completed"]
            if completed.empty:
                logger.info(
                    "Sample '{}' has no completed peak assignment run", sample_id
                )
                return None
            latest = completed.sort_values(
                "peak_assignment_run_utc_created", ascending=False
            ).iloc[0]
            run_id = latest["peak_assignment_run_id"]
            run_meta = latest.to_dict()
        elif runs is not None:
            match = runs[runs["peak_assignment_run_id"] == run_id]
            if not match.empty:
                run_meta = match.iloc[0].to_dict()

        params: dict[str, Any] = {
            "peak_assignment_run_id": run_id,
            "limit": PAGE_LIMIT,
            "offset": 0,
        }
        # `tier_disagrees` is included on `False` too - it is a tri-state, and
        # False is a real query (rows whose engine agreed), not "unset".
        for key, value in (
            ("tier", tier),
            ("engine_tier", engine_tier),
            ("tier_disagrees", tier_disagrees),
            ("role", role),
            ("source", source),
        ):
            if value is not None:
                # Lower-cased rather than left to `str(True)`: the server parses
                # the query string, and "true" is the spelling every HTTP client
                # and every reader of a request log expects.
                params[key] = str(value).lower() if isinstance(value, bool) else value

        # Page until we have `total` rows. Ordering is stable server-side
        # (m/z with the primary key as tiebreak), so paging never drops or
        # repeats a row.
        rows: list[dict[str, Any]] = []
        while True:
            envelope = self._get_envelope(
                f"peak-assignments/sample/{sample_id}", params=dict(params)
            )
            page = envelope.get("data") or []
            total = envelope.get("total", len(page))
            rows.extend(page)
            if not page or len(rows) >= total:
                break
            params["offset"] = len(rows)

        if not rows:
            logger.info(
                "Run '{}' of sample '{}' has no assignments matching the filters",
                run_id,
                sample_id,
            )
            return None

        df = _coerce_utc_columns(_coerce_datetime_columns(pd.DataFrame(rows)))
        df.attrs["run"] = run_meta or {"peak_assignment_run_id": run_id}
        return df

    def detail(self, sample_id: str, peak_assignment_id: str) -> dict | None:
        """Get one assignment in full, including ``alternatives`` and
        ``provenance``.

        The complement of :meth:`get`, whose rows are a slim projection: this
        fetches the inspector-detail JSON of a single assignment - the ranked
        alternative compositions considered for the peak and the full scoring
        provenance.

        Every committed row's provenance carries ``tier_reasons``: why it holds
        the tier it holds, as a list of ``{rule, detail, caps}``. ``caps`` marks
        a reason that would take the top tier - a radical neutral, a nitrate
        cluster around a neutral with no oxygen (rule ``oxygen_free_cluster``),
        a peak whose evidence left rivals standing with nothing else
        corroborating it, a formula the search found on one peak that nothing
        else in the run saw (rule ``lone_peak``), a formula shaped like a mass
        fit, a peak a committed neighbour's envelope already predicts, an
        isotopologue whose mass error does not follow its monoisotopic row's, or
        a demote an earlier pass made. A row with no such reason lists what it
        kept its tier ON instead, what saw it a second time first: a second
        channel (``corroborated``), a line of its own isotope pattern
        (``second_line``) or the list it was matched from (``on_a_list``).
        A row whose evidence is under the top band names that band first (rule
        ``evidence_band``, with the ``band`` its evidence reaches). It is where
        the row stands before any rule, not a rule, so it never ``caps``.
        Rules only ever demote, and the run's ``config.tiering`` records the
        rule set's version and the thresholds it judged at - a tier is only
        comparable across two runs together with those. A peak read as an
        assigned neighbour's isotopologue (rule ``envelope_claim``) carries
        ``provenance.envelope_claim``: the line, its predicted and observed
        height, how its mass error follows the neighbour's, and the reading it
        displaced, which is also its first alternative. The displaced reading's
        ``tier`` is the one its evidence gave it, before any rule of the run
        held it lower. A list hit of a run that searched carries
        ``provenance.grid_rivals``: the known set's own density, how many
        closed-shell formulas the search held for its peak that its evidence
        could not separate (counted into ``candidate_density``), the first few
        of them by name, its fit on the search's scale, and whether the
        searched element box holds its formula. Where a rival cleared the
        list's prior and the list hit kept its peak all the same, the block
        names that rival under ``held_against``, with ``why``:
        ``unexplained_lines`` (the rival leaves the hit's own tracking isotope
        lines, listed by m/z, unexplained) or ``target_library``. A search row
        that took a list hit's peak carries ``provenance.list_reading``: the
        list's formula, ion, source, compound and tier, the evidence of both
        readings, and the prior they were weighed with. As on a claim's
        displaced reading, that ``tier`` is the one the list reading's evidence
        gave it, before any rule of the run held it lower. The list's reading is
        also the row's first alternative, marked ``displaced_by_rival``.

        On a hand-curated row (``source`` ``manual``) the provenance also
        carries a ``manual`` block recording who changed what: ``action``,
        ``user_id``, ``at``, and ``previous`` - the displaced winner, kept
        verbatim so an override can be audited and undone. When the override
        stripped the previous compound's isotopologues, they are
        archived under ``manual.demoted`` (one entry per isotopologue) so
        committing that compound back restores them. Those stripped isotopologues
        are themselves ``source`` ``manual`` rows, but ``unassigned`` and
        carrying no formula - a manual row is not necessarily an assigned one.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param peak_assignment_id: The ID of the assignment
                                   (``peak_assignment_id`` column of
                                   :meth:`get`).
        :type peak_assignment_id: str
        :return: The full assignment record: every :meth:`get` column plus
                 ``alternatives`` (list) and ``provenance`` (dict). Returns
                 None if the response carries no record.
        :rtype: dict | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample or assignment is not found.
        :raises MascopeAPIError: If the API request fails.

        Example::

            assignments = mascope.peak_assignments.get("sample-123")
            top = assignments.sort_values(
                "sample_peak_intensity", ascending=False
            ).iloc[0]
            full = mascope.peak_assignments.detail(
                "sample-123", top["peak_assignment_id"]
            )
            for alternative in full["alternatives"] or []:
                print(alternative)
        """
        data = self._get(
            f"peak-assignments/sample/{sample_id}/assignment/{peak_assignment_id}"
        )
        if not data:
            return None
        return data[0]

    def assign(
        self,
        sample_id: str,
        *,
        config: AssignmentConfig | Mapping[str, Any] | None = None,
        wait: bool = True,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float | None = DEFAULT_WAIT_TIMEOUT,
    ) -> pd.DataFrame | str | None:
        """Run the peak assignment engine on a sample.

        The server decides before it answers: it creates the run and returns
        its id, or refuses with the reason, so a refusal is an error here and
        never a wait that runs out. The run itself goes on in the background.
        With ``wait=True`` this method looks at the run every
        *poll_interval* seconds until it ends and returns its ledger, as
        :meth:`get` with its ``run_id`` does.

        The request is sent once. If its answer is lost - a timeout, a
        dropped connection, a proxy's 502 to 504 - the run may have started
        all the same, and sending it again would only be refused as already
        in flight, so the error says to look at :meth:`list_runs` first.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param config: The run's settings; the server's defaults for any left
            out (see :class:`AssignmentConfig` for the keys).
        :type config: AssignmentConfig, optional
        :param wait: Wait for the run to end and return its ledger. With
            False, return the run's id as soon as it is launched.
        :type wait: bool, optional
        :param poll_interval: Seconds between two looks at the run.
        :type poll_interval: float, optional
        :param timeout: Seconds to wait before giving up on the wait (not on
            the run); None waits as long as the run takes.
        :type timeout: float, optional
        :return: With ``wait=True``, the ledger of the finished run, one row
            per peak, its run on ``df.attrs["run"]`` (None if it assigned no
            peak). With ``wait=False``, the run's ``peak_assignment_run_id``.
        :rtype: pd.DataFrame | str | None
        :raises AssignmentInFlightError: If the sample already has a run in
            flight; it names that run.
        :raises SampleIneligibleError: If the sample cannot be assigned (a
            blank, or an unverified m/z calibration); it names the reason.
        :raises AssignmentRunError: If the run failed, with its error, or was
            cancelled.
        :raises AssignmentTimeoutError: If the run is still going after
            *timeout* seconds; it carries the run's id.
        :raises PermissionDeniedError: Without the editor role on the sample,
            or when the server has peak assignment switched off (403).
        :raises ValidationError: If a ``config`` value is refused.
        :raises MascopeConnectionError: If the answer to the launch was lost.

        Example::

            from mascope_sdk import AssignmentInFlightError

            try:
                assignments = mascope.peak_assignments.assign(
                    "sample-123", config={"run_untargeted": False}
                )
            except AssignmentInFlightError as e:
                # None while the other launch is still creating its run
                if e.peak_assignment_run_id is not None:
                    assignments = mascope.peak_assignments.wait_for_run(
                        "sample-123", e.peak_assignment_run_id
                    )
        """
        body: dict[str, Any] = {}
        if config is not None:
            body["config"] = dict(config)
        try:
            data = self._write(
                "POST",
                f"peak-assignments/sample/{sample_id}/assign",
                Needs("run peak assignment on", "sample", sample_id, _EDITOR),
                data=body,
                max_attempts=1,
            )
        except ConflictError as e:
            raise AssignmentInFlightError(
                e.message, status_code=e.status_code, url=e.url, body=e.body
            ) from e
        except ValidationError as e:
            if not _is_ineligible(e):
                raise
            raise SampleIneligibleError(
                e.message, status_code=e.status_code, url=e.url, body=e.body
            ) from e
        except (MascopeConnectionError, ServerError) as e:
            raise _answer_lost(
                e, f"list_runs('{sample_id}') shows the run if it started"
            ) from e

        run_id = data[0]["peak_assignment_run_id"]
        logger.info("Launched peak assignment run '{}' on '{}'", run_id, sample_id)
        if not wait:
            return run_id
        return self.wait_for_run(
            sample_id, run_id, poll_interval=poll_interval, timeout=timeout
        )

    def wait_for_run(
        self,
        sample_id: str,
        run_id: str,
        *,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        timeout: float | None = DEFAULT_WAIT_TIMEOUT,
    ) -> pd.DataFrame | None:
        """Wait for a run to end, and return its ledger.

        For a run :meth:`assign` launched with ``wait=False``, one whose wait
        timed out, or one another launch has in flight. Looks at the sample's
        runs every *poll_interval* seconds. A look the server does not answer
        is skipped rather than raised, so the wait ends only when the run
        does, or with the run's id when *timeout* passes.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param run_id: The run's ``peak_assignment_run_id``.
        :type run_id: str
        :raises ValueError: If *run_id* is None, as an
            :class:`AssignmentInFlightError` carries when the server could
            not name the run yet.
        :param poll_interval: Seconds between two looks at the run.
        :type poll_interval: float, optional
        :param timeout: Seconds to wait; None waits as long as the run takes.
        :type timeout: float, optional
        :return: The run's ledger, as :meth:`get` with ``run_id`` returns it
            (None if it assigned no peak).
        :rtype: pd.DataFrame | None
        :raises AssignmentRunError: If the run failed, with its error, was
            cancelled, or is no longer listed.
        :raises AssignmentTimeoutError: If the run is still going after
            *timeout* seconds.
        """
        if run_id is None:
            # Waiting on None would read as a run that vanished.
            raise ValueError(
                "No run to wait for: the server could not name the run in "
                "flight yet, as another launch was creating it. "
                f"list_runs('{sample_id}') shows it once it exists."
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        status: str | None = None
        while True:
            try:
                runs = self.list_runs(sample_id)
            except (MascopeConnectionError, ServerError) as e:
                # INFO: the HTTP layer has already retried; the run goes on
                logger.info("Run '{}' not looked at this time: {}", run_id, e)
            else:
                match = (
                    None
                    if runs is None
                    else runs[runs["peak_assignment_run_id"] == run_id]
                )
                if match is None or match.empty:
                    raise AssignmentRunError(sample_id, run_id, None)
                run = match.iloc[0]
                status = run["status"]
                if status == "completed":
                    return self.get(sample_id, run_id=run_id)
                if status in TERMINAL_STATUSES:
                    error = run.get("error")
                    raise AssignmentRunError(
                        sample_id,
                        run_id,
                        status,
                        error if isinstance(error, str) and error else None,
                    )
            if deadline is None:
                pause = poll_interval
            else:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise AssignmentTimeoutError(sample_id, run_id, status, timeout)
                pause = min(poll_interval, left)
            time.sleep(pause)

    def verify(
        self,
        sample_id: str,
        peak_assignment_id: str,
        verdict: str,
        *,
        evidence_level: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Record a person's verdict on one assignment.

        Verdicts are appended, never edited: a new verdict on the same peak,
        formula and adduct replaces the one before it as the current answer
        (stamping its ``superseded_utc``), and both are kept. The server
        snapshots the assignment's ``fit_score``, ``evidence`` and
        ``p_correct`` beside the verdict, which makes ``confirmed`` and
        ``rejected`` labels the instrument's confidence calibration is
        refitted on - except on an imported run, whose verdicts are recorded
        but kept out of that refit, since its scores are an external
        engine's.

        Sent once: a verdict sent twice would count twice in that refit. If
        the answer is lost, look at :meth:`list_verifications` before sending
        it again.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param peak_assignment_id: The assignment (``peak_assignment_id`` of
            :meth:`get`).
        :type peak_assignment_id: str
        :param verdict: ``confirmed`` | ``rejected`` | ``unsure``.
        :type verdict: str
        :param evidence_level: What the verdict rests on:
            ``reference_standard`` (an authentic standard) | ``msms`` (MS/MS
            or diagnostic fragments) | ``orthogonal`` (retention time and
            the like) | ``pattern`` (isotope and adduct corroboration) |
            ``visual`` (a look alone). Required to confirm.
        :type evidence_level: str, optional
        :param note: A free-text note.
        :type note: str, optional
        :return: The verification as recorded, with the scores it snapshot.
        :rtype: dict[str, Any]
        :raises ValueError: If ``confirmed`` comes without an
            ``evidence_level``; nothing is sent.
        :raises NotFoundError: If the sample has no such assignment.
        :raises ValidationError: If the verdict or the evidence level is not
            one the server accepts.
        :raises PermissionDeniedError: Without the editor role on the sample,
            or when the server has peak assignment switched off (403).
        :raises MascopeConnectionError: If the answer was lost.

        Example::

            row = mascope.peak_assignments.get("sample-123").iloc[0]
            mascope.peak_assignments.verify(
                "sample-123",
                row["peak_assignment_id"],
                "confirmed",
                evidence_level="reference_standard",
            )
        """
        if verdict == "confirmed" and evidence_level is None:
            # The server refuses it too; said here, it costs no request.
            raise ValueError(
                "A 'confirmed' verdict needs an evidence_level: one of "
                "reference_standard, msms, orthogonal, pattern or visual."
            )
        body: dict[str, Any] = {
            "peak_assignment_id": peak_assignment_id,
            "verdict": verdict,
        }
        if evidence_level is not None:
            body["evidence_level"] = evidence_level
        if note is not None:
            body["note"] = note
        try:
            data = self._write(
                "POST",
                f"peak-assignments/sample/{sample_id}/verify",
                Needs("verify an assignment of", "sample", sample_id, _EDITOR),
                data=body,
                max_attempts=1,
            )
        except (MascopeConnectionError, ServerError) as e:
            raise _answer_lost(
                e,
                f"list_verifications('{sample_id}') shows the verdict if it "
                "was recorded",
            ) from e
        return data[0]

    def list_verifications(self, sample_id: str) -> pd.DataFrame | None:
        """List the verdicts recorded on a sample's assignments, newest first.

        Every verdict ever recorded, those a later one replaced included: the
        current verdict on a peak, formula and adduct is the one whose
        ``superseded_utc`` is null.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :return: A DataFrame with one row per verdict, containing:

                 - ``assignment_verification_id``
                 - ``sample_item_id``, ``sample_peak_id``
                 - ``peak_assignment_id``, ``peak_assignment_run_id``: the
                   assignment judged; null once its run has been pruned
                 - ``assigned_formula``, ``ionization_mechanism_id``: what
                   was judged on the peak
                 - ``verdict``, ``evidence_level``, ``note``
                 - ``fit_score``, ``evidence``, ``p_correct``: the
                   assignment's scores when it was judged
                 - ``verified_by``: the user's id
                 - ``verified_utc``, ``superseded_utc`` (UTC)

                 Returns None if the sample has no verdicts.
        :rtype: pd.DataFrame | None
        :raises NotFoundError: If the sample is not found.

        Example::

            verdicts = mascope.peak_assignments.list_verifications("sample-123")
            current = verdicts[verdicts["superseded_utc"].isna()]
        """
        data = self._get(f"peak-assignments/sample/{sample_id}/verifications")
        if not data:
            return None
        return _coerce_utc_columns(pd.DataFrame(data))

    def fit_aggregate(
        self, sample_id: str, formula: str, ionization_mechanism_id: str
    ) -> dict[str, Any]:
        """Score a composition against a sample: its isotope table.

        Any neutral formula and ionization mechanism, whether a run assigned
        it or not, scored as the targeted matching scores a compound
        (:meth:`~mascope_sdk.resources.matching.MatchingResource.match_compound`).
        What it is for: checking an untargeted assignment, which has no
        target ion of its own, line by line. Nothing is saved.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param formula: The neutral formula, e.g. ``"C6H12O6"`` (an
            ``assigned_formula`` of :meth:`get`).
        :type formula: str
        :param ionization_mechanism_id: The mechanism that makes its ion (an
            ``ionization_mechanism_id`` of :meth:`get`, or of
            ``mascope.ionization.list()``).
        :type ionization_mechanism_id: str
        :return: ``match_ions`` (the ion: its formula, mechanism and
            ``match`` with the ``match_score``), ``match_isotopes`` (one
            record per isotope line in m/z order: its formula, ``mz``,
            ``relative_abundance`` and the peak it matched under ``match``,
            with ``match_mz_error`` and ``match_abundance_error``) and
            ``matches`` (how many of each). Both lists are empty when no line
            matched.
        :rtype: dict[str, Any]
        :raises NotFoundError: If the sample or the mechanism is not found.
        :raises PermissionDeniedError: Without the guest role on the sample.

        Example::

            row = mascope.peak_assignments.get(
                "sample-123", source="untargeted"
            ).iloc[0]
            fit = mascope.peak_assignments.fit_aggregate(
                "sample-123",
                row["assigned_formula"],
                row["ionization_mechanism_id"],
            )
            pd.json_normalize(fit["match_isotopes"])
        """
        return self._write(
            "POST",
            f"peak-assignments/sample/{sample_id}/fit/aggregate",
            Needs("score a composition on", "sample", sample_id, "guest on the sample"),
            data={
                "assigned_formula": formula,
                "ionization_mechanism_id": ionization_mechanism_id,
            },
        )

    def verifications_for_batch(
        self,
        sample_batch_id: str,
        *,
        verdict: str | None = None,
        evidence_level: str | None = None,
        since: datetime | str | None = None,
        include_superseded: bool = False,
    ) -> pd.DataFrame | None:
        """The current verification verdicts over every sample of a batch,
        one row per judged peak, as each sample's assignment ledger shows them.

        A row is either the sample's own verdict, or a batch-level verdict
        (:meth:`~mascope_sdk.resources.batch_peaks.BatchPeaksResource.verdicts`)
        that reaches the sample: the sample's peak folded into the judged batch
        peak and claims the formula and mechanism judged, and the sample has no
        current verdict of its own on that claim. The second kind is marked
        ``borrowed``. This is the labelled record of the batch: join it to
        :meth:`get` on ``sample_item_id`` and ``sample_peak_id``.

        :param sample_batch_id: The ID of the sample batch.
        :type sample_batch_id: str
        :param verdict: Keep only ``confirmed``, ``rejected`` or ``unsure``.
        :type verdict: str, optional
        :param evidence_level: Keep only verdicts recorded on this basis:
                               ``reference_standard``, ``msms``,
                               ``orthogonal``, ``pattern`` or ``visual``.
        :type evidence_level: str, optional
        :param since: Keep only verdicts recorded at or after this moment; a
                      time without a zone is read as UTC.
        :type since: datetime | str, optional
        :param include_superseded: Also list the sample's own verdicts that a
                                   later one replaced (``superseded_utc``
                                   set). Batch-level verdicts are listed live
                                   only.
        :type include_superseded: bool, optional
        :return: A DataFrame with one row per verdict, by sample acquisition
                 time, then m/z, newest verdict first:

                 - ``sample_item_id``, ``sample_item_name``,
                   ``sample_batch_id``, ``filename``, ``datetime``,
                   ``datetime_utc``: the sample
                 - ``sample_peak_id``, ``sample_peak_mz``: the judged peak
                 - ``assigned_formula``, ``ionization_mechanism_id``: the claim
                   judged
                 - ``verdict``, ``evidence_level``, ``note``, ``context``,
                   ``verified_by``, ``verified_utc``, ``superseded_utc``
                 - ``fit_score``, ``evidence``, ``p_correct``: the score
                   snapshot taken when the sample's own verdict was recorded;
                   null on a borrowed one
                 - ``borrowed``: a batch-level verdict reaching the sample
                 - ``assignment_verification_id``, ``peak_assignment_id``,
                   ``peak_assignment_run_id``: on the sample's own verdict
                 - ``batch_peak_verification_id``, ``batch_peak_id``,
                   ``stale``: on a borrowed one; ``stale`` when the batch
                   peak's consensus has since left the claim judged

                 Returns None when nothing in the batch was judged.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the batch is not found.
        :raises ValidationError: If a filter value is not one the API accepts.
        :raises MascopeAPIError: If the API request fails.

        Example::

            verdicts = mascope.peak_assignments.verifications_for_batch(batch_id)
            confirmed = verdicts[verdicts["verdict"] == "confirmed"]
            own = verdicts[~verdicts["borrowed"]]
        """
        return self._verifications(
            f"peak-assignments/batch/{sample_batch_id}/verifications",
            verdict=verdict,
            evidence_level=evidence_level,
            since=since,
            include_superseded=include_superseded,
        )

    def verifications_for_dataset(
        self,
        dataset_id: str,
        *,
        verdict: str | None = None,
        evidence_level: str | None = None,
        since: datetime | str | None = None,
        include_superseded: bool = False,
    ) -> pd.DataFrame | None:
        """The current verification verdicts over every sample of a dataset,
        in one read: :meth:`verifications_for_batch` over each of its batches,
        with the same filters and columns.

        :param dataset_id: The ID of the dataset.
        :type dataset_id: str
        :return: A DataFrame with one row per verdict, as
                 :meth:`verifications_for_batch` returns. Returns None when
                 nothing in the dataset was judged.
        :rtype: pd.DataFrame | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the dataset is not found.
        :raises ValidationError: If a filter value is not one the API accepts.
        :raises MascopeAPIError: If the API request fails.
        """
        return self._verifications(
            f"peak-assignments/dataset/{dataset_id}/verifications",
            verdict=verdict,
            evidence_level=evidence_level,
            since=since,
            include_superseded=include_superseded,
        )

    def _verifications(
        self,
        path: str,
        *,
        verdict: str | None,
        evidence_level: str | None,
        since: datetime | str | None,
        include_superseded: bool,
    ) -> pd.DataFrame | None:
        """One read of the verdicts over a batch or dataset, as a frame."""
        params: dict[str, Any] = {}
        if include_superseded:
            params["include_superseded"] = "true"
        if verdict is not None:
            params["verdict"] = verdict
        if evidence_level is not None:
            params["evidence_level"] = evidence_level
        if since is not None:
            params["since"] = (
                since.isoformat() if isinstance(since, datetime) else since
            )
        data = self._get(path, params=params)
        if not data:
            return None
        return _coerce_utc_columns(_coerce_datetime_columns(pd.DataFrame(data)))

    def import_run(
        self,
        sample_id: str,
        df: pd.DataFrame,
        *,
        engine: str,
        engine_version: str,
        tier_bands: Mapping[str, float],
        calibration: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> str:
        """Publish an assignment run an engine outside Mascope computed.

        The run lands beside the sample's other runs: the same ledger, the
        same run selector and the same batch fold-in as a run this server
        computed, stamped with *engine*. The contract is the page
        `Publishing an assignment run
        <https://github.com/ultra-trace-systems/mascope/blob/master/docs/user/sdk/publishing-assignments.md>`_;
        this method keeps to it, and checks the frame before anything is
        sent.

        The frame holds one row per peak, the peak's ``sample_peak_id`` and
        ``role`` and its observed ``sample_peak_mz`` and
        ``sample_peak_intensity`` at least, and any other column of a row:
        ``sample_peak_tof``, ``assigned_formula``, ``ion_formula``,
        ``ionization_mechanism_id``, ``isotope_label``, ``isotope_formula``,
        ``source``, ``fit_score``, ``mz_error_ppm``, ``abundance_error``,
        ``tier``, ``engine_tier``, ``target_compound_id``, ``target_ion_id``,
        ``owner_sample_peak_id``, ``alternatives`` and ``provenance``. A
        frame read with :meth:`get` is one: the columns the server computes
        or looks up itself are left out, its ``owner_peak_assignment_id``
        links are sent as ``owner_sample_peak_id`` from ``iso_child`` rows
        (a reagent's isotope lines go without theirs), and its ``tier`` goes
        as ``engine_tier`` where the frame states none, as the server
        derives the tier again. Any other column is left out, with a
        warning.

        What is checked here, so that a refusal costs no upload:

        - ``role``, ``source``, ``tier`` and ``engine_tier`` hold values of
          their vocabularies (``identified`` is read as ``assigned``), and
          ``fit_score`` lies in 0 to 1.
        - Each peak has one row, and an ``owner_sample_peak_id`` names
          another row of the frame, from an ``iso_child`` row, and not
          another ``iso_child``.
        - A NaN in a column that may be null is sent as null. In
          ``sample_peak_mz`` or ``sample_peak_intensity`` it raises, naming
          the rows: which number stands in for a missing one is the
          caller's to say.
        - **A ``tier`` column is better left out**: the server derives each
          row's tier from its ``fit_score``, ``assigned_formula`` and the
          run's *tier_bands*. Where the frame has one, a row stating a tier
          its bare ``fit_score`` cannot reach is refused: the server reads
          the tier off the evidence, which is the fit weighted by the
          formula's plausibility and so never above it. A row the fit does
          clear goes to the server, which has the last word. A tier the
          engine reached by rules of its own goes in ``engine_tier``.

        An ``adduct`` column, notations such as ``[M+H]+``, fills in
        ``ionization_mechanism_id`` where a row has none: the mechanism of
        ``mascope.ionization.list()`` with exactly that notation, and null
        when no single one has it. A null loses nothing but the join, as the
        adduct is in ``ion_formula``.

        The rows go in as many requests as the server's caps call for, by
        row count and by bytes. The first and the last are sent once and
        not again on a lost answer, like :meth:`assign`; the ones between
        may be sent again, as the server applies each row offset once. Any
        request the server holds up behind another one on the sample (a 409
        with the code ``assignment_claim_held``, which applied nothing) is
        sent again once that one finishes. If the server refuses a request after
        the run was created, or a request between goes unanswered on every
        try, the run is deleted with its rows (:meth:`abandon_import`)
        before the error is raised, so such an import leaves no run behind.

        A frame with materially fewer rows than the sample's latest
        completed run is published all the same, with a warning: the batch
        ledger takes the sample's whole contribution from the newest run,
        so the peaks it leaves out leave the batch overview.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param df: The run's rows, one per peak.
        :type df: pd.DataFrame
        :param engine: The engine's name, as the run selector shows it.
            ``mascope``, ``mascope-copy`` and ``batch`` are the server's own.
            Name the engine, not the build: runs are kept per engine.
        :type engine: str
        :param engine_version: The engine's version.
        :type engine_version: str
        :param tier_bands: The ``assigned`` and ``candidate`` thresholds the
            engine tiered with, on the evidence scale.
        :type tier_bands: Mapping[str, float]
        :param calibration: What the engine calibrated against, as JSON of
            its own. An import bypasses the sample's m/z verification, so
            this goes on the record in its place; an engine that calibrated
            nothing says so here.
        :type calibration: Mapping[str, Any]
        :param config: The engine's run configuration, stored as it is.
            Keep the engine's own numbers for a row, such as its own
            P(correct), in the row's ``provenance`` under
            ``engine_provenance``: ``p_correct``, ``calibration`` and
            ``corroboration`` at its top level are stripped, and
            ``evidence`` and ``plausibility`` overwritten.
        :type config: Mapping[str, Any], optional
        :return: The new run's ``peak_assignment_run_id``, completed.
        :rtype: str
        :raises ImportValidationError: If the frame or the bands would be
            refused, or cannot be sent; it names the column and the rows.
            Nothing was sent.
        :raises ConflictError: If the sample has a run in flight (409).
        :raises ValidationError: If the server refused the import (422): a
            peak the sample does not have, a mechanism of the wrong
            polarity, a tier its evidence does not earn.
        :raises PermissionDeniedError: Without the editor role on the
            sample, or when the server has peak assignment switched off.
        :raises MascopeConnectionError: If the answer to the first or the
            last request was lost; it says where to look. If one between
            went unanswered, it says whether the run was abandoned.

        Example::

            peaks = mascope.samples.get_peaks("sample-123", matches=False)
            rows = my_engine.assign(peaks)  # one row per peak
            run_id = mascope.peak_assignments.import_run(
                "sample-123",
                rows,
                engine="my-engine",
                engine_version="0.3.1",
                tier_bands={"assigned": 0.75, "candidate": 0.45},
                calibration={"method": "none"},
            )
            mascope.peak_assignments.get("sample-123", run_id=run_id)
        """
        bands = _import.normalise_bands(tier_bands)
        if not isinstance(calibration, Mapping):
            raise ImportValidationError(
                "calibration must be a mapping: what the engine calibrated "
                "against, or that it calibrated nothing."
            )
        rows, sizes, labels = _import.prepare_rows(
            df, bands, self._client.ionization.list
        )
        head: dict[str, Any] = {"engine": engine, "engine_version": engine_version}
        create = {
            **head,
            "tier_bands": bands,
            "calibration": dict(calibration),
            "config": None if config is None else dict(config),
        }
        chunk: dict[str, Any] = {
            "run_id": None,
            "import_id": uuid.uuid4().hex,
            "index": 0,
            "complete": False,
        }
        # The create carries the most besides its rows; a row that fits
        # beside it fits beside any other request's.
        budget = _import.MAX_BODY_BYTES - _import.body_bytes(
            {**create, "chunk": {**chunk, "run_id": "x" * 32}, "rows": []}
        )
        _import.check_sizes(labels, sizes, budget)
        self._warn_partial(sample_id, len(rows))

        max_rows = _import.DEFAULT_MAX_ROWS
        while True:
            start = chunk["index"]
            end = _import.chunk_end(sizes, start, max_rows, budget)
            chunk["complete"] = end == len(rows)
            body = {
                **(create if chunk["run_id"] is None else head),
                "rows": rows[start:end],
                "chunk": dict(chunk),
            }
            state = self._send_import(sample_id, body)
            chunk["run_id"] = state["peak_assignment_run_id"]
            max_rows = int(state.get("max_rows_per_request") or max_rows)
            if chunk["complete"] and state.get("run_status") == "completed":
                logger.info(
                    "Imported run '{}' into '{}': {} row(s)",
                    chunk["run_id"],
                    sample_id,
                    state["rows"],
                )
                return chunk["run_id"]
            # The next chunk starts where the server says the run ends, which
            # is where this one did unless a retry got there first.
            accepted = int(state["rows"])
            if chunk["complete"] or not start < accepted <= len(rows):
                self._abandon_stopped(sample_id, chunk["run_id"])
                raise MascopeAPIError(
                    f"Import run '{chunk['run_id']}' is "
                    f"'{state.get('run_status')}' with {accepted} row(s) after "
                    f"rows {start} to {end} of {len(rows)} were sent"
                    f"{' to finish it' if chunk['complete'] else ''}; the "
                    "import was abandoned."
                )
            if accepted != end:
                logger.info(
                    "Import run '{}' holds {} row(s), not {}: carrying on from there",
                    chunk["run_id"],
                    accepted,
                    end,
                )
            chunk["index"] = accepted

    def _send_import(self, sample_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """Send one request of an import, and return the import's state.

        The create and the finalize are sent once on a lost answer; the
        requests between are sent again on a transient failure, as the
        server applies a row offset once. A request the sample's claim held
        up applied nothing, and goes again. A request refused after the run
        exists abandons it, and so does a request between left unanswered.
        """
        chunk = body["chunk"]
        run_id = chunk["run_id"]
        once = run_id is None or chunk["complete"]

        def send() -> list[dict[str, Any]]:
            return self._write(
                "POST",
                f"peak-assignments/sample/{sample_id}/runs/import",
                Needs("import an assignment run into", "sample", sample_id, _EDITOR),
                data=body,
                max_attempts=1 if once else None,
            )

        try:
            # An append retried after a timeout meets its first attempt still
            # running under the sample's claim; once that is done, the same
            # chunk is answered as a replay or applied. Any request may meet
            # another client's, and none applied anything.
            data = self._past_the_claim(sample_id, send)
        except (MascopeAPIError, MascopeConnectionError) as e:
            if _is_lost(e) and once:
                raise self._import_lost(e, sample_id, run_id) from e
            if run_id is None:
                raise
            # An append cannot have finished the import, so a run whose
            # append went unanswered is importing whatever became of it.
            abandoned = self._abandon_stopped(sample_id, run_id)
            if _is_lost(e):
                raise _extended(
                    e,
                    f"No answer came for a chunk of import run '{run_id}', "
                    "which cannot have finished it; "
                    + (
                        "the import was abandoned."
                        if abandoned
                        else f"abandon_import('{sample_id}', '{run_id}') "
                        "deletes it while it is 'importing'."
                    ),
                ) from e
            raise
        return data[0]

    @staticmethod
    def _past_the_claim(sample_id: str, send: Callable[[], Any]) -> Any:
        """*send*, sent again while another request holds the sample.

        A request the sample's claim refuses applied nothing, so sending it
        again is safe; any other answer is *send*'s own.
        """
        first, last = CLAIM_PAUSES
        for attempt in range(CLAIM_ATTEMPTS - 1):
            try:
                return send()
            except ConflictError as e:
                if not _is_claim_held(e):
                    raise
            pause = min(first * 2**attempt, last)
            # INFO: the request applied nothing, and goes again
            logger.info(
                "Another request on '{}' is still running; sending again in {} s",
                sample_id,
                pause,
            )
            time.sleep(pause)
        return send()

    @staticmethod
    def _import_lost(error: Exception, sample_id: str, run_id: str | None) -> Exception:
        """*error*, saying where a lost import request would show."""
        if run_id is None:
            what = (
                f"list_runs('{sample_id}') shows the run if it was created, and "
                f"abandon_import('{sample_id}', <its id>) deletes it while it "
                "is 'importing'"
            )
        else:
            what = (
                f"list_runs('{sample_id}') shows run '{run_id}' 'completed' if "
                f"the import finished, and abandon_import('{sample_id}', "
                f"'{run_id}') deletes it while it is 'importing'"
            )
        return _answer_lost(error, what)

    def _abandon_stopped(self, sample_id: str, run_id: str) -> bool:
        """Delete an import that stopped partway, if it still can.

        :return: Whether the run was deleted.
        """
        try:
            self._past_the_claim(
                sample_id, lambda: self.abandon_import(sample_id, run_id)
            )
        except MascopeError as e:
            # WARNING: the run holds the sample until retention reclaims it
            logger.warning(
                "Could not abandon import run '{}' of '{}': {}. "
                "abandon_import('{}', '{}') tries again.",
                run_id,
                sample_id,
                e,
                sample_id,
                run_id,
            )
            return False
        logger.info("Abandoned import run '{}' of '{}'", run_id, sample_id)
        return True

    def _warn_partial(self, sample_id: str, n_rows: int) -> None:
        """Warn when *n_rows* is materially fewer than the latest run holds."""
        latest = _latest_completed(self.list_runs(sample_id))
        if latest is None:
            return
        envelope = self._get_envelope(
            f"peak-assignments/sample/{sample_id}",
            params={
                "peak_assignment_run_id": latest["peak_assignment_run_id"],
                "limit": 1,
                "offset": 0,
            },
        )
        total = int(envelope.get("total") or 0)
        if n_rows < COVERAGE_WARNING_SHARE * total:
            # WARNING: the import withdraws peaks from the batch overview
            logger.warning(
                "The frame holds {} row(s) and the latest completed run of "
                "'{}' ({}) holds {}. The batch ledger takes the sample from "
                "its newest run, so the peaks the frame leaves out leave the "
                "batch overview.",
                n_rows,
                sample_id,
                latest.get("engine"),
                total,
            )

    def abandon_import(self, sample_id: str, run_id: str) -> dict[str, Any]:
        """Delete an import that never finished, with the rows it staged.

        An import that stops partway, its client gone or its answer lost,
        leaves a run ``importing``, and that run refuses every other import
        and in-app run on the sample until it ends. :meth:`import_run`
        abandons the runs the server refuses itself; this is the way out
        for the rest. Only an ``importing`` run can go: a completed one is
        ledger data, aged out by retention. Sent once.

        :param sample_id: The ID of the sample.
        :type sample_id: str
        :param run_id: The run, ``importing`` in :meth:`list_runs`.
        :type run_id: str
        :return: The abandoned run's ``peak_assignment_run_id`` and the
            ``rows`` it had staged.
        :rtype: dict[str, Any]
        :raises ConflictError: If the run is not ``importing``.
        :raises NotFoundError: If the sample has no such run.
        :raises PermissionDeniedError: Without the editor role on the
            sample, or when the server has peak assignment switched off.

        Example::

            runs = mascope.peak_assignments.list_runs("sample-123")
            for run_id in runs[runs["status"] == "importing"]["peak_assignment_run_id"]:
                mascope.peak_assignments.abandon_import("sample-123", run_id)
        """
        try:
            data = self._write(
                "DELETE",
                f"peak-assignments/sample/{sample_id}/runs/{run_id}",
                Needs("abandon an import into", "sample", sample_id, _EDITOR),
                max_attempts=1,
            )
        except (MascopeConnectionError, ServerError) as e:
            raise _answer_lost(
                e, f"list_runs('{sample_id}') no longer lists '{run_id}' if it went"
            ) from e
        return data[0]


def _is_ineligible(error: MascopeAPIError) -> bool:
    """Whether a 422 is the server refusing the sample, not the request.

    By the code; a server from before the code says it by naming a
    ``reason``, which a request it could not read never carries.
    """
    if error.code is not None:
        return error.code == SAMPLE_INELIGIBLE_CODE
    return "reason" in error.detail
