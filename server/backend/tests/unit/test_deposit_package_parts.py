"""
Tests for the parts of a deposit package that need no database
(``mascope_backend.deposit``): writing the archive, the README, the licence
file and the RO-Crate rendered from a manifest, the gaps a deposit would
have, and the command line's outcome line.

The package as a whole, built from a dataset, is tested in
``tests/integration/api/export/test_deposit_package.py``.
"""

import copy
import hashlib
import json
import signal
import subprocess
import sys
import textwrap
import zipfile
import zlib
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

import mascope_backend.deposit.__main__ as deposit_main
from mascope_backend.api.new.dataset_package import service as dataset_package_service
from mascope_backend.deposit import DatasetNotFound, collect, package_gaps
from mascope_backend.deposit.archive import write_package
from mascope_backend.deposit.collect import Package, PackageFile
from mascope_backend.deposit.crate import render_crate
from mascope_backend.deposit.documents import render_licence, render_readme
from mascope_backend.runtime import runtime


_CREATED = datetime(2026, 3, 4, 5, 6, 8, tzinfo=timezone.utc)


def _raw_entry(filename: str, included: bool = True) -> dict:
    return {
        "name": f"{filename}.raw" if included else None,
        "sha256": None,
        "bytes": None,
        "included": included,
        "path": f"raw/{filename}.raw" if included else None,
        "sample_file_id": filename,
        "filename": filename,
        "sha256_at_upload": None,
        "cv_terms": {
            "instrument_model": None,
            "ion_source": {"accession": "MS:1000071", "name": "chemical ionization"},
            "mass_analyser": {"accession": "MS:1000484", "name": "orbitrap"},
            "detector": None,
            "polarity": [{"accession": "MS:1000129", "name": "negative scan"}],
        },
        "acquisition": None,
    }


def _manifest(**dataset) -> dict:
    """A manifest as ``collect_package`` makes one, of a dataset of one file."""
    return {
        "schema": "mascope-package/1",
        "created": "2026-03-04T05:06:08Z",
        "dataset": {
            "dataset_id": "ds1",
            "dataset_global_id": "dep:dataset:ds1",
            "dataset_name": "A dataset",
            "dataset_description": None,
            "license": None,
            "authors": None,
            "keywords": None,
            "related_identifiers": None,
            "funding": None,
            **dataset,
        },
        "measurement": {
            "instruments": ["instrument-x"],
            "instrument_types": ["orbi"],
            "instrument_models": [],
            "acquired": {
                "first": "2026-01-01T00:00:00Z",
                "last": "2026-01-01T00:00:00Z",
            },
        },
        "produced_with": {
            "mascope_version": "v1.0.0",
            "match_score_version": 1,
            "peak_assignment_engine_version": "0.5.0",
            "opentfraw_version": "opentfraw==3.1.0",
        },
        "vocabulary": {"PSI-MS": "4.2.6"},
        "provenance": {"deployment_id": "dep"},
        "raw": [_raw_entry("file-a")],
        "batches": [],
        "target_collections": [],
        "ionization_modes": [],
        "instrument_functions": {"path": "instrument_functions.json"},
    }


@pytest.fixture
def raw_source(tmp_path):
    source = tmp_path / "data.raw"
    source.write_bytes(bytes(range(256)) * 4096)
    return source


def _package(raw_source, produced: list | None = None) -> Package:
    """A package of a raw file, a spreadsheet made on demand and a document."""
    manifest = _manifest()
    raw = manifest["raw"][0]
    spreadsheet = {"path": "batches/b.xlsx", "sha256": None, "bytes": None}
    manifest["batches"] = [
        {
            "sample_batch_name": "b",
            "target_collection_ids": [],
            "samples": [],
            "spreadsheet": spreadsheet,
        }
    ]

    async def produce() -> bytes:
        if produced is not None:
            produced.append("batches/b.xlsx")
        return b"PK spreadsheet bytes"

    return Package(
        name="mascope-package_ds1_A_dataset",
        created=_CREATED,
        manifest=manifest,
        files=[
            PackageFile(path=raw["path"], source=str(raw_source), entry=raw),
            PackageFile(
                path="batches/b.xlsx",
                produce=produce,
                entry=spreadsheet,
                compress=False,
            ),
            PackageFile(
                path="instrument_functions.json",
                data=b'{"instrument_functions": []}\n',
                entry=manifest["instrument_functions"],
            ),
        ],
    )


@pytest.mark.asyncio
async def test_the_same_package_writes_the_same_archive(raw_source, tmp_path):
    """Same order, same dates, same permissions: two writes of one package are
    byte for byte the same archive."""
    first = await write_package(_package(raw_source), str(tmp_path / "first.zip"))
    second = await write_package(_package(raw_source), str(tmp_path / "second.zip"))
    assert (tmp_path / "first.zip").read_bytes() == (
        tmp_path / "second.zip"
    ).read_bytes()
    assert first["files"] == second["files"] == 8


@pytest.mark.asyncio
async def test_each_file_is_checksummed_as_it_is_written(raw_source, tmp_path):
    package = _package(raw_source)
    summary = await write_package(package, str(tmp_path / "p.zip"))
    root = package.name
    with zipfile.ZipFile(summary["archive"]) as archive:
        names = [name.removeprefix(f"{root}/") for name in archive.namelist()]
        assert names == [
            "README.md",
            "DATA_LICENSE",
            "raw/file-a.raw",
            "batches/b.xlsx",
            "instrument_functions.json",
            "manifest.json",
            "ro-crate-metadata.json",
            "SHA256SUMS",
        ]
        contents = {name: archive.read(f"{root}/{name}") for name in names}
    manifest = json.loads(contents["manifest.json"])
    raw = manifest["raw"][0]
    assert raw["sha256"] == hashlib.sha256(raw_source.read_bytes()).hexdigest()
    assert raw["bytes"] == raw_source.stat().st_size
    assert manifest["batches"][0]["spreadsheet"]["sha256"] == (
        hashlib.sha256(b"PK spreadsheet bytes").hexdigest()
    )
    assert manifest["instrument_functions"]["bytes"] == len(
        contents["instrument_functions.json"]
    )
    sums = contents["SHA256SUMS"].decode()
    assert sums == "".join(
        f"{hashlib.sha256(contents[name]).hexdigest()}  {name}\n" for name in names[:-1]
    )
    # The made-on-demand spreadsheet is not kept once written.
    assert package.files[1].data is None


@pytest.mark.asyncio
async def test_files_are_dated_the_package_readable_and_deflated_but_a_spreadsheet(
    raw_source, tmp_path
):
    summary = await write_package(_package(raw_source), str(tmp_path / "p.zip"))
    with zipfile.ZipFile(summary["archive"]) as archive:
        infos = {info.filename.split("/", 1)[1]: info for info in archive.infolist()}
    assert {info.date_time for info in infos.values()} == {(2026, 3, 4, 5, 6, 8)}
    assert {info.external_attr >> 16 for info in infos.values()} == {0o100644}
    assert infos["batches/b.xlsx"].compress_type == zipfile.ZIP_STORED
    assert {
        info.compress_type for name, info in infos.items() if name != "batches/b.xlsx"
    } == {zipfile.ZIP_DEFLATED}
    # Deflated at the lowest level, which keeps most of the saving on a raw
    # file for a fraction of the time.
    raw = infos["raw/file-a.raw"]
    deflate = zlib.compressobj(1, zlib.DEFLATED, -15)
    data = raw_source.read_bytes()
    assert raw.compress_size == len(deflate.compress(data) + deflate.flush())


@pytest.mark.asyncio
async def test_a_raw_file_too_large_for_a_plain_zip_header_is_written(
    raw_source, tmp_path, monkeypatch
):
    """A raw file past what a plain zip header holds (4 GiB) is written with
    a ZIP64 one, chosen from its size before the copy starts. The limit is
    lowered here to what the test's file passes."""
    monkeypatch.setattr(zipfile, "ZIP64_LIMIT", 1 << 16)
    package = _package(raw_source)
    summary = await write_package(package, str(tmp_path / "p.zip"))
    with zipfile.ZipFile(summary["archive"]) as archive:
        assert archive.read(f"{package.name}/raw/file-a.raw") == raw_source.read_bytes()


@pytest.mark.asyncio
async def test_a_failed_write_leaves_neither_archive_nor_partial(raw_source, tmp_path):
    package = _package(raw_source)

    async def broken() -> bytes:
        raise OSError("disk full")

    package.files[1].produce = broken
    with pytest.raises(OSError, match="disk full"):
        await write_package(package, str(tmp_path / "out" / "p.zip"))
    assert list((tmp_path / "out").iterdir()) == []


def test_a_dataset_without_a_licence_is_told_so():
    text = render_licence(_manifest())
    assert text.startswith("A dataset\n")
    assert "No licence was recorded" in text
    assert "Apache-2.0" in text


@pytest.mark.parametrize(
    ("licence", "says"),
    [
        ("CC0-1.0", "https://creativecommons.org/publicdomain/zero/1.0/"),
        ("CC-BY-NC-4.0", "https://creativecommons.org/licenses/by-nc/4.0/"),
        ("proprietary", "The authors keep every right to the data."),
        ("LicenseRef-site-terms", "Licence: LicenseRef-site-terms\n\n"),
    ],
)
def test_a_licence_is_named_and_linked_where_it_is_known(licence, says):
    text = render_licence(_manifest(license=licence))
    assert says in text
    assert "No licence was recorded" not in text


def test_the_readme_says_what_is_not_recorded():
    readme = render_readme(_manifest())
    assert readme.startswith("# A dataset\n\n_No description was recorded._\n")
    assert "| Licence | Not recorded |" in readme
    assert "| Authors | Not recorded |" in readme
    assert "| Dataset id | `dep:dataset:ds1` |" in readme
    assert "Nothing was processed: the dataset holds no batches." in readme
    assert "- 1 sample file from instrument-x" in readme
    assert "chemical ionization (MS:1000071)" in readme
    assert "## Not included" not in readme
    assert "| `raw/` |" in readme
    for row in ("`raw/*.mascope.json`", "`batches/`", "`target_collections/`"):
        assert f"| {row} |" not in readme


def test_user_text_cannot_break_the_readme():
    """A name with Markdown in it, or a table's bar, reads as typed."""
    manifest = _manifest(
        dataset_name="Run *1* | `final`",
        keywords=["a|b"],
        authors=[{"name": "Name [x](y)", "orcid": None, "affiliation": None}],
    )
    readme = render_readme(manifest)
    assert readme.startswith("# Run \\*1\\* \\| \\`final\\`\n")
    assert "| Keywords | a\\|b |" in readme
    assert "| Authors | Name \\[x\\](y) |" in readme


def test_the_readme_lists_files_not_included_up_to_a_limit():
    manifest = _manifest()
    manifest["raw"] = [_raw_entry(f"gone-{n:02d}", included=False) for n in range(25)]
    readme = render_readme(manifest)
    assert "Mascope keeps no raw file of 25 sample files" in readme
    assert "- `gone-19`" in readme
    assert "gone-20" not in readme
    assert "- and 5 more, listed in `manifest.json`." in readme
    assert "| `raw/` |" not in readme


def test_the_readme_of_a_deployment_without_an_id():
    manifest = _manifest()
    manifest["provenance"] = {"deployment_id": None}
    del manifest["dataset"]["dataset_global_id"]
    readme = render_readme(manifest)
    assert "| Dataset id | `ds1` |" in readme
    assert "deployment" not in readme
    assert "Packaged on 2026-03-04T05:06:08Z by Mascope v1.0.0." in readme


def test_rendering_does_not_change_the_manifest():
    manifest = _manifest(license="CC-BY-4.0")
    before = copy.deepcopy(manifest)
    render_readme(manifest)
    render_licence(manifest)
    assert manifest == before


def test_a_dataset_whose_record_is_whole_has_no_gap():
    manifest = _manifest(
        license="CC-BY-4.0",
        authors=[{"name": "Author One", "orcid": None, "affiliation": None}],
        dataset_description="Ambient air.",
    )
    assert package_gaps(manifest) == []


def test_each_part_a_deposit_lacks_is_a_gap_in_the_order_to_fix_it():
    manifest = _manifest(authors=[], dataset_description=None)
    manifest["raw"] = []
    gaps = package_gaps(manifest)
    assert [gap["field"] for gap in gaps] == [
        "license",
        "authors",
        "dataset_description",
        "samples",
    ]
    assert all(gap["message"] for gap in gaps)


def test_keywords_related_identifiers_and_funding_are_no_gap():
    """A repository asks for none of them, and neither does a deposit."""
    manifest = _manifest(
        license="proprietary",
        authors=[{"name": "Author One", "orcid": None, "affiliation": None}],
        dataset_description="Ambient air.",
        keywords=None,
        related_identifiers=None,
        funding=None,
    )
    assert package_gaps(manifest) == []


@pytest.mark.parametrize(
    ("size", "says"),
    [
        (0, "0 B"),
        (999, "999 B"),
        (1000, "1.0 kB"),
        (27_900, "27.9 kB"),
        (3_400_000, "3.4 MB"),
        (1_200_000_000, "1.2 GB"),
        (5_000_000_000_000, "5000.0 GB"),
    ],
)
def test_the_notification_gives_an_archives_size_for_a_person(size, says):
    assert dataset_package_service._size(size) == says


def _outcome(capsys) -> dict:
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_the_command_reports_the_package_on_its_last_line(monkeypatch, capsys):
    built = []

    async def build(dataset_id, output):
        built.append((dataset_id, output))
        return {"archive": f"{output}/p.zip", "files": 4, "bytes": 10}

    monkeypatch.setattr(deposit_main, "_build", build)
    assert deposit_main.main(["ds1", "--output", "/somewhere"]) == 0
    assert built == [("ds1", "/somewhere")]
    assert _outcome(capsys) == {
        "package": {"archive": "/somewhere/p.zip", "files": 4, "bytes": 10}
    }


def test_the_command_writes_to_the_envs_temp_directory_by_default(
    monkeypatch, tmp_path, capsys
):
    """Where the operator command finds it in the backend container, and the
    backend empties it when it starts."""
    built = []

    async def build(dataset_id, output):
        built.append(output)
        return {"archive": f"{output}/p.zip", "files": 4, "bytes": 10}

    monkeypatch.setattr(deposit_main, "_build", build)
    monkeypatch.setattr(
        runtime.env, "path", lambda *parts: str(tmp_path.joinpath(*parts))
    )
    assert deposit_main.main(["ds1"]) == 0
    assert built == [str(tmp_path / "temp" / "packages")]
    assert (tmp_path / "temp" / "packages").is_dir()


@pytest.mark.parametrize(
    ("error", "reported"),
    [
        (DatasetNotFound("There is no dataset 'nope'"), "There is no dataset 'nope'"),
        (OSError("disk full"), "Building the package failed: disk full"),
    ],
)
def test_the_command_reports_a_failure_on_its_last_line(
    monkeypatch, capsys, error, reported
):
    async def build(dataset_id, output):
        raise error

    monkeypatch.setattr(deposit_main, "_build", build)
    assert deposit_main.main(["nope", "--output", "/somewhere"]) == 1
    assert _outcome(capsys) == {"error": reported}


def test_a_run_builds_into_a_directory_of_its_own(monkeypatch, tmp_path, capsys):
    """The operator command names each container build a run, so that two
    builds of one dataset share no path."""
    built = []

    async def build(dataset_id, output):
        built.append(output)
        return {"archive": f"{output}/p.zip", "files": 4, "bytes": 10}

    monkeypatch.setattr(deposit_main, "_build", build)
    monkeypatch.setattr(
        runtime.env, "path", lambda *parts: str(tmp_path.joinpath(*parts))
    )
    assert deposit_main.main(["ds1", "--run", "export-1a"]) == 0
    run = tmp_path / "temp" / "packages" / "export-1a"
    assert built == [str(run)]
    assert run.is_dir()


@pytest.mark.parametrize(
    "error", [DatasetNotFound("There is no dataset 'nope'"), OSError("disk full")]
)
def test_a_run_that_fails_removes_its_directory(monkeypatch, tmp_path, error):
    async def build(dataset_id, output):
        (tmp_path / "temp" / "packages" / "export-1a" / "p.zip.part").write_text("")
        raise error

    monkeypatch.setattr(deposit_main, "_build", build)
    monkeypatch.setattr(
        runtime.env, "path", lambda *parts: str(tmp_path.joinpath(*parts))
    )
    assert deposit_main.main(["nope", "--run", "export-1a"]) == 1
    assert not (tmp_path / "temp" / "packages" / "export-1a").exists()
    assert (tmp_path / "temp" / "packages").is_dir()


def test_a_failure_with_an_output_removes_nothing(monkeypatch, tmp_path):
    """Only a run's own directory is removed, never one it was given."""

    async def build(dataset_id, output):
        raise OSError("disk full")

    monkeypatch.setattr(deposit_main, "_build", build)
    assert deposit_main.main(["ds1", "--output", str(tmp_path)]) == 1
    assert tmp_path.is_dir()


@pytest.mark.parametrize(
    "argv",
    [
        ["ds1", "--run", "../packages"],
        ["ds1", "--run", "a/b"],
        ["ds1", "--run", ""],
        ["ds1", "--run", "r1", "--output", "/somewhere"],
    ],
)
def test_a_run_is_one_directorys_name_and_no_output(monkeypatch, argv):
    built = []

    async def build(dataset_id, output):
        built.append(output)

    monkeypatch.setattr(deposit_main, "_build", build)
    with pytest.raises(SystemExit) as exit_:
        deposit_main.main(argv)
    assert exit_.value.code == 2
    assert built == []


_INTERRUPTED_BUILD = textwrap.dedent(
    """
    import asyncio, os, sys
    import mascope_backend.deposit.__main__ as deposit_main
    from mascope_backend.runtime import runtime

    root = sys.argv[1]
    runtime.env.path = lambda *parts: os.path.join(root, *parts)

    async def build(dataset_id, output):
        with open(os.path.join(output, "p.zip.part"), "w") as part:
            part.write("half an archive")
        sys.stderr.write("writing\\n")
        sys.stderr.flush()
        await asyncio.sleep(60)

    deposit_main._build = build
    sys.exit(deposit_main.main(["ds1", "--run", "export-1a"]))
    """
)


def test_an_interrupted_run_removes_its_directory(tmp_path):
    """What the operator command sends a container build it was interrupted
    in: ``docker exec`` passes no signal on, so ``pkill -INT`` does."""
    with subprocess.Popen(
        [sys.executable, "-c", _INTERRUPTED_BUILD, str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    ) as builder:
        for line in builder.stderr:
            if line.strip() == "writing":
                break
        run = tmp_path / "temp" / "packages" / "export-1a"
        assert (run / "p.zip.part").is_file()
        builder.send_signal(signal.SIGINT)
        builder.stderr.read()
        assert builder.wait(timeout=30) != 0
    assert not run.exists()


def _sample_file(sample_file_id: str, filename: str) -> SimpleNamespace:
    """A sample file row, as much of it as a raw entry reads."""
    return SimpleNamespace(
        sample_file_id=sample_file_id,
        filename=filename,
        source_filename=None,
        instrument="x",
        instrument_type="orbi",
        method_file=None,
        datetime=None,
        datetime_utc=None,
        acquisition_timezone=None,
        length=None,
        range=None,
        polarity="-",
        mz_calibration=None,
        instrument_function_id=None,
        sha256=None,
        acquisition=None,
    )


def test_file_names_that_read_alike_in_a_path_get_paths_of_their_own(monkeypatch):
    """A character no file system takes in a name is replaced, and a name
    that then meets another one already taken is marked with its id."""
    monkeypatch.setattr(
        collect, "_stored_data_file", lambda filename: f"/store/{filename}/data.raw"
    )
    files = [
        _sample_file("first", "x_2026.01.01-12h00m00s_a:b"),
        _sample_file("second", "x_2026.01.01-12h00m00s_a_b"),
    ]
    entries, package_files = collect._raw_files(files, {}, None)
    assert [entry["path"] for entry in entries] == [
        "raw/x_2026.01.01-12h00m00s_a_b.raw",
        "raw/x_2026.01.01-12h00m00s_a_b_second.raw",
    ]
    assert [package_file.source for package_file in package_files] == [
        "/store/x_2026.01.01-12h00m00s_a:b/data.raw",
        "/store/x_2026.01.01-12h00m00s_a_b/data.raw",
    ]


def test_the_readme_names_the_commonest_term_first():
    """Two of three files positive: positive leads, alphabet or not."""
    manifest = _manifest()
    positive = [{"accession": "MS:1000130", "name": "positive scan"}]
    manifest["raw"] = [_raw_entry(f"file-{n}") for n in range(3)]
    for entry in manifest["raw"][1:]:
        entry["cv_terms"]["polarity"] = positive
    readme = render_readme(manifest)
    assert (
        "- Polarities: positive scan (MS:1000130), negative scan (MS:1000129)."
    ) in readme


def _crate(manifest: dict, written=None) -> dict:
    """A manifest's crate, by ``@id``, with a raw file written."""
    written = written or [("raw/file-a.raw", "a" * 64, 4)]
    crate = render_crate(manifest, written, sums_size=100)
    return {entity["@id"]: entity for entity in crate["@graph"]}


@pytest.mark.parametrize(
    "licence", ["CC-BY-4.0", "CC0-1.0", "CC-BY-NC-4.0", "CC-BY-SA-4.0"]
)
def test_a_licence_the_dialog_suggests_is_its_spdx_entry(licence):
    crate = _crate(_manifest(license=licence))
    entity = crate[crate["./"]["license"]["@id"]]
    assert entity["@id"] == f"https://spdx.org/licenses/{licence}"
    assert entity["identifier"] == licence
    assert entity["url"].startswith("https://creativecommons.org/")


@pytest.mark.parametrize(
    "licence, name",
    [(None, "No licence recorded"), ("proprietary", "proprietary"), ("MIT", "MIT")],
)
def test_any_other_licence_is_named_as_it_is_recorded(licence, name):
    """RO-Crate requires a licence: one the crate cannot resolve, or none,
    is a local entity saying what was recorded, not a guess at a URL."""
    crate = _crate(_manifest(license=licence))
    entity = crate[crate["./"]["license"]["@id"]]
    assert entity["@id"] == "#license"
    assert entity["name"] == name


def test_authors_are_their_orcid_and_share_an_organization_with_funders():
    crate = _crate(
        _manifest(
            authors=[
                {"name": "One", "orcid": "0000-0002-1825-0097", "affiliation": "Lab"},
                {"name": "Two", "orcid": None, "affiliation": "Lab"},
            ],
            funding=[{"funder": "Lab", "award": None}],
        )
    )
    root = crate["./"]
    assert [ref["@id"] for ref in root["author"]] == [
        "https://orcid.org/0000-0002-1825-0097",
        "#author-2",
    ]
    organizations = [e for e in crate.values() if e["@type"] == "Organization"]
    assert organizations == [
        {"@id": "#organization-1", "@type": "Organization", "name": "Lab"}
    ]
    grant = crate[root["funding"]["@id"]]
    assert grant["name"] == "Funding from Lab"
    assert "identifier" not in grant
    assert grant["funder"] == root["funder"] == {"@id": "#organization-1"}
    assert crate["#author-2"]["affiliation"] == {"@id": "#organization-1"}


@pytest.mark.parametrize(
    "entry, prop, entity_id",
    [
        (
            {"identifier": "10.1/a", "relation": "IsSupplementTo"},
            "citation",
            "https://doi.org/10.1/a",
        ),
        (
            {"identifier": "doi:10.1/a", "relation": "Cites"},
            "citation",
            "https://doi.org/10.1/a",
        ),
        (
            {"identifier": "x", "relation": "isderivedfrom", "identifier_type": "DOI"},
            "isBasedOn",
            "https://doi.org/x",
        ),
        (
            {"identifier": "https://e.org/a", "relation": "IsNewVersionOf"},
            "isBasedOn",
            "https://e.org/a",
        ),
        ({"identifier": "a", "relation": "IsVersionOf"}, "isBasedOn", "#related-1"),
        ({"identifier": "ark:/1/a", "relation": "IsPartOf"}, "isPartOf", "#related-1"),
        # The prefix in any case, and what an IRI cannot hold encoded: in a
        # DOI, ``#`` and ``?`` are characters of its name too.
        (
            {"identifier": "DOI: 10.1/a", "relation": "Cites"},
            "citation",
            "https://doi.org/10.1/a",
        ),
        (
            {
                "identifier": "10.1002/(SICI)1097-4636(199706)35:4<456::AID-JBM6>3.0.CO;2-#",
                "relation": "References",
                "identifier_type": "DOI",
            },
            "citation",
            "https://doi.org/10.1002/(SICI)1097-4636(199706)35:4%3C456::AID-JBM6%3E"
            "3.0.CO;2-%23",
        ),
        (
            {"identifier": "10.1/a b?c%d", "relation": "Cites"},
            "citation",
            "https://doi.org/10.1/a%20b%3Fc%25d",
        ),
        # A URL keeps its fragment, its query and its escapes, as typed.
        (
            {"identifier": "HTTPS://e.org/a b<c>?q=%41#f", "relation": "Cites"},
            "citation",
            "HTTPS://e.org/a%20b%3Cc%3E?q=%41#f",
        ),
    ],
)
def test_a_related_identifier_takes_the_closest_schema_org_property(
    entry, prop, entity_id
):
    crate = _crate(_manifest(related_identifiers=[entry]))
    assert crate["./"][prop] == {"@id": entity_id}
    work = crate[entity_id]
    assert work["identifier"] == entry["identifier"].strip()
    assert f"as {entry['relation']} (DataCite)" in work["description"]


def test_a_related_identifier_the_crate_has_an_entity_by_is_that_entity():
    """Citing Mascope's repository cites the software, which keeps its
    version; one paper under two relations is one work naming both, which
    each property refers to once."""
    mascope = "https://github.com/ultra-trace-systems/mascope"
    crate = _crate(
        _manifest(
            related_identifiers=[
                {"identifier": mascope, "relation": "References"},
                {"identifier": "10.1/a", "relation": "IsSupplementTo"},
                {"identifier": "doi:10.1/a", "relation": "Cites"},
                {"identifier": "DOI:10.1/a", "relation": "IsDerivedFrom"},
            ]
        )
    )
    root = crate["./"]
    software = crate[mascope]
    assert (software["@type"], software["version"]) == ("SoftwareApplication", "v1.0.0")
    assert crate["#package"]["instrument"][0] == {"@id": mascope}
    assert root["citation"] == [{"@id": mascope}, {"@id": "https://doi.org/10.1/a"}]
    assert root["isBasedOn"] == {"@id": "https://doi.org/10.1/a"}
    assert crate["https://doi.org/10.1/a"]["description"] == (
        "Related to the dataset as IsSupplementTo and Cites and IsDerivedFrom "
        "(DataCite)."
    )
    assert [e["@id"] for e in crate.values() if e["@type"] == "CreativeWork"] == [
        "ro-crate-metadata.json",
        "#license",
        "https://spdx.org/licenses/Apache-2.0",
        "https://doi.org/10.1/a",
    ]


def test_a_work_cited_without_an_iri_is_one_the_dataset_mentions():
    """RO-Crate cites by absolute URI alone: a work cited by an identifier
    that is neither a URL nor a DOI is mentioned instead."""
    crate = _crate(
        _manifest(
            related_identifiers=[
                {"identifier": "PMID 1", "relation": "Cites"},
                {"identifier": "isbn 2", "relation": "IsVersionOf"},
            ]
        )
    )
    root = crate["./"]
    assert "citation" not in root
    assert root["mentions"] == [{"@id": "#package"}, {"@id": "#related-1"}]
    assert root["isBasedOn"] == {"@id": "#related-2"}
    assert crate["#related-1"]["identifier"] == "PMID 1"


def test_each_file_has_the_media_type_of_its_extension():
    written = [
        ("raw/a.raw", "a" * 64, 4),
        ("raw/b.h5", "b" * 64, 4),
        ("raw/a.raw.mascope.json", "c" * 64, 4),
        ("batches/b.xlsx", "d" * 64, 4),
        ("README.md", "e" * 64, 4),
        ("DATA_LICENSE", "f" * 64, 4),
    ]
    crate = _crate(_manifest(), written=written)
    assert {path: crate[path]["encodingFormat"] for path, _, _ in written} == {
        "raw/a.raw": "application/octet-stream",
        "raw/b.h5": "application/x-hdf5",
        "raw/a.raw.mascope.json": "application/json",
        "batches/b.xlsx": (
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        ),
        "README.md": "text/markdown",
        "DATA_LICENSE": "text/plain",
    }
    assert crate["SHA256SUMS"]["encodingFormat"] == "text/plain"


def test_a_term_two_files_share_is_one_measurement_technique():
    manifest = _manifest()
    manifest["raw"].append(_raw_entry("file-b"))
    crate = _crate(manifest)
    assert crate["./"]["measurementTechnique"] == [
        {"@id": "http://purl.obolibrary.org/obo/MS_1000071"},
        {"@id": "http://purl.obolibrary.org/obo/MS_1000484"},
    ]


def test_two_grants_from_one_funder_name_it_once():
    crate = _crate(
        _manifest(
            funding=[{"funder": "Fund", "award": "1"}, {"funder": "Fund", "award": "2"}]
        )
    )
    root = crate["./"]
    assert [crate[ref["@id"]]["name"] for ref in root["funding"]] == [
        "Fund, 1",
        "Fund, 2",
    ]
    assert root["funder"] == {"@id": "#organization-1"}


def test_a_path_is_an_id_with_only_what_a_uri_cannot_hold_encoded():
    """Spaces and ``#`` would break a relative URI; RO-Crate 1.2 keeps
    characters outside ASCII as they are."""
    manifest = _manifest()
    manifest["raw"][0]["path"] = "raw/a b#ä%.raw"
    crate = _crate(manifest, written=[("raw/a b#ä%.raw", "a" * 64, 4)])
    assert crate["raw/"]["hasPart"] == {"@id": "raw/a%20b%23ä%25.raw"}
    assert crate["raw/a%20b%23ä%25.raw"]["name"] == "file-a"


def test_a_list_of_one_is_its_value_and_an_empty_one_is_left_out():
    crate = _crate(_manifest(authors=[{"name": "One"}], keywords=None))
    root = crate["./"]
    assert root["author"] == {"@id": "#author-1"}
    assert "keywords" not in root
    assert "funding" not in root and "funder" not in root
    assert root["description"] == "No description was recorded."


def test_a_deployment_without_an_id_names_the_dataset_by_its_local_id():
    manifest = _manifest(dataset_global_id=None)
    manifest["provenance"] = {"deployment_id": None}
    crate = _crate(manifest)
    identifier = crate[crate["./"]["identifier"]["@id"]]
    assert (identifier["propertyID"], identifier["value"]) == ("mascope-id", "ds1")
    assert "deployment" not in crate["#package"]["description"]


def test_without_the_raw_file_reader_mascope_alone_wrote_the_package():
    manifest = _manifest()
    manifest["produced_with"]["opentfraw_version"] = None
    crate = _crate(manifest)
    assert crate["#package"]["instrument"] == {
        "@id": "https://github.com/ultra-trace-systems/mascope"
    }


@pytest.mark.asyncio
async def test_the_crate_gives_sha256sums_its_size_in_bytes(raw_source, tmp_path):
    """The crate is written before ``SHA256SUMS``, which then checks it, and
    knows that file's size already: in bytes, a path outside ASCII too."""
    package = _package(raw_source)
    package.manifest["raw"][0]["path"] = "raw/fïle-ä.raw"
    package.files[0].path = "raw/fïle-ä.raw"
    summary = await write_package(package, str(tmp_path / "p.zip"))
    with zipfile.ZipFile(summary["archive"]) as archive:
        sums = archive.read(f"{package.name}/SHA256SUMS")
        crate = json.loads(archive.read(f"{package.name}/ro-crate-metadata.json"))
    entities = {entity["@id"]: entity for entity in crate["@graph"]}
    assert entities["SHA256SUMS"]["contentSize"] == str(len(sums))
    assert "  ro-crate-metadata.json\n" in sums.decode()
    assert entities["raw/fïle-ä.raw"]["sha256"] == (
        hashlib.sha256(raw_source.read_bytes()).hexdigest()
    )
