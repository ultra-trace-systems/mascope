"""The lines a run fits its envelope tolerance on, and the level it reaches.

``envelope_fit.envelope_lines`` reads Stage A's gated, fit-scored frame for the
envelopes Stage A committed: a list's reading whose monoisotopic line sits
inside the mass gate's cap, and of its lines those that track the ion's own,
read as the score reads them. ``fit_run_envelope_tolerance`` fits on them, and
on a thin Orbitrap file pools the lines of its nearest files in acquisition
time that share its acquisition settings, each read fresh
(``envelope_fit.read_pool``), never from a run of theirs. The batch and the
settings live in the database and the file's ``.props``; they are stubbed here
and read for real in the integration suite. A decoy run fits nothing and scores
at its control's (``envelope_fit.inherited_envelope_fit``).
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import numpy as np
import pandas as pd
import pytest

from mascope_backend.api.controllers.target.lib.compute.target_ions_compute import (
    LEAD_ISOTOPE_FORMULA,
)
from mascope_backend.api.new.peak_assignments import envelope_fit
from mascope_backend.api.new.peak_assignments.engine import (
    ROLE_ISO_CHILD,
    ROLE_M0,
    SOURCE_DATABASE,
    SOURCE_UNTARGETED,
    SampleMassAccuracy,
)
from mascope_backend.api.new.peak_assignments.envelope_fit import (
    POOL_NEIGHBOURS,
    EnvelopePool,
    PooledFile,
    envelope_lines,
    fit_run_envelope_tolerance,
    form_for,
    inherited_envelope_fit,
    is_carbon13_line,
    read_pool,
)
from mascope_tools.composition.envelope_tolerance import (
    DEFAULT_TOLERANCE,
    FORM_CENTRE_WIDTH,
    FORM_CURVE,
    FORM_DEFAULT,
    LEVEL_DEFAULT,
    LEVEL_OWN,
    LEVEL_POOLED,
    EnvelopeLines,
    EnvelopeTolerance,
    fit_envelope_tolerance,
)
from mascope_tools.composition.measurement_model import MeasurementModel
from mascope_tools.composition.profiles import INSTRUMENT_FALLBACK_SIGMA_PPM


ORBI = INSTRUMENT_FALLBACK_SIGMA_PPM["orbi"]
TOF = INSTRUMENT_FALLBACK_SIGMA_PPM["tof"]
ANCHOR_INTENSITY = 1e5


def _ion(
    ion_id: str,
    lines: list[tuple[str, float, float | None, float]],
    *,
    ion_formula: str = "C10H15O4-",
    anchor_snr: float = 150.0,
    mechanism: str = "m-h",
) -> list[dict]:
    """One ion's rows of a gated, fit-scored Stage A frame.

    :param lines: ``(isotopologue formula, abundance, ratio, mass error)``
        per line, the first the monoisotopic one. The ratio is the line's
        observed share over its predicted one, None for a line the frame holds
        no intensity for; the most abundant line is the anchor and reads 1.
    """
    anchor = max(abundance for _, abundance, _, _ in lines)
    rows = []
    for position, (formula, abundance, ratio, error) in enumerate(lines):
        share = abundance / anchor
        observed = 0.0 if ratio is None else ANCHOR_INTENSITY * share * ratio
        rows.append(
            {
                "target_ion_id": ion_id,
                "target_ion_formula": ion_formula,
                "target_isotope_formula": formula,
                "ionization_mechanism_id": mechanism,
                "mz": 199.0976 + position,
                "relative_abundance": abundance,
                "sample_peak_id": f"{ion_id}-{position}" if ratio is not None else "",
                "sample_peak_intensity": observed,
                "signal_to_noise": anchor_snr * share * (ratio or 0.0),
                "match_mz_error": error if ratio is not None else np.nan,
            }
        )
    return rows


def _committed(frame: pd.DataFrame, source: str = SOURCE_DATABASE) -> list[dict]:
    """The rows Stage A would have built: each ion's monoisotopic row committed."""
    rows = []
    for _, ion in frame.groupby("target_ion_id", sort=False):
        mono = ion.iloc[0]
        rows.append(
            {
                "role": ROLE_M0,
                "source": source,
                "assigned_formula": "C10H14O4",
                "sample_peak_id": mono["sample_peak_id"],
                "ion_formula": mono["target_ion_formula"],
                "ionization_mechanism_id": mono["ionization_mechanism_id"],
            }
        )
    return rows


def _lines(
    frame: pd.DataFrame, rows=None, *, form=FORM_CURVE, **kwargs
) -> EnvelopeLines:
    return envelope_lines(
        frame,
        _committed(frame) if rows is None else rows,
        model=MeasurementModel.v1(
            kwargs.pop("mass_accuracy", SampleMassAccuracy()),
            kwargs.pop("fallback_sigma_ppm", ORBI),
        ),
        form=form,
    )


#: A C10 ion: M0, a 13C line 15% under its share, an 18O line at its share.
C10 = [
    ("C10H15O4-", 1.0, 1.0, 0.1),
    ("C9[13C]H15O4-", 0.11, 0.85, 0.15),
    ("C10H15O3[18O]-", 0.008, 1.0, 0.1),
]


class TestTheLinesRead:
    def test_a_committed_list_envelope_s_13c_line(self):
        frame = pd.DataFrame(_ion("ion-1", C10))

        lines = _lines(frame)

        # The 13C line, as the score reads it: its intensity over the anchor's
        # against its share, at its share of the anchor's S/N.
        np.testing.assert_allclose(lines.ratio, [0.85])
        np.testing.assert_allclose(lines.expected_snr, [0.11 * 150.0])

    def test_every_line_on_a_tof(self):
        frame = pd.DataFrame(_ion("ion-1", C10))

        lines = _lines(frame, form=FORM_CENTRE_WIDTH, fallback_sigma_ppm=TOF)

        np.testing.assert_allclose(lines.ratio, [0.85, 1.0])

    def test_nothing_on_a_class_with_no_form(self):
        frame = pd.DataFrame(_ion("ion-1", C10))

        assert _lines(frame, form=FORM_DEFAULT).expected_snr.size == 0

    def test_a_line_the_frame_holds_no_intensity_for_counts_as_absent(self):
        # No peak, or one the match parameters' ratio window turned away: the
        # scorer sees it absent, and so does the line's bin.
        absent = [C10[0], ("C9[13C]H15O4-", 0.11, None, np.nan)]
        frame = pd.DataFrame(_ion("ion-1", absent))

        lines = _lines(frame)

        assert (lines.lines, lines.absent) == (0, 1)
        np.testing.assert_allclose(lines.expected_snr, [0.11 * 150.0])

    def test_an_absent_line_below_the_first_bin_is_not_kept(self):
        # Expected at 0.11 x 20 = 2.2: no bin reads it and nothing charges it.
        absent = [C10[0], ("C9[13C]H15O4-", 0.11, None, np.nan)]
        frame = pd.DataFrame(_ion("ion-1", absent, anchor_snr=20.0))

        assert _lines(frame).expected_snr.size == 0

    def test_a_present_one_is(self):
        # A TOF reads every present line's share, whatever its S/N says.
        frame = pd.DataFrame(_ion("ion-1", C10, anchor_snr=20.0))

        lines = _lines(frame, form=FORM_CENTRE_WIDTH, fallback_sigma_ppm=TOF)

        np.testing.assert_allclose(lines.expected_snr, [2.2, 20.0 * 0.008])

    def test_a_satellite_is_absent(self):
        frame = pd.DataFrame(_ion("ion-1", C10))
        frame["is_satellite"] = [False, True, False]

        lines = _lines(frame)

        assert (lines.lines, lines.absent) == (0, 1)

    def test_a_line_that_does_not_track_its_ion_s_is_not_read(self):
        # 1.2 ppm off a line 0.1 ppm off: past three of the class's 0.3 ppm.
        untracked = [C10[0], ("C9[13C]H15O4-", 0.11, 0.6, 1.3)]
        frame = pd.DataFrame(_ion("ion-1", untracked))

        lines = _lines(frame)

        assert lines.expected_snr.size == 0

    def test_the_monoisotopic_line_is_read_against_the_anchor(self):
        # A bromine pair: the anchor is 79Br81Br, the M0 79Br2 below it. Every
        # other line tracks the M0; the M0 itself is read against the anchor,
        # which sits 50 ppm off it, so it does not track.
        dibromide = [
            ("Br2-", 0.51, 0.9, 0.0),
            ("[81Br]Br-", 1.0, 1.0, 50.0),
            ("[81Br]2-", 0.49, 0.95, 0.5),
        ]
        frame = pd.DataFrame(_ion("ion-1", dibromide, ion_formula="Br2-"))

        lines = _lines(frame, form=FORM_CENTRE_WIDTH, fallback_sigma_ppm=TOF)

        np.testing.assert_allclose(lines.ratio, [0.95])

    def test_a_labelled_ion_s_13c_line_is_read(self):
        # 15N-nitrate's label is on every line of its ion, the 13C one too.
        labelled = [
            ("[15N]C4H5O5-", 1.0, 1.0, 0.1),
            ("[15N]C3[13C]H5O5-", 0.045, 0.9, 0.12),
            ("C4H5O5-", 0.02, 1.0, 0.1),
        ]
        frame = pd.DataFrame(_ion("ion-1", labelled, ion_formula="C4H5^NO5-"))

        np.testing.assert_allclose(_lines(frame).ratio, [0.9])

    def test_nothing_where_the_anchor_found_no_peak(self):
        dibromide = [
            ("Br2-", 0.51, 0.9, 0.0),
            ("[81Br]Br-", 1.0, None, np.nan),
            ("[81Br]2-", 0.49, 0.95, 0.0),
        ]
        frame = pd.DataFrame(_ion("ion-1", dibromide, ion_formula="Br2-"))

        lines = _lines(frame, form=FORM_CENTRE_WIDTH, fallback_sigma_ppm=TOF)

        assert lines.expected_snr.size == 0

    def test_nothing_where_the_anchor_was_gated_out(self):
        # The scorer zeroes a gated line's intensity and leaves its S/N: an
        # anchor so turned away anchors nothing, and its ion's absent lines are
        # not counted either.
        frame = pd.DataFrame(
            _ion("ion-1", [C10[0], ("C9[13C]H15O4-", 0.11, None, np.nan)])
        )
        frame.loc[0, "sample_peak_intensity"] = 0.0

        assert _lines(frame).expected_snr.size == 0

    def test_a_frame_with_no_signal_to_noise_reads_nothing(self):
        # Every line's place on the curve is its share of the anchor's S/N.
        frame = pd.DataFrame(_ion("ion-1", C10)).drop(columns="signal_to_noise")

        assert _lines(frame, form=FORM_CENTRE_WIDTH).expected_snr.size == 0

    def test_a_labelled_ion_s_13c_line(self):
        assert is_carbon13_line("[15N]C3[13C]H5O5-", {"[15N]": 1})
        assert not is_carbon13_line("C4H5O5-", {"[15N]": 1})
        assert not is_carbon13_line("[15N]C4H5O5-", {"[15N]": 1})
        assert is_carbon13_line("C8[13C]2H15O4-")
        assert not is_carbon13_line("C9[13C]H15O3[18O]-")
        assert is_carbon13_line("C9[13C]H15O4-/C9[13C]H15O4-")
        assert not is_carbon13_line("C9[13C]H15O4-/C10H14[2H]O4-")
        assert not is_carbon13_line(None)


class TestTheEnvelopesRead:
    def test_only_what_stage_a_committed(self):
        frame = pd.DataFrame(_ion("ion-1", C10) + _ion("ion-2", C10, mechanism="m-2"))
        rows = _committed(frame)[:1]

        assert _lines(frame, rows).ratio.size == 1

    def test_not_the_search_s(self):
        frame = pd.DataFrame(_ion("ion-1", C10))

        assert _lines(frame, _committed(frame, SOURCE_UNTARGETED)).ratio.size == 0

    def test_not_a_row_that_names_no_formula(self):
        frame = pd.DataFrame(_ion("ion-1", C10))
        rows = _committed(frame)
        rows[0]["assigned_formula"] = None

        assert _lines(frame, rows).ratio.size == 0

    def test_not_an_isotopologue_row(self):
        frame = pd.DataFrame(_ion("ion-1", C10))
        rows = _committed(frame)
        rows[0]["role"] = ROLE_ISO_CHILD

        assert _lines(frame, rows).ratio.size == 0

    def test_not_one_beyond_the_mass_gate_s_cap(self):
        # The monoisotopic line 1.0 ppm out, at a width of 0.3 widened by the
        # prediction term to 0.58: under the gate's three widths. At 2 ppm
        # out it is past them.
        near = [("C10H15O4-", 1.0, 1.0, 1.0), ("C9[13C]H15O4-", 0.11, 0.85, 1.0)]
        far = [("C10H15O4-", 1.0, 1.0, 2.0), ("C9[13C]H15O4-", 0.11, 0.85, 2.0)]

        assert _lines(pd.DataFrame(_ion("ion-1", near))).ratio.size == 1
        assert _lines(pd.DataFrame(_ion("ion-1", far))).ratio.size == 0

    def test_the_gate_is_about_the_centre_stage_a_scored_at(self):
        far = [("C10H15O4-", 1.0, 1.0, 2.0), ("C9[13C]H15O4-", 0.11, 0.85, 2.0)]
        centred = SampleMassAccuracy(mu_ppm=1.9, sigma_ppm=0.2, anchors=12)

        lines = _lines(pd.DataFrame(_ion("ion-1", far)), mass_accuracy=centred)

        assert lines.ratio.size == 1

    def test_not_one_whose_anchor_carries_no_snr(self):
        frame = pd.DataFrame(_ion("ion-1", C10))
        frame.loc[0, "signal_to_noise"] = np.nan

        assert _lines(frame).ratio.size == 0

    def test_one_envelope_once_however_many_lists_name_it(self):
        frame = pd.DataFrame(_ion("ion-1", C10) + _ion("ion-1-mirror", C10))
        frame.loc[frame["target_ion_id"] == "ion-1-mirror", "sample_peak_id"] = list(
            frame.loc[frame["target_ion_id"] == "ion-1", "sample_peak_id"]
        )

        assert _lines(frame, _committed(frame)[:1]).ratio.size == 1

    def test_a_13c_line_merged_at_the_files_resolution_is_read_by_its_lead(self):
        """At an Orbitrap's resolving power the 13C line holds its 17O line,
        and the run names it by both (MAS-977). It is still the 13C line the
        curve is fitted on: its brightest member, which the frame names in
        ``lead_isotope_formula``. An 18O line that holds a fainter 13C2 line
        is not one."""
        merged = [
            ("C10H15O4-", 1.0, 1.0, 0.1),
            ("C9[13C]H15O4-/C10H15O3[17O]-", 0.112, 0.85, 0.15),
            ("C8[13C]2H15O4-/C10H15O3[18O]-", 0.013, 1.0, 0.1),
        ]
        frame = pd.DataFrame(_ion("ion-1", merged))

        assert _lines(frame).ratio.size == 0
        frame[LEAD_ISOTOPE_FORMULA] = [
            "C10H15O4-",
            "C9[13C]H15O4-",
            "C10H15O3[18O]-",
        ]
        assert _lines(frame).ratio.tolist() == pytest.approx([0.85])

    def test_a_frame_without_isotopologue_formulas_reads_no_curve(self):
        frame = pd.DataFrame(_ion("ion-1", C10)).drop(columns="target_isotope_formula")

        assert _lines(frame).ratio.size == 0
        assert _lines(frame, form=FORM_CENTRE_WIDTH).ratio.size == 2


def test_the_form_follows_the_instrument_class():
    assert (form_for("orbi"), form_for("tof"), form_for(None), form_for("x")) == (
        FORM_CURVE,
        FORM_CENTRE_WIDTH,
        FORM_DEFAULT,
        FORM_DEFAULT,
    )


def _many(count: int, ratio: float = 0.8) -> pd.DataFrame:
    """``count`` committed ions, each with a tracking 13C line at ``ratio``."""
    rows = []
    for index in range(count):
        rows += _ion(
            f"ion-{index}",
            [C10[0], ("C9[13C]H15O4-", 0.11, ratio, 0.15)],
            mechanism=f"m-{index}",
        )
    return pd.DataFrame(rows)


class _Sample:
    sample_item_id = "si-1"
    sample_item_name = "Sample One"
    sample_batch_id = "sb-1"
    polarity = "-"
    datetime_utc = datetime(2026, 7, 4, 12, 0, tzinfo=timezone.utc)

    def __init__(self, filename):
        self.filename = filename


SETTINGS = {"polarity": "-", "microscans": 10.0, "resolution": 120000.0}
OTHER_SETTINGS = {"polarity": "-", "microscans": 100.0, "resolution": 120000.0}
T0 = _Sample.datetime_utc


def _pool(*counts: int) -> EnvelopePool:
    """A pool of one neighbour per count, each with that many lines at 0.7 in
    the list and one predicted line absent."""
    return EnvelopePool(
        files=tuple(
            PooledFile(
                f"si-n{index}",
                T0 + timedelta(minutes=10 * (index + 1)),
                EnvelopeLines(
                    np.r_[np.full(count, 15.0), 12.0],
                    np.r_[np.full(count, 0.7), np.nan],
                ),
            )
            for index, count in enumerate(counts)
        ),
        span=(T0, T0 + timedelta(minutes=10 * len(counts))),
    )


async def _never_read(neighbour):
    raise AssertionError("the pool reads its neighbours through read_pool")


@pytest.mark.asyncio
class TestTheRunsLevel:
    @staticmethod
    async def _fit(frame, filename="orbi-sample.raw", *, settings=SETTINGS, pool=None):
        with (
            patch.object(
                envelope_fit,
                "acquisition_settings",
                AsyncMock(return_value=settings),
            ) as read_settings,
            patch.object(
                envelope_fit,
                "read_pool",
                AsyncMock(return_value=pool if pool is not None else EnvelopePool()),
            ) as read_neighbours,
        ):
            fit = await fit_run_envelope_tolerance(
                _Sample(filename),
                frame,
                _committed(frame),
                model=MeasurementModel.v1(
                    SampleMassAccuracy(),
                    ORBI if filename.endswith(".raw") else TOF,
                ),
                read_neighbour=_never_read,
            )
        return fit, read_settings, read_neighbours

    async def test_an_orbitrap_file_with_lines_enough_fits_its_own_curve(self):
        fit, read_settings, read_neighbours = await self._fit(_many(25))

        assert (fit.fit.form, fit.fit.level) == (FORM_CURVE, LEVEL_OWN)
        assert fit.tolerance.lower[1] == pytest.approx(0.8)
        # Its settings are recorded; no neighbour is read, and none named.
        assert fit.record()["acquisition"] == SETTINGS
        assert "pool" not in fit.record()
        read_settings.assert_awaited_once()
        read_neighbours.assert_not_awaited()

    async def test_a_thin_one_pools_its_nearest_files_lines(self):
        fit, _, read_neighbours = await self._fit(_many(8), pool=_pool(15))

        assert (fit.fit.level, fit.fit.pooled_files) == (LEVEL_POOLED, 2)
        assert fit.tolerance.lower[1] == pytest.approx(0.7)
        record = fit.record()
        assert record["pooled_lines"] == 23
        # The record names the files pooled, the lines each gave and the span.
        assert record["pool"] == {
            "nearest": POOL_NEIGHBOURS,
            "files": [{"sample_item_id": "si-n0", "lines": 15, "absent": 1}],
            "span": {
                "from": T0.isoformat(),
                "to": (T0 + timedelta(minutes=10)).isoformat(),
                "hours": 0.17,
            },
        }
        sample, settings, reader = read_neighbours.await_args.args
        assert (sample.sample_item_id, settings) == ("si-1", SETTINGS)
        # Read through the run's own reader, under its config and parameters.
        assert reader is _never_read

    async def test_a_pool_too_thin_falls_to_today_s_numbers_and_says_so(self):
        # Two neighbours of a few lines each: still under the curve's minimum.
        fit, _, read_neighbours = await self._fit(_many(5), pool=_pool(4, 3))

        assert fit.fit.level == LEVEL_DEFAULT
        assert fit.tolerance == DEFAULT_TOLERANCE
        # The pool it read is named all the same, for a pass to see it fell.
        assert [entry["lines"] for entry in fit.record()["pool"]["files"]] == [4, 3]
        read_neighbours.assert_awaited_once()

    async def test_a_thin_one_with_no_settings_pools_nothing(self):
        fit, _, read_neighbours = await self._fit(_many(8), settings=None)

        assert fit.fit.level == LEVEL_DEFAULT
        assert fit.record()["acquisition"] is None
        assert "pool" not in fit.record()
        read_neighbours.assert_not_awaited()

    async def test_a_tof_fits_its_centre_and_width_and_reads_no_settings(self):
        fit, read_settings, read_neighbours = await self._fit(
            _many(25), "tof-sample.h5"
        )

        assert (fit.fit.form, fit.fit.level) == (FORM_CENTRE_WIDTH, LEVEL_OWN)
        assert fit.tolerance.centre == pytest.approx(-0.2)
        assert "acquisition" not in fit.record()
        read_settings.assert_not_awaited()
        read_neighbours.assert_not_awaited()

    async def test_a_thin_tof_pools_nothing(self):
        fit, _, read_neighbours = await self._fit(_many(8), "tof-sample.h5")

        assert (fit.fit.form, fit.fit.level) == (FORM_CENTRE_WIDTH, LEVEL_DEFAULT)
        read_neighbours.assert_not_awaited()

    async def test_a_file_whose_name_says_no_class_fits_nothing(self):
        fit, read_settings, _ = await self._fit(_many(25), "sample.unknown")

        assert (fit.fit.form, fit.fit.level) == (FORM_DEFAULT, LEVEL_DEFAULT)
        read_settings.assert_not_awaited()


def _candidate(
    name: str,
    minutes: float | None,
    *,
    filename: str | None = None,
) -> SimpleNamespace:
    """A sample of the batch acquired ``minutes`` after the reading file."""
    return SimpleNamespace(
        sample_item_id=f"si-{name}",
        sample_item_name=name,
        sample_batch_id="sb-1",
        filename=filename or f"orbi-{name}.raw",
        polarity="-",
        datetime_utc=None if minutes is None else T0 + timedelta(minutes=minutes),
    )


@pytest.mark.asyncio
class TestThePool:
    """Which files a thin file's curve reads (``read_pool``): the nearest in
    acquisition time that share its settings and that its reader reads."""

    @staticmethod
    async def _read(
        candidates,
        *,
        settings=None,
        refused=(),
        lines=None,
        nearest=POOL_NEIGHBOURS,
        sample=None,
    ):
        settings = settings or {}
        lines = lines or {}
        read: list[str] = []

        async def reader(neighbour):
            read.append(neighbour.sample_item_name)
            if neighbour.sample_item_name in refused:
                return None
            count = lines.get(neighbour.sample_item_name, 3)
            return EnvelopeLines(np.full(count, 15.0), np.full(count, 0.8))

        async def settings_of(candidate):
            return settings.get(candidate.sample_item_name, SETTINGS)

        with (
            patch.object(
                envelope_fit,
                "_batch_candidates",
                AsyncMock(
                    side_effect=lambda sample: envelope_fit._by_distance_in_time(
                        sample, candidates
                    )
                ),
            ),
            patch.object(envelope_fit, "acquisition_settings", settings_of),
        ):
            pool = await read_pool(
                sample or _Sample("orbi-sample.raw"),
                SETTINGS,
                reader,
                nearest=nearest,
            )
        return pool, read

    async def test_the_nearest_files_in_time_either_side(self):
        candidates = [
            _candidate(name, minutes)
            for name, minutes in [
                ("far-before", -300),
                ("before", -20),
                ("after", 10),
                ("far-after", 400),
                ("next", 30),
            ]
        ]

        pool, read = await self._read(candidates, nearest=3)

        assert [f.sample_item_id for f in pool.files] == [
            "si-after",
            "si-before",
            "si-next",
        ]
        # Read nearest first, and no further than the pool needs.
        assert read == ["after", "before", "next"]
        assert pool.span == (T0 - timedelta(minutes=20), T0 + timedelta(minutes=30))
        assert pool.record()["span"]["hours"] == pytest.approx(0.83)

    async def test_a_tie_in_time_goes_by_sample_id(self):
        candidates = [_candidate("b", 10), _candidate("a", -10), _candidate("c", 10)]

        pool, _ = await self._read(candidates, nearest=2)

        assert [f.sample_item_id for f in pool.files] == ["si-a", "si-b"]

    async def test_a_file_at_other_settings_takes_no_place(self):
        candidates = [_candidate("hundred", 5), _candidate("ten", 50)]

        pool, read = await self._read(
            candidates, settings={"hundred": OTHER_SETTINGS}, nearest=1
        )

        assert [f.sample_item_id for f in pool.files] == ["si-ten"]
        # Its settings ruled it out before its lines were read.
        assert read == ["ten"]

    async def test_a_file_the_reader_refuses_takes_no_place(self):
        # A blank the sequence interleaves, or a file with no peak list.
        candidates = [_candidate("blank", 5), _candidate("sample", 50)]

        pool, read = await self._read(candidates, refused={"blank"}, nearest=1)

        assert [f.sample_item_id for f in pool.files] == ["si-sample"]
        assert read == ["blank", "sample"]

    async def test_a_file_of_another_class_takes_no_place(self):
        candidates = [
            _candidate("tof", 5, filename="tof.h5"),
            _candidate("unknown", 6, filename="unknown.file"),
            _candidate("orbi", 50),
        ]

        pool, read = await self._read(candidates, nearest=1)

        assert [f.sample_item_id for f in pool.files] == ["si-orbi"]
        assert read == ["orbi"]

    async def test_a_file_that_gave_no_line_still_takes_its_place(self):
        candidates = [_candidate("empty", 5), _candidate("full", 50)]

        pool, _ = await self._read(candidates, lines={"empty": 0}, nearest=1)

        assert [f.sample_item_id for f in pool.files] == ["si-empty"]
        assert pool.record()["files"] == [
            {"sample_item_id": "si-empty", "lines": 0, "absent": 0}
        ]

    async def test_it_reads_at_most_k_files(self):
        candidates = [_candidate(f"n{index}", index + 1) for index in range(10)]

        pool, read = await self._read(candidates)

        assert len(pool.files) == POOL_NEIGHBOURS == 6
        assert read == [f"n{index}" for index in range(6)]
        # The span runs from the file itself, the first of the sequence.
        assert pool.span == (T0, T0 + timedelta(minutes=6))

    async def test_a_file_that_says_no_time_comes_after_every_one_that_does(self):
        candidates = [_candidate("untimed", None), _candidate("late", 900)]

        pool, _ = await self._read(candidates, nearest=1)

        assert [f.sample_item_id for f in pool.files] == ["si-late"]

    async def test_a_reading_file_that_says_no_time_reads_by_sample_id(self):
        sample = _Sample("orbi-sample.raw")
        sample.datetime_utc = None
        candidates = [_candidate("b", 1), _candidate("a", 500)]

        pool, _ = await self._read(candidates, nearest=1, sample=sample)

        assert [f.sample_item_id for f in pool.files] == ["si-a"]
        # The span is the times the pool knows.
        assert pool.span == (T0 + timedelta(minutes=500),) * 2

    async def test_a_pool_no_file_of_which_says_its_time_has_no_span(self):
        sample = _Sample("orbi-sample.raw")
        sample.datetime_utc = None

        pool, _ = await self._read([_candidate("a", None)], sample=sample)

        assert [f.sample_item_id for f in pool.files] == ["si-a"]
        assert (pool.span, pool.record()["span"]) == (None, None)

    async def test_a_sample_in_no_batch_pools_nothing(self):
        sample = _Sample("orbi-sample.raw")
        sample.sample_batch_id = None

        pool, read = await self._read([_candidate("a", 1)], sample=sample)

        assert (pool.files, pool.span, read) == ((), None, [])
        assert pool.record() == {"nearest": POOL_NEIGHBOURS, "files": [], "span": None}


class TestADecoyInheritsItsControlsTolerance:
    """A decoy's commits are wrong formulas on a moved axis: it scores at the
    tolerance its control recorded, and records that record as its own."""

    @pytest.mark.parametrize(
        "form, lines",
        [
            (FORM_CURVE, EnvelopeLines(np.full(25, 15.0), np.full(25, 0.8))),
            (FORM_CENTRE_WIDTH, EnvelopeLines(np.full(25, 10.0), np.full(25, 0.9))),
        ],
        ids=["curve", "centre and width"],
    )
    def test_it_scores_at_the_tolerance_its_control_recorded(self, form, lines):
        record = fit_envelope_tolerance(form, lines).record()
        record["acquisition"] = SETTINGS

        inherited = inherited_envelope_fit(record)

        assert inherited.tolerance == EnvelopeTolerance.from_record(record)
        assert inherited.tolerance != DEFAULT_TOLERANCE
        assert (inherited.fit.form, inherited.fit.level) == (form, LEVEL_OWN)
        assert inherited.record() == {**record, "inherited": True}

    def test_a_control_from_before_the_fit_scored_at_today_s_numbers(self):
        inherited = inherited_envelope_fit(None)

        assert inherited.tolerance == DEFAULT_TOLERANCE
        assert (inherited.fit.form, inherited.fit.level) == (
            FORM_DEFAULT,
            LEVEL_DEFAULT,
        )
        assert inherited.record() == {
            "form": FORM_DEFAULT,
            "level": LEVEL_DEFAULT,
            "lines": 0,
            "absent": 0,
            "detect_snr": 3.0,
            "inherited": True,
        }

    def test_a_run_that_fits_its_own_records_no_inheritance(self):
        fit = envelope_fit.RunEnvelopeFit(
            fit_envelope_tolerance(FORM_CURVE, EnvelopeLines()), SETTINGS
        )

        assert "inherited" not in fit.record()
