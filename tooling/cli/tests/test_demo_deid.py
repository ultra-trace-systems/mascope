"""
Unit tests for demo raw-file de-identification (``_deidentify_name``).

Pure logic - no infrastructure. Locks in the exact rename transformation.
"""

from mascope_cli.cmd.demo.build_bundle import _deidentify_name


def test_pos_file_renamed():
    name, parsed = _deidentify_name(
        "KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw"
    )
    assert name == "Orbion_pos_Ur_NoRI_20250811142302.raw"
    assert parsed["instrument"] == "KORBI2"
    assert parsed["polarity"] == "pos"
    assert parsed["sample"] == "Ur"
    assert parsed["stamp"] == "20250811142302"


def test_neg_file_renamed():
    name, _ = _deidentify_name(
        "KORBI2_2025.08.11-14h23m58s_neg_Br_NoRI_1_20250811142350.raw"
    )
    assert name == "Orbion_neg_Br_NoRI_20250811142350.raw"


def test_unknown_instrument_kept_with_warning_signal():
    """Unmapped instrument labels are left as-is (caller warns)."""
    name, parsed = _deidentify_name(
        "OTHER_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw"
    )
    assert name == "OTHER_pos_Ur_NoRI_20250811142302.raw"
    assert parsed["instrument"] == "OTHER"


def test_non_matching_name_unchanged():
    """A name that doesn't fit the pattern is returned unchanged with empty parse."""
    name, parsed = _deidentify_name("something_else.raw")
    assert name == "something_else.raw"
    assert parsed == {}


def test_already_deidentified_is_idempotent():
    """Re-running on an already-renamed file leaves it unchanged but still parses."""
    name, parsed = _deidentify_name("Orbion_pos_Ur_NoRI_20250811142302.raw")
    assert name == "Orbion_pos_Ur_NoRI_20250811142302.raw"
    assert parsed["polarity"] == "pos"
    assert parsed["sample"] == "Ur"
    assert parsed["stamp"] == "20250811142302"
    assert parsed["date"] == "2025.08.11"


def test_custom_alias_map():
    name, _ = _deidentify_name(
        "KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw",
        aliases={"KORBI2": "Demo"},
    )
    assert name == "Demo_pos_Ur_NoRI_20250811142302.raw"


# A customer instrument's label is never in the repository's alias map: the
# build gets it from `mascope demo snapshot --alias <label>=Orbion`. The tests
# pass a made-up label the same way.
_SITE_ALIASES = {"SITEORBI": "Orbion"}


def test_plain_name_keeps_its_method_segment():
    """A hand-started acquisition's plain name - no polarity segment, no run
    index - loses only its label and readable timestamp; the method segment
    carries the chemistry token the file binds by, so it stays whole."""
    name, parsed = _deidentify_name(
        "SITEORBI_2026.10.15-03h49m40s_MS2_15NO3_manual-C10H18O2_20261015034940.raw",
        aliases=_SITE_ALIASES,
    )
    assert name == "Orbion_MS2_15NO3_manual-C10H18O2_20261015034940.raw"
    assert parsed["instrument"] == "SITEORBI"
    assert parsed["date"] == "2026.10.15"
    assert parsed["stamp"] == "20261015034940"
    assert "polarity" not in parsed
    assert "sample" not in parsed


def test_plain_name_with_a_plus_in_its_token():
    name, _ = _deidentify_name(
        "SITEORBI_2026.10.16-03h04m43s_NH4+_14N_manual-MS2_20261016030443.raw",
        aliases=_SITE_ALIASES,
    )
    assert name == "Orbion_NH4+_14N_manual-MS2_20261016030443.raw"


def test_plain_name_of_an_unaliased_label_is_kept_for_the_report():
    """Without an alias for its label, a plain name keeps that label, and
    the parse lets the report flag it."""
    name, parsed = _deidentify_name(
        "SITEORBI_2026.10.16-03h04m43s_NH4+_14N_manual-MS2_20261016030443.raw"
    )
    assert name == "SITEORBI_NH4+_14N_manual-MS2_20261016030443.raw"
    assert parsed["instrument"] == "SITEORBI"


def test_sequence_name_is_not_taken_for_a_plain_one():
    """The sequence pattern is tried first, so its polarity and sample
    segments are still parsed out rather than swallowed into `rest`."""
    _, parsed = _deidentify_name(
        "KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw"
    )
    assert parsed["polarity"] == "pos"
    assert parsed["rest"] == "NoRI"


def test_already_deidentified_plain_name_is_idempotent():
    name, parsed = _deidentify_name(
        "Orbion_MS2_15NO3_manual-C10H18O2_20261015034940.raw"
    )
    assert name == "Orbion_MS2_15NO3_manual-C10H18O2_20261015034940.raw"
    assert parsed["instrument"] == "Orbion"
    assert parsed["date"] == "2026.10.15"
    assert "polarity" not in parsed


def test_both_source_instruments_share_the_demo_alias():
    """The second instrument joins the first's workspace on the demo stack,
    which is where the contract and e2e suites look (docs/demo_dataset.md E)."""
    aliases = {"KORBI2": "Orbion", **_SITE_ALIASES}
    plain, _ = _deidentify_name(
        "SITEORBI_2026.10.15-05h06m15s_Ur_RI_manual-MS2_20261015050615.raw",
        aliases=aliases,
    )
    sequence, _ = _deidentify_name(
        "KORBI2_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw",
        aliases=aliases,
    )
    assert plain.split("_", 1)[0] == sequence.split("_", 1)[0] == "Orbion"


def test_report_tells_a_refreshed_label_from_an_unknown_one(tmp_path):
    """A raw set that was de-identified before carries the alias as its
    label; the report says so instead of flagging it as unaliased."""
    from mascope_cli.cmd.demo.build_bundle import _write_deid_report

    renames = [
        {
            "original": "Orbion_pos_Ur_NoRI_20250811142302.raw",
            "new": "Orbion_pos_Ur_NoRI_20250811142302.raw",
            "parsed": _deidentify_name("Orbion_pos_Ur_NoRI_20250811142302.raw")[1],
        },
        {
            "original": "OTHER_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw",
            "new": "OTHER_pos_Ur_NoRI_20250811142302.raw",
            "parsed": _deidentify_name(
                "OTHER_2025.08.11-14h23m12s_pos_Ur_NoRI_1_20250811142302.raw"
            )[1],
        },
        {
            "original": (
                "SITEORBI_2026.10.15-03h49m40s_MS2_15NO3_manual-C10H18O2"
                "_20261015034940.raw"
            ),
            "new": "Orbion_MS2_15NO3_manual-C10H18O2_20261015034940.raw",
            "parsed": _deidentify_name(
                "SITEORBI_2026.10.15-03h49m40s_MS2_15NO3_manual-C10H18O2"
                "_20261015034940.raw",
                aliases=_SITE_ALIASES,
            )[1],
        },
    ]
    bundle = tmp_path / "1.4.0"
    bundle.mkdir()
    report = _write_deid_report(
        bundle, renames, {"KORBI2": "Orbion", **_SITE_ALIASES}
    ).read_text(encoding="utf-8")

    assert "  - SITEORBI -> Orbion" in report
    assert "  - Orbion -> Orbion (already de-identified)" in report
    assert "  - OTHER -> OTHER (NOT aliased!)" in report
    assert "Plain names (method segment kept whole): 1" in report
    assert "  - `Orbion_MS2_15NO3_manual-C10H18O2_20261015034940.raw`" in report
    # The report is a sibling of the bundle, never inside it.
    assert not (bundle / "1.4.0.deid_report.md").exists()
    assert (tmp_path / "1.4.0.deid_report.md").exists()


def test_manifest_acquired_is_the_earliest_day(tmp_path, monkeypatch):
    """A bundle over several days is dated from its first, whatever order
    the raw directory lists the files in, and lists every day."""
    import json

    from mascope_cli.cmd.demo import build_bundle

    raw = tmp_path / "raw"
    raw.mkdir()
    # Sorted by name, the plain 2026 file comes first (upper-case label).
    for name in (
        "SITEORBI_2026.10.15-03h49m40s_MS2_15NO3_manual-C10H18O2_20261015034940.raw",
        "Orbion_pos_Ur_NoRI_20250811142302.raw",
    ):
        (raw / name).write_bytes(b"not a raw file")
    monkeypatch.setattr(build_bundle, "_opentfraw_version", lambda: None)
    monkeypatch.setattr(build_bundle, "_match_score_version", lambda: None)

    out = build_bundle.build(
        out_dir=tmp_path / "1.4.0", raw_dir=raw, aliases=_SITE_ALIASES
    )

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["measurement"]["acquired"] == "2025.08.11"
    assert manifest["measurement"]["acquisition_days"] == ["2025.08.11", "2026.10.15"]
    assert manifest["measurement"]["instrument"] == "Orbion"


def test_alias_option_values_are_parsed_and_checked():
    from mascope_cli.cmd.demo.main import _parse_aliases

    assert _parse_aliases([]) == {}
    assert _parse_aliases(["SITEORBI=Orbion", " OTHER = Demo "]) == {
        "SITEORBI": "Orbion",
        "OTHER": "Demo",
    }
    for bad in ("SITEORBI", "=Orbion", "SITEORBI=", " = "):
        try:
            _parse_aliases([bad])
        except ValueError as e:
            assert "ORIGINAL=ALIAS" in str(e)
        else:
            raise AssertionError(f"{bad!r} was accepted")


def test_command_line_aliases_do_not_touch_the_repository_map():
    """The map in the repository names the maintainer's own instrument only;
    a build's aliases are merged for that build and nothing else."""
    from mascope_cli.cmd.demo.build_bundle import INSTRUMENT_ALIASES

    assert INSTRUMENT_ALIASES == {"KORBI2": "Orbion"}
