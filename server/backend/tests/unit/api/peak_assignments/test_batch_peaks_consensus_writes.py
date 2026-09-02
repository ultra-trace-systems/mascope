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
    _apply_consensus,
)


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
