"""The reagent ions' water ladders, measured on a batch's own series (step 4.2j
of the assignment plan).

The grammar - which cores a profile declares, each core's rungs core.(H2O)n and
the isotope lines a rung is read on - and each rule a rung passes on: present
in half a segment's spectra with its core and every rung below, three times as
often as at its decoys, and, on a TOF, no taller at M+1 than a carbon-free
cluster with five carbons' 13C on it. The batch is synthetic: a ladder laid down
in some spectra, decoy peaks beside its rungs, an organic's 13C line under one.
"""

import pytest

from mascope_tools.composition.reagents import (
    FAMILY_AIR,
    FAMILY_REAGENT,
    KIND_ADDUCT,
    MAX_BATCH_WATERS,
    WATER,
    WATER_CORE_LABELS,
    WATER_LADDERS,
    WATER_RUNG_REFERENCES,
    reagent_library,
    water_ladders,
    water_rung_m1_share,
)
from mascope_tools.composition.water_ladder import (
    C13_PER_CARBON,
    C13_SPACING,
    CARBON_TEST_CARBONS,
    DECOY_FLOOR,
    FAILED_ABSENT,
    FAILED_CARBON,
    FAILED_DECOYS,
    ORBITRAP_DECOY_DA,
    PASSED,
    TOF_DECOY_FWHM,
    WINDOW_DRIFT_MARGIN_PPM,
    Partition,
    Segment,
    Trace,
    TraceIndex,
    acquisition_segments,
    decoy_masses,
    ladder_lines,
    read_ladder,
    read_ladders,
    read_rung,
    thresholds,
)


#: One water, in Da.
WATER_MASS = 18.0105646863

#: A TOF batch peak's window: half its line width and the fold's drift margin.
#: The line is then 160 ppm wide, 0.048 Da at m/z 300.
TOF_TOL = 82.0
#: An Orbitrap batch peak's window.
ORBI_TOL = 5.0


def _ladder(profile: str, label: str):
    return next(
        ladder for ladder in water_ladders(profile) if ladder.core.label == label
    )


BROMIDE = _ladder("BR", "[Br]-")


def _spectra(n: int, prefix: str = "s") -> list[str]:
    return [f"{prefix}{i:03d}" for i in range(n)]


def _laid(
    ladder,
    track,
    spectra,
    *,
    top: int,
    tol: float = TOF_TOL,
    height: float = 1e5,
    skip: tuple[int, ...] = (),
    key: str = "",
) -> list[Trace]:
    """A ladder's track laid down in ``spectra``: its core and rungs 1 to
    ``top``, each falling, but the rungs in ``skip``."""
    return [
        Trace(
            f"{key}{ladder.core.label}{track.label}{n}",
            ladder.line_mz(n, track),
            tol,
            {spectrum: height / (n + 1) for spectrum in spectra},
        )
        for n in range(0, top + 1)
        if n not in skip
    ]


def _partition(traces, instrument="tof", ladders=None) -> Partition:
    ladders = ladders if ladders is not None else water_ladders("BR")
    return Partition.of(traces, instrument, ladders)


def _floor(spectra, tol=TOF_TOL, height=1.0):
    """Faint peaks spread over the m/z axis in every spectrum, away from any
    ladder line: a spectrum's local floor, and a TOF's line width wherever a
    rung is read."""
    lines = ladder_lines(water_ladders("BR"))
    traces = []
    mz = 30.5
    i = 0
    while mz < 900:
        if min(abs(mz - line) for line in lines) > 0.6:
            traces.append(
                Trace(f"floor{i}", mz, tol, {spectrum: height for spectrum in spectra})
            )
            i += 1
        mz += 1.37
    return traces


# --- the grammar ------------------------------------------------------------------


def test_each_profile_declares_its_cores_from_its_own_library():
    assert {
        name: [ladder.core.label for ladder in ladders]
        for name, ladders in WATER_LADDERS.items()
    } == {name: list(labels) for name, labels in WATER_CORE_LABELS.items()}
    assert set(WATER_LADDERS) == {"BR", "NO3", "NO3_15N"}
    for name, ladders in WATER_LADDERS.items():
        library = {cluster.label: cluster for cluster in reagent_library(name)}
        for ladder in ladders:
            assert ladder.core is library[ladder.core.label]
    # A positive profile declares none: its hydrates are the pre-pass's.
    assert water_ladders("UR") == ()
    assert water_ladders("EASYIC_POS") == ()
    assert water_ladders("IODIDE") == ()


def test_a_rung_is_its_core_with_n_waters_up_to_45():
    assert MAX_BATCH_WATERS == 45
    for ladders in WATER_LADDERS.values():
        for ladder in ladders:
            assert len(ladder.rungs) == MAX_BATCH_WATERS
            for waters in (1, 2, 16, 45):
                rung = ladder.rung(waters)
                assert rung.charge == ladder.core.charge
                assert rung.mz == pytest.approx(
                    ladder.core.mz + waters * WATER_MASS, abs=1e-6
                )
    assert BROMIDE.rung(0) is BROMIDE.core


def test_a_rung_the_library_names_is_the_librarys_ion_with_its_works():
    assert BROMIDE.rung(1).label == "[Br+H2O]-"
    assert BROMIDE.rung(1).references == ("san16", "ris19", "wa21")
    assert BROMIDE.rung(2).label == "[Br+2xH2O]-"
    nitrite = _ladder("NO3", "[NO2]-")
    assert nitrite.rung(1).family == FAMILY_AIR
    assert nitrite.rung(1).references


def test_a_rung_no_work_names_says_what_shows_it():
    rung = BROMIDE.rung(16)
    assert rung.label == "[Br+16xH2O]-"
    assert rung.formula == "BrH32O16"
    assert rung.kind == KIND_ADDUCT
    assert rung.family == FAMILY_REAGENT
    assert rung.references == ()
    assert rung.observed.startswith("no work found names it")
    assert "water ladder" in rung.observed
    acid = _ladder("NO3", "[NO3+2xHNO3]-")
    assert acid.rung(3).label == "[NO3+2xHNO3+3xH2O]-"
    # One water is written without a count, as the library writes its own.
    assert _ladder("BR", "[Br2]-").rung(1).label == "[Br2+H2O]-"
    assert _ladder("BR", "[Br2]-").rung(1).references == ()
    assert _ladder("NO3", "[NO2]-").rung(20).family == FAMILY_AIR


def test_the_labelled_ladder_is_labelled_throughout():
    core = _ladder("NO3_15N", "[^NO3+H^NO3]-")
    assert core.rung(5).label == "[^NO3+H^NO3+5xH2O]-"
    assert "^N2" in core.rung(5).formula
    assert core.purity == pytest.approx(0.98)
    assert core.rung(5).mz > _ladder("NO3", "[NO3+HNO3]-").rung(5).mz + 1.9


def test_a_track_is_each_bright_line_of_the_cores_envelope():
    assert [(t.label, round(t.offset, 3)) for t in BROMIDE.tracks] == [
        ("M0", 0.0),
        ("81Br", 1.998),
    ]
    trimer = _ladder("BR", "[Br3]-")
    assert [t.label for t in trimer.tracks] == ["M0", "81Br", "81Br2", "81Br3"]
    # The monoisotopic line is the trimer's faintest, and a track all the same.
    assert trimer.tracks[1].share == pytest.approx(2.92, abs=0.02)
    assert [t.label for t in _ladder("NO3", "[NO3]-").tracks] == ["M0"]
    assert BROMIDE.line_mz(16, BROMIDE.tracks[1]) == pytest.approx(
        BROMIDE.rung(16).mz + 1.99795, abs=1e-4
    )


def test_a_rungs_own_m1_is_its_hydrogens_and_oxygens_only():
    # A carbon-free cluster: 2H and 17O, about 1 % at 16 waters.
    share = BROMIDE.m1_share(16, BROMIDE.tracks[0])
    assert 0.005 < share < 0.02
    assert BROMIDE.m1_share(45, BROMIDE.tracks[0]) > share
    assert water_rung_m1_share(BROMIDE.rung(16), BROMIDE.tracks[1]) == pytest.approx(
        share, rel=0.05
    )


def test_the_grammar_cites_the_water_rule():
    assert WATER_RUNG_REFERENCES == ("good70", "sun88")
    assert WATER == "H2O"
    assert thresholds()["references"] == ["good70", "sun88"]


# --- the segments -----------------------------------------------------------------


def test_one_stretch_of_acquisition_is_one_segment():
    spectra = _spectra(30)
    segments = acquisition_segments({s: 600.0 * i for i, s in enumerate(spectra)})
    assert [len(s.spectra) for s in segments] == [30]
    assert segments[0].start == 0.0 and segments[0].end == 600.0 * 29


def test_a_gap_over_an_hour_but_under_5x_the_spacing_does_not_cut():
    # Forty minutes apart, two hours between the halves: over an hour, under
    # five times the spacing (3 h 20 min).
    times = {f"a{i:02d}": 2400.0 * i for i in range(12)}
    times.update({f"b{i:02d}": 2400.0 * 11 + 7200.0 + 2400.0 * i for i in range(12)})
    assert [len(s.spectra) for s in acquisition_segments(times)] == [24]
    # Past five times the spacing it does.
    times = {f"a{i:02d}": 2400.0 * i for i in range(12)}
    times.update({f"b{i:02d}": 2400.0 * 11 + 13000.0 + 2400.0 * i for i in range(12)})
    assert [len(s.spectra) for s in acquisition_segments(times)] == [12, 12]


def test_a_restart_cuts_the_batch_where_the_gap_passes_an_hour_and_5x_the_spacing():
    times = {f"a{i:02d}": 600.0 * i for i in range(20)}
    times.update({f"b{i:02d}": 600.0 * 19 + 7200.0 + 600.0 * i for i in range(15)})
    segments = acquisition_segments(times)
    assert [len(s.spectra) for s in segments] == [20, 15]
    assert segments[1].spectra[0] == "b00"
    # Under an hour does not cut, however long against the spacing.
    times = {f"a{i:02d}": 60.0 * i for i in range(20)}
    times.update({f"b{i:02d}": 60.0 * 19 + 3000.0 + 60.0 * i for i in range(15)})
    assert len(acquisition_segments(times)) == 1
    # Nor does a gap under five times the median spacing, however long.
    times = {f"s{i:02d}": 7200.0 * i for i in range(30)}
    times["late"] = 7200.0 * 29 + 4 * 7200.0
    assert len(acquisition_segments(times)) == 1


def test_a_short_segment_joins_its_nearer_neighbour():
    times = {f"a{i:02d}": 600.0 * i for i in range(20)}
    end_a = 600.0 * 19
    # Three spectra four hours after the first stretch, then two hours to the third.
    times.update({f"m{i}": end_a + 4 * 3600.0 + 600.0 * i for i in range(3)})
    start_c = end_a + 4 * 3600.0 + 1200.0 + 2 * 3600.0
    times.update({f"c{i:02d}": start_c + 600.0 * i for i in range(12)})
    segments = acquisition_segments(times)
    assert [len(s.spectra) for s in segments] == [20, 15]
    assert segments[1].spectra[:3] == ("m0", "m1", "m2")


def test_spectra_with_no_time_are_a_segment_of_their_own():
    times = {f"s{i:02d}": 600.0 * i for i in range(12)}
    times.update({"x1": None, "x0": None})
    segments = acquisition_segments(times)
    assert [s.spectra for s in segments][1] == ("x0", "x1")
    assert segments[1].start is None
    assert [s.index for s in segments] == [0, 1]
    assert acquisition_segments({}) == []


# --- presence and contiguity ------------------------------------------------------


def test_a_ladder_passes_every_rung_present_with_its_core():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=8) + _floor(spectra)
    reading = read_ladder(
        BROMIDE, BROMIDE.tracks[0], _partition(traces), Segment(0, tuple(spectra))
    )
    assert reading.core_presence == 1.0
    assert [r.waters for r in reading.passed] == list(range(1, 9))
    assert reading.reach == 8
    # The ladder ends at its first absent rung, which is read and recorded.
    assert reading.rungs[-1].waters == 9
    assert reading.rungs[-1].verdict == FAILED_ABSENT
    assert reading.rungs[0].keys == ("[Br]-M01",)


def test_a_rung_above_a_gap_is_not_climbed_to():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=8, skip=(4,))
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert [r.waters for r in reading.rungs] == [1, 2, 3, 4]
    assert reading.reach == 3


def test_a_rung_in_under_half_the_spectra_ends_the_ladder():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=3)
    traces += _laid(BROMIDE, BROMIDE.tracks[0], spectra[:9], top=4, key="x")[4:]
    traces += _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=6, key="y")[5:]
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert reading.rungs[3].presence == pytest.approx(0.45)
    assert reading.rungs[3].verdict == FAILED_ABSENT
    assert reading.reach == 3
    # Half exactly is present.
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=3)
    traces += _laid(BROMIDE, BROMIDE.tracks[0], spectra[:10], top=4, key="x")[4:]
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert reading.rungs[3].verdict == PASSED


def test_no_rung_is_read_where_the_core_is_absent():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=6)[1:]
    traces += _laid(BROMIDE, BROMIDE.tracks[0], spectra[:9], top=0, key="c")
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert reading.core_presence == pytest.approx(0.45)
    assert reading.rungs == ()
    assert reading.reach == 0


def test_a_ladder_is_read_per_segment():
    dry = _spectra(15, "d")
    humid = _spectra(15, "h")
    traces = _laid(BROMIDE, BROMIDE.tracks[0], dry + humid, top=4)
    traces += _laid(BROMIDE, BROMIDE.tracks[0], humid, top=12, key="h")[5:]
    partition = _partition(traces + _floor(dry + humid))
    segments = [Segment(0, tuple(dry)), Segment(1, tuple(humid))]
    readings = read_ladders([BROMIDE], partition, segments)
    by_segment = {r.segment.index: r.reach for r in readings if r.track.label == "M0"}
    assert by_segment == {0: 4, 1: 12}
    # Over the whole batch the high rungs are in half the spectra.
    whole = read_ladder(
        BROMIDE, BROMIDE.tracks[0], partition, Segment(0, tuple(dry + humid))
    )
    assert whole.reach == 12


def test_each_track_is_its_own_ladder():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=10)
    traces += _laid(BROMIDE, BROMIDE.tracks[1], spectra, top=6)
    readings = read_ladders(
        [BROMIDE], _partition(traces + _floor(spectra)), [Segment(0, tuple(spectra))]
    )
    assert {r.track.label: r.reach for r in readings} == {"M0": 10, "81Br": 6}


def test_a_peak_is_at_a_trace_only_within_that_traces_own_window():
    line = 300.0
    narrow = Trace("narrow", line * (1 + 20e-6), 5.0, {"s": 1.0})
    wide = Trace("wide", 310.0, 82.0, {"s": 1.0})
    index = TraceIndex([narrow, wide])
    # 20 ppm off: inside the widest window the index holds, outside its own.
    assert index.at(line) == []
    assert index.at(line * (1 + 18e-6)) == [narrow]
    assert index.presence(line, ["s"]) == 0.0


def test_a_line_width_is_twice_the_window_less_the_folds_margin():
    traces = [Trace("t", 300.0, TOF_TOL, {"s": 1.0})]
    partition = Partition.of(traces, "tof", ())
    assert partition.fwhm_da(300.0) == pytest.approx(
        2 * (TOF_TOL - WINDOW_DRIFT_MARGIN_PPM) * 300e-6
    )
    assert partition.window_da(300.0) == pytest.approx(TOF_TOL * 300e-6)


def test_a_split_trace_counts_both_halves():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=2)
    line = BROMIDE.line_mz(3, BROMIDE.tracks[0])
    traces.append(
        Trace("half-a", line * (1 + 30e-6), TOF_TOL, {s: 10.0 for s in spectra[:6]})
    )
    traces.append(
        Trace("half-b", line * (1 - 30e-6), TOF_TOL, {s: 10.0 for s in spectra[6:12]})
    )
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    rung = reading.rungs[2]
    assert rung.presence == pytest.approx(0.6)
    assert set(rung.keys) == {"half-a", "half-b"}


# --- the decoys -------------------------------------------------------------------


def _with_decoys(spectra, waters, offsets, present, tol=TOF_TOL):
    line = BROMIDE.line_mz(waters, BROMIDE.tracks[0])
    traces = []
    for k, offset in enumerate(offsets):
        for sign in (-1, 1):
            traces.append(
                Trace(
                    f"decoy{k}{sign}",
                    line + sign * offset,
                    tol,
                    {s: 5.0 for s in spectra[:present]},
                )
            )
    return traces


def test_a_rung_no_more_present_than_its_decoys_fails():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=6)
    width = (
        2
        * (TOF_TOL - WINDOW_DRIFT_MARGIN_PPM)
        * 1e-6
        * BROMIDE.line_mz(4, BROMIDE.tracks[0])
    )
    # Decoys in half the spectra: the rung, in all, is twice as present.
    traces += _with_decoys(spectra, 4, [k * width for k in TOF_DECOY_FWHM], 10)
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    rung = reading.rungs[3]
    assert rung.verdict == FAILED_DECOYS
    assert rung.decoys == 12
    assert rung.decoy_presence == pytest.approx(0.5)
    # A failing rung does not end the ladder: the rungs above it are read.
    assert reading.reach == 6
    assert [r.waters for r in reading.passed] == [1, 2, 3, 5, 6]


def test_a_rung_three_times_its_decoys_passes():
    spectra = _spectra(30)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=6)
    width = (
        2
        * (TOF_TOL - WINDOW_DRIFT_MARGIN_PPM)
        * 1e-6
        * BROMIDE.line_mz(4, BROMIDE.tracks[0])
    )
    traces += _with_decoys(spectra, 4, [k * width for k in TOF_DECOY_FWHM], 10)
    rung = read_rung(
        BROMIDE,
        BROMIDE.tracks[0],
        4,
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert rung.decoy_presence == pytest.approx(1 / 3)
    assert rung.verdict == PASSED


def test_an_orbitraps_decoys_sit_at_fixed_masses():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=6, tol=ORBI_TOL)
    traces += _with_decoys(spectra, 4, ORBITRAP_DECOY_DA, 10, tol=ORBI_TOL)
    partition = _partition(traces + _floor(spectra, tol=ORBI_TOL), instrument="orbi")
    rung = read_rung(
        BROMIDE, BROMIDE.tracks[0], 4, partition, Segment(0, tuple(spectra))
    )
    assert rung.decoys == 6
    assert rung.verdict == FAILED_DECOYS
    # The TOF's width-scaled decoys miss the Orbitrap's.
    tof = _partition(traces + _floor(spectra, tol=ORBI_TOL), instrument="tof")
    assert read_rung(
        BROMIDE, BROMIDE.tracks[0], 4, tof, Segment(0, tuple(spectra))
    ).decoy_presence == pytest.approx(DECOY_FLOOR)
    # An Orbitrap reads no carbon test.
    assert rung.carbon is None


def test_an_orbitrap_rung_is_not_asked_for_its_carbon():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=8, tol=ORBI_TOL)
    # A line one unit up a third of the rung's: an organic's on a TOF.
    line = BROMIDE.line_mz(6, BROMIDE.tracks[0])
    traces.append(
        Trace("m1", line + C13_SPACING, ORBI_TOL, {s: 0.33 * 1e5 / 7 for s in spectra})
    )
    partition = _partition(traces + _floor(spectra, tol=ORBI_TOL), instrument="orbi")
    rung = read_rung(
        BROMIDE, BROMIDE.tracks[0], 6, partition, Segment(0, tuple(spectra))
    )
    assert rung.carbon is None
    assert rung.verdict == PASSED


def test_a_decoy_at_another_ladders_line_is_not_read():
    line = BROMIDE.line_mz(4, BROMIDE.tracks[0])
    partition = _partition([], instrument="orbi")
    plain = decoy_masses(line, partition, line)
    assert len(plain) == 6
    other = line + ORBITRAP_DECOY_DA[1]
    partition = Partition(TraceIndex([]), "orbi", tuple(sorted((line, other))))
    decoys = decoy_masses(line, partition, line)
    assert len(decoys) == 5
    assert all(abs(d - other) > 1e-6 for d in decoys)
    # Its own line is not another's.
    partition = Partition(TraceIndex([]), "orbi", (line,))
    assert len(decoy_masses(line, partition, line)) == 6


def test_the_decoys_mean_is_floored():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=3)
    rung = read_rung(
        BROMIDE,
        BROMIDE.tracks[0],
        2,
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert rung.decoy_presence == DECOY_FLOOR


# --- the carbon test --------------------------------------------------------------


def _m1(spectra, waters, ratio, height):
    line = BROMIDE.line_mz(waters, BROMIDE.tracks[0])
    return Trace(
        "m1", line + C13_SPACING, TOF_TOL, {s: ratio * height for s in spectra}
    )


def test_a_tof_rung_with_an_organics_13c_line_fails():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=8)
    height = 1e5 / 7
    # A C30 organic's 13C line: a third of the line.
    traces.append(_m1(spectra, 6, 0.33, height))
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    rung = reading.rungs[5]
    assert rung.verdict == FAILED_CARBON
    assert rung.carbon.asked
    assert rung.carbon.spectra == 20
    assert rung.carbon.ratio == pytest.approx(0.33)
    assert rung.carbon.bar == pytest.approx(
        BROMIDE.m1_share(6, BROMIDE.tracks[0]) + CARBON_TEST_CARBONS * C13_PER_CARBON
    )
    assert reading.reach == 8


def test_a_tof_rung_showing_its_own_m1_is_carbon_free():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=8)
    traces.append(_m1(spectra, 6, 0.02, 1e5 / 7))
    rung = read_rung(
        BROMIDE,
        BROMIDE.tracks[0],
        6,
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert rung.carbon.asked and rung.carbon.ratio == pytest.approx(0.02)
    assert rung.verdict == PASSED


def test_the_carbon_test_reads_only_a_rung_bright_enough_to_show_it():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=8, height=10.0)
    traces.append(_m1(spectra, 6, 0.33, 10.0 / 7))
    # The floor stands at 1: a C5 line under a rung of 1.4 is 0.08, unseen.
    rung = read_rung(
        BROMIDE,
        BROMIDE.tracks[0],
        6,
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert not rung.carbon.asked
    assert rung.verdict == PASSED


def test_the_carbon_test_is_not_asked_where_m1_blends_with_another_ladder():
    spectra = _spectra(20)
    # (HNO3)2.NO3-'s ladder: its rung n - 6 sits 6 mDa from Br-.(H2O)n's M+1.
    acid = _ladder("NO3", "[NO3+2xHNO3]-")
    ladders = water_ladders("BR") + (acid,)
    above = BROMIDE.line_mz(10, BROMIDE.tracks[0]) + C13_SPACING
    blend = acid.line_mz(4, acid.tracks[0])
    assert abs(blend - above) < 0.007
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=12)
    # An organic's 13C-sized line at the M+1, where the acid rung sits too.
    traces.append(_m1(spectra, 10, 0.33, 1e5 / 11))
    shown = _laid(acid, acid.tracks[0], spectra, top=4)
    floor = _floor(spectra)
    rung = read_rung(
        BROMIDE,
        BROMIDE.tracks[0],
        10,
        Partition.of(traces + shown + floor, "tof", ladders),
        Segment(0, tuple(spectra)),
    )
    assert not rung.carbon.asked
    assert rung.carbon.blend == pytest.approx(blend)
    assert rung.verdict == PASSED
    # The acid's ladder absent from the segment: no blend, the test reads.
    rung = read_rung(
        BROMIDE,
        BROMIDE.tracks[0],
        10,
        Partition.of(traces + floor, "tof", ladders),
        Segment(0, tuple(spectra)),
    )
    assert rung.carbon.asked
    assert rung.verdict == FAILED_CARBON


def test_the_readings_serialise():
    spectra = _spectra(20)
    traces = _laid(BROMIDE, BROMIDE.tracks[0], spectra, top=2)
    reading = read_ladder(
        BROMIDE,
        BROMIDE.tracks[0],
        _partition(traces + _floor(spectra)),
        Segment(0, tuple(spectra)),
    )
    assert reading.to_json() == {
        "core": "[Br]-",
        "track": "M0",
        "segment": 0,
        "core_presence": 1.0,
        "tested": 3,
        "passed": 2,
        "reach": 2,
    }
    rung = reading.rungs[0].to_json()
    assert rung["verdict"] == PASSED
    assert rung["carbon"]["asked"] is True
    assert reading.rungs[2].to_json()["decoy_presence"] is None
