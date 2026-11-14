"""
Unit tests for the consensus write-back: an anchor is rewritten only when its
consensus changed.

Every fold recomputes every anchor its sample touched, and most recomputes reach
the answer the previous one did. ``_apply_consensus`` is the comparison that
keeps those from becoming row updates - the difference between a fold writing a
handful of anchors and rewriting most of the batch, which is what stamping the
modified time unconditionally used to do (18x heap bloat on a 32-sample batch).

Pure: the anchor is a plain object with the ``BatchPeak`` attributes, no DB.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

from mascope_backend.api.new.peak_assignments.batch_peaks import Consensus
from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
    _CONSENSUS_CHUNK,
    _apply_consensus,
)


#: asyncpg's hard limit, at protocol/prepared_stmt.pyx: a query carrying more
#: arguments than this is refused before it is sent.
_ASYNCPG_MAX_BIND_PARAMS = 32767


def test_the_consensus_chunk_stays_under_the_drivers_bind_limit():
    """The chunk is what keeps the recompute's IN lists bindable, so raising it
    past the driver's cap would reintroduce the failure it exists to prevent -
    and only on the dense samples nobody tests with."""
    assert 0 < _CONSENSUS_CHUNK < _ASYNCPG_MAX_BIND_PARAMS


_NOW = datetime(2026, 9, 3, 12, 0, tzinfo=timezone.utc)
_THEN = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _consensus(**overrides) -> Consensus:
    base = dict(
        consensus_formula="C6H12O6",
        consensus_ion_formula="C6H13O6+",
        ionization_mechanism_id="mech-1",
        consensus_tier="assigned",
        best_fit_score=0.95,
        support_fraction=1.0,
        n_present=2,
        is_ambiguous=False,
        max_intensity=5000.0,
        isotopologue_of=None,
        alternatives=[{"formula": "C6H12O6", "share": 1.0}],
        provenance={"members": 2},
    )
    base.update(overrides)
    return Consensus(**base)


def _anchor_holding(consensus: Consensus) -> SimpleNamespace:
    """An anchor row as the database would hand it back for ``consensus``."""
    return SimpleNamespace(
        consensus_formula=consensus.consensus_formula,
        consensus_ion_formula=consensus.consensus_ion_formula,
        ionization_mechanism_id=consensus.ionization_mechanism_id,
        consensus_tier=consensus.consensus_tier,
        consensus_role=consensus.consensus_role,
        best_fit_score=consensus.best_fit_score,
        support_fraction=consensus.support_fraction,
        n_present=consensus.n_present,
        is_ambiguous=int(consensus.is_ambiguous),
        max_intensity=consensus.max_intensity,
        isotopologue_of=consensus.isotopologue_of,
        alternatives=list(consensus.alternatives),
        provenance=dict(consensus.provenance),
        batch_peak_utc_modified=_THEN,
    )


def test_an_unchanged_consensus_writes_nothing():
    anchor = _anchor_holding(_consensus())

    assert _apply_consensus(anchor, _consensus(), _NOW) is False
    assert anchor.batch_peak_utc_modified == _THEN


def test_a_changed_scalar_is_written_and_stamped():
    anchor = _anchor_holding(_consensus())

    assert _apply_consensus(anchor, _consensus(n_present=3), _NOW) is True
    assert anchor.n_present == 3
    assert anchor.batch_peak_utc_modified == _NOW
    # The rest of the row is left as it was, not re-assigned.
    assert anchor.consensus_formula == "C6H12O6"


def test_json_columns_compare_by_value_not_identity():
    """A freshly computed list is a different object from the stored one, and
    a tuple in the computed value is a list once stored; neither is a change."""
    anchor = _anchor_holding(_consensus())
    fresh = _consensus(
        alternatives=[{"formula": "C6H12O6", "share": 1.0}],
        provenance={"members": 2},
    )
    fresh.alternatives = [dict(a) for a in fresh.alternatives]
    fresh.provenance = {"members": 2, "pair": (1, 2)}
    anchor.provenance = {"members": 2, "pair": [1, 2]}

    assert _apply_consensus(anchor, fresh, _NOW) is False


def test_a_changed_alternatives_list_is_written():
    anchor = _anchor_holding(_consensus())
    fresh = _consensus(alternatives=[{"formula": "C6H12O6", "share": 0.6}])

    assert _apply_consensus(anchor, fresh, _NOW) is True
    assert anchor.alternatives == [{"formula": "C6H12O6", "share": 0.6}]
    assert anchor.batch_peak_utc_modified == _NOW


def test_ambiguity_is_compared_as_the_integer_the_column_stores():
    anchor = _anchor_holding(_consensus(is_ambiguous=True))
    assert anchor.is_ambiguous == 1

    assert _apply_consensus(anchor, _consensus(is_ambiguous=True), _NOW) is False
    assert _apply_consensus(anchor, _consensus(is_ambiguous=False), _NOW) is True
    assert anchor.is_ambiguous == 0


def test_a_fresh_anchor_takes_its_first_consensus():
    """A just-minted anchor holds column defaults; the first recompute fills it."""
    anchor = SimpleNamespace(
        consensus_formula=None,
        consensus_ion_formula=None,
        ionization_mechanism_id=None,
        consensus_tier="unassigned",
        consensus_role=None,
        best_fit_score=None,
        support_fraction=None,
        n_present=0,
        is_ambiguous=0,
        max_intensity=None,
        isotopologue_of=None,
        alternatives=None,
        provenance=None,
        batch_peak_utc_modified=_THEN,
    )

    assert _apply_consensus(anchor, _consensus(), _NOW) is True
    assert anchor.consensus_formula == "C6H12O6"
    assert anchor.n_present == 2
    assert anchor.alternatives == [{"formula": "C6H12O6", "share": 1.0}]
    assert anchor.batch_peak_utc_modified == _NOW


def test_an_importing_engines_verdict_comes_off_when_the_identity_moves():
    """The verdict is about the formula the import landed; once the consensus
    names another, a verdict left standing would be about nothing shown."""
    anchor = _anchor_holding(_consensus())
    anchor.engine_tier, anchor.engine_claim = "candidate", "level 3"

    assert _apply_consensus(anchor, _consensus(consensus_formula="C7H14O7"), _NOW)
    assert (anchor.engine_tier, anchor.engine_claim) == (None, None)


def test_a_new_channel_for_the_same_formula_is_another_identity_too():
    anchor = _anchor_holding(_consensus())
    anchor.engine_tier, anchor.engine_claim = "candidate", "level 3"

    assert _apply_consensus(anchor, _consensus(ionization_mechanism_id="mech-2"), _NOW)
    assert (anchor.engine_tier, anchor.engine_claim) == (None, None)


def test_the_verdict_stays_while_the_identity_does():
    """A new sample's vote moves the derived tier and the support, not what the
    engine said about the formula."""
    anchor = _anchor_holding(_consensus())
    anchor.engine_tier, anchor.engine_claim = "candidate", "level 3"

    assert _apply_consensus(
        anchor, _consensus(consensus_tier="candidate", n_present=3), _NOW
    )
    assert (anchor.engine_tier, anchor.engine_claim) == ("candidate", "level 3")


# --- the persistence rule's facts, read across the batch (step 4.2b) -------------


def _reading(formula="C9H11NO", mechanism="mech-h", isotopologue_of=None):
    return Consensus(
        consensus_formula=formula,
        ionization_mechanism_id=mechanism,
        consensus_tier="assigned",
        isotopologue_of=isotopologue_of,
    )


def test_a_line_counts_once_per_sample_and_only_under_the_reading():
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        line_samples,
    )

    asked = {"bp-a": _reading()}
    rows = [
        ("bp-a", "s1", "C9H11NO", "mech-h"),
        # The same sample's second line: one sample.
        ("bp-a", "s1", "C9H11NO", "mech-h"),
        ("bp-a", "s2", "C9H11NO", "mech-h"),
        # A sample that read the peak as another formula, or through another
        # mechanism, and committed that reading's line.
        ("bp-a", "s3", "C10H13N", "mech-h"),
        ("bp-a", "s4", "C9H11NO", "mech-na"),
        # A source ion's line names no formula.
        ("bp-a", "s5", None, None),
        ("bp-other", "s1", "C9H11NO", "mech-h"),
    ]
    assert line_samples(rows, asked) == {"bp-a": 2}
    assert line_samples(rows[3:5], asked) == {}


def test_the_channels_are_the_partitions_own_readings_on_peaks_of_their_own():
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        partition_channels,
    )
    from mascope_backend.api.new.peak_assignments.cross_channel import neutral_key

    here, there = ("sb", "mode", "heights"), ("sb", "mode", "areas")
    stored = [
        ("bp-h", here, "C9H11NO", "mech-h", None),
        # The same neutral spelled another way, through sodium.
        ("bp-na", here, "C9H11N1O1", "mech-na", None),
        # Another partition's reading is not this one's channel.
        ("bp-k", there, "C9H11NO", "mech-k", None),
        # A line of another batch peak is no peak of its own.
        ("bp-line", here, "C9H11NO", "mech-nh4", "bp-x"),
        # A reading with no mechanism names no channel.
        ("bp-none", here, "C9H11NO", None, None),
    ]
    key = neutral_key("C9H11NO")
    assert partition_channels(stored, {})[(here, key)] == {"mech-h", "mech-na"}
    assert partition_channels(stored, {})[(there, key)] == {"mech-k"}


def test_a_fresh_reading_replaces_what_the_ledger_stored():
    from mascope_backend.api.new.peak_assignments.batch_peaks_controller import (
        partition_channels,
    )
    from mascope_backend.api.new.peak_assignments.cross_channel import neutral_key

    here = ("sb", "mode", "heights")
    stored = [
        ("bp-h", here, "C9H11NO", "mech-h", None),
        ("bp-na", here, "C9H11NO", "mech-na", None),
    ]
    key = neutral_key("C9H11NO")
    # This pass moved the sodiated batch peak to another formula, and made a
    # third one, stored with nothing, a reading through ammonium.
    fresh = {
        "bp-na": (here, _reading("C10H13N", "mech-na")),
        "bp-nh4": (here, _reading("C9H11NO", "mech-nh4")),
    }
    channels = partition_channels(stored, fresh)
    assert channels[(here, key)] == {"mech-h", "mech-nh4"}
    # A fresh reading that is a line of another batch peak commits nothing.
    fresh = {"bp-na": (here, _reading("C9H11NO", "mech-na", isotopologue_of="bp-h"))}
    assert partition_channels(stored, fresh)[(here, key)] == {"mech-h"}
