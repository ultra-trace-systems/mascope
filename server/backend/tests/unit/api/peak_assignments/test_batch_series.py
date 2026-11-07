"""Unit tests for the series detection's pure parts (step 4.1).

No DB: which anchors the walk starts from and may reach, what the plan does
with each committed proposal, the batch's FDR, the outcome, what a run copies
onto its rows (``service.stamp_series``) and which members a proposal may take
(``batch_untargeted._takes_the_member``). See ``batch_series.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments.batch_peaks import (
    ROLE_ISO_CHILD,
    SERIES_KEY,
    role_code,
)
from mascope_backend.api.new.peak_assignments.batch_series import (
    ModeChemistry,
    batch_fdr,
    ion_formula_of,
    plan_series,
    series_anchors,
    series_outcome,
)
from mascope_backend.api.new.peak_assignments.batch_untargeted import (
    Annotation,
    _takes_the_member,
)
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ARTIFACT,
    ROLE_REAGENT,
    evidence_for,
)
from mascope_backend.api.new.peak_assignments.service import stamp_series
from mascope_tools.composition.series import (
    DECOY_OFFSETS_DA,
    MIN_LINKS,
    SeriesEvidence,
    ion_mz,
)
from mascope_tools.composition.utils import parse_formula_tokens


MECHANISM = "mech-dep"
NOTATION = "[M-H]-"
NOTATIONS = {MECHANISM: NOTATION}


def anchor(key, *, tier="unassigned", formula=None, mz=None, **extra):
    """A batch peak as the walk reads one."""
    if mz is None:
        mz = ion_mz(parse_formula_tokens(formula), NOTATION) if formula else 200.0
    fields = dict(
        batch_peak_id=key,
        ionization_mode_id="mode-1",
        mz=mz,
        consensus_tier=tier,
        consensus_formula=formula,
        consensus_ion_formula=None,
        ionization_mechanism_id=MECHANISM if formula else None,
        consensus_role=None,
        isotopologue_of=None,
        provenance=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def at(formula):
    return ion_mz(parse_formula_tokens(formula), NOTATION)


class TestTheWalksAnchors:
    def test_assigned_readings_are_sources_and_the_rest_targets(self):
        modes = series_anchors(
            [
                anchor("src", tier="assigned", formula="C8H10O5"),
                anchor("open", mz=199.06),
                anchor("held", tier="candidate", formula="C9H12O5"),
            ],
            NOTATIONS,
        )
        mode = modes["mode-1"]
        assert [(s.key, s.formula, s.mechanism) for s in mode.sources] == [
            ("src", "C8H10O5", NOTATION)
        ]
        assert [(t.key, t.formula) for t in mode.targets] == [
            ("open", None),
            ("held", "C9H12O5"),
        ]
        assert mode.formula_of == {"src": "C8H10O5", "held": "C9H12O5"}

    @pytest.mark.parametrize(
        "extra",
        [
            {"consensus_role": "reagent"},
            {"isotopologue_of": "bp-owner"},
            {"consensus_ion_formula": "[13C]C7H9O5-"},
        ],
    )
    def test_a_claimed_peak_or_an_isotope_line_is_neither(self, extra):
        modes = series_anchors(
            [
                anchor("src", tier="assigned", formula="C8H10O5", **extra),
                anchor("held", tier="candidate", formula="C9H12O5", **extra),
                anchor("open", **extra),
            ],
            NOTATIONS,
        )
        assert modes == {}

    def test_a_pinned_anchor_is_no_target(self):
        pinned = {"manual": {"formula": "C9H12O5"}}
        modes = series_anchors(
            [
                anchor("held", tier="candidate", formula="C9H12O5", provenance=pinned),
                anchor("open", provenance=pinned),
            ],
            NOTATIONS,
        )
        assert modes["mode-1"].targets == []

    def test_a_pinned_assigned_anchor_is_still_a_source(self):
        pinned = {"manual": {"formula": "C8H10O5"}}
        modes = series_anchors(
            [anchor("src", tier="assigned", formula="C8H10O5", provenance=pinned)],
            NOTATIONS,
        )
        assert [s.key for s in modes["mode-1"].sources] == ["src"]

    def test_an_assigned_member_of_a_series_is_a_target_not_a_source(self):
        # Measured to assigned by an earlier detection, or lifted by a run of a
        # sample that counted the series: still the series' member.
        series = {SERIES_KEY: {"formula": "C9H12O5", "has_anchor": True}}
        modes = series_anchors(
            [
                anchor("src", tier="assigned", formula="C8H10O5"),
                anchor("member", tier="assigned", formula="C9H12O5", provenance=series),
            ],
            NOTATIONS,
        )
        mode = modes["mode-1"]
        assert [s.key for s in mode.sources] == ["src"]
        assert [(t.key, t.formula) for t in mode.targets] == [("member", "C9H12O5")]
        assert mode.formula_of["member"] == "C9H12O5"

    def test_an_assigned_anchor_whose_record_names_another_formula_is_a_source(self):
        series = {SERIES_KEY: {"formula": "C9H12O5", "has_anchor": True}}
        modes = series_anchors(
            [anchor("src", tier="assigned", formula="C8H10O5", provenance=series)],
            NOTATIONS,
        )
        assert [s.key for s in modes["mode-1"].sources] == ["src"]
        assert modes["mode-1"].targets == []

    def test_a_pinned_member_of_a_series_is_a_source(self):
        provenance = {
            "manual": {"formula": "C9H12O5"},
            SERIES_KEY: {"formula": "C9H12O5", "has_anchor": True},
        }
        modes = series_anchors(
            [anchor("pin", tier="assigned", formula="C9H12O5", provenance=provenance)],
            NOTATIONS,
        )
        assert [s.key for s in modes["mode-1"].sources] == ["pin"]
        assert modes["mode-1"].targets == []

    def test_a_source_whose_mechanism_is_unknown_is_not_walked(self):
        modes = series_anchors([anchor("src", tier="assigned", formula="C8H10O5")], {})
        assert modes["mode-1"].sources == []

    def test_a_reading_below_assignability_is_neither(self):
        modes = series_anchors(
            [anchor("low", tier="below_assignability", formula="C9H12O5")],
            NOTATIONS,
        )
        assert (modes["mode-1"].sources, modes["mode-1"].targets) == ([], [])

    def test_each_mode_is_walked_on_its_own(self):
        modes = series_anchors(
            [
                anchor("a", tier="assigned", formula="C8H10O5"),
                anchor("b", ionization_mode_id="mode-2"),
            ],
            NOTATIONS,
        )
        assert [t.key for t in modes["mode-2"].targets] == ["b"]
        assert modes["mode-1"].targets == []


#: Six assigned anchors, each with a homologue one CH2 above it: twelve
#: proposals is the floor a family needs.
SOURCES = ["C8H10O5", "C9H12O3", "C10H16O4", "C7H8O6", "C11H18O2", "C6H6O7"]


def _up(formula):
    counts = parse_formula_tokens(formula)
    counts["C"] += 1
    counts["H"] += 2
    return "C{C}H{H}O{O}".format(**counts)


def _down(formula):
    counts = parse_formula_tokens(formula)
    counts["C"] -= 1
    counts["H"] -= 2
    return "C{C}H{H}O{O}".format(**counts)


def ladder_batch(**targets):
    """The six sources, an unassigned anchor at each homologue's m/z, and the
    given overrides by homologue formula."""
    anchors = [
        anchor(f"src-{i}", tier="assigned", formula=formula)
        for i, formula in enumerate(SOURCES)
    ]
    for i, formula in enumerate(SOURCES):
        for name, homologue in (("up", _up(formula)), ("down", _down(formula))):
            fields = {"mz": at(homologue), **targets.get(homologue, {})}
            anchors.append(anchor(f"{name}-{i}", **fields))
    return anchors


CHEMISTRY = {"mode-1": ModeChemistry(tolerance_ppm=3.0, labelled=False, profile="NO3")}


def plan(anchors, chemistry=CHEMISTRY, **kwargs):
    return plan_series(
        series_anchors(anchors, NOTATIONS),
        chemistry,
        mechanism_id_by_notation={NOTATION: MECHANISM},
        batch_peak_run_id="run-1",
        **kwargs,
    )


class TestThePlan:
    def test_a_family_that_beats_its_decoys_is_committed(self):
        result = plan(ladder_batch())
        ch2 = next(e for e in result.evidence["mode-1"] if e.unit == "CH2")
        assert (ch2.n_links, ch2.significant) == (2 * len(SOURCES), True)
        assert 2 * len(SOURCES) == MIN_LINKS
        assert set(result.records) == set(result.annotations)
        assert len(result.records) == MIN_LINKS

    def test_each_proposal_carries_its_record(self):
        result = plan(ladder_batch())
        assert result.records["up-0"] == {
            "formula": "C9H12O5",
            "ionization_mechanism_id": MECHANISM,
            "has_anchor": True,
            "unit": "CH2",
            "units": ["CH2"],
            "step": 1,
            "depth": 1,
            "anchor_batch_peak_id": "src-0",
            "root_batch_peak_id": "src-0",
            "root_formula": "C8H10O5",
            "ppm": 0.0,
            "batch_peak_run_id": "run-1",
        }

    def test_an_unassigned_target_is_measured_as_the_formula_it_was_proposed(self):
        result = plan(ladder_batch())
        assert result.annotations["down-0"] == Annotation(
            formula="C7H8O5",
            ion_formula="C7H7O5-",
            ionization_mechanism_id=MECHANISM,
            role="M0",
            owner_batch_peak_id=None,
        )

    def test_a_candidate_already_reading_the_formula_is_corroborated_not_measured(
        self,
    ):
        held = {"tier": "candidate", "formula": "C9H12O5"}
        result = plan(ladder_batch(C9H12O5=held))
        assert "up-0" in result.corroborated
        assert "up-0" not in result.annotations
        assert result.records["up-0"]["formula"] == "C9H12O5"

    def test_a_candidate_reading_another_formula_is_measured_against_it(self):
        rival = {"tier": "candidate", "formula": "C5H10N4O4"}
        result = plan(ladder_batch(C9H12O5=rival))
        assert "up-0" not in result.corroborated
        assert result.annotations["up-0"].formula == "C9H12O5"

    def test_a_family_short_of_the_floor_commits_nothing(self):
        anchors = ladder_batch()[:-1]
        result = plan(anchors)
        assert (result.records, result.annotations) == ({}, {})
        assert plan(anchors, min_links=MIN_LINKS - 1).records

    def test_a_family_too_little_above_its_decoys_commits_nothing(self):
        # One anchor where the first decoy's step lands: the CH2 family's 12
        # proposals over a decoy mean of 1/6, an enrichment of 72.
        decoy = anchor("decoy", mz=at(_up(SOURCES[0])) + DECOY_OFFSETS_DA[0])
        anchors = ladder_batch() + [decoy]
        ch2 = next(e for e in plan(anchors).evidence["mode-1"] if e.unit == "CH2")
        assert ch2.decoy_links == (1, 0, 0, 0, 0, 0)
        assert ch2.enrichment == pytest.approx(72.0)
        assert plan(anchors, min_enrichment=72.0).records
        assert not plan(anchors, min_enrichment=73.0).records

    def test_a_mode_without_its_chemistry_is_not_walked(self):
        result = plan(ladder_batch(), chemistry={})
        assert (result.evidence, result.records) == ({}, {})

    def test_a_mode_with_nothing_to_reach_is_not_walked(self):
        sources = [anchor("src", tier="assigned", formula="C8H10O5")]
        assert plan(sources).evidence == {}

    def test_a_proposal_whose_mechanism_has_no_id_is_dropped(self):
        result = plan_series(
            series_anchors(ladder_batch(), NOTATIONS),
            CHEMISTRY,
            mechanism_id_by_notation={},
        )
        assert result.records == {}
        assert result.evidence["mode-1"]

    def test_the_window_is_the_mode_s(self):
        tight = {
            "mode-1": ModeChemistry(tolerance_ppm=1e-6, labelled=False, profile="")
        }
        anchors = ladder_batch(C9H12O5={"mz": at("C9H12O5") * (1 + 2e-6)})
        assert "up-0" in plan(anchors).records
        assert not plan(anchors, chemistry=tight).records

    def test_a_labelled_mode_proposes_no_alias_reading(self):
        sulfur = [
            anchor(f"src-{i}", tier="assigned", formula=f"C{9 + i}H{14 + 2 * i}O5S")
            for i in range(MIN_LINKS)
        ] + [
            anchor(
                f"up-{i}",
                mz=ion_mz(parse_formula_tokens(f"C{10 + i}H{16 + 2 * i}O5S"), NOTATION),
            )
            for i in range(MIN_LINKS)
        ]
        labelled = {"mode-1": ModeChemistry(3.0, labelled=True, profile="NO3_15N")}
        assert plan(sulfur).records
        assert not plan(sulfur, chemistry=labelled).records


class TestTheBatchsRate:
    def evidence(self, n, decoys, significant=True):
        return SeriesEvidence(
            "CH2",
            "organic",
            n,
            decoys,
            min_links=0 if significant else 10**6,
            min_enrichment=0.0,
        )

    def test_it_pools_the_committed_families(self):
        evidence = {
            "m1": [self.evidence(20, (1, 1)), self.evidence(10, (2, 2))],
            "m2": [self.evidence(10, (9, 9), significant=False)],
        }
        assert batch_fdr(evidence) == pytest.approx(3 / 30)

    def test_the_rate_is_at_most_one(self):
        assert batch_fdr({"m1": [self.evidence(2, (5, 5))]}) == 1.0

    def test_nothing_committed_has_no_rate(self):
        assert batch_fdr({"m1": [self.evidence(10, (9,), significant=False)]}) is None
        assert batch_fdr({}) is None


class TestTheIon:
    @pytest.mark.parametrize(
        "formula, notation, ion",
        [
            ("C9H12O5", "[M-H]-", "C9H11O5-"),
            ("C9H12O5", "[M+NO3]-", "C9H12NO8-"),
            ("C9H12O5", "[M+^NO3]-", "C9H12O8^N-"),
            ("C10H16O3", "+H+", "C10H17O3+"),
        ],
    )
    def test_the_ion_is_spelled_as_the_engine_spells_it(self, formula, notation, ion):
        assert ion_formula_of(formula, notation) == ion


class TestTheOutcome:
    def counts(self, **extra):
        counts = {
            "modes_walked": 1,
            "modes_skipped": 0,
            "families_committed": 3,
            "fdr": 0.036,
            "anchors_proposed": 40,
            "anchors_in_series": 31,
            "members_assigned": 120,
            "samples_failed": 0,
        }
        counts.update(extra)
        return counts

    def test_nothing_walked_says_why(self):
        outcome = series_outcome(self.counts(modes_walked=0), "b1")
        assert outcome["status"] == "partial"
        assert "no batch peak assigned to walk from" in outcome["message"]

    def test_no_mode_resolved_says_so(self):
        outcome = series_outcome(self.counts(modes_walked=0, modes_skipped=1), "b1")
        assert outcome["status"] == "partial"
        assert outcome["message"] == (
            "No series was walked. The chemistry of 1 ionization mode could not "
            "be resolved, so it was not walked and its batch peaks keep the "
            "series an earlier detection found."
        )

    def test_it_says_what_was_found_and_at_what_rate(self):
        outcome = series_outcome(self.counts(), "b1")
        assert outcome["status"] == "success"
        assert outcome["message"] == (
            "3 series families beat the decoys (false discovery rate on the "
            "decoys 3.6%): 40 batch peaks proposed, 31 now read as a member of a "
            "series, and 120 member peaks measured against its formula."
        )
        assert outcome["_notification_data"] == {"sample_batch_id": "b1"}

    def test_one_family_and_no_rate(self):
        outcome = series_outcome(
            self.counts(
                families_committed=1,
                fdr=None,
                anchors_proposed=1,
                members_assigned=1,
            ),
            "b1",
        )
        assert outcome["message"].startswith(
            "1 series family beat the decoys: 1 batch peak proposed"
        )
        assert "1 member peak measured" in outcome["message"]

    def test_a_sample_it_could_not_read_is_named(self):
        outcome = series_outcome(self.counts(samples_failed=2), "b1")
        assert outcome["status"] == "partial"
        assert outcome["message"].endswith(
            " 2 samples could not be read and were skipped."
        )

    def test_a_mode_it_could_not_resolve_makes_it_partial(self):
        outcome = series_outcome(self.counts(modes_skipped=2), "b1")
        assert outcome["status"] == "partial"
        assert outcome["message"].endswith(
            "against its formula. The chemistry of 2 ionization modes could not "
            "be resolved, so they were not walked and their batch peaks keep the "
            "series an earlier detection found."
        )


RECORD = {
    "formula": "C9H12O5",
    "ionization_mechanism_id": MECHANISM,
    "has_anchor": True,
    "unit": "CH2",
}


def ledger_row(**extra):
    row = {
        "role": "M0",
        "sample_peak_id": "p1",
        "assigned_formula": "C9H12O5",
        "ionization_mechanism_id": MECHANISM,
        "provenance": {"cross_channel": {"channels": ["[M-H]-"]}},
    }
    row.update(extra)
    return row


class TestARunReadsItsBatchsSeries:
    def test_a_row_of_the_proposed_reading_carries_it(self):
        rows = [ledger_row()]
        assert stamp_series(rows, {"p1": RECORD}) == 1
        assert rows[0]["provenance"][SERIES_KEY] == RECORD
        assert rows[0]["provenance"]["cross_channel"] == {"channels": ["[M-H]-"]}

    def test_the_record_is_a_copy(self):
        rows = [ledger_row()]
        stamp_series(rows, {"p1": RECORD})
        rows[0]["provenance"][SERIES_KEY]["unit"] = "O"
        assert RECORD["unit"] == "CH2"

    @pytest.mark.parametrize(
        "extra",
        [
            {"assigned_formula": "C5H10N4O4"},
            {"ionization_mechanism_id": "mech-other"},
            {"sample_peak_id": "p2"},
            {"role": "iso_child"},
        ],
    )
    def test_any_other_row_does_not(self, extra):
        rows = [ledger_row(**extra)]
        assert stamp_series(rows, {"p1": RECORD}) == 0
        assert SERIES_KEY not in rows[0]["provenance"]

    def test_a_row_with_no_provenance_gets_one(self):
        rows = [ledger_row(provenance=None)]
        stamp_series(rows, {"p1": RECORD})
        assert rows[0]["provenance"] == {SERIES_KEY: RECORD}


SERIES_READING = Annotation(
    formula="C9H12O5",
    ion_formula="C9H11O5-",
    ionization_mechanism_id=MECHANISM,
    role="M0",
    owner_batch_peak_id=None,
)
REGISTRY = SimpleNamespace(
    candidates=[
        {"formula": "C5H10N4O4", "ion_formula": "C5H9N4O4-"},
        {"formula": "C9H12O5", "ion_formula": "C9H11O5-"},
    ]
)


def member(candidate=None, fit=None, role="M0"):
    return SimpleNamespace(candidate=candidate, fit_score=fit, role=role_code(role))


class TestWhichMembersAProposalTakes:
    def test_a_member_with_no_reading_is_taken(self):
        assert _takes_the_member(
            member(role="unassigned"), REGISTRY, SERIES_READING, 0.1
        )
        # A fit left on a member that names no reading is no reading to keep.
        stale = member(role="unassigned", fit=0.99)
        assert _takes_the_member(stale, REGISTRY, SERIES_READING, 0.1)

    def test_a_weaker_reading_of_another_formula_is_taken(self):
        held = evidence_for(0.5, "C5H10N4O4")
        current = member(candidate=0, fit=0.5)
        assert _takes_the_member(current, REGISTRY, SERIES_READING, held)
        assert _takes_the_member(current, REGISTRY, SERIES_READING, held + 0.1)

    def test_a_better_measured_reading_is_kept(self):
        held = evidence_for(0.9, "C5H10N4O4")
        current = member(candidate=0, fit=0.9)
        assert not _takes_the_member(current, REGISTRY, SERIES_READING, held - 0.01)

    def test_the_same_formula_is_left_as_it_is(self):
        current = member(candidate=1, fit=0.1)
        assert not _takes_the_member(current, REGISTRY, SERIES_READING, 0.99)

    def test_a_reading_with_no_fit_of_its_own_is_taken(self):
        current = member(candidate=0, fit=None)
        assert _takes_the_member(current, REGISTRY, SERIES_READING, 0.2)

    @pytest.mark.parametrize("role", [ROLE_REAGENT, ROLE_ARTIFACT, ROLE_ISO_CHILD])
    def test_a_claimed_member_or_an_isotope_line_is_not_touched(self, role):
        assert not _takes_the_member(
            member(candidate=None, role=role), REGISTRY, SERIES_READING, 0.99
        )


class TestTheModesChemistry:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "instrument, notation, expected",
        [
            ("orbi", "[M+^NO3]-", (3.0, True, "NO3_15N")),
            ("tof", "[M+Br]-", (10.0, False, "BR")),
        ],
    )
    async def test_it_is_resolved_as_a_run_of_its_sample_resolves_it(
        self, monkeypatch, instrument, notation, expected
    ):
        from mascope_backend.api.new.peak_assignments import batch_series
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        async def sample(sample_item_id):
            assert sample_item_id == "s-1"
            return SimpleNamespace(filename="sample-file", polarity="-")

        async def mechanisms(found):
            return ["m-1"], [SimpleNamespace(ionization_mechanism=notation)]

        def instrument_type(filename):
            assert filename == "sample-file"
            return instrument

        monkeypatch.setattr(batch_series, "fetch_sample", sample)
        monkeypatch.setattr(batch_series, "fetch_sample_mechanisms", mechanisms)
        monkeypatch.setattr(batch_series, "get_instrument_type", instrument_type)
        chemistry = await batch_series._mode_chemistry("s-1", PeakAssignmentConfig())
        assert (chemistry.tolerance_ppm, chemistry.labelled, chemistry.profile) == (
            expected
        )

    @pytest.mark.asyncio
    async def test_the_run_s_window_overrides_the_profile_s(self, monkeypatch):
        from mascope_backend.api.new.peak_assignments import batch_series
        from mascope_backend.api.new.peak_assignments.config import (
            PeakAssignmentConfig,
        )

        async def sample(sample_item_id):
            return SimpleNamespace(filename="sample-file", polarity="-")

        async def mechanisms(found):
            return ["m-1"], [SimpleNamespace(ionization_mechanism="[M+Br]-")]

        monkeypatch.setattr(batch_series, "fetch_sample", sample)
        monkeypatch.setattr(batch_series, "fetch_sample_mechanisms", mechanisms)
        monkeypatch.setattr(batch_series, "get_instrument_type", lambda name: "orbi")
        chemistry = await batch_series._mode_chemistry(
            "s-1", PeakAssignmentConfig(mz_precision_ppm=1.5)
        )
        assert chemistry.tolerance_ppm == 1.5
