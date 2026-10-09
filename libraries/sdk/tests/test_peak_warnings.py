"""
Hermetic tests for the warnings a peak read hands on.

A time-ranged read of a sample's peaks (``get_peaks`` with ``t_min`` /
``t_max``, ``load_peaks_by_stage``) leaves out every peak whose time series
the server has not computed yet, and says so in a warning folded into the
response's ``message``. The rows of the frame cannot show that it is short -
on a measured single-stream file a ranged read returned one of its three
strongest peaks - so the warning has to reach the caller twice: logged, for
whoever is watching, and on ``df.attrs["warnings"]``, for a script. And the
caller needs the way out, ``compute_peak_timeseries``.

These mock the HTTP layer and need no running stack.
"""

from typing import Any

import pandas as pd
import pytest
from loguru import logger

from mascope_sdk import MascopeClient, _loaders
from mascope_sdk.resources._base import _api_warnings
from mascope_sdk.resources.samples import SamplesResource


SAMPLE_ID = "sample-1"

#: The sample's peaks, strongest first.
PEAKS = {"p1": 61.0395, "p2": 121.0719, "p3": 83.0215}

EXCLUDED = (
    "{n} peak(s) were excluded because their timeseries have not been computed "
    "yet. A peak's timeseries is computed when it is first requested (SDK: "
    "samples.compute_peak_timeseries); repeat this request afterwards to "
    "include them."
)

BLOCK = {
    "provenance_version": 1,
    "generated_utc": "2026-10-01T12:00:00Z",
    "deployment_id": "example-lab",
    "produced_with": {"mascope_version": "v1.10.1"},
}


class _Response:
    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def json(self) -> dict[str, Any]:
        return self._payload


class FakeServer:
    """A three-peak sample behind the HTTP seam, as the server answers for it.

    A read without a time range lists every peak. A ranged one lists only the
    peaks in ``computed`` and counts the rest in a warning, and asking for a
    peak's time series computes it - the two halves of the behaviour the SDK
    has to make visible. ``sample_name`` is what the message quotes.
    """

    def __init__(
        self, computed=("p1",), sample_name: str = "Urea", computed_meanwhile=()
    ):
        self.computed = set(computed)
        self.sample_name = sample_name
        #: Peaks whose time series something else computes right after the
        #: first ranged read - matching, or a user opening them in the app.
        self.computed_meanwhile = set(computed_meanwhile)
        self.gets: list[tuple[str, dict]] = []
        self.posts: list[tuple[str, dict]] = []

    def http_get(self, url, path, access_token, params=None, **kwargs):
        params = dict(params or {})
        self.gets.append((path, params))
        if path == "provenance":
            return _Response({"message": "Provenance.", "data": dict(BLOCK)})
        if path == f"samples/{SAMPLE_ID}":
            return _Response(
                {
                    "message": f"Sample '{self.sample_name}' retrieved successfully",
                    "data": {
                        "sample_item_id": SAMPLE_ID,
                        "sample_item_name": self.sample_name,
                    },
                }
            )
        if path != f"samples/{SAMPLE_ID}/peaks":
            raise AssertionError(f"unexpected GET {path!r}")

        ranged = "t_min" in params or "t_max" in params
        listed = [p for p in PEAKS if not ranged or p in self.computed]
        left_out = len(PEAKS) - len(listed)
        if listed:
            message = (
                f"Successfully loaded {len(listed)} peaks from sample "
                f"'{self.sample_name}' with polarity '+'"
            )
        else:
            message = (
                f"No peaks found in sample '{self.sample_name}' with polarity '+'."
            )
        if left_out:
            message += f" Warning: {EXCLUDED.format(n=left_out)}"

        data: dict[str, Any] = {
            "peak_id": listed,
            "mz": [PEAKS[p] for p in listed],
            "sparsity": [0.0] * len(listed),
            "signal_to_noise": [50.0] * len(listed),
            "match": None,
        }
        if params.get("areas") == "true":
            data["area"] = [1000.0] * len(listed)
        if params.get("heights") == "true":
            data["height"] = [500.0] * len(listed)
        if ranged:
            self.computed |= self.computed_meanwhile
        return _Response({"message": message, "results": len(listed), "data": data})

    def http_post(self, url, path, access_token, data, **kwargs):
        self.posts.append((path, dict(data)))
        if path != f"samples/{SAMPLE_ID}/peaks/timeseries":
            raise AssertionError(f"unexpected POST {path!r}")
        peak_id = data["peak_id"]
        self.computed.add(peak_id)
        return _Response(
            {
                "message": "Retrieved timeseries with 2 data points",
                "results": 2,
                "data": {
                    "peak_id": peak_id,
                    "mz": PEAKS[peak_id],
                    "height": [1.0, 2.0],
                    "time": [0.0, 1.0],
                },
            }
        )


class _StubClient:
    """What the resources and the loader functions read off the client."""

    url = "http://testserver"
    access_token = "token"
    _timeout = (1, 5)
    _verify_ssl = False
    _service_name = "mascope_sdk"

    def __init__(self):
        self._cache: dict[str, pd.DataFrame] = {}
        self.samples = SamplesResource(self)


def _serve(monkeypatch, server: FakeServer) -> FakeServer:
    monkeypatch.setattr("mascope_sdk.resources._base.http_get", server.http_get)
    monkeypatch.setattr("mascope_sdk.resources._base.http_post", server.http_post)
    monkeypatch.setattr("mascope_sdk._http.http_get", server.http_get)
    return server


@pytest.fixture
def server(monkeypatch) -> FakeServer:
    return _serve(monkeypatch, FakeServer())


@pytest.fixture
def stub() -> _StubClient:
    return _StubClient()


@pytest.fixture
def samples(server, stub) -> SamplesResource:
    return stub.samples


def _capture_warnings() -> tuple[list[str], int]:
    records: list[str] = []
    handler = logger.add(
        lambda message: records.append(message.record["message"]),
        level="WARNING",
        filter="mascope_sdk",
    )
    return records, handler


@pytest.fixture
def logged():
    """What the SDK logs at WARNING and above.

    A test that also builds a ``MascopeClient`` must ask for this fixture
    after the client: the first client of a process clears loguru's handlers.
    """
    records, handler = _capture_warnings()
    yield records
    logger.remove(handler)


class TestTheWarningsOfAMessage:
    def test_a_message_without_the_marker_carries_none(self):
        assert _api_warnings("Successfully loaded 3 peaks from sample 'Urea'") == []

    def test_each_marked_warning_is_one_entry(self):
        message = "Loaded 1 peaks Warning: first one. Warning: second one."

        assert _api_warnings(message) == ["first one.", "second one."]

    def test_the_word_in_a_name_is_not_a_warning(self):
        # The check used to be `"warning" in message.lower()`, which logged the
        # whole message of every read of a sample so named.
        message = "Successfully loaded 3 peaks from sample 'warning_blank'"

        assert _api_warnings(message) == []

    @pytest.mark.parametrize("message", [None, "", 0, {"text": "Warning: no"}])
    def test_a_message_that_is_no_text_carries_none(self, message):
        assert _api_warnings(message) == []


class TestARangedReadShortOfPeaks:
    """``get_peaks`` over a time range, two of three peaks left out."""

    def test_the_warning_is_logged(self, samples, logged):
        samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)

        assert logged == [f"API warning: {EXCLUDED.format(n=2)}"]

    def test_the_warning_rides_on_the_frame(self, samples):
        peaks = samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=2)]

    def test_the_frame_is_shaped_as_a_read_that_warned_of_nothing(self, samples):
        """The warnings go beside the rows, never among them."""
        short = samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)
        whole = samples.get_peaks(SAMPLE_ID, matches=False)

        assert short["peak_id"].tolist() == ["p1"]
        assert list(short.columns) == list(whole.columns)
        assert short.dtypes.to_dict() == whole.dtypes.to_dict()
        pd.testing.assert_frame_equal(
            short, whole[whole["peak_id"] == "p1"], check_like=False
        )

    def test_a_read_that_lists_every_peak_warns_of_nothing(self, samples, logged):
        peaks = samples.get_peaks(SAMPLE_ID, matches=False)

        assert len(peaks) == 3
        assert peaks.attrs["warnings"] == []
        assert logged == []

    def test_a_read_with_every_peak_left_out_still_says_so(
        self, monkeypatch, stub, logged
    ):
        """Empty is the answer that most needs the warning: without it the
        sample reads as one that has no peaks in the range."""
        _serve(monkeypatch, FakeServer(computed=()))

        peaks = stub.samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)

        assert peaks.empty
        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=3)]
        assert logged == [f"API warning: {EXCLUDED.format(n=3)}"]

    def test_a_sample_named_for_the_word_logs_no_warning(
        self, monkeypatch, stub, logged
    ):
        _serve(monkeypatch, FakeServer(sample_name="warning_blank"))

        peaks = stub.samples.get_peaks(SAMPLE_ID, matches=False)

        assert peaks.attrs["warnings"] == []
        assert logged == []


class TestComputePeakTimeseries:
    """The way out: ask for each peak's time series, then read again."""

    def test_it_asks_for_every_peak_of_the_sample_once(self, samples, server):
        asked = samples.compute_peak_timeseries(SAMPLE_ID)

        assert asked == 3
        assert server.posts == [
            (f"samples/{SAMPLE_ID}/peaks/timeseries", {"peak_id": peak_id})
            for peak_id in PEAKS
        ]

    def test_the_peaks_are_listed_without_their_intensities(self, samples, server):
        # Areas, heights and averaging each cost the server a read it does
        # not need to answer with the ids.
        samples.compute_peak_timeseries(SAMPLE_ID)

        ((path, params),) = server.gets
        assert path == f"samples/{SAMPLE_ID}/peaks"
        assert params == {
            "areas": "false",
            "heights": "false",
            "average": "false",
            "matches": "false",
        }

    def test_given_peaks_are_the_only_ones_asked_for(self, samples, server):
        asked = samples.compute_peak_timeseries(SAMPLE_ID, ["p2", "p2", "p3"])

        assert asked == 2
        assert [data["peak_id"] for _, data in server.posts] == ["p2", "p3"]
        assert server.gets == []

    def test_a_single_peak_id_is_one_peak(self, samples, server):
        assert samples.compute_peak_timeseries(SAMPLE_ID, "p2") == 1
        assert [data["peak_id"] for _, data in server.posts] == ["p2"]

    def test_a_ranged_read_made_afterwards_is_whole(self, samples, logged):
        short = samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)
        assert short.attrs["warnings"]
        logged.clear()

        samples.compute_peak_timeseries(SAMPLE_ID)
        whole = samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)

        assert whole["peak_id"].tolist() == ["p1", "p2", "p3"]
        assert whole.attrs["warnings"] == []
        assert logged == []


class TestTheLoaders:
    """``load_peaks_by_stage`` and ``load_peaks`` build one frame from many
    reads, and ``pd.concat`` drops ``attrs`` that differ - so they hand the
    warnings on themselves."""

    STAGES = [(0, 30, "blank"), (30, 120, "sample")]

    def test_the_stages_frame_carries_the_warning_once(self, server, stub):
        # Every stage of a sample is short by the same peaks and warns alike
        peaks = _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, self.STAGES, matches=False
        )

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=2)]
        assert peaks["stage_name"].tolist() == ["blank", "sample"]

    def test_stages_that_warn_differently_keep_every_warning(self, monkeypatch, stub):
        """A time series computed while the stages load - by matching, or by
        someone opening the peak in the app - makes two stages short by
        different peaks. Their frames then differ in ``attrs``, which is when
        ``pd.concat`` drops them all."""
        _serve(monkeypatch, FakeServer(computed_meanwhile=("p2",)))

        peaks = _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, self.STAGES, matches=False, max_workers=1
        )

        assert peaks.attrs["warnings"] == [
            EXCLUDED.format(n=2),
            EXCLUDED.format(n=1),
        ]
        assert peaks.groupby("stage_name")["peak_id"].apply(list).to_dict() == {
            "blank": ["p1"],
            "sample": ["p1", "p2"],
        }

    def test_each_stages_read_logs_it(self, server, stub, logged):
        _loaders.load_peaks_by_stage(stub, SAMPLE_ID, self.STAGES, matches=False)

        assert logged == [f"API warning: {EXCLUDED.format(n=2)}"] * 2

    def test_the_stages_frame_is_shaped_as_one_that_warned_of_nothing(
        self, monkeypatch, stub
    ):
        _serve(monkeypatch, FakeServer())
        short = _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, self.STAGES, matches=False
        )
        _serve(monkeypatch, FakeServer(computed=PEAKS))
        whole = _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, self.STAGES, matches=False
        )

        assert whole.attrs["warnings"] == []
        assert list(short.columns) == list(whole.columns)
        assert len(whole) == 6 and len(short) == 2

    def test_stages_with_every_peak_left_out_return_nothing_but_log_it(
        self, monkeypatch, stub, logged
    ):
        # No frame to carry it: the log is then the only notice
        _serve(monkeypatch, FakeServer(computed=()))

        peaks = _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, self.STAGES, matches=False
        )

        assert peaks is None
        assert logged == [f"API warning: {EXCLUDED.format(n=3)}"] * 2

    def test_load_peaks_gathers_what_its_reads_warned(self, monkeypatch, stub):
        """It reads without a time range, which the server warns nothing
        about today; what a read does warn must still survive the concat."""
        rows = pd.DataFrame(
            {"sample_item_id": ["s1", "s2"], "sample_item_name": ["A", "B"]}
        )
        monkeypatch.setattr(
            _loaders,
            "_collect_sample_tasks",
            lambda *a, **k: ([(row, "Batch") for _, row in rows.iterrows()], "ds-1"),
        )

        def get_peaks(sample_id, **kwargs):
            frame = pd.DataFrame(
                {"sample_item_id": [sample_id], "peak_id": ["p1"], "mz": [61.0]}
            )
            frame.attrs["warnings"] = ["shared", f"only {sample_id}"]
            return frame

        monkeypatch.setattr(stub.samples, "get_peaks", get_peaks)

        peaks = _loaders.load_peaks(stub, "My Dataset")

        assert len(peaks) == 2
        assert sorted(peaks.attrs["warnings"]) == ["only s1", "only s2", "shared"]

    def test_a_frame_without_warnings_of_its_own_is_gathered_as_none(
        self, monkeypatch, stub
    ):
        # A get_peaks stand-in, or a subclass, that sets no attrs
        rows = pd.DataFrame({"sample_item_id": ["s1"], "sample_item_name": ["A"]})
        monkeypatch.setattr(
            _loaders,
            "_collect_sample_tasks",
            lambda *a, **k: ([(row, "Batch") for _, row in rows.iterrows()], "ds-1"),
        )
        monkeypatch.setattr(
            stub.samples,
            "get_peaks",
            lambda sample_id, **k: pd.DataFrame(
                {"sample_item_id": [sample_id], "peak_id": ["p1"], "mz": [61.0]}
            ),
        )

        assert _loaders.load_peaks(stub, "My Dataset").attrs["warnings"] == []


class TestThroughTheClient:
    """The public entry point stamps provenance on the same ``attrs``."""

    @pytest.fixture
    def client(self, monkeypatch, server):
        monkeypatch.setattr(
            MascopeClient, "_resolve_workspace", lambda self, workspace: ("ws-1", "WS")
        )
        # The default, pinned: a developer's own .env may set another level,
        # and the environment variable is read before it
        monkeypatch.setenv("MASCOPE_SDK_LOG_LEVEL", "INFO")
        return MascopeClient(url="http://testserver/", access_token="token")

    def test_the_warnings_sit_beside_the_provenance(self, client):
        # No `logged` here: the first client of a process clears the handlers
        peaks = client.load_peaks_by_stage(
            SAMPLE_ID, [(0, 30, "blank"), (30, 120, "sample")], matches=False
        )

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=2)]
        assert peaks.attrs["provenance"]["deployment_id"] == "example-lab"

    def test_the_clients_own_handler_shows_the_warning(self, client, capsys):
        """What a notebook user sees with nothing configured: the handler the
        client installs is at INFO, so the warning reaches stderr."""
        client.samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)

        assert "API warning: 2 peak(s) were excluded" in capsys.readouterr().err
