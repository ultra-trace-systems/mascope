"""
Unit tests for the axis fingerprint backfill script.

The script decides which files need a document (none, a failed one, or one
computed by another version or with other parameters), which of those are
worth opening, what to do with each answer, how many files one run may read,
and under which reader it may run at all. All of it is tested with stand-ins,
and the read itself against the committed KORBI file. Hermetic: no server, no
database.
"""

import contextlib
import os
from pathlib import Path

import pytest

from mascope_backend.db.scripts import backfill_axis_fingerprint as script
from mascope_thermo import axis_fingerprint as m_axf
from mascope_thermo.axis_fingerprint import reference
from mascope_thermo.thermo import NoScansFoundError


_REPO_ROOT = Path(__file__).resolve().parents[5]
KORBI_POS = (
    _REPO_ROOT / "libraries/thermo/tests/test_files/KORBI2_AMB_POS_20260109174345.raw"
)


def _document(status="ok", **changes) -> dict:
    """A stored document as this build computes it, with ``changes`` on top."""
    document = {
        "schema": reference.SCHEMA,
        "version": reference.VERSION,
        "status": status,
        "extractor": {
            "name": m_axf.EXTRACTOR_NAME,
            "reader": "opentfraw 2.0.0",
            "params": dict(m_axf.PARAMS),
        },
        "modes": [{"mode_id": "pos_R70000_50-750", "segments": {"rows": [[0], [1]]}}],
    }
    document.update(changes)
    return document


CURRENT = {"axis_fingerprint": _document()}
NONE_YET = {"range": [50, 750]}


def _candidates(*names: str) -> list[dict]:
    return [{"sample_file_id": f"sf{i}", "filename": n} for i, n in enumerate(names)]


def _always(value):
    return lambda *_args: value


# --- the entry point --------------------------------------------------------


def test_the_script_can_be_run():
    """`mascope db script run` finds a script by its callable main()."""
    assert callable(script.main)


# --- which documents are computed (the version rule) ------------------------


def test_a_file_converted_before_the_fingerprint_existed_needs_one():
    assert script.why_compute(NONE_YET) == "missing"


def test_an_empty_document_needs_one():
    """What the converter stores with ``[backend] axis_fingerprint = "off"``."""
    assert script.why_compute({"axis_fingerprint": {}}) == "missing"


@pytest.mark.parametrize("status", ["unavailable", "error", "missing"])
def test_a_document_that_describes_nothing_is_computed_again(status):
    stored = reference.minimal_document(status, "ValueError: x")
    assert script.why_compute({"axis_fingerprint": stored}) == "status"


@pytest.mark.parametrize("status", ["ok", "no_fingerprint"])
def test_a_current_document_is_left_alone(status):
    assert script.why_compute({"axis_fingerprint": _document(status)}) is None


def test_a_document_of_another_version_is_computed_again():
    stored = _document(version=reference.VERSION + 1)
    assert script.why_compute({"axis_fingerprint": stored}) == "version"


def test_a_document_computed_with_other_parameters_is_computed_again():
    stored = _document()
    stored["extractor"]["params"] = dict(m_axf.PARAMS, n_scans=4)
    assert script.why_compute({"axis_fingerprint": stored}) == "params"


def test_a_document_without_its_parameters_is_computed_again():
    stored = _document()
    del stored["extractor"]
    assert script.why_compute({"axis_fingerprint": stored}) == "params"


def test_the_rule_compares_with_the_parameters_the_converter_uses():
    """The thresholds on OpenTFRaw's profile, not the Thermo library's.

    Comparing with ``reference.PARAMS`` would call every document the
    converter writes out of date, and a second run would recompute them all.
    """
    assert m_axf.PARAMS == reference.PARAMS_OPENTFRAW != reference.PARAMS
    assert script.why_compute(CURRENT) is None


# --- which files are offered (the selection) --------------------------------


@pytest.mark.asyncio
async def test_the_survey_keeps_the_candidate_order():
    """A bounded run reads the head of this list, so its order is the policy."""
    pending, counts = await script._survey(
        _candidates("c.raw", "b.raw", "a.raw"), _always(NONE_YET), _always(True)
    )
    assert pending == ["c.raw", "b.raw", "a.raw"]
    assert counts["surveyed"] == 3
    assert counts["why_missing"] == 3


@pytest.mark.asyncio
async def test_the_survey_counts_what_it_does_not_return():
    old = _document(version=reference.VERSION + 1)
    pending, counts = await script._survey(
        _candidates(
            "done.raw", "todo.raw", "old.raw", "gone.raw", "tried.raw", "noraw.raw"
        ),
        {
            "done.raw": CURRENT,
            "todo.raw": NONE_YET,
            "old.raw": {"axis_fingerprint": old},
            "gone.raw": None,
            "tried.raw": {"axis_fingerprint_backfill": {"result": "reader"}},
            "noraw.raw": NONE_YET,
        }.get,
        lambda name: name != "noraw.raw",
    )
    assert pending == ["todo.raw", "old.raw"]
    assert counts["current"] == 1
    assert counts["no_props"] == 1
    assert counts["already_tried"] == 1
    assert counts["no_raw"] == 1
    assert counts["why_missing"] == 1
    assert counts["why_version"] == 1


@pytest.mark.asyncio
async def test_a_file_whose_raw_data_was_cleared_is_left_out_without_opening_it():
    asked = []

    def has_raw(filename):
        asked.append(filename)
        return False

    pending, counts = await script._survey(
        _candidates("gone.raw"), _always(NONE_YET), has_raw
    )
    assert pending == []
    assert counts["no_raw"] == 1
    assert asked == ["gone.raw"]


@pytest.mark.asyncio
async def test_a_current_file_is_not_even_checked_for_raw_data():
    """The second run over a filled server opens nothing at all."""
    asked = []
    pending, counts = await script._survey(
        _candidates("done.raw"), _always(CURRENT), lambda name: asked.append(name)
    )
    assert pending == []
    assert counts["current"] == 1
    assert asked == []


@pytest.mark.asyncio
async def test_a_file_tried_before_is_skipped_until_the_retry_flag():
    tried = {
        "axis_fingerprint": reference.minimal_document("error", "ValueError: x"),
        "axis_fingerprint_backfill": {"at": "2026-10-26T00:00:00Z", "result": "error"},
    }

    pending, counts = await script._survey(
        _candidates("tried.raw"), _always(tried), _always(True)
    )
    assert pending == []
    assert counts["already_tried"] == 1

    pending, counts = await script._survey(
        _candidates("tried.raw"), _always(tried), _always(True), retry=True
    )
    assert pending == ["tried.raw"]
    assert counts["already_tried"] == 0


@pytest.mark.asyncio
async def test_a_file_tried_before_whose_document_is_now_current_counts_as_current():
    """An attempt record next to a current document says nothing any more."""
    props = {**CURRENT, "axis_fingerprint_backfill": {"result": "reader"}}
    pending, counts = await script._survey(
        _candidates("a.raw"), _always(props), _always(True)
    )
    assert pending == []
    assert counts["current"] == 1


@pytest.mark.asyncio
async def test_the_survey_stops_at_the_cap():
    read = []

    def read_props(filename):
        read.append(filename)
        return NONE_YET

    total = script._SURVEY_BATCH * 3
    pending, counts = await script._survey(
        _candidates(*[f"f{i}.raw" for i in range(total)]),
        read_props,
        _always(True),
        limit=5,
    )
    assert pending == [f"f{i}.raw" for i in range(5)]
    assert counts["surveyed"] == script._SURVEY_BATCH
    assert len(read) == script._SURVEY_BATCH


# --- what to do with each answer (the attempt record) -----------------------


def _recorder():
    written: dict[str, dict] = {}
    errors: dict[str, dict] = {}
    attempts: dict[str, str] = {}

    def write(filename, document):
        written[filename] = document

    def write_error(filename, document):
        errors[filename] = document

    def write_attempt(filename, result):
        attempts[filename] = result

    return written, errors, attempts, write, write_error, write_attempt


@pytest.mark.asyncio
async def test_each_readable_file_gets_its_document():
    written, errors, attempts, *writers = _recorder()
    no_fingerprint = _document("no_fingerprint")

    counts = await script._fill(
        ["a.raw", "b.raw"],
        {"a.raw": (_document(), None, None), "b.raw": (no_fingerprint, None, None)}.get,
        *writers,
    )

    assert written == {"a.raw": _document(), "b.raw": no_fingerprint}
    assert errors == {}
    assert attempts == {}
    assert counts["ok"] == 1
    assert counts["no_fingerprint"] == 1


@pytest.mark.asyncio
async def test_an_error_document_is_stored_with_the_attempt():
    """Stored, so the routes serve the error; recorded, so the next run moves
    past a file whose status alone would have it computed again."""
    written, errors, attempts, *writers = _recorder()
    failed = reference.minimal_document("error", "ValueError: no profile")

    counts = await script._fill(["bad.raw"], _always((failed, None, None)), *writers)

    assert written == {}
    assert errors == {"bad.raw": failed}
    assert counts["error"] == 1


@pytest.mark.asyncio
async def test_the_failures_that_leave_no_document_are_recorded_apart():
    written, errors, attempts, *writers = _recorder()

    counts = await script._fill(
        ["gone.raw", "bad.raw", "empty.raw", "odd.raw"],
        {
            "gone.raw": (None, "no_raw", None),
            "bad.raw": (None, "reader", ValueError("the reader said no")),
            "empty.raw": (None, "empty", None),
            "odd.raw": (reference.minimal_document("unavailable"), None, None),
        }.get,
        *writers,
    )

    assert written == {}
    assert errors == {}
    # A file with no raw data is left out by the survey anyway, so it keeps
    # no marker; an unavailable document is not stored over what is there.
    assert attempts == {
        "bad.raw": "reader",
        "empty.raw": "empty",
        "odd.raw": "unavailable",
    }
    assert counts["no_raw"] == 1
    assert counts["reader"] == 1
    assert counts["empty"] == 1
    assert counts["unexpected"] == 1


@pytest.mark.asyncio
async def test_one_failed_write_does_not_end_the_run():
    written, errors, attempts, _write, write_error, write_attempt = _recorder()

    def write(filename, document):
        if filename == "gone.raw":
            raise FileNotFoundError(filename)
        written[filename] = document

    counts = await script._fill(
        ["a.raw", "gone.raw", "b.raw"],
        _always((_document(), None, None)),
        write,
        write_error,
        write_attempt,
    )

    assert set(written) == {"a.raw", "b.raw"}
    assert counts["ok"] == 2
    assert counts["unwritable"] == 1


def _capture_update_props(monkeypatch) -> dict:
    updates = {}
    monkeypatch.setattr(
        script.m_io,
        "update_props",
        lambda filename, fields: updates.__setitem__(filename, fields),
    )
    return updates


def test_writing_a_document_clears_an_earlier_failed_attempt(monkeypatch):
    updates = _capture_update_props(monkeypatch)
    script._write_document("retried.raw", _document())
    assert updates["retried.raw"] == {
        script.DOCUMENT_FIELD: _document(),
        script.ATTEMPT_FIELD: None,
    }


def test_an_error_document_and_its_attempt_are_one_write(monkeypatch):
    updates = _capture_update_props(monkeypatch)
    failed = reference.minimal_document("error", "ValueError: x")
    script._write_error("bad.raw", failed)
    assert updates["bad.raw"][script.DOCUMENT_FIELD] == failed
    assert updates["bad.raw"][script.ATTEMPT_FIELD]["result"] == "error"


def test_an_attempt_leaves_the_document_alone(monkeypatch):
    updates = _capture_update_props(monkeypatch)
    script._write_attempt("bad.raw", "reader")
    assert set(updates["bad.raw"]) == {script.ATTEMPT_FIELD}
    assert updates["bad.raw"][script.ATTEMPT_FIELD]["result"] == "reader"
    assert updates["bad.raw"][script.ATTEMPT_FIELD]["at"]


# --- the bound --------------------------------------------------------------


def test_no_limit_by_default(monkeypatch):
    monkeypatch.delenv("AXF_LIMIT", raising=False)
    assert script._limit() is None


def test_the_limit_is_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("AXF_LIMIT", "500")
    assert script._limit() == 500


@pytest.mark.parametrize("value", ["0", "-1", "lots", "5k", "5e3"])
def test_a_limit_that_is_not_a_positive_number_is_refused(monkeypatch, value):
    monkeypatch.setenv("AXF_LIMIT", value)
    with pytest.raises(script.BadLimit):
        script._limit()


# --- the reader -------------------------------------------------------------


def test_the_thermo_reader_is_refused(monkeypatch):
    """Every document would come out unavailable, and be written."""
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "thermo")
    with pytest.raises(script.NoReader):
        script._reader()


def test_an_opentfraw_too_old_for_the_profile_is_refused(monkeypatch):
    import opentfraw

    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.setattr(opentfraw, "__version__", "1.4.0")
    with pytest.raises(script.NoReader):
        script._reader()


def test_the_installed_opentfraw_is_accepted(monkeypatch):
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    assert script._reader() == "opentfraw"


# --- the run: the dry run, and the refusal before any work ------------------


@pytest.fixture
def stand_ins(monkeypatch):
    """``run()`` on stand-ins: two files with no document, their raw data
    kept, each computed to ``ok``. Records every props write."""

    async def no_engine():
        pass

    async def candidates():
        return _candidates("a.raw", "b.raw")

    computed = []

    def compute(filename, reader):
        computed.append((filename, reader))
        return _document(), None, None

    monkeypatch.setattr(script, "configure_database_engine", no_engine)
    monkeypatch.setattr(script, "_candidates", candidates)
    monkeypatch.setattr(script, "_read_props", _always(NONE_YET))
    monkeypatch.setattr(script, "_has_raw", _always(True))
    monkeypatch.setattr(script, "_compute", compute)
    monkeypatch.delenv("MASCOPE_THERMO_BACKEND", raising=False)
    monkeypatch.delenv("DRY_RUN", raising=False)
    monkeypatch.delenv("AXF_LIMIT", raising=False)
    monkeypatch.delenv("AXF_RETRY", raising=False)
    return computed, _capture_update_props(monkeypatch)


@pytest.mark.asyncio
async def test_a_run_writes_each_document(stand_ins):
    computed, updates = stand_ins
    await script.run()
    assert computed == [("a.raw", "opentfraw"), ("b.raw", "opentfraw")]
    assert set(updates) == {"a.raw", "b.raw"}
    assert updates["a.raw"][script.DOCUMENT_FIELD] == _document()


@pytest.mark.asyncio
async def test_a_dry_run_computes_a_sample_and_writes_nothing(stand_ins, monkeypatch):
    computed, updates = stand_ins
    monkeypatch.setenv("DRY_RUN", "1")
    await script.run()
    assert computed, "a dry run shows what a document holds"
    assert updates == {}


@pytest.mark.asyncio
async def test_a_dry_run_surveys_past_the_cap(stand_ins, monkeypatch):
    """Its counts are the point of it, and they are true of the whole server
    only when the survey went through every file."""
    computed, updates = stand_ins
    surveyed = []

    async def survey(candidates, read_props, has_raw, limit=None, retry=False):
        surveyed.append(limit)
        return [], {key: 0 for key in _COUNTS}

    monkeypatch.setattr(script, "_survey", survey)
    monkeypatch.setenv("AXF_LIMIT", "1")
    monkeypatch.setenv("DRY_RUN", "1")
    await script.run()
    monkeypatch.delenv("DRY_RUN")
    await script.run()
    assert surveyed == [None, 1]


_COUNTS = (
    "surveyed",
    "current",
    "no_props",
    "no_raw",
    "already_tried",
    "why_missing",
    "why_status",
    "why_version",
    "why_params",
)


@pytest.mark.asyncio
async def test_the_retry_flag_reaches_the_survey(stand_ins, monkeypatch):
    retried = []

    async def survey(candidates, read_props, has_raw, limit=None, retry=False):
        retried.append(retry)
        return [], {key: 0 for key in _COUNTS}

    monkeypatch.setattr(script, "_survey", survey)
    await script.run()
    monkeypatch.setenv("AXF_RETRY", "1")
    await script.run()
    assert retried == [False, True]


@pytest.mark.asyncio
async def test_a_run_under_a_reader_that_cannot_compute_reads_nothing(
    stand_ins, monkeypatch
):
    computed, updates = stand_ins
    monkeypatch.setenv("MASCOPE_THERMO_BACKEND", "thermo")
    with pytest.raises(script.NoReader):
        await script.run()
    assert computed == []
    assert updates == {}


# --- the read itself --------------------------------------------------------


@pytest.mark.skipif(not KORBI_POS.exists(), reason="KORBI test file not present")
def test_the_document_of_a_real_file_is_computed_from_its_raw_data(monkeypatch):
    monkeypatch.setattr(
        script.m_name, "filename_to_datafile_path", lambda name: str(KORBI_POS)
    )

    document, reason, error = script._compute("korbi.raw", "opentfraw")

    assert (reason, error) == (None, None)
    assert document["status"] in script.DESCRIBED
    # What a second run reads it back as: current.
    assert script.why_compute({"axis_fingerprint": document}) is None


def test_a_missing_raw_file_answers_no_raw(monkeypatch):
    monkeypatch.setattr(
        script.m_name,
        "filename_to_datafile_path",
        lambda name: os.path.join("no", "such", "data.raw"),
    )
    assert script._compute("missing.raw", "opentfraw") == (None, "no_raw", None)


def test_a_sample_with_no_raw_data_is_not_offered(monkeypatch):
    monkeypatch.setattr(script.m_name, "get_sample_file_type", lambda name: "orbi_zarr")
    assert not script._has_raw("archived.raw")

    monkeypatch.setattr(script.m_name, "get_sample_file_type", lambda name: "orbi_raw")
    assert script._has_raw("kept.raw")


def _raising_compute(monkeypatch, tmp_path, error: Exception) -> None:
    """A raw file that is there, and a computation that raises ``error``."""
    raw = tmp_path / "data.raw"
    raw.write_bytes(b"")
    monkeypatch.setattr(
        script.m_name, "filename_to_datafile_path", lambda name: str(raw)
    )
    monkeypatch.setattr(script, "open_backend", lambda path: contextlib.nullcontext())

    def compute(backend, *, reader_name):
        raise error

    monkeypatch.setattr(script.m_axf, "compute_axis_fingerprint", compute)


def test_a_file_of_no_scans_answers_empty(monkeypatch, tmp_path):
    """The library raises for a file holding no scans rather than hand back a
    document, so the converter can refuse it; here it is an attempt."""
    _raising_compute(monkeypatch, tmp_path, NoScansFoundError("no scans"))
    assert script._compute("empty.raw", "opentfraw") == (None, "empty", None)


def test_a_reader_that_cannot_open_the_file_answers_reader(monkeypatch, tmp_path):
    cause = OSError("not a raw file")
    _raising_compute(monkeypatch, tmp_path, cause)
    assert script._compute("bad.raw", "opentfraw") == (None, "reader", cause)
