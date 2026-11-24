"""How a bench run meets a server that fails it (MAS-1253).

An assign request the server answers 5xx is sent again and then fails its
sample at once, named with the server's message; a 4xx is not sent again; a
poll that fails is one missed poll; and a run the server never ends is left
out as ``stalled`` at the run limit, not waited on until the set's budget
runs out. The HTTP side is a stub session behind the bench's own
``TokenApi``, over the fake server of ``test_assignment_bench_run``. No host,
no server.
"""

import json

import pytest
import test_assignment_bench_run as base


bench = base.bench
brun = base.brun

#: What Postgres says when it is out of connections, as the server passes it on.
TOO_MANY = "sorry, too many clients already"


class _Response:
    def __init__(self, status, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError("no JSON")
        return self._body


class StubBackend:
    """The bench's routes over HTTP, answered from the fake server, with the
    assign requests of some samples answered 5xx or 4xx first."""

    def __init__(self, server):
        self.server = server
        #: Per sample, the answers its assign requests get before the server's
        #: own: a status and a body each.
        self.assign_answers: dict[str, list] = {}
        #: Per sample, the answer every one of its assign requests gets.
        self.assign_always: dict[str, tuple] = {}
        #: Per sample, how many of its polls answer 503 first.
        self.poll_failures: dict[str, int] = {}
        self.sent: list[tuple[str, str]] = []

    def request(self, method, url, timeout, **kwargs):
        path = url.split("://", 1)[1].split("/", 1)[1]
        sample = path.split("/")[3]
        if path.startswith("api/batch-peaks/"):
            return self._batch(method, path, sample, kwargs)
        self.sent.append((method, sample))
        if method == "POST":
            if sample in self.assign_always:
                return _Response(*self.assign_always[sample])
            queue = self.assign_answers.get(sample) or []
            if queue:
                return _Response(*queue.pop(0))
            config = (kwargs.get("json") or {}).get("config")
            launch = self.server.assign(sample, config)
            if launch.run_id:
                return _Response(
                    202, {"data": [{"peak_assignment_run_id": launch.run_id}]}
                )
            if launch.busy:
                return _Response(409, {"detail": {"code": launch.code}})
            return _Response(
                422, {"detail": {"code": launch.code, "reason": launch.reason}}
            )
        decoys = (kwargs.get("params") or {}).get("include_decoys") == "true"
        if decoys and self.poll_failures.get(sample):
            self.poll_failures[sample] -= 1
            return _Response(503, None, text="<html>Service Unavailable</html>")
        return _Response(200, {"data": self.server.runs(sample, decoys)})

    def _batch(self, method, path, batch, kwargs):
        if method == "POST":
            launch = self.server.assign_batch(batch, kwargs["json"]["fold"])
            if launch.run_id:
                return _Response(202, {"data": [{"batch_peak_run_id": launch.run_id}]})
            return _Response(
                422, {"detail": {"code": launch.code, "reason": launch.reason}}
            )
        if path.endswith("/runs"):
            return _Response(200, {"data": self.server.batch_runs(batch)})
        return _Response(200, {"data": self.server.assignment_status(batch)})

    def posts(self, sample):
        return sum(1 for m, s in self.sent if m == "POST" and s == sample)


class HttpRig(base.Rig):
    """The run rig with the bench's own ``TokenApi`` over a stub session."""

    def __init__(self, tmp_path):
        super().__init__(tmp_path)
        self.backend = StubBackend(self.server)

    def api_of(self, url, token):
        self.apis.append((url, token))
        api = brun.TokenApi(url, token)
        api.session.request = self.backend.request
        return api

    def run(self, ref="topic", run_limit_s=None, **kwargs):
        options = {
            k: kwargs.pop(k)
            for k in ("jobs", "budget_s", "workers", "stall_s")
            if k in kwargs
        }
        self.instance.calls.clear()
        return self.runner(run_limit_s=run_limit_s, **options).run(ref, **kwargs)


@pytest.fixture
def rig(tmp_path):
    return HttpRig(tmp_path)


def _summary(rig):
    return json.loads((rig.card_dir() / "run.json").read_text())


# ------------------------------------------------------------ the assign


def test_an_assign_that_answers_500_twice_then_202_runs(rig):
    rig.backend.assign_answers["S1"] = [(500, {"error": TOO_MANY})] * 2

    assert rig.run("topic", sets=["Db"]) == 0

    assert rig.backend.posts("S1") == 3
    assert [s for s, _, _ in rig.server.assigned].count("S1") == 1
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S1", "S2"]
    assert _summary(rig)["sets"]["Db"]["left_out"] == {}


def test_an_assign_that_always_answers_500_fails_its_sample_in_the_retry_time(
    rig, capsys
):
    rig.backend.assign_always["S1"] = (500, {"error": TOO_MANY})

    assert rig.run("topic", sets=["Db"]) == 1

    # Sent the brief's count of times, a poll apart, and never polled for:
    # its runs were read once, before it ran, for the reference check.
    assert rig.backend.posts("S1") == brun.TRANSIENT_TRIES
    assert rig.backend.sent.count(("GET", "S1")) == 1
    # S1's tries took five pauses of a poll each, and S2's run ended at its
    # first poll: far inside the 600 s budget and the 1,200 s run limit, so
    # the wait on S1 ended on the retries.
    assert rig.clock() == (brun.TRANSIENT_TRIES - 1) * 1.0
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S2"]
    db = _summary(rig)["sets"]["Db"]
    assert db["left_out"] == {"failed": 1}
    assert db["over_budget"] is False
    cause = f"the assign request answered 500: {TOO_MANY}"
    assert db["causes"] == {"failed": {cause: 1}}
    printed = capsys.readouterr().out
    assert f"set Db: 1 sample(s) failed: {cause} (1)" in printed
    log = (rig.card_dir() / "run.log").read_text()
    assert "S1: failed: failed: unreachable POST" in log and TOO_MANY in log


def test_an_assign_refused_with_a_4xx_is_not_sent_again(rig):
    rig.backend.assign_always["S1"] = (400, {"detail": "bad request"})

    assert rig.run("topic", sets=["Db"]) == 1

    assert rig.backend.posts("S1") == 1
    assert _summary(rig)["sets"]["Db"]["causes"] == {
        "failed": {"the assign request was refused (http_400)": 1}
    }


def test_an_assign_that_gets_no_answer_names_the_kind_not_the_url(rig):
    import requests

    def refused(method, url, timeout, **kwargs):
        if method == "POST" and "/S1/" in url:
            raise requests.ConnectionError(f"refused: {url}")
        return rig.backend.request(method, url, timeout, **kwargs)

    real = rig.api_of

    def api_of(url, token):
        api = real(url, token)
        api.session.request = refused
        return api

    rig.api_of = api_of
    assert rig.run("topic", sets=["Db"]) == 1
    causes = _summary(rig)["sets"]["Db"]["causes"]
    assert causes == {
        "failed": {"the assign request did not answer (ConnectionError)": 1}
    }


# --------------------------------------------------------------- the poll


def test_a_5xx_on_the_poll_is_one_missed_poll(rig):
    rig.backend.poll_failures["S1"] = brun.TRANSIENT_TRIES - 1

    assert rig.run("topic", sets=["Db"]) == 0
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S1", "S2"]


def test_polls_that_keep_failing_name_the_answer(rig):
    rig.backend.poll_failures["S1"] = 10**6

    assert rig.run("topic", sets=["Db"]) == 1
    assert _summary(rig)["sets"]["Db"]["causes"] == {
        "failed": {
            "its runs could not be read: answered 503: <html>Service Unavailable</html>": 1
        }
    }


def test_a_sample_whose_runs_cannot_be_read_before_it_runs_names_the_answer(rig):
    real = rig.backend.request

    def unreadable(method, url, timeout, **kwargs):
        if method == "GET" and "/S2/" in url and not kwargs.get("params"):
            return _Response(503, {"detail": "the database is away"})
        return real(method, url, timeout, **kwargs)

    rig.backend.request = unreadable
    assert rig.run("topic", sets=["Db"]) == 1
    assert _summary(rig)["sets"]["Db"]["causes"] == {
        "failed": {"its runs could not be read: answered 503: the database is away": 1}
    }


# --------------------------------------------------------- the run limit


def test_a_run_the_server_never_ends_is_stalled_at_the_limit(rig, capsys):
    rig.server.ends[("S2", None)] = None  # stays running

    assert rig.run("topic", sets=["Db"], run_limit_s=30.0) == 1

    # The set is carded without S2, well inside its 600 s budget.
    assert rig.clock() < 60.0
    assert rig.compare.calls[0]["manifest"]["sets"]["Db"]["samples"] == ["S1"]
    db = _summary(rig)["sets"]["Db"]
    assert db["over_budget"] is False
    assert db["left_out"] == {"stalled": 1}
    cause = "its run did not end within the 0.5 min limit, status running"
    assert db["causes"] == {"stalled": {cause: 1}}
    printed = capsys.readouterr().out
    assert "within 10 min, each run within 0.5 min" in printed
    assert f"set Db: 1 sample(s) stalled: {cause} (1)" in printed
    log = (rig.card_dir() / "run.log").read_text()
    assert "S2: stalled: stalled: running no end 0.5 min after its start" in log
    # Another run might not meet it: the card is not one to reuse.
    assert brun.passing_problems(_summary(rig)["sets"]) == ["set Db: stalled"]


def test_the_limit_reads_the_last_status_seen_when_the_polls_fail(rig):
    rig.server.ends[("S2", None)] = None
    real = rig.backend.request
    polls = {"n": 0}

    def fails_after_one(method, url, timeout, **kwargs):
        if method == "GET" and "/S2/" in url and kwargs.get("params"):
            polls["n"] += 1
            if polls["n"] > 1 and polls["n"] % 3:
                return _Response(503, {"error": TOO_MANY})
        return real(method, url, timeout, **kwargs)

    rig.backend.request = fails_after_one
    assert rig.run("topic", sets=["Db"], run_limit_s=30.0) == 1
    assert _summary(rig)["sets"]["Db"]["left_out"] == {"stalled": 1}


def test_a_sample_held_busy_past_the_limit_is_stalled(rig):
    rig.server.busy["S1"] = 10**6

    assert rig.run("topic", sets=["Db"], run_limit_s=30.0) == 1

    assert rig.clock() < 60.0
    assert _summary(rig)["sets"]["Db"]["causes"] == {
        "stalled": {"another run of the sample was in flight past the 0.5 min limit": 1}
    }


def test_a_decoy_run_the_server_never_ends_is_stalled(rig, capsys):
    arm = brun.arm_name({"mz_shift_ppm": 9})
    rig.server.ends[("S1", arm)] = None

    assert rig.run("topic", sets=["Db"], decoys=True, run_limit_s=30.0) == 1

    db = _summary(rig)["sets"]["Db"]
    assert db["decoys"][arm] == {"completed": 1, "stalled": 1}
    assert db["over_budget"] is False
    assert (
        "set Db: 1 decoy run(s) stalled, past the run limit" in capsys.readouterr().out
    )
    assert f"set Db: decoy {arm} stalled" in brun.passing_problems(
        _summary(rig)["sets"]
    )


def test_the_default_limit_is_longer_with_the_decoy_arms(rig):
    runner = rig.runner()
    assert runner.run_limit() == brun.RUN_LIMIT_MIN * 60 == 1200.0
    assert runner.run_limit(decoys=True) == brun.RUN_LIMIT_DECOYS_MIN * 60 == 3600.0
    assert rig.runner(run_limit_s=90.0).run_limit(decoys=True) == 90.0


@pytest.mark.parametrize("decoys", [False, True])
def test_a_run_between_the_two_limits_stalls_only_without_the_arms(rig, decoys):
    # Each run takes 30 min: past the control limit, inside the decoy one.
    rig.server.polls = 1800
    rig.server.ends[("S3", None)] = ("completed", None)

    code = rig.run("topic", sets=["Db"], decoys=decoys, budget_s=10.0**6)

    db = _summary(rig)["sets"]["Db"]
    if decoys:
        assert code == 0 and db["left_out"] == {}
    else:
        # Nothing left to read: no card.
        assert code == 2 and db["left_out"] == {"stalled": 2}


def test_the_batch_arm_s_runs_keep_the_set_s_limit(rig):
    # S1's third in-app run, the one after the pass, never ends.
    rig.server.nth_end[("S1", 3)] = None

    assert rig.run("topic", sets=["Db"], batch=True, run_limit_s=30.0) == 1

    arm = _summary(rig)["sets"]["Db"]["batch"]
    assert arm["off"] == "runs_failed"


def test_the_batch_arm_s_runs_keep_the_decoy_limit(rig):
    # Every run takes 30 min, inside the decoy limit: the run for the pass and
    # the run after it are given the set's limit, not the control one.
    rig.server.polls = 1800

    code = rig.run("topic", sets=["Db"], decoys=True, batch=True, budget_s=10.0**6)

    db = _summary(rig)["sets"]["Db"]
    assert db["left_out"] == {}
    assert db["batch"]["off"] is None
    assert code == 0


def test_a_sample_whose_runs_cannot_be_read_after_it_ran_names_the_answer(rig):
    real = rig.backend.request

    def unreadable(method, url, timeout, **kwargs):
        ran = rig.server.controls("S2")
        if method == "GET" and "/S2/" in url and not kwargs.get("params") and ran:
            return _Response(503, {"error": TOO_MANY})
        return real(method, url, timeout, **kwargs)

    rig.backend.request = unreadable
    assert rig.run("topic", sets=["Db"]) == 1
    assert _summary(rig)["sets"]["Db"]["causes"] == {
        "failed": {f"its runs could not be read: answered 503: {TOO_MANY}": 1}
    }


def test_the_command_line_takes_the_run_limit(tmp_path, monkeypatch):
    calls = []

    class Runner:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def reuse(self, *args, **kwargs):
            return None

        def run(self, *args, **kwargs):
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    home = str(tmp_path / "home")
    bench.main(["--home", home, "run", "topic", "--run-limit-min", "45"])
    bench.main(["--home", home, "run", "topic"])
    assert calls[0]["run_limit_s"] == 2700.0
    assert calls[1]["run_limit_s"] is None


def test_the_server_s_message_is_read_off_its_answer():
    assert brun.server_message(_Response(500, {"error": TOO_MANY})) == TOO_MANY
    assert brun.server_message(_Response(500, {"detail": "Internal Server Error"})) == (
        "Internal Server Error"
    )
    assert brun.server_message(_Response(502, None, text="<p>Bad\n  gateway</p>")) == (
        "<p>Bad gateway</p>"
    )
    assert brun.server_message(_Response(500, {"detail": {"code": "x"}})) == ""
    long = brun.server_message(_Response(500, {"error": "x" * 500}))
    assert len(long) == 160 and long.endswith("...")
