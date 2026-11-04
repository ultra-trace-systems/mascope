"""
Unit tests for the instrument model backfill script.

The script takes, for each instrument with no model, the model its newest
Orbitrap file names: in the props first (the converter's ``instrument_model``,
else the axis fingerprint's source), and in the raw file header only when no
file's props name one. It writes the model with its PSI-MS term. The rules
are tested here with stand-ins; the database side is in
``tests/integration/api/instruments/test_instrument_model_backfill.py``.
Hermetic: no server, no database.
"""

import pytest

from mascope_backend.db.scripts import backfill_instrument_model as script


EXPLORIS = "Orbitrap Exploris 120"
EXPLORIS_TERM = "MS:1003095"


def _props(by_file: dict[str, dict | None]):
    return lambda filename: by_file.get(filename)


def _never(what: str):
    def fail(filename):
        raise AssertionError(f"{what} asked about {filename}")

    return fail


def _instrument(instrument_id="i1", instrument_type="orbi", orbitrap_files=1):
    return {
        "instrument_id": instrument_id,
        "name": f"name-{instrument_id}",
        "instrument_type": instrument_type,
        "orbitrap_files": orbitrap_files,
    }


# --- the entry point --------------------------------------------------------


def test_the_script_can_be_run():
    """`mascope db script run` finds a script by its callable main()."""
    assert callable(script.main)


# --- where the props name a model -------------------------------------------


def test_the_props_name_the_model_the_converter_read():
    assert script.model_in_props({"instrument_model": EXPLORIS}) == EXPLORIS


def test_the_axis_fingerprint_source_names_it_for_an_older_file():
    props = {"axis_fingerprint": {"source": {"instrument_model": "Q Exactive Plus"}}}
    assert script.model_in_props(props) == "Q Exactive Plus"


def test_the_converters_model_comes_before_the_fingerprints():
    props = {
        "instrument_model": EXPLORIS,
        "axis_fingerprint": {"source": {"instrument_model": "Q Exactive"}},
    }
    assert script.model_in_props(props) == EXPLORIS


@pytest.mark.parametrize(
    "props",
    [
        None,
        {},
        {"instrument_model": None},
        {"instrument_model": "  "},
        {"axis_fingerprint": {}},
        {"axis_fingerprint": {"source": {}}},
        {"axis_fingerprint": {"source": {"instrument_model": ""}}},
        {"axis_fingerprint": "off"},
    ],
)
def test_props_that_name_no_model_give_none(props):
    assert script.model_in_props(props) is None


def test_an_empty_converter_model_falls_through_to_the_fingerprint():
    props = {
        "instrument_model": None,
        "axis_fingerprint": {"source": {"instrument_model": "Q Exactive"}},
    }
    assert script.model_in_props(props) == "Q Exactive"


# --- which file names it ----------------------------------------------------


def test_the_newest_file_whose_props_name_a_model_gives_it():
    model, where = script.find_model(
        ["new", "mid", "old"],
        _props(
            {
                "new": {},
                "mid": {"instrument_model": EXPLORIS},
                "old": {"instrument_model": "Q Exactive"},
            }
        ),
        _never("has_raw"),
        _never("model_in_raw"),
    )
    assert (model, where) == (EXPLORIS, "props")


def test_props_of_an_older_file_come_before_the_raw_file_of_a_newer_one():
    """The raw file is opened only when no file's props name the model."""
    model, where = script.find_model(
        ["new", "old"],
        _props({"new": {}, "old": {"instrument_model": EXPLORIS}}),
        _never("has_raw"),
        _never("model_in_raw"),
    )
    assert (model, where) == (EXPLORIS, "props")


def test_the_raw_file_is_read_when_no_props_name_a_model():
    opened = []

    def model_in_raw(filename):
        opened.append(filename)
        return EXPLORIS

    model, where = script.find_model(
        ["new", "old"], _props({}), lambda f: True, model_in_raw
    )
    assert (model, where) == (EXPLORIS, "raw")
    assert opened == ["new"]


def test_a_file_whose_raw_data_was_cleared_is_not_opened():
    opened = []

    def model_in_raw(filename):
        opened.append(filename)
        return EXPLORIS

    model, _ = script.find_model(
        ["cleared", "kept"], _props({}), lambda f: f == "kept", model_in_raw
    )
    assert model == EXPLORIS
    assert opened == ["kept"]


def test_a_raw_file_that_names_nothing_moves_on_to_the_next():
    model, where = script.find_model(
        ["a", "b"],
        _props({}),
        lambda f: True,
        lambda f: None if f == "a" else EXPLORIS,
    )
    assert (model, where) == (EXPLORIS, "raw")


def test_only_a_few_raw_files_are_opened_per_instrument():
    opened = []

    def model_in_raw(filename):
        opened.append(filename)
        return None

    names = [f"f{i}" for i in range(script._RAW_ATTEMPTS + 5)]
    model, where = script.find_model(names, _props({}), lambda f: True, model_in_raw)
    assert (model, where) == (None, "none")
    assert opened == names[: script._RAW_ATTEMPTS]


def test_cleared_raw_files_do_not_use_up_the_attempts():
    opened = []

    def model_in_raw(filename):
        opened.append(filename)
        return EXPLORIS if filename == "kept" else None

    names = [f"cleared{i}" for i in range(script._RAW_ATTEMPTS + 2)] + ["kept"]
    model, _ = script.find_model(
        names, _props({}), lambda f: not f.startswith("cleared"), model_in_raw
    )
    assert model == EXPLORIS
    assert opened == ["kept"]


# --- the PSI-MS term written beside it --------------------------------------


def test_the_term_is_the_one_a_registration_would_store():
    assert script.cv_term_of(EXPLORIS) == EXPLORIS_TERM


def test_a_model_the_vocabulary_does_not_know_gets_no_term():
    assert script.cv_term_of("Some family name") is None


# --- the run over the instruments -------------------------------------------


async def _files(instrument_id):
    return [f"{instrument_id}-file"]


def _recorder(answer=True):
    written = []

    async def write(instrument_id, model, cv_term):
        written.append((instrument_id, model, cv_term))
        return answer

    return written, write


@pytest.mark.asyncio
async def test_each_instrument_gets_the_model_and_its_term():
    written, write = _recorder()
    counts = await script.backfill(
        [_instrument("a"), _instrument("b")],
        _files,
        lambda filenames: (
            (EXPLORIS, "props") if filenames == ["a-file"] else ("Q Exactive", "raw")
        ),
        write,
    )
    assert written == [
        ("a", EXPLORIS, EXPLORIS_TERM),
        ("b", "Q Exactive", script.cv_term_of("Q Exactive")),
    ]
    assert counts["without_model"] == 2
    assert counts["from_props"] == 1
    assert counts["from_raw"] == 1


@pytest.mark.asyncio
async def test_a_model_with_no_term_is_written_with_none():
    written, write = _recorder()
    counts = await script.backfill(
        [_instrument("a")], _files, lambda f: ("Some family name", "props"), write
    )
    assert written == [("a", "Some family name", None)]
    assert counts["with_term"] == 0


@pytest.mark.asyncio
async def test_the_terms_written_are_counted():
    _, write = _recorder()
    counts = await script.backfill(
        [_instrument("a")], _files, lambda f: (EXPLORIS, "props"), write
    )
    assert counts["with_term"] == 1


@pytest.mark.asyncio
async def test_an_instrument_without_orbitrap_files_is_not_searched():
    """A TOF names no model; a name only a mode or a device gave has no files."""
    written, write = _recorder()

    async def files(instrument_id):
        raise AssertionError(f"files of {instrument_id} were listed")

    counts = await script.backfill(
        [
            _instrument("tof", instrument_type="tof", orbitrap_files=0),
            _instrument("named", instrument_type=None, orbitrap_files=0),
        ],
        files,
        _never("find"),
        write,
    )
    assert written == []
    assert counts["tof"] == 1
    assert counts["no_files"] == 1


@pytest.mark.asyncio
async def test_an_instrument_no_file_names_is_left_alone():
    written, write = _recorder()
    counts = await script.backfill(
        [_instrument("a")], _files, lambda f: (None, "none"), write
    )
    assert written == []
    assert counts["not_found"] == 1


@pytest.mark.asyncio
async def test_a_row_a_registration_filled_meanwhile_is_counted_apart():
    _, write = _recorder(answer=False)
    counts = await script.backfill(
        [_instrument("a")], _files, lambda f: (EXPLORIS, "props"), write
    )
    assert counts["filled_meanwhile"] == 1
    assert counts["from_props"] == 0


@pytest.mark.asyncio
async def test_one_failed_write_does_not_end_the_run():
    written = []

    async def write(instrument_id, model, cv_term):
        if instrument_id == "a":
            raise RuntimeError("connection lost")
        written.append(instrument_id)
        return True

    counts = await script.backfill(
        [_instrument("a"), _instrument("b")],
        _files,
        lambda f: (EXPLORIS, "props"),
        write,
    )
    assert written == ["b"]
    assert counts["unwritable"] == 1
    assert counts["from_props"] == 1


# --- the readers ------------------------------------------------------------


def test_only_a_kept_orbitrap_raw_file_counts_as_raw_data(monkeypatch):
    kinds = {"kept.raw": "orbi_raw", "cleared.raw": "orbi", "tof.h5": "tof_h5"}
    monkeypatch.setattr(script, "get_sample_file_type", lambda f: kinds[f])
    assert script._has_raw("kept.raw")
    assert not script._has_raw("cleared.raw")
    assert not script._has_raw("tof.h5")


def test_a_sample_that_cannot_be_typed_has_no_raw_data(monkeypatch):
    def gone(filename):
        raise FileNotFoundError(filename)

    monkeypatch.setattr(script, "get_sample_file_type", gone)
    assert not script._has_raw("gone.raw")


def test_the_raw_model_is_the_headers_model(monkeypatch):
    class Metadata:
        instrument_details = {"Model": EXPLORIS, "SerialNumber": "x"}

    monkeypatch.setattr(script.m_metadata, "metadata_factory", lambda f: Metadata())
    assert script._model_in_raw("kept.raw") == EXPLORIS


@pytest.mark.parametrize("details", [{}, {"Model": ""}, {"Model": None}])
def test_a_header_that_names_no_model_gives_none(monkeypatch, details):
    class Metadata:
        instrument_details = details

    monkeypatch.setattr(script.m_metadata, "metadata_factory", lambda f: Metadata())
    assert script._model_in_raw("kept.raw") is None


def test_an_unreadable_raw_file_gives_none(monkeypatch):
    def broken(filename):
        raise OSError("truncated")

    monkeypatch.setattr(script.m_metadata, "metadata_factory", broken)
    assert script._model_in_raw("kept.raw") is None


def test_unreadable_props_give_none(monkeypatch):
    def broken(filename):
        raise ValueError("not JSON")

    monkeypatch.setattr(script.m_io, "read_props", broken)
    assert script._read_props("x.raw") is None
