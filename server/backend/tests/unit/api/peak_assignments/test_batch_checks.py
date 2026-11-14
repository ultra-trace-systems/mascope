"""Unit tests for the batch's isotope checks, their pure parts (step 4.2g).

No DB: which batch peaks each fact is read for, how the halogen lock reads the
isotopologue stamping's records (made here by the stamping's own code), how a
labelled ion's twin and a uronium pair are read, what the records and the
run's summary say, what survives the write, how the consensus keeps a record,
and what the ledger, a snapshot, a member row and a sample's row serve. The
rules themselves are the library's
(``libraries/tools/tests/test_batch_check_rules.py``). See ``batch_checks.py``.
"""

from types import SimpleNamespace

import pytest

from mascope_backend.api.new.peak_assignments import batch_isotopologues
from mascope_backend.api.new.peak_assignments.batch_checks import (
    checks_outcome,
    halogens_of,
    is_labelled_ion,
    judge_silicon,
    needed_peaks,
    plan_batch,
    plan_lock,
    read_batch,
    still_read,
    twin_samples,
)
from mascope_backend.api.new.peak_assignments.batch_export import member_export_row
from mascope_backend.api.new.peak_assignments.batch_isotopologues import Member
from mascope_backend.api.new.peak_assignments.batch_peaks import (
    BATCH_CHECKS_KEY,
    CO_VARIATION_KEY,
    DIAGNOSTIC_ISOTOPOLOGUES_KEY,
    STAMPED_ISOTOPOLOGUE_OF_KEY,
    batch_checks_record_of,
    compute_consensus,
    resolution_adaptive_tol_ppm,
    row_batch_checks,
    standing_batch_checks_record,
)
from mascope_backend.api.new.peak_assignments.batch_peaks_records import (
    _batch_peak_meta,
)
from mascope_backend.api.new.peak_assignments.batch_runs import (
    anchor_snapshot_row,
    snapshot_anchor_meta,
)
from mascope_tools.composition.batch_checks import (
    CL37_SPACING,
    LOCK_LOCKED,
    LOCK_NOT_LOCKED,
    LOCK_SILICON,
    LOCK_UNTESTABLE,
    PAIR_ESTABLISHED,
    PAIR_NOT_ESTABLISHED,
    PAIR_UNTESTABLE,
    REASON_BOTH_HALOGENS,
    REASON_NO_OFFSET,
    REASON_OFFSET,
    REASON_SILICON_UNTESTABLE,
    SI29_PER_SI30,
    SI29_SPACING,
    TWIN_ABSENT,
    TWIN_PRESENT,
    TWIN_UNTESTABLE,
)
from mascope_tools.composition.diagnostic_isotopologues import (
    predict_diagnostic_isotopologues,
)
from mascope_tools.composition.envelope_tolerance import EnvelopeTolerance


SAMPLES = [f"s{i}" for i in range(6)]
#: An Orbitrap anchor's frozen tolerance: a 2 ppm half FWHM and the margin.
ORBI_TOL = resolution_adaptive_tol_ppm(200.0, 250000.0)
BR_OFFSET = 1.997952
CL_RATIO = 0.32
BR_RATIO = 0.9728
TWIN_OFFSET = -0.997035
TWIN_RATIO = 0.02 / 0.98
STAMPING_RUN = "stamp-run"
NOTATIONS = {
    "mech-br": "[M+Br]-",
    "mech-h": "[M-H]-",
    "mech-ln": "[M+^NO3]-",
    "mech-n": "[M+NO3]-",
    "mech-p": "[M+H]+",
    "mech-u": "[M+CH4N2O+H]+",
    "mech-r": "[^NO3]-",
}


def anchor(key, mz, formula=None, ion=None, mechanism=None, **extra):
    """A batch peak as the checks read one."""
    fields = dict(
        batch_peak_id=key,
        mz=mz,
        mz_tol_ppm=ORBI_TOL,
        ionization_mode_id="mode-1",
        intensity_variable="sum_peak_heights",
        consensus_formula=formula,
        consensus_ion_formula=ion,
        ionization_mechanism_id=mechanism,
        consensus_tier="assigned" if formula else "unassigned",
        consensus_role=None,
        isotopologue_of=None,
        provenance=None,
    )
    fields.update(extra)
    return SimpleNamespace(**fields)


def trace(mz, intensities, prefix, mzs=None):
    """Sample -> member, one intensity per sample (None for absent)."""
    out = {}
    for i, (sample, value) in enumerate(zip(SAMPLES, intensities)):
        if value is None:
            continue
        out[sample] = Member(
            sample_peak_id=f"{prefix}-{i}",
            intensity=value,
            mz=mzs[i] if mzs else mz,
        )
    return out


PARENT = [1e5 * (1 + i) for i in range(6)]


def stamp(anchors, members, run_id=STAMPING_RUN):
    """Stamp the batch as step 4.2c does and leave its records on the anchors,
    as its write would."""
    parents, lines, _ = batch_isotopologues.read_batch(
        batch_isotopologues.plan_batch(anchors, members),
        {},
        {},
        batch_peak_run_id=run_id,
    )
    for a in anchors:
        provenance = dict(a.provenance or {})
        if a.batch_peak_id in parents:
            provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY] = parents[a.batch_peak_id]
        if a.batch_peak_id in lines:
            provenance[STAMPED_ISOTOPOLOGUE_OF_KEY] = lines[a.batch_peak_id]
        a.provenance = provenance
    return anchors, members


def check(anchors, members, snr=None, purity=None, run_id="checks-run"):
    plan = plan_batch(anchors, members, NOTATIONS, {"mode-1": purity})
    return read_batch(plan, NOTATIONS, {}, snr or {}, batch_peak_run_id=run_id)


def halogen_batch(ion, formula, mz, shift_ppm=0.0, line_samples=None, extra=()):
    """A halogen parent and its heavy line at the exact spacing (shifted by
    ``shift_ppm`` of the parent's m/z), stamped."""
    (line,) = [
        d
        for d in predict_diagnostic_isotopologues(ion, 250000.0)
        if d.label in ("37Cl", "81Br") and d.ratio > 0.3
    ][:1]
    line_mz = mz + line.mz_offset + shift_ppm * mz * 1e-6
    anchors = [
        anchor("p", mz, formula, ion, "mech-br"),
        anchor("line", line_mz),
        anchor("other", 400.0, "C9H14O4", "C9H13O4-", "mech-h"),
        *[a for a, _ in extra],
    ]
    members = {
        "p": trace(mz, PARENT, "p"),
        "line": trace(
            line_mz,
            [
                PARENT[i] * line.ratio
                if line_samples is None or i in line_samples
                else None
                for i in range(6)
            ],
            "line",
        ),
        "other": trace(400.0, [1e4] * 6, "o"),
        **{a.batch_peak_id: m for a, m in extra},
    }
    return stamp(anchors, members)


class TestTheHalogenLock:
    def test_a_bromide_cluster_locks_bromine_the_adduct_s(self):
        records, summary = check(*halogen_batch("C2H4BrO-", "C2H4O", 122.9451))
        lock = records["p"]["halogen_lock"]
        assert (lock["verdict"], lock["element"], lock["label"]) == (
            LOCK_LOCKED,
            "Br",
            "81Br",
        )
        assert (lock["n_ion"], lock["n_neutral"]) == (1, 0)
        assert lock["batch_peak_id"] == "line"
        assert lock["share"] == 1.0
        assert lock["n_stamped_in"] == 6
        assert lock["offset_error_ppm"] == pytest.approx(0.0, abs=1e-3)
        assert lock["stamping_run_id"] == STAMPING_RUN
        assert lock["silicon"] is None
        assert (summary["locked"], summary["locked_br"], summary["locked_cl"]) == (
            1,
            1,
            0,
        )
        assert summary["halogen_parents"] == 1
        # Only the halogen parent carries a lock.
        assert "halogen_lock" not in records.get("other", {})

    def test_a_line_off_the_exact_spacing_is_no_lock(self):
        # 2.5 ppm off: inside the line's own tolerance, so the stamping finds
        # and stamps it, but outside the batch's 1 ppm position window.
        records, summary = check(
            *halogen_batch("C2H4BrO-", "C2H4O", 122.9451, shift_ppm=2.5)
        )
        lock = records["p"]["halogen_lock"]
        assert (lock["verdict"], lock["reason"]) == (LOCK_NOT_LOCKED, REASON_OFFSET)
        assert lock["offset_error_ppm"] == pytest.approx(2.5, abs=0.05)
        assert summary["not_locked"] == 1

    def test_a_chlorine_line_below_m_z_206_needs_no_silicon_test(self):
        records, _ = check(*halogen_batch("C4H6ClO2-", "C4H7ClO2", 121.0056))
        lock = records["p"]["halogen_lock"]
        assert lock["verdict"] == LOCK_LOCKED
        assert (lock["element"], lock["n_ion"], lock["n_neutral"]) == ("Cl", 1, 1)
        assert lock["silicon"] is None

    def si29(self, mz, ratios, samples=range(6)):
        si_mz = mz + SI29_SPACING
        line = anchor("si29", si_mz)
        return (
            line,
            trace(
                si_mz,
                [PARENT[i] * ratios if i in samples else None for i in range(6)],
                "si",
            ),
        )

    def test_above_m_z_206_the_29si_line_decides_a_chlorine_lock(self):
        mz = 237.0894
        silicon = CL_RATIO * SI29_PER_SI30
        records, summary = check(
            *halogen_batch(
                "C10H18ClO4-", "C10H19ClO4", mz, extra=[self.si29(mz, silicon)]
            )
        )
        lock = records["p"]["halogen_lock"]
        assert lock["verdict"] == LOCK_SILICON
        assert lock["silicon"]["batch_peak_id"] == "si29"
        assert lock["silicon"]["verdict"] == "stamped"
        assert lock["silicon"]["ratio"] == pytest.approx(silicon, rel=1e-3)
        assert (summary["silicon"], summary["locked"]) == (1, 0)
        # The silicon test reads the noise of the parent and the 29Si line.
        plan = plan_batch(
            *halogen_batch(
                "C10H18ClO4-", "C10H19ClO4", mz, extra=[self.si29(mz, silicon)]
            ),
            NOTATIONS,
            {},
        )
        assert needed_peaks(plan)["s0"] == {"p-0", "si-0"}

    def test_the_silicon_test_reads_each_sample_s_run_tolerance(self):
        # A 29Si line reading 20 % high holds only where the run admits it.
        mz = 237.0894
        silicon = CL_RATIO * SI29_PER_SI30 * 1.2
        anchors, members = halogen_batch(
            "C10H18ClO4-", "C10H19ClO4", mz, extra=[self.si29(mz, silicon)]
        )
        plan = plan_batch(anchors, members, NOTATIONS, {})
        (lock,) = plan.locks
        assert judge_silicon(lock, {}, {}).verdict == "not_stamped"
        high = {sample: EnvelopeTolerance(centre=0.2) for sample in SAMPLES}
        assert judge_silicon(lock, high, {}).verdict == "stamped"

    def test_a_bromine_lock_reads_no_silicon_test(self):
        # Where the window cannot tell 30Si from the line, only a chlorine
        # lock asks the 29Si line: no silicon count makes a bromine's height.
        anchors, members = halogen_batch("C10H18BrO4-", "C10H19O4", 281.04)
        traces = plan_batch(anchors, members, NOTATIONS, {}).traces
        from mascope_backend.api.new.peak_assignments.batch_isotopologues import (
            CentreIndex,
        )

        record = anchors[0].provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY]
        lock = plan_lock(traces["p"], record, traces, CentreIndex(traces.values()), 5.0)
        assert lock.read().verdict == LOCK_LOCKED
        assert lock.silicon is None

    def test_a_chlorine_line_off_its_ratio_reads_no_silicon_test(self):
        mz = 237.0894
        anchors, members = halogen_batch("C10H18ClO4-", "C10H19ClO4", mz)
        record = anchors[0].provenance[DIAGNOSTIC_ISOTOPOLOGUES_KEY]
        for entry in record["predicted"]:
            if entry["label"] == "37Cl":
                entry["verdict"] = "not_stamped"
        records, _ = check(anchors, members)
        lock = records["p"]["halogen_lock"]
        assert lock["verdict"] == LOCK_NOT_LOCKED
        assert lock["silicon"] is None

    def test_a_line_stamped_for_another_parent_gives_no_offset(self):
        anchors, members = halogen_batch("C2H4BrO-", "C2H4O", 122.9451)
        line = anchors[1].provenance[STAMPED_ISOTOPOLOGUE_OF_KEY]
        line["batch_peak_id"] = "someone-else"
        records, summary = check(anchors, members)
        lock = records["p"]["halogen_lock"]
        assert (lock["verdict"], lock["reason"]) == (LOCK_UNTESTABLE, REASON_NO_OFFSET)
        assert lock["offset_error_ppm"] is None
        assert lock["n_stamped_in"] == 0
        assert (summary["halogen_untestable"], summary["not_locked"]) == (1, 0)

    def test_a_line_no_stamped_sample_still_holds_gives_no_offset(self):
        anchors, members = halogen_batch("C2H4BrO-", "C2H4O", 122.9451)
        # Every member the line was stamped with went since.
        members["line"] = {}
        records, _ = check(anchors, members)
        lock = records["p"]["halogen_lock"]
        assert (lock["verdict"], lock["reason"]) == (LOCK_UNTESTABLE, REASON_NO_OFFSET)

    def test_a_stamped_sample_whose_member_went_is_not_read(self):
        anchors, members = halogen_batch("C2H4BrO-", "C2H4O", 122.9451)
        del members["line"]["s0"]
        records, _ = check(anchors, members)
        lock = records["p"]["halogen_lock"]
        assert lock["verdict"] == LOCK_LOCKED
        assert lock["n_stamped_in"] == 5

    def test_no_29si_line_or_one_off_its_height_leaves_the_lock_chlorine(self):
        mz = 237.0894
        records, _ = check(*halogen_batch("C10H18ClO4-", "C10H19ClO4", mz))
        lock = records["p"]["halogen_lock"]
        assert lock["verdict"] == LOCK_LOCKED
        assert lock["silicon"]["batch_peak_id"] is None
        assert lock["silicon"]["verdict"] == "not_stamped"
        records, _ = check(
            *halogen_batch("C10H18ClO4-", "C10H19ClO4", mz, extra=[self.si29(mz, 0.1)])
        )
        assert records["p"]["halogen_lock"]["verdict"] == LOCK_LOCKED
        assert records["p"]["halogen_lock"]["silicon"]["verdict"] == "not_stamped"

    def test_a_29si_line_too_few_samples_show_leaves_the_lock_untestable(self):
        mz = 237.0894
        silicon = CL_RATIO * SI29_PER_SI30
        records, summary = check(
            *halogen_batch(
                "C10H18ClO4-",
                "C10H19ClO4",
                mz,
                extra=[self.si29(mz, silicon, samples=(0, 1))],
            )
        )
        lock = records["p"]["halogen_lock"]
        assert (lock["verdict"], lock["reason"]) == (
            LOCK_UNTESTABLE,
            REASON_SILICON_UNTESTABLE,
        )
        assert summary["halogen_untestable"] == 1

    def test_an_ion_with_both_halogens_is_not_locked(self):
        records, _ = check(*halogen_batch("C6H9BrClO3-", "C6H10ClO3", 242.9429))
        lock = records["p"]["halogen_lock"]
        assert (lock["verdict"], lock["reason"]) == (
            LOCK_UNTESTABLE,
            REASON_BOTH_HALOGENS,
        )
        assert lock["element"] == "Br"

    def test_a_parent_the_stamping_has_not_read_gets_no_lock(self):
        anchors = [anchor("p", 122.9451, "C2H4O", "C2H4BrO-", "mech-br")]
        records, summary = check(anchors, {"p": trace(122.9451, PARENT, "p")})
        assert "p" not in records
        assert (summary["halogen_parents"], summary["halogen_unstamped"]) == (1, 1)
        assert checks_outcome(summary, "sb")["status"] == "partial"
        assert "Stamp isotopologues" in checks_outcome(summary, "sb")["message"]

    def test_a_stamping_of_another_reading_is_not_read(self):
        anchors, members = halogen_batch("C2H4BrO-", "C2H4O", 122.9451)
        anchors[0].consensus_formula = "C2H5O"
        records, summary = check(anchors, members)
        assert "p" not in records
        assert summary["halogen_unstamped"] == 1

    @pytest.mark.parametrize(
        "ion, held",
        [
            ("C2H4BrO-", {"Br": 1}),
            ("C6H9BrCl2O3-", {"Br": 1, "Cl": 2}),
            ("C10H16O4^NO3-", {}),
            (None, {}),
        ],
    )
    def test_the_halogens_an_ion_carries(self, ion, held):
        assert halogens_of(SimpleNamespace(consensus_ion_formula=ion)) == held


def labelled_batch(
    twin_ratio=TWIN_RATIO, twin_samples=range(6), snr=1000.0, core_ratio=TWIN_RATIO
):
    """The labelled reagent core, a labelled analyte cluster and an
    unlabelled analyte; the analyte's 14N twin at ``twin_ratio`` of it, the
    core's at ``core_ratio``."""
    core, analyte = 62.9854, 263.0903
    anchors = [
        anchor(
            "core",
            core,
            None,
            "^NO3-",
            "mech-r",
            consensus_role="reagent",
        ),
        anchor(
            "core14",
            core + TWIN_OFFSET,
            None,
            "NO3-",
            "mech-r",
            consensus_role="reagent",
            isotopologue_of="core",
        ),
        anchor("x", analyte, "C10H16O4", "C10H16O4^NO3-", "mech-ln"),
        anchor("x14", analyte + TWIN_OFFSET, "C10H16O4", "C10H16NO7-", "mech-n"),
        anchor("plain", 300.0, "C10H16O5", "C10H15O5-", "mech-h"),
    ]
    members = {
        "core": trace(core, [1e7] * 6, "core"),
        "core14": trace(core + TWIN_OFFSET, [1e7 * core_ratio] * 6, "core14"),
        "x": trace(analyte, PARENT, "x"),
        "x14": trace(
            analyte + TWIN_OFFSET,
            [PARENT[i] * twin_ratio if i in twin_samples else None for i in range(6)],
            "x14",
        ),
        "plain": trace(300.0, PARENT, "plain"),
    }
    noise = {
        sample: {
            **{f"{key}-{i}": snr for key in ("core", "x", "plain")},
            **{f"{key}-{i}": snr * TWIN_RATIO for key in ("core14", "x14")},
        }
        for i, sample in enumerate(SAMPLES)
    }
    return anchors, members, noise


class TestTheLabelTwin:
    def test_the_labelled_core_and_cluster_find_their_twins(self):
        anchors, members, noise = labelled_batch()
        records, summary = check(anchors, members, noise)
        core = records["core"]["label_twin"]
        assert (core["verdict"], core["batch_peak_id"]) == (TWIN_PRESENT, "core14")
        assert core["judged"] == 6
        assert core["twin_role"] == "reagent"
        cluster = records["x"]["label_twin"]
        assert (cluster["verdict"], cluster["batch_peak_id"]) == (TWIN_PRESENT, "x14")
        assert cluster["ratio"] == pytest.approx(TWIN_RATIO, abs=1e-6)
        assert cluster["excess"] == pytest.approx(1.0, rel=1e-3)
        assert cluster["twin_formula"] == "C10H16O4"
        assert (summary["labelled_ions"], summary["twin_present"]) == (2, 2)

    def test_no_unlabelled_ion_is_read(self):
        anchors, members, noise = labelled_batch()
        records, _ = check(anchors, members, noise)
        for key in ("plain", "x14", "core14"):
            assert "label_twin" not in records.get(key, {})

    def test_a_reading_whose_twin_is_missing_is_absent(self):
        anchors, members, noise = labelled_batch(twin_samples=(0,))
        records, summary = check(anchors, members, noise)
        cluster = records["x"]["label_twin"]
        assert (cluster["verdict"], cluster["present"]) == (TWIN_ABSENT, 1)
        assert summary["twin_absent"] == 1

    def test_dim_samples_cannot_tell(self):
        anchors, members, noise = labelled_batch(twin_samples=(), snr=100.0)
        records, summary = check(anchors, members, noise)
        cluster = records["x"]["label_twin"]
        assert (cluster["verdict"], cluster["judged"]) == (TWIN_UNTESTABLE, 0)
        assert (cluster["present"], cluster["excess"]) == (0, None)
        assert summary["twin_untestable"] >= 1

    def test_an_excess_14n_line_runs_above_the_impurity_share(self):
        anchors, members, noise = labelled_batch(twin_ratio=TWIN_RATIO * 6)
        records, _ = check(anchors, members, noise)
        cluster = records["x"]["label_twin"]
        assert cluster["verdict"] == TWIN_PRESENT
        assert cluster["excess"] == pytest.approx(6.0, rel=1e-3)

    def test_the_reagent_s_own_ions_measure_its_impurity(self):
        # A reagent purer than its setup says: the core's 14N line at 0.4 of
        # the share. The analyte's twin at that share is the label's.
        anchors, members, noise = labelled_batch(
            twin_ratio=TWIN_RATIO * 0.4, core_ratio=TWIN_RATIO * 0.4
        )
        records, summary = check(anchors, members, noise)
        cluster = records["x"]["label_twin"]
        assert cluster["verdict"] == TWIN_PRESENT
        assert cluster["impurity_factor"] == pytest.approx(0.4)
        assert cluster["read_ratio"] == pytest.approx(TWIN_RATIO * 0.4, abs=1e-6)
        assert cluster["excess"] == pytest.approx(1.0, rel=1e-3)
        assert records["core"]["label_twin"]["verdict"] == TWIN_PRESENT
        (partition,) = summary["partitions"]
        assert (partition["impurity_factor"], partition["impurity_cores"]) == (0.4, 1)

    def test_without_a_reagent_ion_the_setup_s_purity_holds(self):
        anchors, members, noise = labelled_batch(twin_ratio=TWIN_RATIO * 0.4)
        anchors = [a for a in anchors if a.batch_peak_id not in ("core", "core14")]
        records, summary = check(anchors, members, noise)
        cluster = records["x"]["label_twin"]
        assert cluster["impurity_factor"] is None
        assert cluster["read_ratio"] == pytest.approx(TWIN_RATIO, abs=1e-6)
        # Far under the share the setup's purity gives it.
        assert cluster["verdict"] == TWIN_ABSENT
        (partition,) = summary["partitions"]
        assert (partition["impurity_factor"], partition["impurity_cores"]) == (None, 0)

    def test_the_twin_s_own_noise_widens_its_band(self):
        # 30 % under the share is six of its floor widths: missing. The same
        # line at an S/N of 3 is a third of its height wide, within two.
        anchors, members, noise = labelled_batch(twin_ratio=TWIN_RATIO * 0.7)
        anchors = [a for a in anchors if a.batch_peak_id not in ("core", "core14")]
        records, _ = check(anchors, members, noise)
        assert records["x"]["label_twin"]["verdict"] == TWIN_ABSENT
        for i, sample in enumerate(SAMPLES):
            noise[sample][f"x14-{i}"] = 3.0
        records, _ = check(anchors, members, noise)
        cluster = records["x"]["label_twin"]
        assert (cluster["verdict"], cluster["present"]) == (TWIN_PRESENT, 6)

    def test_an_analyte_measures_no_impurity(self):
        # Only the reagent's own ions read the label's impurity: an analyte's
        # 14N line holds ambient nitrate too.
        anchors, members, noise = labelled_batch(twin_ratio=TWIN_RATIO * 6)
        anchors = [a for a in anchors if a.batch_peak_id not in ("core", "core14")]
        records, _ = check(anchors, members, noise)
        assert records["x"]["label_twin"]["impurity_factor"] is None

    def test_the_setup_s_purity_is_read(self):
        # At 90 % the twin is 11 % of the ion. With no reagent ion to measure
        # the impurity, a 2 % line is far under it.
        anchors, members, noise = labelled_batch()
        anchors = [a for a in anchors if a.batch_peak_id not in ("core", "core14")]
        records, _ = check(anchors, members, noise, purity=0.9)
        cluster = records["x"]["label_twin"]
        assert cluster["purity"] == 0.9
        assert cluster["ratio"] == pytest.approx(0.1 / 0.9, abs=1e-6)
        assert cluster["verdict"] == TWIN_ABSENT
        # The core measures the reagent at 98 % again.
        anchors, members, noise = labelled_batch()
        records, _ = check(anchors, members, noise, purity=0.9)
        cluster = records["x"]["label_twin"]
        assert cluster["impurity_factor"] == pytest.approx(TWIN_RATIO * 9, rel=1e-3)
        assert cluster["read_ratio"] == pytest.approx(TWIN_RATIO, abs=1e-6)
        assert cluster["verdict"] == TWIN_PRESENT

    def test_each_sample_s_run_detection_threshold_is_read(self):
        anchors, members, noise = labelled_batch()
        plan = plan_batch(anchors, members, NOTATIONS, {})
        twin = next(t for t in plan.twins if t.labelled.anchor.batch_peak_id == "x")
        strict = {"s0": EnvelopeTolerance(detect_snr=1e9)}
        samples = twin_samples(twin, strict, noise)
        assert [s.tolerance.detect_snr for s in samples][:2] == [1e9, None]
        records, _ = read_batch(plan, NOTATIONS, strict, noise)
        assert records["x"]["label_twin"]["judged"] == 5

    def test_the_noise_read_is_the_labelled_ions_and_their_twins(self):
        anchors, members, _ = labelled_batch()
        plan = plan_batch(anchors, members, NOTATIONS, {})
        needed = needed_peaks(plan)
        assert needed["s0"] == {"core-0", "core14-0", "x-0", "x14-0"}

    @pytest.mark.parametrize(
        "extra, labelled",
        [
            ({}, True),
            ({"consensus_role": "reagent", "consensus_formula": None}, True),
            ({"consensus_role": "artifact"}, False),
            ({"consensus_tier": "unassigned"}, False),
            ({"isotopologue_of": "p"}, False),
            ({"consensus_ion_formula": "[13C]C9H16O4^NO3-"}, False),
            ({"consensus_ion_formula": "C10H16NO7-"}, False),
        ],
    )
    def test_which_ions_are_labelled(self, extra, labelled):
        a = anchor("x", 263.0, "C10H16O4", "C10H16O4^NO3-", "mech-ln", **extra)
        assert is_labelled_ion(a) is labelled


def co_variation(own, mechanism, partner, partner_mechanism, verdict, run="cov"):
    reading = {
        "verdict": verdict,
        "r": 0.82 if verdict == "co_varying" else 0.1,
        "n_spectra": 24,
        "reason": "few_spectra" if verdict == "untestable" else None,
    }
    return {
        "formula": "C10H16O",
        "ionization_mechanism_id": mechanism,
        "channel": NOTATIONS[mechanism],
        **reading,
        "partner_batch_peak_id": partner,
        "partners": [
            {
                "batch_peak_id": partner,
                "formula": "C10H16O",
                "ion_formula": "ion",
                "ionization_mechanism_id": partner_mechanism,
                "channel": NOTATIONS[partner_mechanism],
                **reading,
            }
        ],
        "batch_peak_run_id": run,
    }


def uronium_batch(verdict="co_varying", read=True):
    anchors = [
        anchor("h", 153.1274, "C10H16O", "C10H17O+", "mech-p"),
        anchor("u", 213.1598, "C10H16O", "C11H21N2O2+", "mech-u"),
        anchor("other", 300.0, "C10H16O5", "C10H17O5+", "mech-p"),
    ]
    if read:
        anchors[0].provenance = {
            CO_VARIATION_KEY: co_variation("h", "mech-p", "u", "mech-u", verdict)
        }
        anchors[1].provenance = {
            CO_VARIATION_KEY: co_variation("u", "mech-u", "h", "mech-p", verdict)
        }
    members = {
        "h": trace(153.1274, PARENT, "h"),
        "u": trace(213.1598, PARENT[:4] + [None, None], "u"),
        "other": trace(300.0, PARENT, "o"),
    }
    return anchors, members


class TestTheNeutralPair:
    @pytest.mark.parametrize(
        "verdict, read, key",
        [
            ("co_varying", PAIR_ESTABLISHED, "pairs_established"),
            ("not_co_varying", PAIR_NOT_ESTABLISHED, "pairs_not_established"),
            ("untestable", PAIR_UNTESTABLE, "pairs_untestable"),
        ],
    )
    def test_both_channels_carry_the_co_variation_s_verdict(self, verdict, read, key):
        records, summary = check(*uronium_batch(verdict))
        for own, partner in (("h", "u"), ("u", "h")):
            pair = records[own]["neutral_pair"]
            assert pair["verdict"] == read
            assert pair["partner_batch_peak_id"] == partner
            assert pair["co_variation_run_id"] == "cov"
        pair = records["h"]["neutral_pair"]
        assert (pair["channel"], pair["partner_channel"]) == (
            "[M+H]+",
            "[M+CH4N2O+H]+",
        )
        assert (pair["n_present"], pair["partner_n_present"]) == (6, 4)
        assert (summary["pairs"], summary[key], summary["pair_channels"]) == (1, 1, 2)
        assert "neutral_pair" not in records.get("other", {})

    @pytest.mark.parametrize(
        "h1, h2, u",
        [("a-h1", "b-h2", "c-u"), ("b-h1", "c-h2", "a-u"), ("a-h1", "c-h2", "b-u")],
    )
    def test_each_pair_counts_once_whatever_the_ids(self, h1, h2, u):
        # One compound protonated at two batch peaks (a split trace) and
        # clustered with urea at one: two pairs. The urea channel's record
        # names both, the stronger first; each protonated one names it.
        anchors = [
            anchor(h1, 153.1274, "C10H16O", "C10H17O+", "mech-p"),
            anchor(h2, 153.1275, "C10H16O", "C10H17O+", "mech-p"),
            anchor(u, 213.1598, "C10H16O", "C11H21N2O2+", "mech-u"),
        ]
        anchors[0].provenance = {
            CO_VARIATION_KEY: co_variation(h1, "mech-p", u, "mech-u", "not_co_varying")
        }
        anchors[1].provenance = {
            CO_VARIATION_KEY: co_variation(h2, "mech-p", u, "mech-u", "co_varying")
        }
        urea = co_variation(u, "mech-u", h2, "mech-p", "co_varying")
        weaker = co_variation(u, "mech-u", h1, "mech-p", "not_co_varying")
        urea["partners"] += weaker["partners"]
        anchors[2].provenance = {CO_VARIATION_KEY: urea}
        members = {
            h1: trace(153.1274, PARENT, "h1"),
            h2: trace(153.1275, PARENT, "h2"),
            u: trace(213.1598, PARENT, "u"),
        }
        records, summary = check(anchors, members)
        assert (
            summary["pair_channels"],
            summary["pairs"],
            summary["pairs_established"],
            summary["pairs_not_established"],
        ) == (3, 2, 1, 1)
        # The urea channel's fact is its strongest partner's.
        pair = records[u]["neutral_pair"]
        assert (pair["partner_batch_peak_id"], pair["verdict"]) == (
            h2,
            PAIR_ESTABLISHED,
        )
        assert records[h1]["neutral_pair"]["verdict"] == PAIR_NOT_ESTABLISHED

    def test_a_pair_the_co_variation_has_not_read_is_counted_not_recorded(self):
        records, summary = check(*uronium_batch(read=False))
        assert not records
        # The two channels of one compound; the compound seen protonated alone
        # has no pair to read.
        assert summary["pair_channels_unread"] == 2
        assert "Read co-variation" in checks_outcome(summary, "sb")["message"]

    def test_a_channel_with_no_partner_through_the_other_is_no_pair(self):
        anchors, members = uronium_batch()
        anchors[0].provenance[CO_VARIATION_KEY]["partners"][0]["channel"] = "[M+NH4]+"
        records, summary = check(anchors, members)
        assert "h" not in records
        assert records["u"]["neutral_pair"]["verdict"] == PAIR_ESTABLISHED
        assert summary["pair_channels_unread"] == 1

    def test_a_compound_seen_through_one_channel_has_no_pair(self):
        anchors, members = uronium_batch()
        anchors[1].consensus_formula = "C10H18O"
        records, summary = check(anchors, members)
        assert not records
        assert summary["pair_channels_unread"] == 0


class TestTheRecords:
    def test_a_record_names_the_reading_and_the_run(self):
        records, summary = check(*uronium_batch())
        record = records["h"]
        assert {
            k: record[k]
            for k in (
                "formula",
                "ion_formula",
                "ionization_mechanism_id",
                "channel",
                "batch_peak_run_id",
            )
        } == {
            "formula": "C10H16O",
            "ion_formula": "C10H17O+",
            "ionization_mechanism_id": "mech-p",
            "channel": "[M+H]+",
            "batch_peak_run_id": "checks-run",
        }
        assert summary["anchors_recorded"] == 2
        assert summary["thresholds"]["absent_share"] == 0.8
        (partition,) = summary["partitions"]
        assert partition["window_ppm"] == 1.0

    def test_the_outcome_counts_each_fact(self):
        anchors, members, noise = labelled_batch()
        _, summary = check(anchors, members, noise)
        outcome = checks_outcome(summary, "sb")
        assert outcome["status"] == "success"
        assert "2 of 2 labelled ions with their 14N twin" in outcome["message"]
        assert outcome["message"].endswith("No tier moved.")

    def test_the_write_keeps_what_still_reads(self):
        anchors, members = uronium_batch()
        records, _ = check(anchors, members)
        moved = anchor("u", 213.1598, "C10H18O", "C11H23N2O2+", "mech-u")
        kept = still_read([anchors[0], moved, anchors[2]], records)
        assert set(kept) == {"h"}
        # The partner the fact names went: the fact, and the record, go too.
        assert still_read([moved, anchors[2]], {"h": records["h"]}) == {}

    def test_a_fact_naming_a_batch_peak_that_went_is_left_out(self):
        anchors, members, noise = labelled_batch()
        records, _ = check(anchors, members, noise)
        stay = [a for a in anchors if a.batch_peak_id != "x14"]
        kept = still_read(stay, records)
        assert "x" not in kept
        assert kept["core"]["label_twin"]["batch_peak_id"] == "core14"


def checked(anchors, records):
    for a in anchors:
        if a.batch_peak_id in records:
            a.provenance = {
                **(a.provenance or {}),
                BATCH_CHECKS_KEY: records[a.batch_peak_id],
            }
    return {a.batch_peak_id: a for a in anchors}


class TestWhatIsServed:
    def test_a_lock_stands_while_its_stamping_does(self):
        anchors, members = halogen_batch("C2H4BrO-", "C2H4O", 122.9451)
        records, _ = check(anchors, members)
        parent = checked(anchors, records)["p"]
        assert standing_batch_checks_record(parent)["halogen_lock"]["verdict"] == (
            LOCK_LOCKED
        )
        # A later stamping replaces the record the lock read.
        stamp(anchors, members, run_id="another-stamping")
        assert standing_batch_checks_record(parent) is None
        # Raw, the record is still there: the consensus keeps it.
        assert batch_checks_record_of(parent) is not None

    def test_a_pair_stands_while_its_co_variation_reading_does(self):
        anchors, members = uronium_batch()
        records, _ = check(anchors, members)
        h = checked(anchors, records)["h"]
        assert standing_batch_checks_record(h)["neutral_pair"]["verdict"] == (
            PAIR_ESTABLISHED
        )
        h.provenance[CO_VARIATION_KEY] = {
            **h.provenance[CO_VARIATION_KEY],
            "batch_peak_run_id": "cov-2",
        }
        assert standing_batch_checks_record(h) is None

    def test_a_twin_reads_the_members_alone(self):
        anchors, members, noise = labelled_batch()
        records, _ = check(anchors, members, noise)
        core = checked(anchors, records)["core"]
        served = standing_batch_checks_record(core)
        assert served["label_twin"]["verdict"] == TWIN_PRESENT
        assert set(served) - {"label_twin"} == {
            "formula",
            "ion_formula",
            "ionization_mechanism_id",
            "channel",
            "batch_peak_run_id",
        }

    @pytest.mark.parametrize(
        "change",
        [
            {"consensus_formula": "C10H18O"},
            {"consensus_ion_formula": "C10H19O+"},
            {"ionization_mechanism_id": "mech-u"},
        ],
    )
    def test_nothing_stands_beside_another_reading(self, change):
        anchors, members = uronium_batch()
        records, _ = check(anchors, members)
        h = checked(anchors, records)["h"]
        for key, value in change.items():
            setattr(h, key, value)
        assert standing_batch_checks_record(h) is None

    def test_the_ledger_a_snapshot_and_a_member_row_serve_it(self):
        anchors, members = halogen_batch("C2H4BrO-", "C2H4O", 122.9451)
        records, _ = check(anchors, members)
        parent = checked(anchors, records)["p"]
        for key, value in dict(
            sample_batch_id="sb-1",
            best_fit_score=0.9,
            support_fraction=1.0,
            n_present=6,
            is_ambiguous=0,
            max_intensity=6e5,
            candidates=[{"formula": "C2H4O"}],
            engine_tier=None,
            engine_claim=None,
            seeded=False,
            continuity_grade=None,
            continuity=None,
        ).items():
            setattr(parent, key, value)
        served = standing_batch_checks_record(parent)
        assert _batch_peak_meta(parent)["batch_checks"] == served
        row = anchor_snapshot_row("run-1", parent, [])
        assert row["batch_checks"] == served
        assert snapshot_anchor_meta(SimpleNamespace(**row), "sb-1")["batch_checks"] == (
            served
        )
        member = SimpleNamespace(
            sample_item_id="s0",
            sample_peak_id="1",
            mz_delta_ppm=0.0,
            intensity=1.0,
            candidate=None,
            tier=None,
            role=None,
            fit_score=None,
            p_correct=None,
            owner_batch_peak_id=None,
        )
        export = member_export_row(member, parent, "sample")
        assert (export["halogen_lock"], export["halogen_element"]) == (
            LOCK_LOCKED,
            "Br",
        )
        assert export["label_twin"] is None
        assert export["neutral_pair"] is None

    def test_a_pair_s_member_rows_carry_its_r(self):
        anchors, members = uronium_batch()
        records, _ = check(anchors, members)
        h = checked(anchors, records)["h"]
        for key, value in dict(
            sample_batch_id="sb-1",
            candidates=[],
            n_present=6,
            support_fraction=1.0,
            is_ambiguous=0,
            max_intensity=1.0,
            engine_tier=None,
            engine_claim=None,
        ).items():
            setattr(h, key, value)
        member = SimpleNamespace(
            sample_item_id="s0",
            sample_peak_id="1",
            mz_delta_ppm=0.0,
            intensity=1.0,
            candidate=None,
            tier=None,
            role=None,
            fit_score=None,
            p_correct=None,
            owner_batch_peak_id=None,
        )
        export = member_export_row(member, h, "sample")
        assert (export["neutral_pair"], export["neutral_pair_r"]) == (
            PAIR_ESTABLISHED,
            0.82,
        )

    @pytest.mark.parametrize(
        "row, served",
        [
            (
                {
                    "role": "M0",
                    "assigned_formula": "C10H16O",
                    "ion_formula": "C10H17O+",
                    "ionization_mechanism_id": "mech-p",
                },
                True,
            ),
            (
                {
                    "role": "M0",
                    "assigned_formula": "C9H12N2",
                    "ion_formula": "C10H17O+",
                    "ionization_mechanism_id": "mech-p",
                },
                False,
            ),
            (
                {
                    "role": "M0",
                    "assigned_formula": "C10H16O",
                    "ion_formula": "C10H17O+",
                    "ionization_mechanism_id": "mech-u",
                },
                False,
            ),
            (
                {
                    "role": "iso_child",
                    "assigned_formula": "C10H16O",
                    "ion_formula": "C10H17O+",
                    "ionization_mechanism_id": "mech-p",
                },
                False,
            ),
        ],
    )
    def test_a_sample_row_reading_the_anchor_s_reading_carries_it(self, row, served):
        anchors, members = uronium_batch()
        records, _ = check(anchors, members)
        h = checked(anchors, records)["h"]
        assert (row_batch_checks(row, h) is not None) is served
        assert row_batch_checks(row, None) is None

    def test_a_reagent_row_naming_the_core_carries_its_twin(self):
        anchors, members, noise = labelled_batch()
        records, _ = check(anchors, members, noise)
        core = checked(anchors, records)["core"]
        row = {
            "role": "reagent",
            "assigned_formula": None,
            "ion_formula": "^NO3-",
            "ionization_mechanism_id": "mech-r",
        }
        assert row_batch_checks(row, core)["label_twin"]["verdict"] == TWIN_PRESENT
        assert row_batch_checks({**row, "ion_formula": "NO3-"}, core) is None
        # A reagent row reads the ion, whatever formula it carries beside it.
        named = {**row, "assigned_formula": "HNO3"}
        assert row_batch_checks(named, core)["label_twin"]["verdict"] == TWIN_PRESENT
        # A row of another role naming the ion is not the reagent's reading.
        assert row_batch_checks({**row, "role": "iso_child"}, core) is None


def _fit(formula, ion="C10H17O+", mechanism="mech-p"):
    return {
        "assigned_formula": formula,
        "ion_formula": ion,
        "role": "M0",
        "fit_score": 0.9,
        "intensity": 1000.0,
        "tier": "assigned",
        "ionization_mechanism_id": mechanism,
    }


RECORD = {
    "formula": "C10H16O",
    "ion_formula": "C10H17O+",
    "ionization_mechanism_id": "mech-p",
    "neutral_pair": {"verdict": PAIR_ESTABLISHED},
}


class TestTheConsensusKeepsTheRecord:
    def test_while_it_names_the_reading(self):
        c = compute_consensus([_fit("C10H16O")], batch_checks=RECORD)
        assert c.provenance[BATCH_CHECKS_KEY] == RECORD

    @pytest.mark.parametrize(
        "member",
        [
            _fit("C10H18O"),
            _fit("C10H16O", ion="C10H19O+"),
            _fit("C10H16O", mechanism="mech-u"),
        ],
    )
    def test_not_once_the_reading_moves(self, member):
        c = compute_consensus([member], batch_checks=RECORD)
        assert BATCH_CHECKS_KEY not in c.provenance

    def test_a_reagent_s_record_names_its_ion(self):
        record = {
            "formula": None,
            "ion_formula": "^NO3-",
            "ionization_mechanism_id": "mech-r",
            "label_twin": {"verdict": TWIN_PRESENT},
        }
        reagent = {
            "assigned_formula": None,
            "ion_formula": "^NO3-",
            "role": "reagent",
            "ionization_mechanism_id": "mech-r",
            "intensity": 1e7,
        }
        c = compute_consensus([reagent], batch_checks=record)
        assert c.provenance[BATCH_CHECKS_KEY] == record

    def test_not_a_record_naming_no_ion(self):
        holder = {"provenance": {BATCH_CHECKS_KEY: {"formula": "C10H16O"}}}
        assert batch_checks_record_of(holder) is None
        assert batch_checks_record_of({"provenance": None}) is None


def test_the_37cl_spacing_is_the_one_the_stamping_predicts():
    (line,) = [
        d
        for d in predict_diagnostic_isotopologues("C4H6ClO2-", 250000.0)
        if d.label == "37Cl"
    ]
    assert line.mz_offset == pytest.approx(CL37_SPACING, abs=1e-6)
    assert line.ratio == pytest.approx(CL_RATIO, abs=1e-3)
    assert BR_OFFSET == pytest.approx(1.997952, abs=1e-6)
    assert BR_RATIO == pytest.approx(0.4931 / 0.5069, abs=1e-3)
