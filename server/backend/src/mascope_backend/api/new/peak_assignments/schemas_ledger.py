"""A sample's ledger: its runs, its rows and what a row reads beside it."""

from datetime import datetime

from pydantic import BaseModel, Field

from mascope_backend.api.new.peak_assignments.schemas_vocabulary import (
    AssignmentRole,
    AssignmentSource,
    AssignmentTier,
    IdentificationLevel,
)


# A run holds one row per detected peak, so an unbounded read serializes tens
# of megabytes through Pydantic on the event loop. Clients page instead; the
# response carries `total` so they know when they have the run.
DEFAULT_PAGE_LIMIT = 1000
MAX_PAGE_LIMIT = 5000


class PeakAssignmentRunRecord(BaseModel):
    """One peak assignment run over a sample."""

    peak_assignment_run_id: str
    sample_item_id: str
    #: Which engine produced this run: the in-app engine, or an external one
    #: that published its ledger here. Never null - existing rows were
    #: backfilled to the in-app identity - so a reader can compare it without
    #: handling a sentinel. The run selector's provenance badge renders this.
    #: ``batch`` is the ledger derived from the batch peaks for a sample that
    #: has no run of its own - listed after the real runs, always completed,
    #: and what the ledger read falls back to (see ``fold_view``).
    engine: str
    engine_version: str
    status: str
    config: dict | None = None
    #: The assigned/candidate evidence (fit x plausibility) thresholds this run
    #: tiered with. Served because 'assigned' means nothing comparable across
    #: engines until the bands that produced it are visible: the server
    #: validates every imported row against these, and a reader needs the same
    #: yardstick.
    #: Null only for runs predating the column.
    tier_bands: dict | None = None
    #: What the producing engine calibrated against, as it disclosed at import.
    #: An import bypasses the server-side m/z verification gate because it
    #: calibrates client-side, and this disclosure is what replaces that gate -
    #: which only works if a reader can actually see it. Null for in-app runs,
    #: whose calibration state is the sample's own.
    calibration: dict | None = None
    #: The confidence calibration the run's P(correct) values were read off -
    #: instrument class, provisional flag, source - recorded once per run and
    #: folded back into each row's provenance by the assignment detail read.
    #: Null when the run was not calibrated, and for imported runs.
    confidence_calibration: dict | None = None
    #: The width the run judged a mass error against, in ppm: for an in-app
    #: run the width it scored and capped at, lifted from its
    #: ``measurement_model`` record (``width_ppm``; a run from before the
    #: record, its ``pattern_scoring`` snapshot); for an imported run what its
    #: engine disclosed in ``scoring_width``. Served so an external engine
    #: comparing itself to a run reads the width that run scored at instead of
    #: measuring it again.
    #: Null for a run that never reached scoring, an import that disclosed
    #: none, and the batch-derived run.
    sigma_ppm: float | None = None
    #: Where ``sigma_ppm`` came from: ``fitted`` (the sample's own lines) or
    #: ``instrument_class`` (too few lines, the class stood in) for an in-app
    #: run; the engine's own word for an imported one.
    sigma_source: str | None = None
    #: The mass-error centre the run corrected by, in ppm, beside its width.
    mu_ppm: float | None = None
    #: Where ``mu_ppm`` came from: ``fitted``, ``reagent`` or ``none`` for an
    #: in-app run; the engine's own word for an imported one.
    mu_source: str | None = None
    #: The arm a decoy run ran - ``mz_shift_ppm=+9``, ``mz_shift_da=+0.35`` or
    #: ``swap_adducts`` - and null for every other run. A decoy is the in-app
    #: engine re-run on the sample with its chemistry made wrong on purpose,
    #: scored at the calibration of the run its config's ``decoy_arm`` names;
    #: it is listed only with ``include_decoys`` and is never the sample's
    #: result.
    decoy: str | None = None
    #: Whether the run's rows still name the sample's peaks. Peak detection
    #: mints a fresh id for every peak, so after the peaks are found again (a
    #: re-run of peak detection, an operator refit, a restored filestore) a
    #: run from before names none of them: its rows cannot be opened against
    #: the peak list, and a reader taking the newest completed run as the
    #: sample's result should know. Computed when the runs are listed, from one
    #: row per run, since a run is current or stale as a whole. Null for a run
    #: that is not completed, a run with no rows, the batch-derived run (its
    #: rows' peak ids are stored on the batch peaks, not read from the file),
    #: and when the sample's peak ids could not be read.
    peaks_current: bool | None = None
    error: str | None = None
    peak_assignment_run_utc_created: datetime | None = None
    peak_assignment_run_utc_completed: datetime | None = None


class PeakAssignmentRecord(BaseModel):
    """One observed peak with its committed assignment (slim ledger row).

    The list endpoint serves this slim projection: the `alternatives` and
    `provenance` JSON blobs are roughly three quarters of a full row's bytes
    and are read only when a user inspects a single peak, so they live on
    :class:`PeakAssignmentDetailRecord` (the per-assignment detail endpoint)
    instead. The few provenance-derived scalars the ledger columns render are
    flattened onto the row.
    """

    peak_assignment_id: str
    peak_assignment_run_id: str
    sample_item_id: str
    sample_peak_id: str
    sample_peak_mz: float
    sample_peak_intensity: float
    sample_peak_tof: float | None = None
    role: str
    assigned_formula: str | None = None
    ion_formula: str | None = None
    ionization_mechanism_id: str | None = None
    isotope_label: str | None = None
    isotope_formula: str | None = None
    source: str | None = None
    fit_score: float | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    tier: str
    #: The tier the producing engine itself concluded, when it said so. Absent
    #: on in-app rows, whose engine tier is ``tier``. Where the two differ, the
    #: engines disagree about how much confidence the evidence supports - which
    #: is what makes an imported run worth reading beside an in-app one.
    engine_tier: str | None = None
    #: The identification level the producing engine itself stated, in the
    #: shared scale, when it said so: an imported run's, beside its
    #: ``engine_tier``. Absent on in-app rows, whose level is ``level``; a derived
    #: row carries its batch peak's where it carries its ``engine_tier``.
    engine_level: str | None = None
    target_compound_id: str | None = None
    target_ion_id: str | None = None
    owner_peak_assignment_id: str | None = None
    #: The evidence this row's tier was read off - ``fit_score`` weighted by the
    #: chemical plausibility of ``assigned_formula`` (provenance.evidence),
    #: flattened because the tier chip displays it beside the tier it produced.
    evidence: float | None = None
    #: Calibrated probability the assignment is correct (provenance.p_correct),
    #: flattened for the API's and the SDK's readers. The app does not show it
    #: while the curve behind it is provisional (assignment quality plan, step
    #: 3.4d).
    p_correct: float | None = None
    #: Whether the calibration curve behind p_correct is provisional.
    p_correct_provisional: bool | None = None
    #: Number of adducts corroborating the compound (provenance.corroboration),
    #: flattened for the ledger's corroboration marker.
    corroboration_adducts: int | None = None
    #: How many of the run's ionization channels committed this row's neutral
    #: (provenance.cross_channel.channels), flattened for the same marker. The
    #: mechanical form of the field above and a superset of it: that one counts
    #: the adducts a curated compound matched through and is therefore null on
    #: every untargeted row, which is most of a ledger. Unlike that one, this
    #: count is NOT folded into ``p_correct`` - it is evidence the run recorded,
    #: not a score it applied.
    corroboration_channels: int | None = None
    #: How many formulas this peak's own evidence could not tell apart
    #: (provenance.candidate_density): 1 means the winner stood alone at the top
    #: of the run's arbitration, and more is the size of the tie it won from.
    #: Read off the row's rival set (provenance.rival_set), at the model the
    #: run's caps read: on a list hit of a run that searched, the formula
    #: search's closed-shell rivals are counted there beside the known set's.
    #: Flattened because it cannot be recovered from the row's ``alternatives``,
    #: which are capped at the run's ``max_alternatives`` - counting those
    #: counts the cap. Null on an isotopologue, which was predicted from its
    #: owner rather than searched, and on an imported row: an external engine may
    #: publish a count of its own inside ``provenance.engine_provenance``, and
    #: this column deliberately does not read it - the number means what THIS
    #: engine's arbitration measured, and peaky's is a different measurement
    #: rendered as text.
    candidate_density: int | None = None
    #: How far this row's mass error sits from the run's own fitted centre at
    #: the row's m/z, in the run's own fitted widths (provenance.mass_z). Flattened because it is
    #: the one per-row number a reader scanning a whole ledger needs and cannot
    #: derive: the ppm error beside it means nothing without the calibration the
    #: run recorded, and a gated row's tier cannot be audited without it.
    mass_z: float | None = None
    #: What a reference list calls this row's formula, as the inspector's
    #: "listed as" field shows it: ``name`` and ``source`` of the first identity
    #: the run matched from a list, the ``tags`` its lists carry (``background``)
    #: and the ``total`` of names matched (``listing.reference_listing``, off
    #: provenance.reference_identities). A row matched to a target compound
    #: carries what the compound declares under ``target``: ``label``
    #: ``"target"``, its ``name`` and ``inchikey``, and ``listed``, whether a
    #: list carries that key (None where it declares none); with no list name
    #: the reference fields are None and ``total`` 0
    #: (``listing.with_target``). None on a row neither names. A row
    #: derived from the batch ledger carries the listing its member's registry
    #: entry recorded.
    reference_listing: dict | None = None
    #: The ``rule`` of each of the row's tier reasons (provenance.tier_reasons),
    #: in order; None where it records none. The reasons' details are in the
    #: detail read. This and the next three are what the gate card reads of
    #: every committed row (``ledger_reads._rule_records``), flattened so a whole
    #: run is one paged read rather than one detail read per row.
    tier_reason_rules: list[str] | None = None
    #: Whether the mass gate names a corroboration for the row
    #: (provenance.mass_gate.corroborated_by); None where the row has no mass
    #: gate record.
    mass_gate_corroborated: bool | None = None
    #: How many lines of the row's predicted envelope the spectrum should have
    #: shown and does not hold (provenance.predicted_envelope, state
    #: ``absent``); None where the row has no predicted envelope.
    envelope_absent_lines: int | None = None
    #: Whether the instrument did not separate the row's peak from a
    #: neighbour (provenance.peak_separation) and no other channel commits its
    #: neutral on a separated peak (provenance.cross_channel.separated_through);
    #: None where the run did not read the peak's width.
    peak_unseparated: bool | None = None
    #: The batch peak this row's peak is a member of: carried by a row derived
    #: from the batch ledger (``fold_view``), looked up on read for a run's own
    #: row; None when the peak is not in the ledger. What the sample ledger
    #: plots in the batch chart.
    batch_peak_id: str | None = None
    #: The row's identification level in the shared scale (``4a``, ``5a``,
    #: ...): what a reader may state of the row, from the facts the run
    #: recorded (``identification_level``). Every committed row of a run made
    #: since the level carries one, an isotopologue its monoisotopic row's;
    #: None on a row that commits no formula, an imported row, a row derived
    #: from the batch ledger and a row from before the level. Moves no tier.
    level: str | None = None
    #: One line naming what earned the level
    #: (provenance.identification_level.evidence).
    level_evidence: str | None = None
    #: What would raise the level, written from the tests the row is missing
    #: (provenance.identification_level.lift).
    level_lift: str | None = None
    manual_action: str | None = None  #: provenance.manual.action (ledger_reads)


class PeakAssignmentDetailRecord(PeakAssignmentRecord):
    """A full assignment row: the slim ledger row plus the inspector detail."""

    alternatives: list | None = None
    provenance: dict | None = None
    #: The reference-list compounds ``assigned_formula`` is listed as, looked up
    #: when the row is read and scoped as Stage A matches
    #: (``ledger_reads.with_known_compounds``): name, source, licence, InChIKey, the
    #: source's own id and its cross-references, at most as many as a run keeps.
    #: Each alternative a list holds carries the same key. Empty where no list
    #: holds the formula, and None where the row commits none or the lookup could
    #: not run.
    known_compounds: list | None = None
    #: How many records name ``assigned_formula``, the ones past the cap of
    #: ``known_compounds`` included; 0 where none does.
    known_compounds_total: int | None = None
    #: What the batch's co-variation reading (``batch_coherence``) says of the
    #: batch peak this row's peak sits in, read when the row is read: how its
    #: trace follows the same neutral's other channels across the batch - the
    #: strongest partner's ``verdict``, ``r``, ``n_spectra`` and ``reason``,
    #: and every partner under ``partners``. None where the batch peak carries
    #: no reading, or one read for another formula or mechanism than the row's,
    #: and on an isotope line. Display-only: no tier reads it.
    batch_co_variation: dict | None = None
    #: What the batch's isotope checks (``batch_checks``) state of the batch
    #: peak this row's peak sits in, for the row's own reading (an M0 row's
    #: formula, ion and mechanism, or a reagent row's ion): ``halogen_lock``,
    #: ``label_twin`` and ``neutral_pair``; and the level they read this row at
    #: where they move its own (``batch_level``, ``batch_level.row_batch_level``,
    #: marked ``borrowed``). None elsewhere, on an isotope line too.
    #: Display-only: no tier reads them.
    batch_checks: dict | None = None
    batch_level: dict | None = None
    #: The batch search's admission of the batch peak this row's peak sits in
    #: on the peak's persistence (``batch_untargeted``), read when the row is
    #: read: the ``share`` of its partition's spectra the peak was in, with
    #: ``n_present`` and ``n_samples``, the partition's ``split`` it cleared,
    #: and the reading the search elected there (``formula``, ``ion_formula``,
    #: ``ionization_mechanism_id``). ``tier_reasons`` is the batch's reasons
    #: about that reading - held at candidate on persistence alone, or what
    #: corroborates it - and ``consensus_tier`` the batch peak's tier, which
    #: says whether the cap holds the reading at candidate or the samples'
    #: vote already put it lower; both on a monoisotopic row that reads it,
    #: None on any other. None where the search took the peak on its height,
    #: or never searched it.
    batch_persistence: dict | None = None
    #: *Read co-variation*'s reading of the trace of the batch peak this row's
    #: peak sits in against its partition's normaliser (step 4.2e,
    #: ``batch_coherence.flat_series_record``), on a monoisotopic row that
    #: reads the reading it was read on: the ``verdict`` (``flat``,
    #: ``varying``, ``untestable`` with its ``reason``), the ``cv`` over the
    #: normaliser, ``n_present`` of ``n_spectra`` and the ``share``, the
    #: ``normaliser``, and on a flat reading the ion of the batch's own source
    #: list its ion is (``source_ion``). An origin flag: no tier reads it.
    #: None where no reading was read for it.
    batch_flat_series: dict | None = None
    #: The batch's isotopologue stamp (step 4.2c, ``batch_isotopologues``) on
    #: the batch peak member this row's peak is, in this sample: the parent
    #: batch peak (``batch_peak_id``) whose isotope line the batch reads the
    #: peak as, with the parent's ``formula``, ``ion_formula`` and
    #: ``ionization_mechanism_id``, the isotope (``label``), the predicted
    #: ``ratio``, the track's reading (``judged``, ``holds``, ``share``,
    #: ``verdict``), ``n_predicted`` and ``n_picker``, this sample's
    #: ``stamp_source`` (``predicted`` or ``picker``), and the parent's peak
    #: in this sample (``parent_sample_peak_id``). Served on any row, as the
    #: stamp is about the peak, not about what the row reads it as. None
    #: where the peak carries no stamp in this sample. Display-only: no tier
    #: reads it.
    batch_stamped_isotopologue_of: dict | None = None
    #: The batch's diagnostic isotopologues record of the batch peak this
    #: row's peak sits in (the ledger's ``diagnostic_isotopologues``): each
    #: isotope line predicted for its formula, whether a batch peak stands at
    #: it, and whether it was stamped. On a monoisotopic row that reads the
    #: formula and mechanism the record was predicted for; None otherwise.
    #: Display-only: no tier reads it.
    batch_diagnostic_isotopologues: dict | None = None
    #: The batch's series record (step 4.1, ``batch_series``) of the batch peak
    #: this row's peak sits in, as the ledger serves it (``series``): the
    #: ``formula`` proposed, its ``ionization_mechanism_id``, the ``unit``,
    #: ``step`` and ``depth``, the ``root_batch_peak_id`` and ``root_formula``
    #: its chain starts at and the stage's ``version``, with the ``root`` as
    #: a group member (``batch_groups``' shape; None where no group lists
    #: it). None where no series record stands.
    batch_series: dict | None = None
    #: The ratio-stability reading (``continuity``) of that batch peak: its
    #: ``grade``, whether the pass left some of its pairs ungraded
    #: (``partial``), whether the ledger has moved past it since (``stale``),
    #: and the pass's ``detail`` (the ledger's ``continuity``). None where the
    #: pass never graded nor withdrew it.
    batch_continuity: dict | None = None
    #: The batch's known-species decision on that batch peak
    #: (``batch_known_species``), as the ledger serves it (``known_species``);
    #: None where no decision stands.
    batch_known_species: dict | None = None
    #: The water-ladder rung that batch peak sits at (``batch_water_ladder``),
    #: as the ledger serves it (``water_ladder``); None where no reading
    #: stands.
    batch_water_ladder: dict | None = None
    #: The probable-blend flag of that batch peak (``batch_blend``), as the
    #: ledger serves it (``blend``); None where its members do not separate.
    batch_blend: dict | None = None
    #: The groups that batch peak is a member of (``batch_series.batch_groups``,
    #: the same the batch-peaks read serves as ``batch_groups``): each with
    #: ``group_id``, ``kind`` (``series``, ``channels``, ``co_variation``,
    #: ``check_pair``) and ``members``, each member with its
    #: ``batch_peak_id``, ``formula``, ``ion_formula``,
    #: ``ionization_mechanism_id``, ``mechanism`` (the notation), ``mz``,
    #: ``consensus_tier``, and ``sample_peak_id``, this sample's own peak at
    #: it (None where the sample holds none); a series member also its
    #: ``step`` from the root. A step with no batch peak is no member. Empty
    #: where it is in none, None where the peak sits in no batch peak. These
    #: six, from ``batch_series`` on, are about the batch peak, whatever the
    #: row reads its peak as.
    batch_groups: list | None = None


class PeakAssignmentsResponse(BaseModel):
    """Peaks-with-assignments for a sample.

    Uses the standard list envelope (status/message/results/data) like every
    other data-bearing endpoint. Run identity is denormalized onto each row
    (``peak_assignment_run_id``); full run metadata is served separately by the
    runs endpoint, so there is no run object echoed here.
    """

    status: str = "success"
    message: str
    results: int
    #: Rows matching the query across every page, so a client knows when paging
    #: is done. ``results`` is the size of this page.
    total: int = 0
    data: list[PeakAssignmentRecord]


class PeakAssignmentDetailResponse(BaseModel):
    """One full assignment record, with the inspector-only JSON detail."""

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentDetailRecord]


class AlternativeScoreRecord(BaseModel):
    """One formula-only alternative, measured against the peak on demand.

    Either scored - `fit_score` and the adduct it was measured under are
    present - or blocked, where `blocked_reason` says in plain language why no
    adduct puts the formula on this peak. Never both.

    These numbers are session data, not run data: they are computed per request
    and never written onto the stored row, so a client that wants to commit one
    sends it back as a `set_assignment` (the composition-search action) rather
    than promoting a stored alternative.
    """

    #: Position of the entry in the row's stored `alternatives` list. Clients
    #: should match on `assigned_formula` instead where the list they render is
    #: filtered, since the index is only meaningful against the stored order.
    alternative_index: int
    assigned_formula: str
    #: Seven Golden Rules plausibility, a pure function of the formula.
    plausibility: float | None = None
    #: How many of the sample's adducts were tried, and how many placed the
    #: formula on this peak. `adducts_matched` is absent when none did.
    adducts_tried: int
    adducts_matched: int | None = None
    fit_score: float | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    #: `fit x plausibility`, the currency a tier is read off.
    evidence: float | None = None
    ionization_mechanism_id: str | None = None
    ionization_mechanism: str | None = None
    ion_formula: str | None = None
    #: Always "M0" when scored: the shortlist proposes a composition for this
    #: peak's own mass, so only the ion's monoisotopic peak was allowed to pair.
    isotope_label: str | None = None
    blocked_reason: str | None = None


class AlternativeScoresResponse(BaseModel):
    """A row's formula-only alternatives, measured against its peak."""

    status: str = "success"
    message: str
    results: int
    data: list[AlternativeScoreRecord]


class MeasuredIsotopologueRecord(BaseModel):
    """One predicted isotopologue of a measured composition, with the peak it
    paired to and the errors of that pairing (absent when it paired to none)."""

    #: The offset from the ion's monoisotopic isotopologue (``M0``, ``M+2`` ...),
    #: counted the way an isotope table counts it: for a bromine-rich ion the
    #: M0 is the lightest peak of the cluster, not the most intense.
    isotope_label: str | None = None
    isotope_formula: str | None = None
    #: Theoretical m/z and relative abundance, the latter a fraction of the
    #: ion's most abundant isotopologue, so it never exceeds 1.
    mz: float | None = None
    relative_abundance: float | None = None
    sample_peak_id: str | None = None
    mz_error_ppm: float | None = None
    abundance_error: float | None = None


class DerivedEvidenceRecord(BaseModel):
    """A derived row's family measured against its sample on demand: what a
    run would have stored beside the fit and the tier.

    Keyed by the family's M0 (``peak_assignment_id``); every member reads its
    own numbers off ``isotopologues`` by ``sample_peak_id``. Session data,
    computed per request and never written onto the ledger.
    """

    peak_assignment_id: str
    sample_peak_id: str
    assigned_formula: str | None = None
    ionization_mechanism_id: str | None = None
    ion_formula: str | None = None
    #: The stored fit, which the tier was read off; the measurement's own fit
    #: is reported beside it.
    fit_score: float | None = None
    measured_fit_score: float | None = None
    plausibility: float | None = None
    #: The stored fit times the plausibility, the currency the tier is banded on.
    evidence: float | None = None
    #: The family's main peak's own errors, read off the isotopologue that
    #: paired with it; absent when none did, in which case `main_peak_note`
    #: says which prediction came nearest and where it went. The pattern was
    #: still measured then - `blocked_reason` is for a measurement that could
    #: not be made at all.
    mz_error_ppm: float | None = None
    abundance_error: float | None = None
    isotopologues: list[MeasuredIsotopologueRecord] = []
    blocked_reason: str | None = None
    main_peak_note: str | None = None


class DerivedEvidenceResponse(BaseModel):
    """A derived row's on-demand measurement (one entry), or nothing for a
    run's own row."""

    status: str = "success"
    message: str
    results: int
    data: list[DerivedEvidenceRecord]


class PeakAssignmentRunsResponse(BaseModel):
    """Peak assignment runs of a sample, newest first."""

    status: str = "success"
    message: str
    results: int
    data: list[PeakAssignmentRunRecord]


class PeakAssignmentQueryParams(BaseModel):
    """Optional filters and paging for the peaks-with-assignments query."""

    peak_assignment_run_id: str | None = Field(
        None, description="Specific run to read; defaults to the latest completed run."
    )
    tier: AssignmentTier | None = Field(None, description="Filter by confidence tier.")
    engine_tier: AssignmentTier | None = Field(
        None,
        description=(
            "Filter by the tier the producing engine itself concluded. Only "
            "imported runs carry one, and a sample read from the batch ledger "
            "on the M0 row an import's verdict is about; an in-app run's "
            "engine tier is its `tier`."
        ),
    )
    tier_disagrees: bool | None = Field(
        None,
        description=(
            "Keep only rows where the engine's own tier differs from this "
            "server's (`engine_tier` is set and not equal to `tier`), or with "
            "false only rows where they agree. Rows carrying no engine tier are "
            "excluded either way: absence is not agreement. This is the "
            "comparison an imported run exists for, so it is a filter rather "
            "than something to reconstruct client-side over a paged read."
        ),
    )
    role: AssignmentRole | None = Field(None, description="Filter by peak role.")
    source: AssignmentSource | None = Field(
        None, description="Filter by assignment source."
    )
    level: IdentificationLevel | None = Field(
        None,
        description=(
            "Filter by identification level (`4a`, `5a`, ...). Only rows of an "
            "in-app run made since the level carry one."
        ),
    )
    limit: int = Field(
        DEFAULT_PAGE_LIMIT,
        ge=1,
        le=MAX_PAGE_LIMIT,
        description=(
            "Maximum rows to return. A run holds one row per detected peak, so a "
            "dense sample runs to tens of thousands; read the whole run by paging "
            "with `offset` until the rows returned reach `total`."
        ),
    )
    offset: int = Field(0, ge=0, description="Rows to skip, for paging.")
