"""The words of an assignment row: its roles, its sources, and the keys a run's
records sit under.

A row's ``role`` and ``source``, the placeholders an untargeted row carries
where it names no match, and the provenance and run-config keys every stage
and every reader of a ledger name. Constants only, below every module that
writes or reads a row.
"""

from mascope_tools.composition.measurement_model import RECORD_KEY as MODEL_RECORD_KEY


# Peak roles within an assignment run
ROLE_M0 = "M0"
ROLE_ISO_CHILD = "iso_child"
ROLE_UNASSIGNED = "unassigned"
# A peak the source made rather than the sample: an ion of its reagent, of the
# air it ionizes or of its calibrant beam, or one of their isotopologues,
# claimed by the pre-pass before either stage runs; or a fragment it made of an
# analyte the stages committed, claimed after them. The role is what takes such
# a peak out of the analyte ledger - it carries no `assigned_formula`, so it
# votes on nothing and is counted as explained by its role rather than by a
# formula it has no business claiming.
ROLE_REAGENT = "reagent"
# An instrument artifact rather than an ion: an FT sidelobe, the ringing a very
# intense centroid leaves around itself. Like a reagent row it carries no
# formula and votes on nothing; unlike one it names no ion either, because there
# is none - the peak is the detector's answer to a neighbour, not a species.
ROLE_ARTIFACT = "artifact"

# Which stage won the peak
SOURCE_DATABASE = "database"
SOURCE_UNTARGETED = "untargeted"
# ...or the reagent pre-pass, which is neither: it does not assign a formula to a
# peak, it declares the peak to be the source's own chemistry.
SOURCE_REAGENT = "reagent"
# ...or the artifact pre-pass, which declares it to be the instrument's.
SOURCE_ARTIFACT = "artifact"
# ...or, when no stage did, the person who decided it instead. A manually
# curated row is not the output of a stage, and saying 'database' or
# 'untargeted' on it would credit an engine with a choice a human made.
SOURCE_MANUAL = "manual"

# Carried DataFrame column: reference-database identities (a list of dicts) for
# isotope rows that came from the reference mirror rather than the curated target
# library. Present only on reference rows; the inversion drops it into provenance
# and nulls the (synthetic) target FK ids so reference winners persist without a
# dangling target_compound_id / target_ion_id.
REFERENCE_IDENTITIES_COL = "reference_identities"

# Placeholder used by the untargeted finder for unassigned peaks
UNTARGETED_NO_MATCH = "---"
# The finder emits "()" for ionization/reagent peaks (an adduct with no
# molecular core). That is not a molecular formula, so it must not be persisted
# as one. A reagent peak that reaches the finder at all has escaped the pre-pass
# (`reagent_pass`), whose library claims these before either stage runs; this
# stays as the backstop for a source whose profile has no library.
UNTARGETED_IONIZATION = "()"

#: Key under which a run records its measurement model
#: (``measurement_model.MeasurementModel``): the version it scored and capped
#: at, its centre (a line that follows m/z where the commits demanded one), the
#: width a mass error was judged in and where it came from, the anchors it was
#: fitted on by kind, what the refit on the commits measured and whether the
#: run took it, the v1 the search ranked at, how a line was matched, and what
#: the mass gate capped (:func:`measurement_model_record`). One record, because
#: the score and the caps read one model: a row's fit and its ``mass_z`` are
#: read at one centre and one width. The line-share tolerance the model reads is
#: recorded beside it, under :data:`ENVELOPE_TOLERANCE_KEY`.
MEASUREMENT_MODEL_KEY = MODEL_RECORD_KEY

#: Key under which a run records what its own channels corroborated: the
#: mechanisms it searched, which of them could be donating a reported nitrogen,
#: how many committed neutrals a second channel confirmed, and what the reagent-N
#: rule capped. Separate from :data:`MEASUREMENT_MODEL_KEY` because the evidence
#: is of a different kind - that one is where a row sits on the mass axis, this
#: one is how many independent chemistries saw the same neutral.
CROSS_CHANNEL_KEY = "cross_channel"

#: Key under which a run records what its partner gate decided
#: (:func:`apply_partner_gates`): which opportunistic readings stood on a
#: partner, which it turned to another reading of their ion, what its contest
#: between two borne-out readings took and at what margin, and the rounds its
#: walk took. Beside :data:`CROSS_CHANNEL_KEY`, whose pass reads what it leaves.
PARTNER_GATE_KEY = "partner_gate"

#: Key under which a run records the rule set that judged its commits: the
#: version, the thresholds it demoted on, and what each rule took. On the run
#: for the same reason the tier BANDS are - a tier is only comparable across
#: two runs together with the rules that produced it, and these rules will
#: change while old ledgers stay readable.
TIERING_KEY = "tiering"

#: Key under which a run records how well it measured an isotope line's share:
#: the tolerance Stage A fitted on its committed envelopes, in the form the
#: instrument class takes (a response curve against expected signal-to-noise on
#: an Orbitrap, a centre and a width on a TOF), the level it reached (the file's
#: own lines, pooled with its siblings', or today's numbers), and the lines it
#: read. Every fit score of the run - Stage A's rows, the search's ranking, the
#: seeded re-score - was read at it (``envelope_fit``).
ENVELOPE_TOLERANCE_KEY = "envelope_tolerance"

#: Key under which a run records how much of its spectrum the untargeted stage
#: was actually offered. On the run's config beside the resolved profile, and for
#: the same reason: "searched 300 of 2,577 peaks" and "searched all 2,577" are
#: different results, and nothing else on the row would ever say which happened.
SEARCH_SCOPE_KEY = "search_scope"

#: Key under which a run records what it read of its sample's batch decisions
#: (``batch_decisions.BatchDecisions.record``): the version and state of every
#: batch stage whose records it read, how many records of each it read and at
#: which versions, and what they did to its rows. A run that read an older
#: decision than the batch now holds is told apart by it (MAS-985, MAS-1013).
BATCH_DECISIONS_KEY = "batch_decisions"

#: Key under which a run records what its fragment claim took
#: (``reagent_pass.claim_fragments``): the ladders its profile names, the parents
#: it found committed, the peaks it read as their fragments and those it left,
#: by why. Written only by a run whose profile names a ladder.
FRAGMENTS_KEY = "fragments"


#: Key under which a decoy run's stored config records what it ran against:
#: the control's id, the window a shift had to clear, the channels it swapped
#: and the calibration it inherited. Server state, beside the request's own
#: ``decoy`` block, for the reason the licence gate is: a client says which arm,
#: never which run it bounds.
DECOY_ARM_KEY = "decoy_arm"
