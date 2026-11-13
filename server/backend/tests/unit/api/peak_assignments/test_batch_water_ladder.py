"""Unit tests for the batch's water ladders, their pure parts (step 4.2j).

No DB: which batch peaks sit at a passing rung and in which segments, what
each sample's member becomes and why, how a heavier track's line follows its
rung's monoisotopic peak, how two rungs at one batch peak are settled, what
the records, the run's summary and its outcome say, how the consensus keeps a
record, what the ledger and a snapshot serve, and how a sample's row is
rewritten as the rung. The ladder's rules are the library's
(``libraries/tools/tests/test_water_ladder.py``). See
``batch_water_ladder.py``.

The batch: one TOF partition of a bromide source, 24 spectra in two segments
three hours apart, dry (``d``) and humid (``h``). The bromide's two tracks
climb to n = 3 in the dry segment and to n = 7 in the humid one.
"""

from dataclasses import replace
from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_water_ladder
from mascope_backend.api.new.peak_assignments.batch_known_species import (
    ALREADY,
    KEPT_CARRIES_LINES,
    KEPT_CLAIMED_OTHERWISE,
    KEPT_HAND_ASSIGNED,
    KEPT_LINE_OF_ANOTHER,
    KEPT_TARGET_LIBRARY,
    Member,
    Row,
)
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    DEFAULT_DRIFT_MARGIN_PPM,
    WATER_LADDER_KEY,
    compute_consensus,
    reading_of,
    standing_water_ladder_record,
    water_ladder_record_of,
    water_ladder_summary,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    ACTION_WATER_LADDERS,
    ACTIONS,
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_backend.api.new.peak_assignments.batch_water_ladder import (
    HELD_CURATED,
    KEPT_LISTED,
    KEPT_NO_MONOISOTOPIC,
    WITH_MONOISOTOPIC,
    Claim,
    Written,
    _chunks,
    _monoisotopic_samples,
    claimed_samples,
    explained,
    ladder_record,
    lines_of,
    measuring_outcome,
    plan_batch,
    plan_partition,
    reads_rung,
    row_record,
    rungs_own_line,
    sample_decision,
    strength,
    summarise,
    unexplained_share,
)
from mascope_backend.api.new.peak_assignments.engine import DISPLACED_BY_CLAIM
from mascope_backend.api.new.peak_assignments.reagent_pass import (
    REASON_BATCH_WATER_LADDER,
    water_rung_row,
)
from mascope_tools.composition.reagents import water_ladders
from mascope_tools.composition.water_ladder import WINDOW_DRIFT_MARGIN_PPM


BROMIDE = water_ladders("BR")[0]
M0, BR81 = BROMIDE.tracks
PARTITION = ("mode-br", "tof")
DRY = [f"d{i:02d}" for i in range(12)]
HUMID = [f"h{i:02d}" for i in range(12)]
#: Acquisition times: ten minutes apart, three hours between the segments.
TIMES = {
    **{sample: 600.0 * i for i, sample in enumerate(DRY)},
    **{sample: 600.0 * 11 + 3 * 3600.0 + 600.0 * i for i, sample in enumerate(HUMID)},
}
TOF_TOL = 82.0
MECH = "mech-br"


def ion(waters: int) -> str:
    return BROMIDE.rung(waters).formula + "-"


def anchor(key, mz, *, formula=None, ion_formula=None, role=None, **extra):
    fields = dict(
        batch_peak_id=key,
        mz=mz,
        mz_tol_ppm=TOF_TOL,
        ionization_mode_id=PARTITION[0],
        intensity_variable="sum_peak_areas",
        consensus_formula=formula,
        consensus_ion_formula=ion_formula,
        ionization_mechanism_id=MECH if formula else None,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=role,
        isotopologue_of=None,
        max_intensity=1e6,
        provenance=None,
        candidates=[],
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def member(sample, mz, intensity, **fields):
    defaults = dict(
        sample_item_id=sample,
        sample_peak_id=f"p-{sample}-{mz:.3f}",
        intensity=intensity,
        mz=mz,
        role="unassigned",
        peak_assignment_id=f"pa-{sample}-{mz:.3f}",
    )
    defaults.update(fields)
    return Member(**defaults)


def reagent(sample, mz, intensity, ion_formula, **fields):
    return member(
        sample,
        mz,
        intensity,
        role="reagent",
        ion_formula=ion_formula,
        tier="unassigned",
        **fields,
    )


def analyte(sample, mz, intensity, formula, **fields):
    defaults = dict(
        role="M0",
        formula=formula,
        ion_formula=formula + "Br-",
        ionization_mechanism_id=MECH,
        tier="assigned",
        fit_score=0.8,
    )
    defaults.update(fields)
    return member(sample, mz, intensity, **defaults)


#: The formulas the stages read the rungs as.
F3, F5, F5B, F6 = "C2H8O2", "C4H4O", "C5H2", "C3H10O3"


def _batch():
    """The anchors and their members, keyed by a short name."""
    anchors, members = {}, {}

    def put(key, waters, track, by_sample, **extra):
        mz = BROMIDE.line_mz(waters, track)
        anchors[key] = anchor(key, mz, **extra)
        members[key] = {s: make(s, mz) for s, make in by_sample.items()}

    every = DRY + HUMID
    put(
        "core",
        0,
        M0,
        {s: lambda s, mz: reagent(s, mz, 1e6, "Br-") for s in every},
        role="reagent",
        ion_formula="Br-",
    )
    put(
        "core81",
        0,
        BR81,
        {
            s: lambda s, mz: reagent(
                s, mz, 9.7e5, "Br-", owner_batch_peak_id="core", isotope_label="81Br"
            )
            for s in every
        },
        role="reagent",
        ion_formula="Br-",
    )
    for n in (1, 2):
        # The library's own hydrates, which a file's pre-pass claims.
        put(
            f"r{n}",
            n,
            M0,
            {
                s: (lambda n: lambda s, mz: reagent(s, mz, 1e5 / n, ion(n)))(n)
                for s in every
            },
            role="reagent",
            ion_formula=ion(n),
        )
        put(
            f"r{n}b",
            n,
            BR81,
            {
                s: (
                    lambda n: (
                        lambda s, mz: reagent(
                            s,
                            mz,
                            1e5 / n,
                            ion(n),
                            owner_batch_peak_id=f"r{n}",
                            isotope_label="81Br",
                        )
                    )
                )(n)
                for s in every
            },
            role="reagent",
            ion_formula=ion(n),
        )
    # n = 3, in every spectrum: an organic to the stages, its 81Br line its own.
    put(
        "r3",
        3,
        M0,
        {s: lambda s, mz: analyte(s, mz, 3e4, F3) for s in every},
        formula=F3,
        ion_formula=F3 + "Br-",
    )
    put(
        "r3b",
        3,
        BR81,
        {
            s: lambda s, mz: analyte(
                s,
                mz,
                3e4,
                F3,
                role="iso_child",
                owner_batch_peak_id="r3",
                isotope_label="81Br",
            )
            for s in every
        },
    )
    # n = 4: the humid segment only, nothing read; the dry ladder ends at 3.
    put("r4", 4, M0, {s: lambda s, mz: member(s, mz, 2e4) for s in HUMID})
    put("r4b", 4, BR81, {s: lambda s, mz: member(s, mz, 2e4) for s in HUMID})
    # n = 5: in every spectrum, an organic in the dry segment, where the ladder
    # does not reach it, and nothing in the humid one; its 81Br line an organic
    # of its own in the humid segment.
    put(
        "r5",
        5,
        M0,
        {
            **{s: lambda s, mz: analyte(s, mz, 1.5e4, F5) for s in DRY},
            **{s: lambda s, mz: member(s, mz, 1.5e4) for s in HUMID},
        },
        formula=F5,
        ion_formula=F5 + "Br-",
    )
    put(
        "r5b",
        5,
        BR81,
        {s: lambda s, mz: analyte(s, mz, 1.4e4, F5B) for s in HUMID},
        formula=F5B,
        ion_formula=F5B + "Br-",
    )
    # n = 6: an organic in the humid segment, its 81Br line its isotopologue.
    hand, target, listed, lined = HUMID[:4]
    put(
        "r6",
        6,
        M0,
        {
            s: (
                lambda s: (
                    lambda _, mz: analyte(
                        s, mz, 1.2e4, F6, hand=s == hand, target=s == target
                    )
                )
            )(s)
            for s in HUMID
        },
        formula=F6,
        ion_formula=F6 + "Br-",
        candidates=[
            {
                "formula": F6,
                "ion_formula": F6 + "Br-",
                "ionization_mechanism_id": MECH,
                "listing": {"name": "a listed compound", "tags": []},
            }
        ],
    )
    members["r6"][listed] = replace(members["r6"][listed], formula=F6)
    put(
        "r6b",
        6,
        BR81,
        {
            s: lambda s, mz: analyte(
                s,
                mz,
                1.1e4,
                F6,
                role="iso_child",
                owner_batch_peak_id="r6",
                isotope_label="81Br",
            )
            for s in HUMID
        },
    )
    # The listed reading's sample alone names the list; the others read F6 as
    # the stages found it.
    for s in HUMID:
        if s != listed:
            members["r6"][s] = replace(
                members["r6"][s], ionization_mechanism_id="mech-other"
            )
    # A 13C line under the rung in one sample: carbon a cluster cannot have.
    c13 = BROMIDE.line_mz(6, M0) + 1.00336
    anchors["r6c13"] = anchor("r6c13", c13)
    members["r6c13"] = {
        lined: analyte(
            lined,
            c13,
            4e2,
            F6,
            role="iso_child",
            owner_batch_peak_id="r6",
            isotope_label="13C",
        )
    }
    # n = 7: curated by hand; its 81Br line read as its line in half the humid
    # samples, on its own in the rest.
    put(
        "r7",
        7,
        M0,
        {s: lambda s, mz: member(s, mz, 1e4) for s in HUMID},
        provenance={"manual": {"formula": "C9H8"}},
    )
    put(
        "r7b",
        7,
        BR81,
        {
            **{
                s: lambda s, mz: member(
                    s, mz, 1e4, role="iso_child", owner_batch_peak_id="r7"
                )
                for s in HUMID[:6]
            },
            **{s: lambda s, mz: member(s, mz, 1e4) for s in HUMID[6:]},
        },
    )
    return anchors, members


@pytest.fixture
def plan(monkeypatch):
    anchors, members = _batch()
    return plan_batch(anchors.values(), members, {PARTITION: "BR"}, TIMES), anchors


def _claims(plan):
    return {claim.batch_peak_id: claim for claim in plan.claims}


def _lines(members):
    return lines_of(
        (m.sample_item_id, m.owner_batch_peak_id, m.isotope_label, m.mz, TOF_TOL)
        for by_sample in members.values()
        for m in by_sample.values()
    )


# --- the plan --------------------------------------------------------------------


class TestThePlan:
    def test_the_partition_is_cut_into_its_two_segments(self, plan):
        plan, _ = plan
        (part,) = plan.partitions
        assert part.profile == "BR"
        assert part.spectra == 24
        assert [segment.spectra for segment in part.segments] == [
            tuple(DRY),
            tuple(HUMID),
        ]

    def test_each_track_climbs_as_far_as_its_segment_shows_it(self, plan):
        plan, _ = plan
        (part,) = plan.partitions
        reach = {
            (r.ladder.core.label, r.track.label, r.segment.index): r.reach
            for r in part.readings
        }
        assert reach[("[Br]-", "M0", 0)] == 3
        assert reach[("[Br]-", "81Br", 0)] == 3
        assert reach[("[Br]-", "M0", 1)] == 7
        assert reach[("[Br]-", "81Br", 1)] == 7
        # The other cores show nowhere: nothing is climbed on them.
        assert reach[("[Br3]-", "M0", 1)] == 0
        passed = {
            (line.track.label, line.waters): line.passed_in for line in part.lines
        }
        assert passed[("M0", 3)] == [0, 1]
        assert passed[("M0", 5)] == [1]
        assert part.rungs_passed == 14
        # Tested: each track's seven rungs and the eighth that ended it.
        assert part.rungs_tested == 16

    def test_a_rung_is_claimed_only_in_the_segments_it_passed_in(self, plan):
        plan, anchors = plan
        claims = _claims(plan)
        assert set(claims["r3"].samples) == set(DRY + HUMID)
        assert all(d is None for d in claims["r3"].samples.values())
        # n = 5 passes in the humid segment only: the dry samples' organic is
        # another line at that mass, and keeps its reading.
        assert set(claims["r5"].samples) == set(HUMID)
        assert claims["r5"].segment_of == {s: 1 for s in HUMID}
        assert all(d is None for d in claims["r5"].samples.values())

    def test_a_rung_a_files_pre_pass_claimed_reads_already(self, plan):
        plan, _ = plan
        claims = _claims(plan)
        assert set(claims["r1"].samples.values()) == {ALREADY}
        assert set(claims["r2b"].samples.values()) == {ALREADY}
        assert not claims["r1"].claims
        # The core is no rung: nothing claims it.
        assert "core" not in claims and "core81" not in claims

    def test_the_holds_keep_their_samples(self, plan):
        plan, _ = plan
        r6 = _claims(plan)["r6"].samples
        hand, target, listed, lined = HUMID[:4]
        assert r6[hand] == KEPT_HAND_ASSIGNED
        assert r6[target] == KEPT_TARGET_LIBRARY
        assert r6[listed] == KEPT_LISTED
        assert r6[lined] == KEPT_CARRIES_LINES
        assert all(r6[s] is None for s in HUMID[4:])

    def test_a_heavier_line_moves_with_its_rungs_monoisotopic_peak(self, plan):
        plan, _ = plan
        r6b = _claims(plan)["r6b"]
        assert r6b.monoisotopic == ("r6",)
        # Where the rung's peak is rewritten its line goes with it; where the
        # peak is kept, the line stays the analyte's.
        assert all(r6b.samples[s] == WITH_MONOISOTOPIC for s in HUMID[4:])
        assert all(r6b.samples[s] == KEPT_LINE_OF_ANOTHER for s in HUMID[:4])
        assert not r6b.claims

    def test_a_heavier_line_read_on_its_own_is_the_rungs_line(self, plan):
        plan, _ = plan
        r5b = _claims(plan)["r5b"]
        assert r5b.monoisotopic == ("r5",)
        assert all(d is None for d in r5b.samples.values())
        assert r5b.line.track.label == "81Br"
        assert r5b.line.label == "[Br+5xH2O]- (81Br)"

    def test_a_curated_batch_peak_is_held(self, plan):
        plan, _ = plan
        r7 = _claims(plan)["r7"]
        assert r7.held == HELD_CURATED
        assert not r7.claims

    def test_a_curated_rungs_peak_owns_no_heavier_line(self, plan):
        """Its members stay as a person left them, so its line stays where it
        was, and a heavier peak read on its own has no rung's peak to be the
        line of."""
        plan, _ = plan
        r7b = _claims(plan)["r7b"]
        assert all(r7b.samples[s] == KEPT_LINE_OF_ANOTHER for s in HUMID[:6])
        assert all(r7b.samples[s] == KEPT_NO_MONOISOTOPIC for s in HUMID[6:])
        assert not r7b.claims

    def test_monoisotopic_tracks_come_first(self, plan):
        plan, _ = plan
        order = [c.batch_peak_id for c in plan.claims]
        assert order.index("r6") < order.index("r6b")
        assert order.index("r5") < order.index("r5b")
        assert order.index("r3") < order.index("r5")

    def test_a_partition_with_no_declared_core_reads_nothing(self):
        anchors, members = _batch()
        plan = plan_batch(anchors.values(), members, {PARTITION: "UR"}, TIMES)
        (part,) = plan.partitions
        assert part.ladders == () and part.claims == [] and part.segments == []
        plan = plan_batch(anchors.values(), members, {}, TIMES)
        assert plan.partitions[0].profile is None
        assert plan.partitions[0].claims == []

    def test_two_rungs_at_one_batch_peak_the_one_present_more_often_claims_it(
        self, monkeypatch
    ):
        twin = replace(BROMIDE, core=replace(BROMIDE.core, label="[Br]- (twin)"))
        monkeypatch.setattr(
            batch_water_ladder, "water_ladders", lambda profile: (twin, BROMIDE)
        )
        anchors, members = _batch()
        plan = plan_batch(anchors.values(), members, {PARTITION: "BR"}, TIMES)
        r3 = _claims(plan)["r3"]
        # Equally present: the profile's first core claims it, the other is named.
        assert r3.line.ladder.core.label == "[Br]- (twin)"
        assert r3.also_reads_as == ["[Br+3xH2O]-"]


class TestStrength:
    def test_the_line_present_more_often_claims_a_batch_peak(self):
        from mascope_tools.composition.water_ladder import RungReading

        def line(core, presence, waters=3):
            found = batch_water_ladder.Line(core, M0, waters)
            found.readings[0] = RungReading(
                waters, 1.0, ("k",), presence, 0.02, 12, None, "passed"
            )
            return found

        trimer = water_ladders("BR")[2]
        order = {"[Br]-": 0, "[Br3]-": 2}
        seldom, often = line(BROMIDE, 0.6), line(trimer, 0.9)
        assert sorted([seldom, often], key=lambda x: strength(x, order))[0] is often
        # Equally present, the profile's first core, then the fewer waters.
        tie = line(trimer, 0.6)
        assert sorted([tie, seldom], key=lambda x: strength(x, order))[0] is seldom
        low = line(BROMIDE, 0.6, waters=2)
        assert sorted([seldom, low], key=lambda x: strength(x, order))[0] is low

    def test_a_heavier_line_follows_only_its_own_rungs_monoisotopic_claim(self, plan):
        plan, _ = plan
        claims = _claims(plan)
        r6b = claims["r6b"]
        assert _monoisotopic_samples(r6b, claims) == {s: None for s in HUMID[4:]}
        # The batch peak at the monoisotopic line claimed for another rung.
        claims = {**claims, "r6": replace_line(claims["r6"], claims["r5"].line)}
        assert _monoisotopic_samples(r6b, claims) == {}
        assert _monoisotopic_samples(claims["r6"], claims) is None

    def test_a_rungs_peak_that_read_it_already_is_its_lines_owner(self, plan):
        """As its own ion: a member read as another peak's line is not."""
        plan, _ = plan
        claims = _claims(plan)
        r6 = claims["r6"]
        samples = {**r6.samples, "h05": ALREADY, "h06": ALREADY}
        members = {
            **r6.row.members,
            "h06": replace(r6.row.members["h06"], owner_batch_peak_id="elsewhere"),
        }
        mono = Claim(Row(r6.row.anchor, members), r6.line, samples=samples)
        found = _monoisotopic_samples(claims["r6b"], {**claims, "r6": mono})
        assert found["h05"] == ALREADY
        assert "h06" not in found
        assert found["h07"] is None


def replace_line(claim, line):
    return Claim(
        claim.row, line, samples=dict(claim.samples), segment_of=claim.segment_of
    )


class TestSampleDecision:
    def _claim(self):
        anchors, members = _batch()
        plan = plan_batch(anchors.values(), members, {PARTITION: "BR"}, TIMES)
        return _claims(plan), members

    def test_a_source_claim_of_another_ion_is_kept(self):
        claims, members = self._claim()
        other = reagent("h05", BROMIDE.line_mz(5, M0), 1.0, "NO3-")
        assert (
            sample_decision(other, claims["r5"], _lines(members))
            == KEPT_CLAIMED_OTHERWISE
        )

    def test_a_line_of_another_ion_is_kept(self):
        claims, members = self._claim()
        line = member(
            "h05",
            BROMIDE.line_mz(5, M0),
            1.0,
            role="iso_child",
            owner_batch_peak_id="elsewhere",
        )
        assert sample_decision(line, claims["r5"], {}) == KEPT_LINE_OF_ANOTHER

    def test_a_heavier_line_owned_by_its_rung_is_kept_where_the_rung_is_not_claimed(
        self,
    ):
        claims, members = self._claim()
        line = members["r6b"]["h05"]
        assert (
            sample_decision(line, claims["r6b"], {}, {"h05": None}) == WITH_MONOISOTOPIC
        )
        assert sample_decision(line, claims["r6b"], {}, {}) == KEPT_LINE_OF_ANOTHER
        # Where the rung's peak read the rung already, its line read so too, or
        # is another reading's line the claim does not move.
        assert (
            sample_decision(line, claims["r6b"], {}, {"h05": ALREADY})
            == KEPT_LINE_OF_ANOTHER
        )
        # A monoisotopic track's member owned by another peak is that peak's line.
        assert (
            sample_decision(
                replace(members["r5"]["h05"], owner_batch_peak_id="r6"),
                claims["r5"],
                {},
                {"h05": None},
            )
            == KEPT_LINE_OF_ANOTHER
        )

    def test_a_heavier_line_read_on_its_own_is_claimed_only_under_its_rungs_peak(
        self,
    ):
        """The ledger says a line by its owner alone: a heavier line claimed
        where the rung's peak is not the rung would read as the rung's ion."""
        claims, members = self._claim()
        r5b = members["r5b"]["h05"]
        assert sample_decision(r5b, claims["r5b"], {}, {"h05": None}) is None
        assert sample_decision(r5b, claims["r5b"], {}, {"h05": ALREADY}) is None
        assert (
            sample_decision(r5b, claims["r5b"], {}, {"h04": None})
            == KEPT_NO_MONOISOTOPIC
        )
        # A monoisotopic track's peak needs no other.
        assert sample_decision(members["r5"]["h05"], claims["r5"], {}, None) is None

    def test_a_line_the_rung_can_have_does_not_keep_it(self):
        claims, members = self._claim()
        r6 = members["r6"]["h05"]
        far = 999.0
        lines = {("h05", "r6"): (("81Br", far, TOF_TOL),)}
        assert sample_decision(r6, claims["r6"], lines) is None
        lines = {("h05", "r6"): (("81Br", far, TOF_TOL), ("18O", far, TOF_TOL))}
        assert sample_decision(r6, claims["r6"], lines) is None
        lines = {("h05", "r6"): (("15N", far, TOF_TOL),)}
        assert sample_decision(r6, claims["r6"], lines) == KEPT_CARRIES_LINES

    def test_an_unlabelled_line_is_the_rungs_where_it_sits_on_its_envelope(self):
        claims, members = self._claim()
        r6 = members["r6"]["h05"]
        line = claims["r6"].line
        at_81br = BROMIDE.line_mz(6, BR81)
        lines = {("h05", "r6"): ((None, at_81br * (1 + 50e-6), TOF_TOL),)}
        assert rungs_own_line(line, None, at_81br * (1 + 50e-6), TOF_TOL)
        assert sample_decision(r6, claims["r6"], lines) is None
        # One mass unit up from the rung, where its 17O and 2H sit too, at the
        # 13C spacing: within a TOF's window of its own M+1.
        assert rungs_own_line(line, None, BROMIDE.line_mz(6, M0) + 1.00336, TOF_TOL)
        # Between the rung's lines it is another ion's.
        between = BROMIDE.line_mz(6, M0) + 0.5
        lines = {("h05", "r6"): ((None, between, TOF_TOL),)}
        assert not rungs_own_line(line, None, between, TOF_TOL)
        assert sample_decision(r6, claims["r6"], lines) == KEPT_CARRIES_LINES
        # At an Orbitrap's window the 13C spacing is not the rung's own M+1.
        assert not rungs_own_line(line, None, BROMIDE.line_mz(6, M0) + 1.00336, 3.0)

    def test_reading_the_rung_is_a_claim_of_its_ion(self):
        claims, members = self._claim()
        line = claims["r5"].line
        assert reads_rung(reagent("x", 1.0, 1.0, ion(5)), line)
        assert reads_rung(
            reagent("x", 1.0, 1.0, ion(5), owner_batch_peak_id="r5"), line
        )
        assert not reads_rung(reagent("x", 1.0, 1.0, ion(4)), line)
        assert not reads_rung(analyte("x", 1.0, 1.0, F5, ion_formula=ion(5)), line)
        assert not reads_rung(
            member("x", 1.0, 1.0, role="M0", ion_formula=ion(5)), line
        )
        assert not reads_rung(reagent("x", 1.0, 1.0, ion(5), formula="H10O5"), line)


# --- the unexplained signal ------------------------------------------------------


class TestUnexplained:
    def test_a_committed_reading_a_source_role_and_a_line_explain_a_peak(self):
        assert explained(anchor("a", 1.0, formula="C", ion_formula="CBr-"))
        assert explained(anchor("a", 1.0, formula="C", consensus_tier="candidate"))
        assert not explained(
            anchor("a", 1.0, formula="C", consensus_tier="below_assignability")
        )
        assert explained(anchor("a", 1.0, role="reagent"))
        assert explained(anchor("a", 1.0, role="artifact"))
        assert explained(anchor("a", 1.0, isotopologue_of="b"))
        assert not explained(anchor("a", 1.0))

    def test_the_share_is_of_the_partitions_summed_intensity(self):
        anchors = [
            anchor("a", 1.0, role="reagent"),
            anchor("b", 2.0),
            anchor("c", 3.0, formula="C"),
        ]
        assert unexplained_share(anchors, {"a": 6.0, "b": 3.0, "c": 1.0}) == 0.3
        assert unexplained_share(anchors, {}) is None

    def test_the_plan_reads_the_share_before(self, plan):
        plan, _ = plan
        before = plan.partitions[0].unexplained_before
        assert 0.0 < before < 0.2


# --- the records ------------------------------------------------------------------


READS_REAGENT = {
    "role": "reagent",
    "formula": None,
    "ion_formula": ion(3),
    "ionization_mechanism_id": None,
}


def _record(plan, key="r3", reads=READS_REAGENT, **extra):
    claim = _claims(plan)[key]
    part = plan.partitions[0]
    return ladder_record(
        claim,
        part.segments,
        before={
            "role": None,
            "formula": F3,
            "ion_formula": F3 + "Br-",
            "tier": "assigned",
        },
        reads=reads,
        batch_peak_run_id="run-1",
        displaced_members={"d00": {"role": 1}},
        rows_rewritten=3,
        lines_rewritten=2,
        rows_kept={"no_row": 1},
        claimed=["h00", "d00"],
        **extra,
    )


class TestRecords:
    def test_the_record_names_the_rung_its_segments_and_what_was_written(self, plan):
        plan, _ = plan
        record = _record(plan)
        assert record["rung"]["core"] == "[Br]-"
        assert record["rung"]["ion"] == "[Br+3xH2O]-"
        assert record["rung"]["ion_formula"] == ion(3)
        assert record["rung"]["waters"] == 3
        assert record["rung"]["track"] == "M0"
        assert record["rung"]["rule_references"] == ["good70", "sun88"]
        assert record["rung"]["observed"].startswith("no work found")
        assert record["passed_in"] == [0, 1]
        assert [s["segment"] for s in record["segments"]] == [0, 1]
        assert record["partition_segments"] == 2
        assert record["segments"][0]["spectra"] == 12
        assert record["segments"][0]["verdict"] == "passed"
        assert record["samples"] == 24
        assert record["claimed"] is True
        assert record["members_rewritten"] == 1
        assert (record["rows_rewritten"], record["lines_rewritten"]) == (3, 2)
        assert record["rows_kept"] == {"no_row": 1}
        assert record["claimed_samples"] == ["d00", "h00"]
        assert record["turned"] is True
        assert record["moved_analyte"] is True
        assert record["batch_peak_run_id"] == "run-1"

    def test_a_rung_its_ladder_reached_in_one_segment_counts_them_all(self, plan):
        plan, _ = plan
        record = _record(plan, "r5")
        assert [s["segment"] for s in record["segments"]] == [1]
        assert record["partition_segments"] == 2

    def test_the_claimed_samples_carry_an_earlier_measurements_claim(self, plan):
        """A second measurement finds the first one's claims reading the rung
        already: they stay the ladder's, where they read it still."""
        plan, _ = plan
        claims = _claims(plan)
        assert claimed_samples(claims["r3"], ["h01", "h00"], None) == ["h00", "h01"]
        earlier = {
            "rung": {"ion_formula": ion(1)},
            "reads": {},
            "claimed_samples": ["d00", "h03", "gone"],
        }
        # The members of r1 read the rung; a sample no longer at it is gone.
        assert claimed_samples(claims["r1"], [], earlier) == ["d00", "h03"]
        # A member read otherwise since is no claim of the ladder's.
        assert claimed_samples(claims["r3"], ["h00"], earlier) == ["h00"]
        on_r3 = {**earlier, "rung": {"ion_formula": ion(3)}}
        assert claimed_samples(claims["r3"], [], on_r3) == []
        # An earlier record of another rung carries nothing.
        on_r2 = {**earlier, "rung": {"ion_formula": ion(2)}}
        assert claimed_samples(claims["r1"], [], on_r2) == []

    def test_a_record_lists_the_kept_samples_with_their_readings(self, plan):
        plan, _ = plan
        record = _record(plan, "r6", reads={**READS_REAGENT, "ion_formula": ion(6)})
        assert record["files_by_reason"] == {
            KEPT_CARRIES_LINES: 1,
            KEPT_HAND_ASSIGNED: 1,
            KEPT_LISTED: 1,
            KEPT_TARGET_LIBRARY: 1,
        }
        assert {entry["sample_item_id"] for entry in record["files_recorded"]} == set(
            HUMID[:4]
        )
        assert record["files_recorded"][0]["formula"] == F6

    def test_a_reading_that_stayed_an_analyte_did_not_turn(self, plan):
        plan, _ = plan
        record = _record(
            plan,
            "r5",
            reads={
                "role": None,
                "formula": F5,
                "ion_formula": F5 + "Br-",
                "ionization_mechanism_id": MECH,
            },
        )
        assert record["turned"] is False
        assert record["moved_analyte"] is False

    def test_an_unexplained_peak_that_turned_moved_no_analyte(self, plan):
        plan, _ = plan
        claim = _claims(plan)["r4"]
        record = ladder_record(
            claim,
            plan.partitions[0].segments,
            before={
                "role": None,
                "formula": None,
                "ion_formula": None,
                "tier": "unassigned",
            },
            reads={**READS_REAGENT, "ion_formula": ion(4)},
            batch_peak_run_id=None,
        )
        assert record["turned"] is True
        assert record["moved_analyte"] is False
        assert record["claimed"] is False

    def test_the_consensus_keeps_the_record_while_it_reads_the_rung(self, plan):
        plan, _ = plan
        record = _record(plan)
        claimed = [
            {
                "role": "reagent",
                "ion_formula": ion(3),
                "tier": "unassigned",
                "intensity": 1.0,
            }
            for _ in range(3)
        ]
        consensus = compute_consensus(claimed, "r3", water_ladder=record)
        assert consensus.provenance[WATER_LADDER_KEY] == record
        moved = [
            {
                "assigned_formula": F3,
                "ion_formula": F3 + "Br-",
                "ionization_mechanism_id": MECH,
                "tier": "assigned",
                "fit_score": 0.9,
                "intensity": 1.0,
            }
            for _ in range(3)
        ]
        assert (
            WATER_LADDER_KEY
            not in compute_consensus(moved, "r3", water_ladder=record).provenance
        )

    def test_only_a_record_with_a_rung_and_a_reading_is_one(self):
        assert water_ladder_record_of(anchor("a", 1.0)) is None
        assert (
            water_ladder_record_of(anchor("a", 1.0, provenance={WATER_LADDER_KEY: {}}))
            is None
        )
        assert (
            water_ladder_record_of(
                anchor("a", 1.0, provenance={WATER_LADDER_KEY: {"rung": {}}})
            )
            is None
        )
        assert (
            water_ladder_record_of(
                anchor("a", 1.0, provenance={WATER_LADDER_KEY: {"reads": {}}})
            )
            is None
        )

    def test_the_ledger_and_a_snapshot_serve_it_without_the_member_states(self, plan):
        plan, _ = plan
        record = _record(plan)
        bp = anchor(
            "r3",
            BROMIDE.line_mz(3, M0),
            role="reagent",
            ion_formula=ion(3),
            provenance={WATER_LADDER_KEY: record},
        )
        assert reading_of(bp) == READS_REAGENT
        bp.engine_tier = bp.engine_claim = None
        bp.continuity_grade = bp.continuity = None
        bp.sample_batch_id = "b"
        bp.best_fit_score = None
        bp.support_fraction = None
        bp.n_present = 24
        bp.is_ambiguous = 0
        served = _batch_peak_meta(bp)["water_ladder"]
        assert served["rung"]["ion"] == "[Br+3xH2O]-"
        assert "displaced_members" not in served
        assert "claimed_samples" not in served
        assert water_ladder_record_of(bp)["displaced_members"]
        assert water_ladder_record_of(bp)["claimed_samples"] == ["d00", "h00"]
        snapshot = SimpleNamespace(**anchor_snapshot_row("run-1", bp, []))
        assert snapshot_anchor_meta(snapshot, "b")["water_ladder"] == served

    def test_a_record_the_anchor_moved_off_is_not_served(self, plan):
        plan, _ = plan
        bp = anchor(
            "r3",
            1.0,
            formula=F3,
            ion_formula=F3 + "Br-",
            provenance={WATER_LADDER_KEY: _record(plan)},
        )
        assert standing_water_ladder_record(bp) is None
        assert water_ladder_summary(bp) is None

    def test_a_rows_record_names_its_segment_and_the_rungs_numbers_there(self, plan):
        plan, _ = plan
        claim = _claims(plan)["r5"]
        record = row_record(claim, "h03", "run-1")
        assert record["batch_peak_id"] == "r5"
        assert (record["core"], record["waters"], record["track"]) == ("[Br]-", 5, "M0")
        assert record["segment"] == 1
        assert record["presence"] == 1.0
        assert record["decoy_presence"] == 0.02
        assert record["references"] == ["good70", "sun88"]
        assert record["batch_peak_run_id"] == "run-1"

    def test_the_action_is_a_batch_run(self):
        assert ACTION_WATER_LADDERS == "measure_water_ladders"
        assert ACTION_WATER_LADDERS in ACTIONS

    def test_the_window_margin_is_the_folds(self):
        assert WINDOW_DRIFT_MARGIN_PPM == DEFAULT_DRIFT_MARGIN_PPM


# --- the summary ------------------------------------------------------------------


class TestSummary:
    def test_the_summary_counts_per_partition_and_over_the_batch(self, plan):
        plan, _ = plan
        claims = _claims(plan)
        written = [
            Written(
                claims["r3"],
                True,
                samples=tuple(DRY + HUMID),
                members_rewritten=24,
                rows_rewritten=2,
                lines_rewritten=24,
                rows_kept={"no_row": 22},
                turned=True,
                moved=True,
            ),
            Written(
                claims["r4"],
                True,
                samples=tuple(HUMID),
                members_rewritten=12,
                turned=True,
            ),
            Written(
                claims["r5"],
                True,
                samples=tuple(HUMID),
                members_rewritten=12,
                rows_rewritten=1,
            ),
            Written(claims["r7"], False),
        ]
        summary = summarise(plan, written, {PARTITION: 0.01}, cleared=2)
        (part,) = summary["partitions"]
        assert part["profile"] == "BR"
        assert part["cores"] == ["[Br]-", "[Br2]-", "[Br3]-", "[Br+HBr]-"]
        assert [s["spectra"] for s in part["segments"]] == [12, 12]
        assert part["rungs_passed"] == 14 and part["rungs_tested"] == 16
        assert part["claimed"] == 3
        assert part["held"] == 1
        # 48 sample peaks, in the 24 samples.
        assert part["members_rewritten"] == 48
        assert part["samples_claimed"] == 24
        assert part["rows_rewritten"] == 3
        assert part["lines_rewritten"] == 24
        assert part["peaks_turned"] == 2
        assert part["rows_moved"] == 1
        assert part["unexplained_after"] == 0.01
        assert part["unexplained_before"] > 0
        # Only the cores the segments show are listed with their reach.
        assert {(r["core"], r["segment"]) for r in part["ladders"]} == {
            ("[Br]-", 0),
            ("[Br]-", 1),
        }
        assert summary["claimed"] == 3
        assert summary["members_rewritten"] == 48
        assert summary["samples_claimed"] == 24
        assert summary["peaks_turned"] == 2
        assert summary["rows_moved"] == 1
        assert summary["held_by_reason"] == {HELD_CURATED: 1}
        assert summary["rows_kept"] == {"no_row": 22}
        assert summary["samples_kept"][KEPT_HAND_ASSIGNED] == 1
        assert summary["samples_kept"][KEPT_LINE_OF_ANOTHER] == 10
        assert summary["samples_kept"][KEPT_NO_MONOISOTOPIC] == 6
        assert summary["records_cleared"] == 2
        assert summary["thresholds"]["presence_share"] == 0.5
        listed = {entry["batch_peak_id"]: entry for entry in summary["rungs_listed"]}
        assert listed["r5"]["passed_in"] == [1]
        assert listed["r5"]["claimed"] == 12
        assert listed["r6"]["kept"] == 4
        assert listed["r7"]["held"] == HELD_CURATED

    def test_the_outcome_says_what_moved(self, plan):
        plan, _ = plan
        claims = _claims(plan)
        summary = summarise(
            plan,
            [
                Written(
                    claims["r3"],
                    True,
                    samples=tuple(DRY + HUMID),
                    members_rewritten=24,
                    turned=True,
                    moved=True,
                ),
                Written(claims["r5"], True, samples=tuple(HUMID), members_rewritten=12),
            ],
            {},
        )
        outcome = measuring_outcome(summary, "b")
        assert outcome["status"] == "success"
        assert "16 water-ladder rungs: 14 passed" in outcome["message"]
        # Samples, not the 36 sample peaks.
        assert "2 batch peaks claimed in 24 samples;" in outcome["message"]
        assert (
            "1 now read as the reagent, 1 of them analytes before"
            in (outcome["message"])
        )
        empty = summarise(plan_batch([], {}, {}, {}), [], {})
        assert measuring_outcome(empty, "b")["status"] == "partial"

    def test_chunks_never_split_a_rung(self, plan, monkeypatch):
        plan, _ = plan
        monkeypatch.setattr(batch_water_ladder, "WRITE_CHUNK", 1)
        chunks = _chunks(plan.claims)
        for chunk in chunks:
            rungs = {c.line.rung_key for c in chunk}
            assert len(rungs) == 1
        assert sum(len(chunk) for chunk in chunks) == len(plan.claims)
        together = {c.batch_peak_id: i for i, chunk in enumerate(chunks) for c in chunk}
        assert together["r6"] == together["r6b"]


# --- the row ------------------------------------------------------------------------


ROW = {
    "peak_assignment_id": "pa-1",
    "sample_peak_mz": BROMIDE.line_mz(6, M0) * (1 + 3e-6),
    "role": "M0",
    "assigned_formula": F6,
    "ion_formula": F6 + "Br-",
    "ionization_mechanism_id": MECH,
    "isotope_label": None,
    "source": "untargeted",
    "fit_score": 0.8,
    "mz_error_ppm": 1.0,
    "abundance_error": 0.1,
    "tier": "assigned",
    "target_compound_id": None,
    "target_ion_id": None,
    "owner_peak_assignment_id": None,
    "alternatives": [{"formula": "C9", "reason": "x"}],
    "provenance": {"evidence": 1},
}


class TestRow:
    def test_the_rungs_peak_becomes_the_reagents_ion(self):
        rung = BROMIDE.rung(6)
        values = water_rung_row(
            ROW, rung, {"core": "[Br]-"}, exact_mz=BROMIDE.line_mz(6, M0)
        )
        assert values["role"] == "reagent"
        assert values["assigned_formula"] is None
        assert values["ion_formula"] == "BrH12O6-"
        assert values["tier"] == "unassigned"
        assert values["source"] == "reagent"
        assert values["fit_score"] is None
        assert values["isotope_label"] is None
        assert values["owner_peak_assignment_id"] is None
        assert values["mz_error_ppm"] == pytest.approx(3.0, abs=1e-3)
        assert values["abundance_error"] is None
        assert values["alternatives"][0]["assigned_formula"] == F6
        assert values["alternatives"][0][DISPLACED_BY_CLAIM] is True
        assert values["alternatives"][1] == {"formula": "C9", "reason": "x"}
        reagent = values["provenance"]["reagent"]
        assert reagent["ion"] == "[Br+6xH2O]-"
        assert reagent["reason"] == REASON_BATCH_WATER_LADDER
        assert reagent[REASON_BATCH_WATER_LADDER] == {"core": "[Br]-"}
        assert reagent["read_as"]["formula"] == F6
        assert reagent["read_as"]["tier"] == "assigned"
        assert reagent["observed"].startswith("no work found")
        assert reagent["mz"] == round(BROMIDE.line_mz(6, M0), 5)

    def test_a_line_of_the_rewritten_row_keeps_its_label_errors_and_owner(self):
        line = {
            **ROW,
            "role": "iso_child",
            "isotope_label": "81Br",
            "owner_peak_assignment_id": "pa-0",
        }
        values = water_rung_row(
            line, BROMIDE.rung(6), {}, exact_mz=BROMIDE.line_mz(6, M0), keep_line=True
        )
        assert values["isotope_label"] == "81Br"
        assert values["mz_error_ppm"] == 1.0
        assert values["abundance_error"] == 0.1
        assert values["owner_peak_assignment_id"] == "pa-0"
        assert values["alternatives"] == ROW["alternatives"]

    def test_a_heavier_line_read_on_its_own_takes_the_label_and_the_owner(self):
        values = water_rung_row(
            ROW,
            BROMIDE.rung(6),
            {},
            exact_mz=BROMIDE.line_mz(6, BR81),
            isotope_label="81Br",
            owner_peak_assignment_id="pa-m0",
        )
        assert values["isotope_label"] == "81Br"
        assert values["owner_peak_assignment_id"] == "pa-m0"
        assert values["alternatives"][0]["assigned_formula"] == F6
        # No label: the monoisotopic peak, which no owner holds.
        values = water_rung_row(
            ROW, BROMIDE.rung(6), {}, exact_mz=1.0, owner_peak_assignment_id="pa-m0"
        )
        assert values["owner_peak_assignment_id"] is None

    def test_a_library_rung_keeps_its_works(self):
        values = water_rung_row(ROW, BROMIDE.rung(1), {}, exact_mz=96.93)
        reagent = values["provenance"]["reagent"]
        assert reagent["references"] == ["san16", "ris19", "wa21"]
        assert "observed" not in reagent


def test_a_plan_is_read_per_partition(monkeypatch):
    anchors, members = _batch()
    other = {key: replace_anchor(a) for key, a in anchors.items()}
    plan = plan_batch(
        list(anchors.values()) + list(other.values()),
        {**members, **{f"o-{k}": v for k, v in members.items()}},
        {PARTITION: "BR", ("mode-b", "tof"): None},
        TIMES,
    )
    assert [part.key for part in plan.partitions] == [("mode-b", "tof"), PARTITION]
    assert plan.partitions[0].claims == []
    assert plan.partitions[1].claims


def replace_anchor(a):
    fields = dict(vars(a))
    fields["batch_peak_id"] = f"o-{a.batch_peak_id}"
    fields["ionization_mode_id"] = "mode-b"
    return SimpleNamespace(**fields)


def test_plan_partition_reads_no_spectra_as_no_ladder():
    part = plan_partition(PARTITION, [], "BR", {})
    assert part.claims == [] and part.unexplained_before is None


def test_a_claim_with_every_sample_kept_claims_nothing():
    anchors, members = _batch()
    claim = Claim(Row(anchors["r6"], members["r6"]), None)  # type: ignore[arg-type]
    claim.samples = {"h00": KEPT_HAND_ASSIGNED}
    assert not claim.claims
    claim.samples = {"h00": None}
    assert claim.claims
    claim.held = HELD_CURATED
    assert not claim.claims
