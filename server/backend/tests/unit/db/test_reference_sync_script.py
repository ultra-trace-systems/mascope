"""The backend's reference_sync script, which loads a dump on a server.

`mascope reference` is a monorepo command, so on a deployment this script is
the only way a dump is loaded. A spectral library goes to the spectral ingest
with what the command line says, its window flags are refused before any
database work, and a load the ingest refuses ends the script with its reason.
The publication ``--citation`` with ``--doi`` or ``--isbn`` names reaches
either ingest, and one that does not cite its source ends the script first.
Hermetic: the ingest and the engine are stood in for.
"""

import sys

import pytest

from mascope_backend.db.scripts import reference_sync as script
from mascope_reference.ingest import (
    EmptyIngest,
    IngestResult,
    SourceKindConflict,
    SpectrumIngestResult,
)
from mascope_reference.scope import UNBOUNDED, SourceScope


class _Engine:
    def dispose(self):
        pass


@pytest.fixture
def dump(tmp_path):
    path = tmp_path / "MassBank-data-2026.03.tar.gz"
    path.write_bytes(b"")
    return path


@pytest.fixture
def spectral_ingest(monkeypatch):
    """Record what the script hands the spectral ingest."""
    seen = {}

    def _ingest_spectra(engine, adapter, path, version, **kwargs):
        seen.update(kwargs, adapter=adapter, path=path, version=version)
        return SpectrumIngestResult(
            source=kwargs["source_name"] or adapter.name,
            version=version,
            reference_source_id=3,
            ingested=113,
            skipped={"not MS2": 22},
        )

    def _no_compound_ingest(*args, **kwargs):
        raise AssertionError("a spectral library is not loaded as compounds")

    monkeypatch.setattr(script, "_sync_engine", _Engine)
    monkeypatch.setattr(script, "ingest_spectra", _ingest_spectra)
    monkeypatch.setattr(script, "ingest", _no_compound_ingest)
    return seen


def _run(monkeypatch, *argv: str) -> None:
    monkeypatch.setattr(sys, "argv", ["reference_sync", *argv])
    script.main()


def test_a_spectral_library_goes_to_the_spectral_ingest(
    monkeypatch, dump, spectral_ingest
):
    _run(monkeypatch, "massbank", str(dump), "--version", "2026.03")
    assert spectral_ingest["adapter"].name == "massbank"
    assert spectral_ingest["path"] == dump
    assert spectral_ingest["version"] == "2026.03"
    assert spectral_ingest["source_name"] is None
    assert spectral_ingest["batch_size"] == 5000
    assert spectral_ingest["activate"] is True
    assert spectral_ingest["prune"] is False


def test_the_spectral_load_takes_a_name_stages_and_prunes(
    monkeypatch, dump, spectral_ingest
):
    _run(
        monkeypatch,
        "mona",
        str(dump),
        "--version",
        "1",
        "--name",
        "mona-negative",
        "--batch-size",
        "100",
        "--stage",
        "--prune",
    )
    assert spectral_ingest["source_name"] == "mona-negative"
    assert spectral_ingest["batch_size"] == 100
    assert spectral_ingest["activate"] is False
    assert spectral_ingest["prune"] is True


@pytest.mark.parametrize(
    "flag",
    [
        ["--elements", "C,H,O"],
        ["--max-carbon", "20"],
        ["--max-mass", "any"],
        ["--allow-radicals"],
        ["--no-allow-radicals"],
        ["--polarity", "both"],
    ],
)
def test_window_flags_are_refused_for_a_spectral_library(monkeypatch, dump, flag):
    def _boom():
        raise AssertionError("the database must not be touched")

    monkeypatch.setattr(script, "_sync_engine", _boom)
    with pytest.raises(SystemExit, match="'gnps' is a spectral library"):
        _run(monkeypatch, "gnps", str(dump), "--version", "1", *flag)


def test_a_missing_library_dump_is_refused(monkeypatch, tmp_path):
    with pytest.raises(SystemExit, match="Reference dump not found"):
        _run(monkeypatch, "gnps", str(tmp_path / "nope.mgf"), "--version", "1")


@pytest.mark.parametrize(
    "error", [EmptyIngest("no usable spectra"), SourceKindConflict("holds compounds")]
)
def test_a_refused_spectral_load_ends_with_its_reason(monkeypatch, dump, error):
    def _refuse(*args, **kwargs):
        raise error

    monkeypatch.setattr(script, "_sync_engine", _Engine)
    monkeypatch.setattr(script, "ingest_spectra", _refuse)
    with pytest.raises(SystemExit, match=str(error)):
        _run(monkeypatch, "massbank", str(dump), "--version", "1")


def test_a_compound_load_under_a_librarys_name_ends_with_its_reason(
    monkeypatch, tmp_path
):
    dump = tmp_path / "list.csv"
    dump.write_text("name,formula\nGlucose,C6H12O6\n", encoding="utf-8")

    def _refuse(*args, **kwargs):
        raise SourceKindConflict("'massbank' holds spectra")

    monkeypatch.setattr(script, "_sync_engine", _Engine)
    monkeypatch.setattr(script, "ingest", _refuse)
    with pytest.raises(SystemExit, match="'massbank' holds spectra"):
        _run(monkeypatch, "custom", str(dump), "--version", "1", "--name", "massbank")


# --- What a load cites ------------------------------------------------------------

_CITED = ["--citation", "A. Author, A paper.", "--doi", "10.1234/t.1"]


@pytest.fixture
def csv_dump(tmp_path):
    path = tmp_path / "list.csv"
    path.write_text("name,formula\nGlucose,C6H12O6\n", encoding="utf-8")
    return path


@pytest.fixture
def compound_ingest(monkeypatch):
    """Record what the script hands the compound ingest."""
    seen = {}

    def _ingest(engine, adapter, path, version, **kwargs):
        seen.update(kwargs)
        return IngestResult(
            source=kwargs["source_name"] or adapter.name,
            version=version,
            reference_source_id=4,
            ingested=1,
            skipped=0,
            scope=SourceScope(UNBOUNDED),
            citations=tuple(kwargs["citations"] or ()),
        )

    monkeypatch.setattr(script, "_sync_engine", _Engine)
    monkeypatch.setattr(script, "ingest", _ingest)
    return seen


def test_without_citation_flags_the_dump_says_what_it_cites(
    monkeypatch, csv_dump, compound_ingest
):
    _run(monkeypatch, "custom", str(csv_dump), "--version", "1", "--name", "my-list")
    assert compound_ingest["citations"] is None


def test_the_publication_the_flags_name_reaches_the_compound_ingest(
    monkeypatch, csv_dump, compound_ingest
):
    _run(monkeypatch, "custom", str(csv_dump), "--version", "1", *_CITED)
    assert compound_ingest["citations"] == [
        {"citation": "A. Author, A paper.", "doi": "10.1234/t.1"}
    ]


def test_the_publication_the_flags_name_reaches_the_spectral_ingest(
    monkeypatch, dump, spectral_ingest
):
    _run(monkeypatch, "massbank", str(dump), "--version", "1", *_CITED)
    assert spectral_ingest["citations"] == [
        {"citation": "A. Author, A paper.", "doi": "10.1234/t.1"}
    ]


def test_a_spectral_load_given_no_publication_cites_none(
    monkeypatch, dump, spectral_ingest
):
    _run(monkeypatch, "massbank", str(dump), "--version", "1")
    assert spectral_ingest["citations"] == ()


@pytest.mark.parametrize("source", ["custom", "massbank"])
@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        (["--citation", "A paper."], "neither a DOI nor an ISBN"),
        (["--doi", "10.1234/t.1"], "no citation"),
        (["--citation", "A paper.", "--doi", "doi:10.1234/t.1"], "is not a DOI"),
        (["--citation", "A book.", "--isbn", "978-3-95806-596-4"], "valid ISBN"),
    ],
)
def test_a_citation_that_cannot_be_followed_ends_the_script_first(
    monkeypatch, csv_dump, source, flags, expected
):
    def _boom(*args, **kwargs):
        raise AssertionError("nothing may be loaded")

    monkeypatch.setattr(script, "_sync_engine", _boom)
    monkeypatch.setattr(script, "ingest", _boom)
    monkeypatch.setattr(script, "ingest_spectra", _boom)
    with pytest.raises(SystemExit, match=expected):
        _run(monkeypatch, source, str(csv_dump), "--version", "1", *flags)
