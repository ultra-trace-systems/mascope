"""
Unit tests: the calibration trend of a TOF instrument, built from its files'
records (``calibration_trend``).

What one file's record gives (``file_summary``), how the records line up into
one series per calibrant line (``build_trend``), where the collection the
files were fitted with changed (``collection_changes``), how a binned trend
is put together from what the database sums up (``binned_trend``, its bins'
width ``bin_unit``), and the export's rows. The route, the window, which
records are on the trend (``on_the_trend``, in SQL), the sums themselves and
the reads before the window are the integration tests'
(``integration/api/calibration/test_calibration_trend.py``).
"""

import csv
import io
from datetime import datetime, timedelta, timezone

import pytest

from mascope_backend.api.controllers.calibration.calibration_trend import (
    EXPORT_COLUMNS,
    TREND_HOURLY_MAX_DAYS,
    bin_unit,
    binned_trend,
    build_trend,
    calibrant_key,
    collection_changes,
    file_summary,
    trend_rows,
)
from mascope_sdk import read_export_csv


_T0 = datetime(2026, 10, 15, 8, 0, tzinfo=timezone.utc)

_SHA_V1 = "1" * 64
_SHA_V2 = "2" * 64


def _point(ion, target, offset_ppm, residual, retained=True, snr=100.0):
    """A record's point: the line read ``offset_ppm`` off before the fit."""
    return {
        "ion": ion,
        "target_mz": target,
        "observed_mz": target * (1 + offset_ppm * 1e-6),
        "residual_ppm": residual,
        "snr": snr,
        "retained": retained,
    }


def _record(
    points=None, collection=("set", _SHA_V1), acquisition_offsets=None, **quality
):
    """A TOF fit's record as the node stores it, points and collection named."""
    block = {
        "n_points": sum(1 for p in points or [] if p["retained"]),
        "n_ions": 2,
        "pre_fit_mz_error_ppm": 80.0,
        "post_fit_mz_error_ppm": 1.5,
        **quality,
    }
    if points is not None:
        block["points"] = points
    if collection is not None:
        block["collection"] = {"id": collection[0], "isotopes_sha256": collection[1]}
    block["model"] = {"mode": 0, "equation": "i = p1*sqrt(m) + p2"}
    block["node_version"] = 2
    record = {
        "mode": 0,
        "par": [2800.0, -0.5],
        "status": "ok",
        "verified": True,
        "quality": block,
    }
    if acquisition_offsets is not None:
        record["acquisition_offsets"] = acquisition_offsets
    return record


#: The acquisition axis of :func:`_record`'s fit read 80 ppm high: a mode-0
#: m/z goes with ``1 / p1 ** 2``.
_ACQUISITION = {
    "mode": 0,
    "par": [2800.0 / (1 + 80e-6) ** 0.5, -0.5],
    "raw_index_offset": 0,
}


def _refit(record, fitted_at=None):
    """``record`` as a refit wrote it: its lines observed on the axis the
    first fit set, near 0 ppm, and the acquisition axis kept."""
    points = [
        {**point, "observed_mz": point["target_mz"] * (1 + 0.2e-6)}
        for point in record["quality"].get("points") or []
    ]
    refit = {
        **record,
        "quality": {**record["quality"], "points": points},
        "acquisition_axis": dict(_ACQUISITION),
    }
    if fitted_at is not None:
        refit["fitted_at"] = fitted_at.isoformat()
    return refit


def _rows(*records):
    """Rows for build_trend, an hour apart, oldest first."""
    return [
        (f"file{i}", f"tof_file{i}.h5", _T0 + timedelta(hours=i), record)
        for i, record in enumerate(records)
    ]


class TestFileSummary:
    def test_the_axis_offset_is_the_signed_mean_of_the_kept_lines(self):
        points = [
            _point("NO3-", 61.9884, -80.0, 0.5),
            _point("HNO3NO3-", 124.9840, -70.0, -0.5),
            # Left out: its offset is not the axis'.
            _point("C2F3O2-", 112.9856, +20.0, 30.0, retained=False),
        ]
        summary = file_summary(_record(points))

        assert abs(summary["axis_offset_ppm"] - (-75.0)) < 1e-6

    def test_a_refit_reads_the_acquisition_axis_it_kept_not_the_axis_it_ran_on(
        self,
    ):
        """MAS-937: the first fit and the refit report the same offsets."""
        first = _record(
            [_point("NO3-", 61.9884, 80.0, 0.5), _point("Br-", 78.9189, 80.0, -0.5)]
        )

        summaries = [file_summary(first), file_summary(_refit(first))]

        assert [s["axis_offset_ppm"] for s in summaries] == [
            pytest.approx(80.0, abs=1e-6),
            pytest.approx(80.0, abs=1e-6),
        ]

    def test_a_refit_that_kept_offsets_reads_them(self):
        refit = _record(
            [_point("NO3-", 61.9884, 0.2, 0.5)],
            acquisition_offsets=[
                {"ion": "NO3-", "target_mz": 61.9884, "offset_ppm": 77.0}
            ],
        )

        assert file_summary(refit)["axis_offset_ppm"] == 77.0

    def test_reads_when_the_record_was_fitted(self):
        record = _record(None)
        assert file_summary(record)["fitted_utc"] is None

        record["fitted_at"] = "2026-11-20T09:15:00+02:00"
        assert file_summary(record)["fitted_utc"] == "2026-11-20T07:15:00+00:00"

    def test_counts_kept_and_chosen_from_lines(self):
        points = [
            _point("NO3-", 61.9884, -80.0, 0.5),
            _point("C2F3O2-", 112.9856, +20.0, 30.0, retained=False),
        ]
        summary = file_summary(_record(points))

        assert summary["n_points"] == 1
        assert summary["n_candidates"] == 2

    def test_reads_the_quality_blocks_errors_model_and_version(self):
        summary = file_summary(_record([_point("NO3-", 61.9884, -80.0, 0.5)]))

        assert summary["status"] == "ok"
        assert summary["post_fit_ppm"] == 1.5
        assert summary["pre_fit_abs_ppm"] == 80.0
        assert summary["model_mode"] == 0
        assert summary["node_version"] == 2
        assert summary["n_ions"] == 2

    def test_a_record_without_points_has_no_signed_offset(self):
        """Its pre-fit error is a mean size: the sign is gone."""
        record = _record(None)
        summary = file_summary(record)

        assert summary["axis_offset_ppm"] is None
        assert summary["n_candidates"] is None
        assert summary["pre_fit_abs_ppm"] == 80.0

    def test_an_older_record_names_its_model_by_its_mode(self):
        record = _record(None)
        del record["quality"]["model"]
        record["mode"] = 2

        assert file_summary(record)["model_mode"] == 2

    def test_a_failed_calibration_has_its_status_and_nothing_else(self):
        """Its converter coefficients' mode is not a fit's model."""
        summary = file_summary(
            {"mode": 0, "par": [1, 2], "status": "failed", "verified": False}
        )

        assert summary["status"] == "failed"
        assert summary["model_mode"] is None
        assert summary["n_points"] is None
        assert summary["axis_offset_ppm"] is None

    def test_non_numbers_read_as_none(self):
        record = _record(None, post_fit_mz_error_ppm=float("nan"))
        record["quality"]["pre_fit_mz_error_ppm"] = True

        summary = file_summary(record)

        assert summary["post_fit_ppm"] is None
        assert summary["pre_fit_abs_ppm"] is None


class TestBuildTrend:
    def test_one_series_per_line_aligned_with_the_files(self):
        trend = build_trend(
            _rows(
                _record([_point("NO3-", 61.9884, -80, 0.5)]),
                _record(
                    [
                        _point("NO3-", 61.9884, -81, 0.7),
                        _point("Br-", 78.9189, -79, -2.0, retained=False),
                    ]
                ),
                _record(None),
            )
        )

        lines = {(c["ion"], c["target_mz"]): c for c in trend["calibrants"]}
        assert lines[("NO3-", 61.9884)]["residual_ppm"] == [0.5, 0.7, None]
        assert lines[("NO3-", 61.9884)]["retained"] == [True, True, None]
        assert lines[("Br-", 78.9189)]["residual_ppm"] == [None, -2.0, None]
        assert lines[("Br-", 78.9189)]["retained"] == [None, False, None]

    def test_lines_are_ordered_by_mass(self):
        trend = build_trend(
            _rows(
                _record(
                    [
                        _point("Br3-", 236.7536, -80, 1.0),
                        _point("NO3-", 61.9884, -80, 0.5),
                        _point("Br-", 78.9189, -80, 0.2),
                    ]
                )
            )
        )

        assert [c["target_mz"] for c in trend["calibrants"]] == [
            61.9884,
            78.9189,
            236.7536,
        ]

    def test_a_line_is_one_line_whatever_the_rounding_of_its_mass(self):
        trend = build_trend(
            _rows(
                _record([_point("NO3-", 61.98839, -80, 0.5)]),
                _record([_point("NO3-", 61.988394, -80, 0.6)]),
            )
        )

        assert len(trend["calibrants"]) == 1
        assert calibrant_key({"ion": "NO3-", "target_mz": 61.98839}) == (
            "NO3-",
            61.9884,
        )

    def test_two_ions_on_one_mass_are_two_lines(self):
        trend = build_trend(
            _rows(
                _record(
                    [
                        _point("A-", 100.0, -80, 0.5),
                        _point("B-", 100.0, -80, 0.9),
                    ]
                )
            )
        )

        assert len(trend["calibrants"]) == 2

    def test_a_line_listed_twice_is_read_once_kept_over_left_out(self):
        """The node lists a line once; a record that lists it twice is read
        as the fit keeping it if it kept either, else as the first."""
        left_then_kept = [
            _point("NO3-", 61.9884, -80, 9.0, retained=False),
            _point("NO3-", 61.9884, -80, 0.5),
        ]
        kept_then_left = list(reversed(left_then_kept))
        twice_left = [
            _point("NO3-", 61.9884, -80, 9.0, retained=False),
            _point("NO3-", 61.9884, -80, 7.0, retained=False),
        ]

        trend = build_trend(
            _rows(_record(left_then_kept), _record(kept_then_left), _record(twice_left))
        )

        (line,) = trend["calibrants"]
        assert line["residual_ppm"] == [0.5, 0.5, 9.0]
        assert line["retained"] == [True, True, False]

    def test_each_line_serves_its_offset_on_the_acquisition_axis(self):
        """A first fit, a refit and another: the line reads 80 ppm high on
        all three, and the left-out line has its offset too."""
        first = _record(
            [
                _point("NO3-", 61.9884, 80.0, 0.5),
                _point("Br-", 78.9189, 95.0, 9.0, retained=False),
            ]
        )
        trend = build_trend(_rows(first, _refit(first), _refit(first)))

        nitrate, bromide = trend["calibrants"]
        assert nitrate["offset_ppm"] == [pytest.approx(80.0, abs=1e-6)] * 3
        # The refits read it at the fit's index of its exact m/z, where its
        # peak is not: its residual is the fit's, 9 ppm.
        assert bromide["offset_ppm"][0] == pytest.approx(95.0)
        assert bromide["offset_ppm"][1:] == [pytest.approx(80.0, abs=1e-6)] * 2
        assert [f["axis_offset_ppm"] for f in trend["files"]] == [
            pytest.approx(80.0, abs=1e-6)
        ] * 3

    def test_files_carry_their_summary_and_time(self):
        trend = build_trend(_rows(_record([_point("NO3-", 61.9884, -80, 0.5)])))

        entry = trend["files"][0]
        assert entry["sample_file_id"] == "file0"
        assert entry["filename"] == "tof_file0.h5"
        assert entry["datetime_utc"] == _T0.isoformat()
        assert entry["post_fit_ppm"] == 1.5
        assert abs(entry["axis_offset_ppm"] - (-80.0)) < 1e-6

    def test_collection_versions_in_the_order_of_their_first_file(self):
        trend = build_trend(
            _rows(
                _record(collection=("set", _SHA_V1)),
                _record(collection=("set", _SHA_V2)),
                _record(collection=("set", _SHA_V1)),
                _record(collection=None),
            )
        )

        assert [
            (c["id"], c["isotopes_sha256"], c["n_files"]) for c in trend["collections"]
        ] == [("set", _SHA_V1, 2), ("set", _SHA_V2, 1)]
        assert trend["collections"][0]["first_utc"] == _T0.isoformat()
        assert (
            trend["collections"][0]["last_utc"]
            == (_T0 + timedelta(hours=2)).isoformat()
        )
        assert [entry["collection"] for entry in trend["files"]] == [0, 1, 0, None]


def _changes(records, seen=(), last=None, recorded=False):
    trend = build_trend(_rows(*records))
    return collection_changes(trend["collections"], set(seen), last or {}, recorded)


class TestCollectionChanges:
    def test_an_edit_is_marked_at_the_first_file_of_the_new_version(self):
        changes = _changes(
            [
                _record(collection=("set", _SHA_V1)),
                _record(collection=("set", _SHA_V1)),
                _record(collection=("set", _SHA_V2)),
                _record(collection=("set", _SHA_V2)),
            ]
        )

        assert [(c["kind"], c["sample_file_id"]) for c in changes] == [
            ("edited", "file2")
        ]
        assert changes[0]["previous_isotopes_sha256"] == _SHA_V1
        assert changes[0]["isotopes_sha256"] == _SHA_V2
        assert changes[0]["collection"] == 1

    def test_the_first_version_on_record_is_no_change(self):
        """Nothing earlier names a collection: the records begin there."""
        assert _changes([_record(collection=("set", _SHA_V1))]) == []

    def test_a_version_in_use_before_the_window_is_no_change(self):
        changes = _changes(
            [_record(collection=("set", _SHA_V2))],
            seen=[("set", _SHA_V2)],
            last={"set": _SHA_V2},
            recorded=True,
        )

        assert changes == []

    def test_an_edit_made_before_the_window_starts_it(self):
        """The window's first file is on a version no earlier file had."""
        changes = _changes(
            [_record(collection=("set", _SHA_V2))],
            last={"set": _SHA_V1},
            recorded=True,
        )

        assert [(c["kind"], c["previous_isotopes_sha256"]) for c in changes] == [
            ("edited", _SHA_V1)
        ]

    def test_another_collection_taking_over_is_marked_first_used(self):
        changes = _changes(
            [
                _record(collection=("old", _SHA_V1)),
                _record(collection=("new", _SHA_V2)),
            ]
        )

        assert [(c["kind"], c["collection_id"]) for c in changes] == [
            ("first_used", "new")
        ]
        assert changes[0]["previous_isotopes_sha256"] is None

    def test_a_new_collection_is_first_used_after_records_before_the_window(self):
        changes = _changes([_record(collection=("new", _SHA_V2))], recorded=True)

        assert [c["kind"] for c in changes] == ["first_used"]

    def test_interleaved_modes_mark_nothing_once_both_are_in_use(self):
        """Two modes alternate on one polarity, each on its own collection."""
        changes = _changes(
            [
                _record(collection=("nitrate", _SHA_V1)),
                _record(collection=("bromide", _SHA_V2)),
                _record(collection=("nitrate", _SHA_V1)),
                _record(collection=("bromide", _SHA_V2)),
            ],
            seen=[("nitrate", _SHA_V1), ("bromide", _SHA_V2)],
            last={"nitrate": _SHA_V1, "bromide": _SHA_V2},
            recorded=True,
        )

        assert changes == []

    def test_a_refit_with_the_new_version_moves_its_mark_back(self):
        """A version is new at the earliest file fitted with it: refitting an
        older file with it marks there, once, not at every alternation."""
        changes = _changes(
            [
                _record(collection=("set", _SHA_V1)),
                _record(collection=("set", _SHA_V2)),  # refitted
                _record(collection=("set", _SHA_V1)),
                _record(collection=("set", _SHA_V2)),
            ]
        )

        assert [c["sample_file_id"] for c in changes] == ["file1"]

    def test_versions_are_ordered_by_when_they_were_fitted(self):
        """MAS-937. V1 was fitted on the 20th onto the later file, V2 on the
        22nd onto the earlier one, refitting it: V2 is the edit, made on the
        22nd, though its file comes first on the trend."""
        v1 = _record([_point("NO3-", 61.9884, 80, 0.5)], collection=("set", _SHA_V1))
        v2 = _record([_point("NO3-", 61.9884, 80, 0.5)], collection=("set", _SHA_V2))
        fitted_v1 = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
        fitted_v2 = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        trend = build_trend(
            _rows(_refit(v2, fitted_at=fitted_v2), _refit(v1, fitted_at=fitted_v1))
        )

        changes = collection_changes(trend["collections"], set(), {}, False)

        assert [
            (
                c["kind"],
                c["sample_file_id"],
                c["datetime_utc"],
                c["isotopes_sha256"],
                c["previous_isotopes_sha256"],
            )
            for c in changes
        ] == [("edited", "file0", fitted_v2.isoformat(), _SHA_V2, _SHA_V1)]

    def test_a_version_is_placed_at_its_first_record_by_fit_time(self):
        """V2's records were written in the other order from their files'."""
        v1 = _record(collection=("set", _SHA_V1))
        v2 = _record([_point("NO3-", 61.9884, 80, 0.5)], collection=("set", _SHA_V2))
        trend = build_trend(
            _rows(
                v1,
                _refit(v2, fitted_at=_T0 + timedelta(days=3)),
                _refit(v2, fitted_at=_T0 + timedelta(days=2)),
            )
        )

        (change,) = collection_changes(trend["collections"], set(), {}, False)

        assert (change["sample_file_id"], change["datetime_utc"]) == (
            "file2",
            (_T0 + timedelta(days=2)).isoformat(),
        )
        assert trend["collections"][1]["first_fitted_utc"] == change["datetime_utc"]

    def test_files_naming_no_collection_are_passed_over(self):
        changes = _changes(
            [
                _record(collection=("set", _SHA_V1)),
                _record(None, collection=None),
                _record(collection=("set", _SHA_V2)),
            ]
        )

        assert [c["sample_file_id"] for c in changes] == ["file2"]

    def test_changes_come_oldest_first(self):
        changes = _changes(
            [
                _record(collection=("a", _SHA_V1)),
                _record(collection=("a", _SHA_V2)),
                _record(collection=("b", _SHA_V1)),
            ]
        )

        assert [c["sample_file_id"] for c in changes] == ["file1", "file2"]


def _csv(trend, rows):
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(EXPORT_COLUMNS)
    writer.writerows(trend_rows(trend, rows))
    return list(csv.DictReader(io.StringIO(buffer.getvalue())))


class TestBinUnit:
    def test_a_month_or_less_is_binned_per_hour_and_longer_per_day(self):
        month = timedelta(days=TREND_HOURLY_MAX_DAYS)

        assert bin_unit(_T0, _T0 + month) == "hour"
        assert bin_unit(_T0, _T0 + month + timedelta(seconds=1)) == "day"


def _version(
    sha, first, last, n_files, first_file, fitted=None, fitted_file=None, **more
):
    """A version as the database counts it: times in UTC, without an offset.

    ``fitted`` and ``fitted_file``: the first record fitted with it, its
    first file where None.
    """
    return {
        "collection_id": more.get("collection_id", "set"),
        "isotopes_sha256": sha,
        "first_utc": first,
        "last_utc": last,
        "n_files": n_files,
        "first_file_id": first_file,
        "first_fitted_utc": fitted or first,
        "first_fitted_file_id": fitted_file or first_file,
    }


def _bin(bucket, collection_id, n_files, **more):
    return {
        "bucket": bucket,
        "collection_id": collection_id,
        "n_files": n_files,
        "n_failed": 0,
        "n_without_lines": 0,
        "axis_offset_ppm": [-82.0, -80.0, -78.0],
        "post_fit_ppm": [0.4, 0.6, 0.8],
        "n_points": [3.0, 3.0, 4.0],
        "n_candidates": [4.0, 4.0, 4.0],
        **more,
    }


def _line(
    bucket, collection_id, ion, target, residuals, n_listed, n_kept, offsets=None
):
    return {
        "bucket": bucket,
        "collection_id": collection_id,
        "ion": ion,
        "target_mz": target,
        "residual_ppm": residuals,
        "offset_ppm": offsets,
        "n_listed": n_listed,
        "n_kept": n_kept,
    }


class TestBinnedTrend:
    """Two days of one collection, edited on the second, and a day's failed
    calibrations, which name no collection: three bins."""

    RAW = {
        "bins": [
            _bin("2026-10-15T00:00:00", "set", 5),
            _bin("2026-10-16T00:00:00", "set", 4),
            _bin(
                "2026-10-16T00:00:00",
                None,
                2,
                n_failed=2,
                axis_offset_ppm=None,
                post_fit_ppm=None,
                n_points=None,
                n_candidates=None,
            ),
        ],
        # As the database orders them: by m/z, then by bin.
        "lines": [
            _line(
                "2026-10-15T00:00:00",
                "set",
                "NO3-",
                61.9884,
                [0.1, 0.2, 0.3],
                5,
                5,
                offsets=[-81.0, -80.0, -79.0],
            ),
            _line("2026-10-16T00:00:00", "set", "NO3-", 61.9884, [0.2, 0.3, 0.4], 4, 3),
            _line("2026-10-16T00:00:00", "set", None, 125.9873, None, 4, 0),
        ],
        # In no order of their own.
        "versions": [
            _version(_SHA_V2, "2026-10-16T09:30:00", "2026-10-16T20:00:00", 3, "f7"),
            _version(
                _SHA_V1, "2026-10-15T01:00:00", "2026-10-16T08:00:00.250000", 6, "f1"
            ),
        ],
    }

    def test_serves_each_bin_with_its_start_in_utc(self):
        bins = binned_trend(self.RAW)["bins"]

        assert [(b["start_utc"], b["collection_id"], b["n_files"]) for b in bins] == [
            ("2026-10-15T00:00:00+00:00", "set", 5),
            ("2026-10-16T00:00:00+00:00", "set", 4),
            ("2026-10-16T00:00:00+00:00", None, 2),
        ]
        assert bins[0]["axis_offset_ppm"] == [-82.0, -80.0, -78.0]
        assert bins[0]["post_fit_ppm"] == [0.4, 0.6, 0.8]
        assert bins[0]["n_points"] == [3.0, 3.0, 4.0]
        assert bins[0]["n_candidates"] == [4.0, 4.0, 4.0]
        assert (bins[2]["n_failed"], bins[2]["n_without_lines"]) == (2, 0)
        assert bins[2]["axis_offset_ppm"] is None
        assert "bucket" not in bins[0]

    def test_aligns_each_lines_quartiles_and_counts_with_the_bins(self):
        """A bin whose files do not list a line has no quartiles and counts
        of zero; the lines come by exact m/z."""
        nitrate, other = binned_trend(self.RAW)["calibrants"]

        assert (nitrate["ion"], nitrate["target_mz"]) == ("NO3-", 61.9884)
        assert nitrate["residual_ppm"] == [[0.1, 0.2, 0.3], [0.2, 0.3, 0.4], None]
        assert nitrate["offset_ppm"] == [[-81.0, -80.0, -79.0], None, None]
        assert nitrate["n_listed"] == [5, 4, 0]
        assert nitrate["n_kept"] == [5, 3, 0]
        assert (other["ion"], other["target_mz"]) == (None, 125.9873)
        assert other["residual_ppm"] == [None, None, None]
        assert other["n_listed"] == [0, 4, 0]

    def test_sorts_the_lines_by_mass(self):
        raw = {**self.RAW, "lines": list(reversed(self.RAW["lines"]))}

        lines = binned_trend(raw)["calibrants"]

        assert [line["target_mz"] for line in lines] == [61.9884, 125.9873]
        assert lines[0]["residual_ppm"][0] == [0.1, 0.2, 0.3]

    def test_orders_the_versions_by_their_first_file(self):
        collections = binned_trend(self.RAW)["collections"]

        assert collections == [
            {
                "id": "set",
                "isotopes_sha256": _SHA_V1,
                "first_utc": "2026-10-15T01:00:00+00:00",
                "last_utc": "2026-10-16T08:00:00.250000+00:00",
                "n_files": 6,
                "first_fitted_utc": "2026-10-15T01:00:00+00:00",
                "first_fitted_file_id": "f1",
            },
            {
                "id": "set",
                "isotopes_sha256": _SHA_V2,
                "first_utc": "2026-10-16T09:30:00+00:00",
                "last_utc": "2026-10-16T20:00:00+00:00",
                "n_files": 3,
                "first_fitted_utc": "2026-10-16T09:30:00+00:00",
                "first_fitted_file_id": "f7",
            },
        ]

    def test_versions_first_on_one_time_are_told_apart_by_their_first_file(self):
        raw = {
            **self.RAW,
            "versions": [
                _version(
                    _SHA_V2, "2026-10-15T01:00:00", "2026-10-15T01:00:00", 1, "f2"
                ),
                _version(
                    _SHA_V1, "2026-10-15T01:00:00", "2026-10-15T01:00:00", 1, "f1"
                ),
            ],
        }

        collections = binned_trend(raw)["collections"]

        assert [c["isotopes_sha256"] for c in collections] == [_SHA_V1, _SHA_V2]

    def test_places_the_changes_where_the_versions_were_first_fitted(self):
        """The edit is on the first record fitted with the new version, as
        it would be on the files."""
        trend = binned_trend(self.RAW)

        changes = collection_changes(trend["collections"], set(), {}, False)

        assert [
            (c["kind"], c["sample_file_id"], c["datetime_utc"]) for c in changes
        ] == [("edited", "f7", "2026-10-16T09:30:00+00:00")]

    def test_a_version_fitted_later_on_older_files_is_placed_when_it_was_fitted(
        self,
    ):
        """V2 was fitted a week after V1, onto files acquired before V1's:
        the change is V2's, when it was fitted, though V2's files come first
        on the trend."""
        raw = {
            **self.RAW,
            "versions": [
                _version(
                    _SHA_V2,
                    "2026-10-15T00:30:00",
                    "2026-10-15T00:50:00",
                    2,
                    "f0",
                    fitted="2026-10-23T10:00:00",
                    fitted_file="f0b",
                ),
                _version(
                    _SHA_V1,
                    "2026-10-15T01:00:00",
                    "2026-10-16T08:00:00",
                    6,
                    "f1",
                    fitted="2026-10-16T09:00:00",
                ),
            ],
        }
        trend = binned_trend(raw)

        assert [c["isotopes_sha256"] for c in trend["collections"]] == [
            _SHA_V2,
            _SHA_V1,
        ]
        changes = collection_changes(trend["collections"], set(), {}, False)
        assert [
            (
                c["sample_file_id"],
                c["datetime_utc"],
                c["isotopes_sha256"],
                c["previous_isotopes_sha256"],
            )
            for c in changes
        ] == [("f0b", "2026-10-23T10:00:00+00:00", _SHA_V2, _SHA_V1)]

    def test_an_empty_window_has_no_bins(self):
        trend = binned_trend({"bins": None, "lines": None, "versions": None})

        assert trend == {"bins": [], "calibrants": [], "collections": []}


class TestExportRows:
    def test_one_row_per_line_and_one_for_a_file_without_lines(self):
        rows = _rows(
            _record(
                [
                    _point("NO3-", 61.9884, -80, 0.5),
                    _point("Br-", 78.9189, -70, 9.0, retained=False),
                ]
            ),
            _record(None),
        )
        trend = build_trend(rows)
        for version in trend["collections"]:
            version["name"] = "Nitrate set"

        table = _csv(trend, rows)

        assert [(r["sample_file_id"], r["ion"]) for r in table] == [
            ("file0", "NO3-"),
            ("file0", "Br-"),
            ("file1", ""),
        ]
        first = table[0]
        assert first["collection_name"] == "Nitrate set"
        assert first["axis_offset_ppm"] != ""
        assert first["collection_isotopes_sha256"] == _SHA_V1
        assert abs(float(first["offset_ppm"]) - (-80.0)) < 1e-6
        assert first["residual_ppm"] == "0.5"
        assert first["retained"] == "True"
        assert table[1]["retained"] == "False"
        assert table[2]["target_mz"] == ""

    def test_a_refits_lines_are_exported_with_their_acquisition_offsets(self):
        rows = _rows(_refit(_record([_point("NO3-", 61.9884, 80, 0.5)])))
        trend = build_trend(rows)

        (row,) = _csv(trend, rows)

        assert float(row["offset_ppm"]) == pytest.approx(80.0, abs=1e-6)
        assert float(row["axis_offset_ppm"]) == pytest.approx(80.0, abs=1e-6)
        # What the line was observed at is the refit's own reading.
        observed = (float(row["observed_mz"]) / 61.9884 - 1) * 1e6
        assert observed == pytest.approx(0.2, abs=1e-3)

    def test_a_name_a_spreadsheet_would_run_is_written_as_text(self):
        rows = _rows(_record([_point("NO3-", 61.9884, -80, 0.5)]))
        trend = build_trend(rows)
        trend["collections"][0]["name"] = "=HYPERLINK(1)"
        trend["files"][0]["filename"] = "-cmd.h5"

        table = _csv(trend, rows)

        assert table[0]["collection_name"] == "'=HYPERLINK(1)"
        assert table[0]["filename"] == "'-cmd.h5"
        # Numbers stay numbers, the negative ones included.
        assert float(table[0]["offset_ppm"]) < 0

    def test_the_sdks_reader_gives_a_name_back_as_it_was_typed(self, tmp_path):
        rows = _rows(_record([_point("NO3-", 61.9884, -80, 0.5)]))
        trend = build_trend(rows)
        trend["collections"][0]["name"] = "=HYPERLINK(1)"
        trend["files"][0]["filename"] = "-cmd.h5"
        path = tmp_path / "trend.csv"
        buffer = io.StringIO()
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(EXPORT_COLUMNS)
        writer.writerows(trend_rows(trend, rows))
        path.write_text(buffer.getvalue())

        back = read_export_csv(path, sep=",")

        assert back["collection_name"].tolist() == ["=HYPERLINK(1)"]
        assert back["filename"].tolist() == ["-cmd.h5"]
        assert back["offset_ppm"].iloc[0] < 0
