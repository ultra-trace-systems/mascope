"""The chemistry detector in the pipeline: rung 5, as an audit.

What is pinned here is what the pipeline does with the detector's answer -
which spectrum it reads, which scan ranges coverage is judged against, what is
stored beside which binding, and that nothing about it can cost a file its
run. The detector's own rules are pinned in the library's tests
(``libraries/tools/tests/test_chemistry_detection.py``).

The rows are real; the peak store is not, so the spectrum is stood in for.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.orm import undefer
from test_utils import captured_logs

from mascope_backend.api.controllers.sample.files.process import detection
from mascope_backend.api.controllers.sample.files.process.detection import (
    PolarityBinding,
    ms1_polarities,
    polarity_evidence,
    polarity_scan_ranges,
    record_chemistry_detection,
)
from mascope_backend.db import IonizationMechanism, IonizationMode, SampleFile
from mascope_backend.db.id import gen_id
from mascope_backend.runtime import runtime
from mascope_tools.composition.chemistry_detection import detect_chemistry
from mascope_tools.composition.reagents import ion_mz


def _stream(polarity, ranges, ms_order=1):
    return {
        "key": f"FTMS {polarity} {ranges}",
        "signature": {
            "polarity": polarity,
            "ms_order": ms_order,
            "scan_ranges": [list(scan_range) for scan_range in ranges],
        },
    }


def _nitrate_spectrum():
    """A nitrate source's spectrum: its ladder above a scatter of analytes."""
    mz = [ion_mz("NO3", -1), ion_mz("HN2O6", -1), ion_mz("H2N3O9", -1)]
    intensity = [1.0e7, 4.0e6, 6.0e5]
    for k in range(40):
        mz.append(140.0371 + 7.0 * k)
        intensity.append(3.0e4)
    return mz, intensity


class TestWhatIsRead:
    def test_the_polarities_with_ms1_scans(self):
        census = [_stream("-", [(50, 500)]), _stream("+", [(50, 500)], ms_order=2)]
        assert ms1_polarities(census, "+-") == ["-"]

    def test_every_polarity_of_a_file_without_a_census(self):
        assert ms1_polarities([], "+-") == ["+", "-"]
        assert ms1_polarities(None, "-") == ["-"]

    def test_the_ranges_of_the_polaritys_ms1_streams(self):
        census = [
            _stream("-", [(50, 130)]),
            _stream("-", [(120, 600)]),
            _stream("-", [(50, 130)]),
            _stream("+", [(40, 400)]),
            _stream("-", [(100, 1000)], ms_order=2),
        ]
        assert polarity_scan_ranges(census, "-", [40.0, 1000.0]) == [
            (50.0, 130.0),
            (120.0, 600.0),
        ]

    def test_the_files_range_without_a_census(self):
        assert polarity_scan_ranges([], "-", [40.0, 600.0]) == [(40.0, 600.0)]
        assert polarity_scan_ranges(None, "-", None) == []

    def test_a_malformed_range_is_passed_over(self):
        census = [_stream("-", [(50, 500)])]
        census[0]["signature"]["scan_ranges"].append(["x", None])
        assert polarity_scan_ranges(census, "-", None) == [(50.0, 500.0)]


class TestTheSpectrum:
    @pytest.mark.parametrize(
        "instrument_type, expected", [("orbi", [5.0, 6.0]), ("tof", [7.0, 8.0])]
    )
    def test_heights_on_an_orbitrap_areas_on_a_tof(
        self, monkeypatch, instrument_type, expected
    ):
        """What the polarity's sample reads, as the assignment engine does."""
        from mascope_backend.api.controllers.samples.lib.samples_peaks import (
            PeakData,
        )

        asked = []

        def peaks(filename, polarity, t0, t1):
            asked.append((filename, polarity))
            return PeakData(
                peak_ids=["a", "b"],
                mz_values=[61.99, 124.98],
                heights=[5.0, 6.0],
                areas=[7.0, 8.0],
            )

        monkeypatch.setattr(detection, "extract_peaks", peaks)
        mz, intensity = detection._read_spectrum("f.raw", "-", instrument_type)
        assert asked == [("f.raw", "-")]
        assert mz == [61.99, 124.98]
        assert intensity == expected


class TestWhatIsStored:
    def _detection(self):
        return detect_chemistry(
            *_nitrate_spectrum(), polarity="-", instrument_type="orbi"
        )

    def test_a_call_that_names_the_bound_profile_agrees(self):
        stored = polarity_evidence(
            self._detection(), PolarityBinding("im-1", "token"), "NO3"
        )
        assert stored["agrees"] is True
        assert stored["bound"] == {
            "ionization_mode_id": "im-1",
            "bound_by": "token",
            "profile": "NO3",
        }
        assert stored["detection"]["profile"] == "NO3"

    def test_a_call_that_names_another_disagrees(self):
        stored = polarity_evidence(
            self._detection(), PolarityBinding("im-1", "method"), "BR"
        )
        assert stored["agrees"] is False

    def test_a_mode_without_a_reagent_disagrees_with_any_call(self):
        """An electrospray or ambient mode: its spectrum's answer is no call."""
        stored = polarity_evidence(
            self._detection(), PolarityBinding("im-1", "token"), "ESI_NEG"
        )
        assert stored["agrees"] is False

    def test_a_parked_polarity_has_nothing_to_agree_with(self):
        stored = polarity_evidence(self._detection(), None, None)
        assert stored["bound"] is None
        assert stored["agrees"] is None

    def test_no_call_neither_agrees_nor_disagrees(self):
        nothing = detect_chemistry([], [], polarity="-", instrument_type="orbi")
        stored = polarity_evidence(nothing, PolarityBinding("im-1", "token"), "NO3")
        assert stored["agrees"] is None


@pytest_asyncio.fixture
async def rows(async_session_factory):
    """A converted negative file and a nitrate mode with its mechanisms."""
    sample_file_id = gen_id(16)
    mode_id = gen_id(16)
    async with async_session_factory() as session:
        mechanism_ids = []
        for notation in ("[M+NO3]-", "[M-H]-"):
            found = (
                await session.execute(
                    select(IonizationMechanism).where(
                        IonizationMechanism.ionization_mechanism == notation
                    )
                )
            ).scalar_one_or_none()
            if found is None:
                found = IonizationMechanism(
                    ionization_mechanism_id=gen_id(12),
                    ionization_mechanism=notation,
                    ionization_mechanism_polarity="-",
                )
                session.add(found)
                await session.flush()
            mechanism_ids.append(found.ionization_mechanism_id)
        session.add(
            IonizationMode(
                ionization_mode_id=mode_id,
                ionization_mode_name=f"Detect nitrate {mode_id}",
                ionization_mode_token=None,
                ionization_mode_polarity="-",
                ionization_mechanism_ids=mechanism_ids,
            )
        )
        sample_file = SampleFile(
            sample_file_id=sample_file_id,
            filename=f"detect_{sample_file_id}.raw",
            instrument="Detect-Orbi",
            instrument_type="orbi",
            instrument_function_id=None,
            datetime=datetime(2026, 10, 1, 12, 0, 0),
            datetime_utc=datetime(2026, 10, 1, 9, 0, 0, tzinfo=timezone.utc),
            length=60.0,
            range=[40.0, 600.0],
            polarity="-",
        )
        session.add(sample_file)
        await session.commit()
    yield {"sample_file": sample_file, "mode": mode_id}
    async with async_session_factory() as session:
        await session.delete(await session.get(SampleFile, sample_file_id))
        await session.delete(await session.get(IonizationMode, mode_id))
        await session.commit()


async def _stored(async_session_factory, sample_file_id):
    async with async_session_factory() as session:
        return (
            await session.get(
                SampleFile,
                sample_file_id,
                options=[undefer(SampleFile.binding_evidence)],
                populate_existing=True,
            )
        ).binding_evidence


def _peaks_of(monkeypatch, spectrum=None, error=None):
    """Stand in for the peak store, recording what was read."""
    reads = []

    def read(filename, polarity, instrument_type):
        reads.append((filename, polarity, instrument_type))
        if error is not None:
            raise error
        return spectrum or _nitrate_spectrum()

    monkeypatch.setattr(detection, "_read_spectrum", read)
    return reads


class TestTheRecord:
    @pytest.mark.asyncio
    async def test_the_call_is_stored_beside_the_rung_and_the_mode(
        self, async_session_factory, rows, monkeypatch
    ):
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        reads = _peaks_of(monkeypatch)

        written = await record_chemistry_detection(
            sample_file,
            [_stream("-", [(50, 500)])],
            ["-"],
            {"-": PolarityBinding(rows["mode"], "token")},
        )

        assert reads == [(sample_file.filename, "-", "orbi")]
        stored = await _stored(async_session_factory, sample_file.sample_file_id)
        assert stored == written
        assert stored["-"]["bound"] == {
            "ionization_mode_id": rows["mode"],
            "bound_by": "token",
            # Read off the mode's mechanisms, as assignment reads a sample's.
            "profile": "NO3",
        }
        assert stored["-"]["agrees"] is True
        assert stored["-"]["detection"]["profile"] == "NO3"
        # Coverage was judged against the census's range, not the file's.
        assert stored["-"]["detection"]["scan_ranges"] == [[50.0, 500.0]]

    @pytest.mark.asyncio
    async def test_a_parked_file_is_stored_with_nothing_beside_it(
        self, async_session_factory, rows, monkeypatch
    ):
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        _peaks_of(monkeypatch)

        await record_chemistry_detection(sample_file, [], ["-"])

        stored = await _stored(async_session_factory, sample_file.sample_file_id)
        assert stored["-"]["bound"] is None
        assert stored["-"]["agrees"] is None
        assert stored["-"]["detection"]["decision"] == "profile"
        # No census: the file's own range.
        assert stored["-"]["detection"]["scan_ranges"] == [[40.0, 600.0]]

    @pytest.mark.asyncio
    async def test_a_disagreement_is_logged_and_changes_nothing_else(
        self, async_session_factory, rows, monkeypatch
    ):
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        _peaks_of(monkeypatch)

        async def bromide(bindings, instrument_type):
            return {"-": "BR"}

        monkeypatch.setattr(detection, "_bound_profiles", bromide)
        with captured_logs("INFO") as logs:
            written = await record_chemistry_detection(
                sample_file, [], ["-"], {"-": PolarityBinding(rows["mode"], "method")}
            )

        assert written["-"]["agrees"] is False
        said = [
            r["message"] for r in logs if "Chemistry detection reads" in r["message"]
        ]
        assert len(said) == 1
        assert "as NO3" in said[0] and "BR mode by method" in said[0]
        assert {r["level"].name for r in logs if r["message"] in said} == {"INFO"}

    @pytest.mark.asyncio
    async def test_a_blank_has_no_spectrum_and_clears_an_earlier_runs(
        self, async_session_factory, rows, monkeypatch
    ):
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        _peaks_of(monkeypatch)
        await record_chemistry_detection(sample_file, [], ["-"])
        assert await _stored(async_session_factory, sample_file.sample_file_id)

        sample_file.instrument_function_id = None
        reads = _peaks_of(monkeypatch)
        assert await record_chemistry_detection(sample_file, [], ["-"]) is None

        assert reads == []
        assert await _stored(async_session_factory, sample_file.sample_file_id) is None

    @pytest.mark.asyncio
    async def test_switched_off_it_reads_nothing_and_clears_an_earlier_runs(
        self, async_session_factory, rows, monkeypatch
    ):
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        _peaks_of(monkeypatch)
        await record_chemistry_detection(sample_file, [], ["-"])
        assert await _stored(async_session_factory, sample_file.sample_file_id)

        monkeypatch.setattr(runtime.config, "chemistry_detection", "off")
        reads = _peaks_of(monkeypatch)
        assert await record_chemistry_detection(sample_file, [], ["-"]) is None

        assert reads == []
        assert await _stored(async_session_factory, sample_file.sample_file_id) is None

    @pytest.mark.asyncio
    async def test_a_failed_read_costs_the_run_nothing_and_leaves_no_stale_evidence(
        self, async_session_factory, rows, monkeypatch
    ):
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        _peaks_of(monkeypatch)
        await record_chemistry_detection(sample_file, [], ["-"])

        _peaks_of(monkeypatch, error=FileNotFoundError("no peak store"))
        with captured_logs("WARNING") as logs:
            written = await record_chemistry_detection(
                sample_file, [], ["-"], {"-": PolarityBinding(rows["mode"], "token")}
            )

        assert written is None
        assert await _stored(async_session_factory, sample_file.sample_file_id) is None
        assert any(
            "Chemistry detection on" in r["message"] and "no peak store" in r["message"]
            for r in logs
        )

    @pytest.mark.asyncio
    async def test_a_listing_does_not_carry_it(
        self, async_session_factory, rows, monkeypatch
    ):
        """Deferred like the acquisition record: a kilobyte or two a file."""
        sample_file = rows["sample_file"]
        sample_file.instrument_function_id = "ifunc-1"
        _peaks_of(monkeypatch)
        await record_chemistry_detection(sample_file, [], ["-"])

        async with async_session_factory() as session:
            listed = (
                await session.execute(
                    select(SampleFile).where(
                        SampleFile.sample_file_id == sample_file.sample_file_id
                    )
                )
            ).scalar_one()
            assert "binding_evidence" not in listed.to_dict()
            loaded = await session.get(
                SampleFile,
                sample_file.sample_file_id,
                options=[undefer(SampleFile.binding_evidence)],
                populate_existing=True,
            )
            assert loaded.to_dict()["binding_evidence"]["-"]["agrees"] is None
