"""
Hermetic tests for the warnings a peak read hands on.

A time-ranged read of a sample's peaks (``get_peaks`` with ``t_min`` /
``t_max``, ``load_peaks_by_stage``) leaves out every peak whose time series
the server has not computed yet, and says so in a warning. The rows of the
frame cannot show that it is short - on a measured single-stream file a ranged
read returned one of its three strongest peaks - so the warning has to reach
the caller twice: logged, for whoever is watching, and on
``df.attrs["warnings"]``, for a script. And the caller needs the way out,
``compute_peak_timeseries``.

The SDK is on PyPI and talks to servers older than itself. One that lists its
warnings beside the data is read exactly; one from before that only folds
them into its message, and gives none at all when it left out every peak.
Both are served here.

These mock the HTTP layer and need no running stack.
"""

from typing import Any

import pandas as pd
import pytest
from loguru import logger

from mascope_sdk import MascopeClient, ServerError, _loaders
from mascope_sdk.resources._base import _api_warnings
from mascope_sdk.resources.samples import (
    _COMPUTE_HINT,
    _EMPTY_RANGE_UNEXPLAINED,
    SamplesResource,
)


SAMPLE_ID = "sample-1"

#: The sample's peaks, strongest first.
PEAKS = {"p1": 61.0395, "p2": 121.0719, "p3": 83.0215}

EXCLUDED = (
    "{n} peak(s) were excluded because their timeseries have not been computed "
    "yet. A peak's timeseries is computed when it is first requested (POST "
    "/api/samples/{{sample_item_id}}/peaks/timeseries); repeat this request "
    "afterwards to include them."
)

#: The same warning as servers up to 1.10.1 word it.
EXCLUDED_BEFORE = (
    "{n} peak(s) were excluded because their timeseries have not been computed "
    "yet. Re-run peak detection to include them."
)

HINT = _COMPUTE_HINT.format(sample_id=SAMPLE_ID)

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

    ``before=True`` is a server up to 1.10.1: it folds the warning into the
    message alone, words its advice the old way, and gives no warning with an
    answer that left out every peak.
    """

    def __init__(
        self,
        computed=("p1",),
        sample_name: str = "Urea",
        computed_meanwhile=(),
        before: bool = False,
        peaks: dict[str, float] | None = None,
    ):
        self.peaks = PEAKS if peaks is None else peaks
        self.computed = set(computed)
        self.sample_name = sample_name
        #: Peaks whose time series something else computes right after the
        #: first ranged read - matching, or a user opening them in the app.
        self.computed_meanwhile = set(computed_meanwhile)
        self.before = before
        #: Peaks whose time series request fails, until a test clears it.
        self.failing: set[str] = set()
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
        in_range = [
            p
            for p, mz in self.peaks.items()
            if params.get("mz_min", 0) <= mz <= params.get("mz_max", float("inf"))
        ]
        listed = [p for p in in_range if not ranged or p in self.computed]
        left_out = len(in_range) - len(listed)

        text = EXCLUDED_BEFORE if self.before else EXCLUDED
        warnings = [text.format(n=left_out)] if left_out else []
        if listed:
            message = (
                f"Successfully loaded {len(listed)} peaks from sample "
                f"'{self.sample_name}' with polarity '+'"
            )
        else:
            message = (
                f"No peaks found in sample '{self.sample_name}' with polarity '+'."
            )
            if self.before:
                warnings = []
        message += "".join(f" Warning: {warning}" for warning in warnings)

        data: dict[str, Any] = {
            "peak_id": listed,
            "mz": [self.peaks[p] for p in listed],
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

        body = {"message": message, "results": len(listed), "data": data}
        if not self.before:
            body["warnings"] = warnings
        return _Response(body)

    def http_post(self, url, path, access_token, data, **kwargs):
        self.posts.append((path, dict(data)))
        if path != f"samples/{SAMPLE_ID}/peaks/timeseries":
            raise AssertionError(f"unexpected POST {path!r}")
        peak_id = data["peak_id"]
        if peak_id in self.failing:
            raise ServerError("The file could not be read", status_code=500)
        self.computed.add(peak_id)
        return _Response(
            {
                "message": "Retrieved timeseries with 2 data points",
                "results": 2,
                "data": {
                    "peak_id": peak_id,
                    "mz": self.peaks[peak_id],
                    "height": [1.0, 2.0],
                    "time": [0.0, 1.0],
                },
            }
        )

    @property
    def asked_for(self) -> list[str]:
        """The peaks whose time series was requested, in order."""
        return [data["peak_id"] for _, data in self.posts]


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


@pytest.fixture
def logged():
    """What the SDK logs at WARNING and above.

    A test that also builds a ``MascopeClient`` must ask for this fixture
    after the client: the first client of a process clears loguru's handlers.
    """
    records: list[str] = []
    handler = logger.add(
        lambda message: records.append(message.record["message"]),
        level="WARNING",
        filter="mascope_sdk",
    )
    yield records
    logger.remove(handler)


def _ranged(samples: SamplesResource) -> pd.DataFrame:
    return samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)


class TestTheWarningsOfAResponse:
    """``_api_warnings``: the list where the server sends one, else the marker."""

    def test_a_listed_warning_is_taken_as_it_is(self):
        body = {"message": "Loaded 1 peaks", "warnings": ["first one.", "second."]}

        assert _api_warnings(body) == ["first one.", "second."]

    def test_a_list_is_believed_over_the_message(self):
        """The message quotes the sample's name, which the user chose. Split
        on the marker, a sample named for it reads as a warning that says
        ``blank 3' with polarity '+'`` - on every read of a whole frame."""
        body = {
            "message": "Successfully loaded 3 peaks from sample 'Warning: blank 3' "
            "with polarity '+'",
            "warnings": [],
        }

        assert _api_warnings(body) == []

    def test_what_is_no_text_in_a_list_is_left_out(self):
        assert _api_warnings({"warnings": ["real", None, "", 3]}) == ["real"]

    def test_without_a_list_each_marked_warning_of_the_message_is_one(self):
        body = {"message": "Loaded 1 peaks Warning: first one. Warning: second one."}

        assert _api_warnings(body) == ["first one.", "second one."]

    def test_without_a_list_a_message_without_the_marker_carries_none(self):
        assert _api_warnings({"message": "Successfully loaded 3 peaks"}) == []

    def test_the_word_in_a_name_is_not_the_marker(self):
        # The check used to be `"warning" in message.lower()`, which logged the
        # whole message of every read of a sample so named.
        body = {"message": "Successfully loaded 3 peaks from sample 'warning_blank'"}

        assert _api_warnings(body) == []

    @pytest.mark.parametrize(
        "body", [None, "", [], {"message": None}, {"message": {"text": "Warning: x"}}]
    )
    def test_a_body_with_nothing_to_read_carries_none(self, body):
        assert _api_warnings(body) == []


class TestAnyRead:
    """``_get``, which every other read goes through, logs what it is sent."""

    @pytest.mark.parametrize(
        "body",
        [
            {"message": "Done.", "warnings": ["half of it"], "data": [1]},
            {"message": "Done. Warning: half of it", "data": [1]},
        ],
        ids=["listed", "in-the-message"],
    )
    def test_a_warning_on_any_route_is_logged(self, monkeypatch, stub, logged, body):
        monkeypatch.setattr(
            "mascope_sdk.resources._base.http_get", lambda **kwargs: _Response(body)
        )

        assert stub.samples._get("anything") == [1]
        assert logged == ["API warning: half of it"]


class TestARangedReadShortOfPeaks:
    """``get_peaks`` over a time range, two of three peaks left out."""

    def test_the_warning_is_logged_with_what_the_sdk_can_do_about_it(
        self, samples, logged
    ):
        """The server names the remedy as a route. The SDK's name for it is
        the SDK's to add, so each is right whatever the other's version."""
        _ranged(samples)

        assert logged == [f"API warning: {EXCLUDED.format(n=2)} {HINT}"]
        assert "samples.compute_peak_timeseries('sample-1')" in HINT

    def test_the_warning_rides_on_the_frame_as_the_server_gave_it(self, samples):
        peaks = _ranged(samples)

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=2)]

    def test_the_frame_is_shaped_as_a_read_that_warned_of_nothing(self, samples):
        """The warnings go beside the rows, never among them."""
        short = _ranged(samples)
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

        peaks = _ranged(stub.samples)

        assert peaks.empty
        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=3)]
        assert logged == [f"API warning: {EXCLUDED.format(n=3)} {HINT}"]

    def test_a_range_that_holds_no_peaks_warns_of_nothing(
        self, monkeypatch, stub, logged
    ):
        """From a server that lists its warnings, an empty list is its word
        that nothing was left out - no peak is at this m/z."""
        _serve(monkeypatch, FakeServer())

        peaks = stub.samples.get_peaks(
            SAMPLE_ID, matches=False, t_min=0, t_max=30, mz_min=500
        )

        assert peaks.empty
        assert peaks.attrs["warnings"] == []
        assert logged == []

    def test_a_sample_named_for_the_marker_warns_of_nothing(
        self, monkeypatch, stub, logged
    ):
        _serve(monkeypatch, FakeServer(sample_name="Warning: blank 3"))

        peaks = stub.samples.get_peaks(SAMPLE_ID, matches=False)

        assert len(peaks) == 3
        assert peaks.attrs["warnings"] == []
        assert logged == []


class TestAServerFromBeforeTheList:
    """Servers up to 1.10.1: the warning is in the message alone, and an
    answer that left out every peak carries none."""

    @pytest.fixture
    def before(self, monkeypatch) -> FakeServer:
        return _serve(monkeypatch, FakeServer(before=True))

    def test_a_short_read_is_warned_about_from_its_message(self, before, stub, logged):
        peaks = _ranged(stub.samples)

        assert peaks.attrs["warnings"] == [EXCLUDED_BEFORE.format(n=2)]
        # Its own advice is the one that does not work; the SDK's follows it
        assert logged == [f"API warning: {EXCLUDED_BEFORE.format(n=2)} {HINT}"]

    def test_an_empty_ranged_read_is_not_taken_for_a_whole_one(
        self, monkeypatch, stub, logged
    ):
        """It says "No peaks found" and nothing else. That is how it answers a
        range with no peaks, and how it answers a sample none of whose peaks
        has a time series: an empty list of warnings here would be read as the
        first by a script, wrongly in the case this is all for."""
        _serve(monkeypatch, FakeServer(computed=(), before=True))

        peaks = _ranged(stub.samples)

        assert peaks.empty
        assert peaks.attrs["warnings"] == [_EMPTY_RANGE_UNEXPLAINED]
        assert logged == [f"{_EMPTY_RANGE_UNEXPLAINED} {HINT}"]

    def test_an_empty_read_without_a_time_range_warns_of_nothing(
        self, before, stub, logged
    ):
        # Nothing is left out of a read off the stored sums, on any server
        peaks = stub.samples.get_peaks(SAMPLE_ID, matches=False, mz_min=500)

        assert peaks.empty
        assert peaks.attrs["warnings"] == []
        assert logged == []

    def test_a_whole_ranged_read_warns_of_nothing(self, monkeypatch, stub, logged):
        _serve(monkeypatch, FakeServer(computed=PEAKS, before=True))

        peaks = _ranged(stub.samples)

        assert len(peaks) == 3
        assert peaks.attrs["warnings"] == []
        assert logged == []

    def test_stages_with_every_peak_left_out_carry_the_sdks_own_warning(
        self, monkeypatch, stub
    ):
        _serve(monkeypatch, FakeServer(computed=(), before=True))

        peaks = _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, [(0, 30, "blank"), (30, 120, "sample")], matches=False
        )

        assert peaks.empty
        assert peaks.attrs["warnings"] == [_EMPTY_RANGE_UNEXPLAINED]


class TestComputePeakTimeseries:
    """The way out: ask for the time series that are missing, then read again."""

    def test_it_asks_only_for_the_peaks_that_have_none(self, samples, server):
        computed = samples.compute_peak_timeseries(SAMPLE_ID)

        assert computed == 2
        assert server.asked_for == ["p2", "p3"]

    def test_it_learns_which_from_two_reads_of_the_ids_alone(self, samples, server):
        """Every peak, and the peaks a time-ranged read lists - which are the
        ones with a time series. Areas, heights and averaging each cost the
        server a read it does not need to answer with the ids."""
        samples.compute_peak_timeseries(SAMPLE_ID)

        ids_only = {
            "areas": "false",
            "heights": "false",
            "average": "false",
            "matches": "false",
        }
        assert server.gets == [
            (f"samples/{SAMPLE_ID}/peaks", ids_only),
            (f"samples/{SAMPLE_ID}/peaks", {**ids_only, "t_min": 0}),
        ]

    def test_its_own_reads_log_no_warning(self, samples, logged):
        # The ranged read it makes is short by design: that is its question
        samples.compute_peak_timeseries(SAMPLE_ID)

        assert logged == []

    def test_a_sample_with_nothing_missing_is_asked_for_nothing(
        self, monkeypatch, stub
    ):
        server = _serve(monkeypatch, FakeServer(computed=PEAKS))

        assert stub.samples.compute_peak_timeseries(SAMPLE_ID) == 0
        assert server.posts == []

    def test_of_given_peaks_the_ones_that_have_none_are_asked_for(
        self, samples, server
    ):
        computed = samples.compute_peak_timeseries(SAMPLE_ID, ["p2", "p2", "p1"])

        assert computed == 1
        assert server.asked_for == ["p2"]
        # The listing of every peak is not needed to know that
        assert [params.get("t_min") for _, params in server.gets] == [0]

    def test_a_single_peak_id_is_one_peak(self, samples, server):
        assert samples.compute_peak_timeseries(SAMPLE_ID, "p2") == 1
        assert server.asked_for == ["p2"]

    def test_an_mz_range_limits_it_to_the_peaks_inside(self, samples, server):
        """A read that warned about a narrow window does not need the whole
        sample computed."""
        computed = samples.compute_peak_timeseries(SAMPLE_ID, mz_min=100, mz_max=200)

        assert computed == 1
        assert server.asked_for == ["p2"]

    def test_peaks_and_an_mz_range_together_are_refused(self, samples, server):
        with pytest.raises(ValueError, match="not both"):
            samples.compute_peak_timeseries(SAMPLE_ID, ["p2"], mz_min=100)

        assert server.posts == []

    def test_a_failure_says_how_far_it_got_and_a_second_call_goes_on(
        self, samples, server, logged
    ):
        """Request 2,900 of 3,000 failing must not cost the 2,899 before it:
        the failure is raised with the progress logged, and what was computed
        is not asked for again."""
        server.failing = {"p3"}

        with pytest.raises(ServerError):
            samples.compute_peak_timeseries(SAMPLE_ID)

        assert server.asked_for == ["p2", "p3"]
        (line,) = logged
        assert line.startswith("Stopped at peak 'p3' with 1 of 2 timeseries computed")

        server.failing = set()
        server.posts.clear()

        assert samples.compute_peak_timeseries(SAMPLE_ID) == 1
        assert server.asked_for == ["p3"]

    def test_a_peak_that_fails_every_time_can_be_left_out(
        self, samples, server, logged
    ):
        """Going on from where it stopped does not get past a peak that always
        fails: the order is fixed, so every run stops there and the peaks
        after it are never reached. The log line names the way round."""
        server.failing = {"p2"}

        for _ in range(2):
            with pytest.raises(ServerError):
                samples.compute_peak_timeseries(SAMPLE_ID)

        assert server.asked_for == ["p2", "p2"]
        assert len(logged) == 2
        assert all("pass peak_ids without that one" in line for line in logged)

        server.posts.clear()

        assert samples.compute_peak_timeseries(SAMPLE_ID, ["p3"]) == 1
        assert server.asked_for == ["p3"]

    def test_a_ranged_read_made_afterwards_is_whole(self, samples, logged):
        assert _ranged(samples).attrs["warnings"]
        logged.clear()

        samples.compute_peak_timeseries(SAMPLE_ID)
        whole = _ranged(samples)

        assert whole["peak_id"].tolist() == ["p1", "p2", "p3"]
        assert whole.attrs["warnings"] == []
        assert logged == []


class TestTheLoaders:
    """``load_peaks_by_stage`` and ``load_peaks`` build one frame from many
    reads, and ``pd.concat`` drops ``attrs`` that differ - so they hand the
    warnings on themselves."""

    STAGES = [(0, 30, "blank"), (30, 120, "sample")]

    def _stages(self, stub, **kwargs):
        return _loaders.load_peaks_by_stage(
            stub, SAMPLE_ID, self.STAGES, matches=False, **kwargs
        )

    def test_the_stages_frame_carries_the_warning_once(self, server, stub):
        # Every stage of a sample is short by the same peaks and warns alike
        peaks = self._stages(stub)

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=2)]
        assert peaks["stage_name"].tolist() == ["blank", "sample"]

    def test_stages_that_warn_differently_keep_every_warning(self, monkeypatch, stub):
        """A time series computed while the stages load - by matching, or by
        someone opening the peak in the app - makes two stages short by
        different peaks. Their frames then differ in ``attrs``, which is when
        ``pd.concat`` drops them all."""
        _serve(monkeypatch, FakeServer(computed_meanwhile=("p2",)))

        peaks = self._stages(stub, max_workers=1)

        assert peaks.attrs["warnings"] == [
            EXCLUDED.format(n=2),
            EXCLUDED.format(n=1),
        ]
        assert peaks.groupby("stage_name")["peak_id"].apply(list).to_dict() == {
            "blank": ["p1"],
            "sample": ["p1", "p2"],
        }

    def test_each_stages_read_logs_it(self, server, stub, logged):
        self._stages(stub)

        assert logged == [f"API warning: {EXCLUDED.format(n=2)} {HINT}"] * 2

    def test_the_stages_frame_is_shaped_as_one_that_warned_of_nothing(
        self, monkeypatch, stub
    ):
        _serve(monkeypatch, FakeServer())
        short = self._stages(stub)
        _serve(monkeypatch, FakeServer(computed=PEAKS))
        whole = self._stages(stub)

        assert whole.attrs["warnings"] == []
        assert list(short.columns) == list(whole.columns)
        assert len(whole) == 6 and len(short) == 2

    def test_stages_with_every_peak_left_out_are_an_empty_frame_that_says_so(
        self, monkeypatch, stub, logged
    ):
        """The state of any sample that was detected and never matched. None
        here would tell a script "no peaks in these stages", and the recipe
        the docs give - ``if peaks.attrs["warnings"]:`` - would raise on it in
        the one case it is for."""
        _serve(monkeypatch, FakeServer(computed=PEAKS))
        whole = self._stages(stub)
        _serve(monkeypatch, FakeServer(computed=()))

        peaks = self._stages(stub)

        assert peaks is not None and peaks.empty
        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=3)]
        # Every column a load with rows has is there to group or select by. (A
        # load with rows also drops the columns it has nothing in, so the
        # empty one may hold more.)
        assert set(whole.columns) <= set(peaks.columns)
        assert {"stage", "stage_name", "t_min", "t_max"} <= set(peaks.columns)
        assert peaks.groupby("stage_name")["area"].sum().empty
        assert logged == [f"API warning: {EXCLUDED.format(n=3)} {HINT}"] * 2

    def test_stages_of_a_sample_without_peaks_are_still_nothing(
        self, monkeypatch, stub
    ):
        # Nothing found and nothing warned: None, as it always was
        _serve(monkeypatch, FakeServer(peaks={}))

        assert self._stages(stub) is None

    @pytest.fixture
    def two_samples(self, monkeypatch):
        rows = pd.DataFrame(
            {"sample_item_id": ["s1", "s2"], "sample_item_name": ["A", "B"]}
        )
        monkeypatch.setattr(
            _loaders,
            "_collect_sample_tasks",
            lambda *a, **k: ([(row, "Batch") for _, row in rows.iterrows()], "ds-1"),
        )

    @staticmethod
    def _one_peak(sample_id: str) -> pd.DataFrame:
        return pd.DataFrame(
            {"sample_item_id": [sample_id], "peak_id": ["p1"], "mz": [61.0]}
        )

    def test_load_peaks_gathers_what_its_reads_warned(
        self, monkeypatch, stub, two_samples
    ):
        """It reads without a time range, which the server warns nothing
        about today; what a read does warn must still survive the concat."""

        def get_peaks(sample_id, **kwargs):
            frame = self._one_peak(sample_id)
            frame.attrs["warnings"] = ["shared", f"only {sample_id}"]
            return frame

        monkeypatch.setattr(stub.samples, "get_peaks", get_peaks)

        peaks = _loaders.load_peaks(stub, "My Dataset")

        assert len(peaks) == 2
        assert sorted(peaks.attrs["warnings"]) == ["only s1", "only s2", "shared"]

    def test_a_frame_without_warnings_of_its_own_is_gathered_as_none(
        self, monkeypatch, stub, two_samples
    ):
        # A get_peaks stand-in, or a subclass, that sets no attrs
        monkeypatch.setattr(
            stub.samples, "get_peaks", lambda sample_id, **k: self._one_peak(sample_id)
        )

        assert _loaders.load_peaks(stub, "My Dataset").attrs["warnings"] == []

    def test_load_peaks_of_samples_without_peaks_is_still_nothing(
        self, monkeypatch, stub, two_samples
    ):
        def get_peaks(sample_id, **kwargs):
            frame = self._one_peak(sample_id).iloc[0:0]
            frame.attrs["warnings"] = []
            return frame

        monkeypatch.setattr(stub.samples, "get_peaks", get_peaks)

        assert _loaders.load_peaks(stub, "My Dataset") is None

    def test_load_peaks_keeps_the_rows_of_the_samples_that_have_some(
        self, monkeypatch, stub, two_samples
    ):
        # An empty read among filled ones adds no rows and no columns
        def get_peaks(sample_id, **kwargs):
            frame = self._one_peak(sample_id)
            return frame.iloc[0:0] if sample_id == "s1" else frame

        monkeypatch.setattr(stub.samples, "get_peaks", get_peaks)

        peaks = _loaders.load_peaks(stub, "My Dataset")

        assert peaks["sample_item_id"].tolist() == ["s2"]
        assert peaks["sample_item_name"].tolist() == ["B"]


class TestCombiningLoads:
    """``MascopeClient.concat``: what ``pd.concat`` drops, kept."""

    @staticmethod
    def _load(warnings, block=BLOCK) -> pd.DataFrame:
        frame = pd.DataFrame({"mz": [61.0]})
        frame.attrs["warnings"] = list(warnings)
        if block is not None:
            frame.attrs["provenance"] = dict(block)
        return frame

    def test_pd_concat_drops_everything_from_two_loads_short_by_different_counts(
        self,
    ):
        """Why the method exists. The warning's text carries a count, so two
        samples that are both short differ, and ``pd.concat`` keeps ``attrs``
        only when every input's are equal - all of it or none."""
        a = self._load([EXCLUDED.format(n=212)])
        b = self._load([EXCLUDED.format(n=198)])

        assert pd.concat([a, b], ignore_index=True).attrs == {}

    def test_two_short_loads_keep_their_warnings_and_their_provenance(self):
        a = self._load([EXCLUDED.format(n=212)])
        b = self._load([EXCLUDED.format(n=198)])

        peaks = MascopeClient.concat([a, b])

        assert len(peaks) == 2
        assert peaks.attrs["warnings"] == [
            EXCLUDED.format(n=212),
            EXCLUDED.format(n=198),
        ]
        assert peaks.attrs["provenance"] == BLOCK

    def test_a_short_load_among_whole_ones_is_not_lost(self):
        peaks = MascopeClient.concat(
            [self._load([]), self._load([EXCLUDED.format(n=5)]), self._load([])]
        )

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=5)]
        assert peaks.attrs["provenance"] == BLOCK

    def test_a_warning_shared_by_every_load_is_listed_once(self):
        peaks = MascopeClient.concat(self._load(["same"]) for _ in range(3))

        assert peaks.attrs["warnings"] == ["same"]

    def test_loads_from_different_builds_carry_no_provenance(self):
        """As ``pd.concat`` has it: no single build produced the result."""
        other = {**BLOCK, "produced_with": {"mascope_version": "v1.11.0"}}

        peaks = MascopeClient.concat([self._load(["x"]), self._load([], other)])

        assert "provenance" not in peaks.attrs
        assert peaks.attrs["warnings"] == ["x"]

    def test_a_load_without_provenance_leaves_the_result_without(self):
        peaks = MascopeClient.concat([self._load([]), self._load([], None)])

        assert "provenance" not in peaks.attrs

    def test_the_provenance_is_the_results_own_copy(self):
        a = self._load([])

        peaks = MascopeClient.concat([a, self._load(["x"])])
        peaks.attrs["provenance"]["deployment_id"] = "edited"

        assert a.attrs["provenance"]["deployment_id"] == "example-lab"

    def test_a_load_that_found_nothing_is_skipped(self):
        peaks = MascopeClient.concat([None, self._load(["x"]), None])

        assert len(peaks) == 1
        assert peaks.attrs["warnings"] == ["x"]

    @pytest.mark.parametrize("frames", [[], [None, None]])
    def test_nothing_to_stack_is_nothing(self, frames):
        assert MascopeClient.concat(frames) is None

    def test_frames_that_carry_no_warnings_get_none_invented(self):
        plain = [pd.DataFrame({"mz": [1.0]}), pd.DataFrame({"mz": [2.0]})]

        peaks = MascopeClient.concat(plain)

        assert peaks.attrs == {}
        assert peaks["mz"].tolist() == [1.0, 2.0]
        assert peaks.index.tolist() == [0, 1]

    def test_the_frames_stacked_keep_their_own_attrs(self):
        # They are stacked without their attrs; that must not empty the inputs
        a, b = self._load(["x"]), self._load(["y"])

        MascopeClient.concat([a, b])

        assert a.attrs == {"warnings": ["x"], "provenance": BLOCK}
        assert b.attrs == {"warnings": ["y"], "provenance": BLOCK}

    def test_an_entry_only_some_frames_carry_is_dropped(self):
        a = self._load([])
        a.attrs["run"] = {"peak_assignment_run_id": "run-1"}

        peaks = MascopeClient.concat([a, self._load([])])

        assert "run" not in peaks.attrs
        assert peaks.attrs["provenance"] == BLOCK

    @staticmethod
    def _ledger(species) -> pd.DataFrame:
        """A frame as ``load_batch_ledger`` returns it: the species table
        rides on ``attrs``, a frame itself."""
        frame = pd.DataFrame({"batch_peak_id": ["bp-1"]})
        frame.attrs["batch_peaks"] = pd.DataFrame({"batch_peak_id": list(species)})
        frame.attrs["provenance"] = dict(BLOCK)
        return frame

    def test_frames_whose_attrs_hold_a_frame_are_stacked_where_pd_concat_raises(
        self,
    ):
        """pandas compares ``attrs`` with ``==``. A frame's ``==`` answers
        cell by cell, and the truth of that answer raises - so ``pd.concat``
        cannot stack two batch ledgers at all. The method is offered for the
        frames the SDK returns, and these are among them."""
        a, b = self._ledger(["x", "y"]), self._ledger(["x", "y"])

        with pytest.raises(ValueError, match="ambiguous"):
            pd.concat([a, b], ignore_index=True)

        ledger = MascopeClient.concat([a, b])

        assert len(ledger) == 2
        assert ledger.attrs["provenance"] == BLOCK
        assert ledger.attrs["batch_peaks"]["batch_peak_id"].tolist() == ["x", "y"]

    def test_a_frame_in_attrs_that_differs_between_them_is_dropped(self):
        ledger = MascopeClient.concat([self._ledger(["x"]), self._ledger(["y"])])

        assert "batch_peaks" not in ledger.attrs
        assert ledger.attrs["provenance"] == BLOCK


class TestThroughTheClient:
    """The public entry point stamps provenance on the same ``attrs``."""

    STAGES = [(0, 30, "blank"), (30, 120, "sample")]

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
        peaks = client.load_peaks_by_stage(SAMPLE_ID, self.STAGES, matches=False)

        assert peaks.attrs["warnings"] == [EXCLUDED.format(n=2)]
        assert peaks.attrs["provenance"]["deployment_id"] == "example-lab"

    def test_the_documented_recipe_runs_when_every_peak_was_left_out(
        self, client, server
    ):
        """``if peaks.attrs["warnings"]: compute...; load again`` - on the
        sample it is written for, one with no time series at all."""
        server.computed.clear()

        peaks = client.load_peaks_by_stage(SAMPLE_ID, self.STAGES, matches=False)
        assert peaks.empty
        assert peaks.attrs["provenance"]["deployment_id"] == "example-lab"

        if peaks.attrs["warnings"]:
            client.samples.compute_peak_timeseries(SAMPLE_ID)
            peaks = client.load_peaks_by_stage(SAMPLE_ID, self.STAGES, matches=False)

        assert len(peaks) == 6
        assert peaks.attrs["warnings"] == []

    def test_loads_of_one_sample_at_two_moments_combine_with_everything_kept(
        self, client, server
    ):
        first = client.load_peaks_by_stage(SAMPLE_ID, self.STAGES, matches=False)
        server.computed.add("p2")
        second = client.load_peaks_by_stage(SAMPLE_ID, self.STAGES, matches=False)

        peaks = client.concat([first, second])

        assert len(peaks) == 6
        assert peaks.attrs["warnings"] == [
            EXCLUDED.format(n=2),
            EXCLUDED.format(n=1),
        ]
        assert peaks.attrs["provenance"]["deployment_id"] == "example-lab"

    def test_the_clients_own_handler_shows_the_warning(self, client, capsys):
        """What a notebook user sees with nothing configured: the handler the
        client installs is at INFO, so the warning reaches stderr."""
        client.samples.get_peaks(SAMPLE_ID, matches=False, t_min=0, t_max=30)

        shown = capsys.readouterr().err
        assert "API warning: 2 peak(s) were excluded" in shown
        assert "samples.compute_peak_timeseries('sample-1')" in shown
