"""A sample file record the server already holds is not a failure.

``create_sample_file_db_record`` posts after the directory was made, and the
directory can only be made where none was; so a 409 means the record is
there: the server committed and its answer was lost on the way, and the
retry met the row it made - or a record that an earlier run left without a
directory. Reading it as a failure would remove the directory that now backs
the record, and leave the record directory-less for good. The two are not
told apart in what is done, only in what is logged: after a retried POST
the row is most likely this call's own, an INFO that says no more than that
(an attempt refused before it reached the server is a retry too, and then
the row was there already - the converter cannot tell); on the first answer
it is an earlier upload's row that was sitting there without its directory,
which an operator hears of as a WARNING.
"""

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
import requests

import mascope_backend.file_converter.api as api


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(api.time, "sleep"):
        yield


@pytest.fixture
def logger(monkeypatch):
    logger = MagicMock()
    monkeypatch.setattr(api, "runtime", SimpleNamespace(logger=logger))
    return logger


def _response(status_code: int) -> MagicMock:
    response = MagicMock(spec=requests.Response)
    response.status_code = status_code
    return response


def _props():
    return SimpleNamespace(
        filename="TOF-1_2026.01.01-00h00m00s_sample",
        utc_offset=0,
        timestamp="2026-01-01T00:00:00",
        length=10.0,
        range=[40.0, 600.0],
        method_file=None,
        mz_calibration=None,
        polarity="+",
        acquisition_timezone=None,
        utc_offset_source=None,
        instrument_type="tof",
    )


def test_a_409_after_a_lost_answer_is_the_record_made(logger):
    responses = [_response(500), _response(409)]
    with patch.object(api.requests, "request", side_effect=responses) as request:
        api.create_sample_file_db_record(_props(), "function", access_token="t")

    assert request.call_count == 2
    # The retry's own WARNING for the 500 is the trace; the 409 is an INFO
    assert logger.warning.call_count == 1
    assert "retrying" in logger.warning.call_args.args[0]
    assert any(
        "after a retried POST" in call.args[0] for call in logger.info.call_args_list
    )


def test_a_409_after_a_refused_connection_is_logged_the_same_way(logger):
    """An attempt that never reached the server is an attempt too: the retry
    is what the INFO stands on, not who made the row."""
    responses = [requests.exceptions.ConnectionError("refused"), _response(409)]
    with patch.object(api.requests, "request", side_effect=responses) as request:
        api.create_sample_file_db_record(_props(), "function", access_token="t")

    assert request.call_count == 2
    assert logger.warning.call_count == 1
    assert "retrying" in logger.warning.call_args.args[0]
    assert any(
        "after a retried POST" in call.args[0] for call in logger.info.call_args_list
    )


def test_a_409_on_the_first_answer_is_the_record_too_and_is_warned_about(logger):
    with patch.object(api.requests, "request", return_value=_response(409)):
        api.create_sample_file_db_record(_props(), "function", access_token="t")

    assert logger.warning.call_count == 1
    assert "without its directory" in logger.warning.call_args.args[0]
    assert _props().filename in logger.warning.call_args.args[0]


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_any_other_refusal_is_still_a_failure(status):
    with patch.object(api.requests, "request", return_value=_response(status)):
        with pytest.raises(Exception, match=f"Status code: {status}"):
            api.create_sample_file_db_record(_props(), "function", access_token="t")
