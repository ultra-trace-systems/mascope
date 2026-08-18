"""
Unit tests for the demo bundle's golden-coverage guard.

Pure logic - no database, no bundle. Locks in the check that refuses to publish
goldens captured from a rebuild that ingested only part of the raw set (the
defect that shipped bundle v1.0.0 with 152 of its 161 files).
"""

from mascope_cli.cmd.demo.build_bundle import _acquisition_key, check_raw_coverage


def _entries(*names: str) -> list[dict]:
    """Manifest ``raw`` entries for the given published filenames."""
    return [{"name": n, "sha256": "x" * 64, "bytes": 1} for n in names]


def _db_name(published: str) -> str:
    """The filename the demo database holds for a published bundle filename.

    The converter reconstructs it from the file's own metadata, re-inserting the
    acquisition timestamp de-identification stripped and dropping the extension.
    """
    instrument, rest = published.removesuffix(".raw").split("_", 1)
    stamp = rest.rsplit("_", 1)[-1]
    readable = (
        f"{stamp[0:4]}.{stamp[4:6]}.{stamp[6:8]}-"
        f"{stamp[8:10]}h{stamp[10:12]}m{stamp[12:14]}s"
    )
    return f"{instrument}_{readable}_{rest}"


def test_acquisition_key_survives_the_rename():
    """Bundle and database flavours of one acquisition share a key."""
    published = "Orbion_neg_Br_NoRI_20250811142350.raw"
    assert _acquisition_key(published) == "20250811142350"
    assert _acquisition_key(_db_name(published)) == "20250811142350"


def test_acquisition_key_ignores_the_readable_timestamp():
    """The reconstructed `2025.08.11-14h23m50s` segment is not a 14-digit run."""
    assert _acquisition_key("Orbion_2025.08.11-14h23m50s_pos_Ur_NoRI") == (
        "orbion_2025.08.11-14h23m50s_pos_ur_nori"
    )


def test_acquisition_key_falls_back_to_the_stem():
    """A name with no acquisition stamp still keys on something unique."""
    assert _acquisition_key("Something_Else.raw") == "something_else"


def test_acquisition_key_rejects_a_longer_digit_run():
    """A 15-digit run is not silently truncated into a 14-digit match."""
    assert _acquisition_key("Orbion_neg_Br_202508111423501.raw") != "20250811142350"


def test_full_coverage_has_no_problems():
    raw = _entries(
        "Orbion_neg_Br_NoRI_20250811142350.raw",
        "Orbion_pos_Ur_NoRI_20250811142302.raw",
    )
    ingested = [_db_name(e["name"]) for e in raw]
    assert check_raw_coverage(raw, ingested, set(ingested)) == []


def test_missing_file_is_named():
    """The v1.0.0 defect: a raw file the rebuild never ingested."""
    raw = _entries(
        "Orbion_neg_Br_NoRI_20250811142350.raw",
        "Orbion_pos_Ur_NoRI_20250811142302.raw",
    )
    ingested = [_db_name("Orbion_pos_Ur_NoRI_20250811142302.raw")]

    problems = check_raw_coverage(raw, ingested, set(ingested))

    assert len(problems) == 1
    assert "1 of 2 raw file(s) were never ingested" in problems[0]
    assert "Orbion_neg_Br_NoRI_20250811142350.raw" in problems[0]


def test_ingested_file_without_goldens_is_reported_separately():
    """Converted but matched nothing - a different fault than never ingested."""
    raw = _entries(
        "Orbion_neg_Br_NoRI_20250811142350.raw",
        "Orbion_pos_Ur_NoRI_20250811142302.raw",
    )
    ingested = [_db_name(e["name"]) for e in raw]
    goldens = {_db_name("Orbion_pos_Ur_NoRI_20250811142302.raw")}

    problems = check_raw_coverage(raw, ingested, goldens)

    assert len(problems) == 1
    assert "produced no matched peaks" in problems[0]
    assert "Orbion_neg_Br_NoRI_20250811142350.raw" in problems[0]


def test_foreign_sample_file_is_reported():
    """Data from another run in the demo database poisons the goldens too."""
    raw = _entries("Orbion_neg_Br_NoRI_20250811142350.raw")
    ingested = [
        _db_name("Orbion_neg_Br_NoRI_20250811142350.raw"),
        _db_name("Orbion_pos_Ur_NoRI_20250811142302.raw"),
    ]

    problems = check_raw_coverage(raw, ingested, set(ingested))

    assert len(problems) == 1
    assert "not in the bundle's raw set" in problems[0]
    assert "20250811142302" in problems[0]


def test_shared_stamp_is_compared_by_count():
    """Two files sharing a stamp need both sides to have both."""
    raw = _entries(
        "Orbion_neg_Br_NoRI_20250811142350.raw",
        "Orbion_pos_Br_NoRI_20250811142350.raw",
    )
    ingested = [_db_name("Orbion_neg_Br_NoRI_20250811142350.raw")]

    problems = check_raw_coverage(raw, ingested, set(ingested))

    assert len(problems) == 1
    assert "1 of 2 raw file(s) were never ingested" in problems[0]


def test_long_lists_are_truncated():
    """A wholesale failure names a sample, not 161 filenames."""
    raw = _entries(*(f"Orbion_neg_Br_NoRI_202508111420{i:02d}.raw" for i in range(30)))

    problems = check_raw_coverage(raw, [], set())

    assert "30 of 30 raw file(s) were never ingested" in problems[0]
    assert "... and 10 more" in problems[0]
