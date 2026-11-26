"""``bench run --config``: a run config on the bench's in-app runs (MAS-893).

A step that ships behind a run option off by default is carded by asking
every in-app run of ``bench run`` with that option. The run config reaches
every run (the control, each decoy arm beside its block, the batch arm's
runs), each run must record it, the card says it, and a config's card is
reused only for the same config and is never develop's baseline. Over the
run's fake instance, server and comparison (``test_assignment_bench_run.py``)
and the fake PR of ``test_assignment_bench_post.py``. No host, no server.
"""

import json

import pytest
import test_assignment_bench_post as base_post
import test_assignment_bench_reuse as base_reuse
import test_assignment_bench_run as base


bench = base.bench
brun = base.brun
post = bench.bench_post

DEVELOP, BRANCH = base.DEVELOP, base.BRANCH

OPTION = {"stage_b_confidence": True}


@pytest.fixture
def rig(tmp_path):
    return base.Rig(tmp_path)


def _summary(rig, n=-1):
    return json.loads((rig.card_dir(n) / "run.json").read_text())


# ------------------------------------------------------------ the config


def test_a_config_is_a_json_object_of_run_options():
    assert brun.parse_config(None) is None
    assert brun.parse_config('{"stage_b_confidence": true}') == OPTION
    # An empty object asks for nothing: the defaults' card.
    assert brun.parse_config("{}") is None


@pytest.mark.parametrize(
    "text, why",
    [
        ("{stage_b_confidence: true}", "--config is not JSON"),
        ("[1, 2]", "must be a JSON object of run options, not list"),
        ('"stage_b_confidence"', "must be a JSON object of run options, not str"),
        ('{"decoy": {"swap_adducts": true}}', "names `decoy`"),
    ],
)
def test_a_config_that_is_no_object_of_run_options_is_refused(text, why):
    with pytest.raises(brun.RunError, match=why):
        brun.parse_config(text)


@pytest.mark.parametrize(
    "asked, recorded, why",
    [
        (OPTION, {"stage_b_confidence": True, "search_scope": {}}, None),
        ({"a": {"b": 1}}, {"a": {"b": 1, "c": 2}}, None),
        ({"a": 1}, {"a": 1.0}, None),
        (OPTION, None, "the run records no config"),
        (OPTION, {}, "no `stage_b_confidence`: the engine does not know it"),
        (OPTION, {"stage_b_confidence": False}, "`stage_b_confidence` false, not"),
        ({"a": {"b": 1}}, {"a": {"b": 2}}, "in `a`, `b` 2, not the 1 asked for"),
        ({"a": {"b": 1}}, {"a": {}}, "in `a`, no `b`"),
        ({"a": {"b": 1}}, {"a": 3}, "`a` 3, not the"),
    ],
)
def test_a_run_s_recorded_config_holds_every_option_asked(asked, recorded, why):
    got = brun.config_problem(asked, recorded)
    if why is None:
        assert got is None
    else:
        assert why in got


# ------------------------------------------------------------ the run


def test_a_run_given_a_config_asks_every_run_with_it_and_the_card_says_so(rig):
    assert rig.run("topic", config=OPTION) == 0

    assert sorted((s, c) for s, _, c in rig.server.assigned) == [
        ("S1", OPTION),
        ("S2", OPTION),
        ("S3", OPTION),
    ]
    summary = _summary(rig)
    assert summary["config"] == OPTION
    record = rig.state()["cards"][-1]
    assert record["config"] == OPTION
    log = (rig.card_dir() / "run.log").read_text()
    assert 'run config: {"stage_b_confidence": true}' in log


def test_a_run_without_a_config_writes_what_it_always_did(rig):
    assert rig.run("topic") == 0
    assert all(c is None for _, _, c in rig.server.assigned)
    assert "config" not in _summary(rig)
    assert "config" not in rig.state()["cards"][-1]
    assert "run config" not in (rig.card_dir() / "run.log").read_text()


def test_each_decoy_arm_runs_with_the_config_beside_its_own_block(rig):
    assert rig.run("topic", config=OPTION, decoys=True) == 0
    arms = [(s, a, c) for s, a, c in rig.server.assigned if a is not None]
    assert arms
    for _, _, config in arms:
        assert config["stage_b_confidence"] is True
        assert set(config) == {"stage_b_confidence", "decoy"}
    # The control's own config keeps no decoy block.
    controls = [c for _, a, c in rig.server.assigned if a is None]
    assert controls == [OPTION] * 3


def test_the_batch_arm_s_runs_run_with_the_config(rig):
    assert rig.run("topic", config=OPTION, batch=True) == 0
    assert rig.server.passes
    # The control, the run the pass folds and the run after it, per sample.
    for sample in ("S1", "S2", "S3"):
        asked = [c for s, _, c in rig.server.assigned if s == sample]
        assert asked == [OPTION] * 3


def test_a_run_that_does_not_record_the_config_fails_at_once(rig):
    # A typo, or an option the ref's engine does not have: the server drops
    # it without a word, and the card would be the defaults' under its name.
    rig.server.unknown = {"stage_b_confidence"}
    rig.server.polls = 5
    code = rig.run("topic", config=OPTION, decoys=True)
    assert code != 0
    # Let go at its first poll, and no arm runs behind a control that failed.
    assert len(rig.server.assigned) == 3
    assert rig.clock() < 5
    summary = _summary(rig)
    causes = summary["sets"]["Db"]["causes"]["failed"]
    assert list(causes) == [
        "its run did not take the run config: the run's config has no "
        "`stage_b_confidence`: the engine does not know it"
    ]


def test_a_card_with_a_config_is_posted_with_it(rig):
    gh = base_post.FakeGh(head=BRANCH)
    assert rig.run("topic", post=post.Poster(gh=gh, number=7), config=OPTION) == 0
    [comment] = gh.bench_comments()
    body = comment["body"]
    line = '**Run config:** `{"stage_b_confidence": true}`'
    assert line in body
    # Under the line that says what was benched, above the table.
    assert body.index("`topic` at") < body.index(line) < body.index("| metric |")


def test_a_card_without_a_config_is_posted_without_the_line(rig):
    gh = base_post.FakeGh(head=BRANCH)
    assert rig.run("topic", post=post.Poster(gh=gh, number=7)) == 0
    assert "Run config" not in gh.bench_comments()[0]["body"]


# ------------------------------------------------------------ the reuse


def test_a_config_s_card_is_reused_only_for_the_same_config(rig):
    assert rig.run("topic", config=OPTION) == 0
    base_reuse._docs_head(rig)

    # No config: the defaults' card is another reading, and runs.
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None
    assert len(rig.compare.calls) == 2

    # Another config runs too.
    assert rig.run("topic", config={"stage_b_confidence": False}) == 0
    assert rig.state()["cards"][-1]["reused"] is None
    assert len(rig.compare.calls) == 3

    # The same config is served the first card, and says its config.
    assert rig.run("topic", config=dict(OPTION)) == 0
    record = rig.state()["cards"][-1]
    assert record["reused"] == "topic/20261112-120000"
    assert record["config"] == OPTION
    assert _summary(rig)["config"] == OPTION
    assert 'run config: {"stage_b_confidence": true}' in (
        (rig.card_dir() / "run.log").read_text()
    )
    assert len(rig.compare.calls) == 3


def test_check_asks_after_a_card_of_the_same_config(rig, capsys):
    assert rig.run("topic", config=OPTION) == 0
    base_reuse._docs_head(rig)
    runner = rig.runner()
    assert runner.check("topic", config=OPTION) == 0
    assert runner.check("topic") == 1
    assert runner.check("topic", config={"stage_b_confidence": False}) == 1


def test_a_defaults_card_does_not_serve_a_config(rig, capsys):
    assert rig.run("topic") == 0
    base_reuse._docs_head(rig)
    capsys.readouterr()
    assert rig.runner().reuse("topic", config=OPTION) is None
    assert 'and the run config {"stage_b_confidence": true}; it runs' in (
        capsys.readouterr().out
    )


# ------------------------------------------------------------ the baseline


def test_a_config_s_card_of_develop_s_head_is_never_the_baseline(rig, capsys):
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 0
    first = rig.state()["baseline"]
    capsys.readouterr()

    assert rig.run("develop", baseline=True, config=OPTION) == 0
    assert rig.state()["baseline"] == first
    assert _summary(rig)["is_baseline"] is False
    assert (
        "not made the baseline: it ran with a run config, and the baseline is "
        "the defaults'" in capsys.readouterr().out
    )
    # Read against the defaults' baseline, as a branch is: its moves are the
    # option's.
    call = rig.compare.calls[-1]
    assert call["baseline"] == rig.home.cards / first["path"] / "card.json"
    assert call["absolute"] is False


def test_baseline_takes_no_card_run_with_a_config(rig):
    assert rig.run("origin/develop", config=OPTION) == 0
    with pytest.raises(brun.RunError, match="ran with the run config"):
        rig.runner().baseline(rig.state()["cards"][-1]["path"])
    assert rig.state().get("baseline") is None


def test_a_config_s_card_of_develop_is_no_stand_in_baseline(rig):
    rig.instance.ancestry.add((DEVELOP, BRANCH))
    assert rig.run("origin/develop", config=OPTION) == 0
    runner = rig.runner()
    assert runner._newest_ancestor(rig.state(), BRANCH, ["Db", "Et"]) is None
    assert rig.run("origin/develop") == 0
    older = runner._newest_ancestor(rig.state(), BRANCH, ["Db", "Et"])
    assert older["path"] == rig.state()["cards"][-1]["path"]


# ------------------------------------------------------------ the command line


def test_the_command_line_passes_the_config_to_the_reuse_and_the_run(
    tmp_path, monkeypatch, capsys
):
    configs = []

    class Runner:
        def __init__(self, **kwargs):
            pass

        def reuse(self, ref, **kwargs):
            configs.append(("reuse", kwargs["config"]))
            return None

        def run(self, ref, **kwargs):
            configs.append(("run", kwargs["config"]))
            return 0

        def check(self, ref, **kwargs):
            configs.append(("check", kwargs["config"]))
            return 1

    monkeypatch.setattr(brun, "BenchRun", Runner)
    home = str(tmp_path / "home")
    run = ["--home", home, "run", "topic"]

    assert bench.main(run + ["--config", '{"stage_b_confidence": true}']) == 0
    assert configs == [("reuse", OPTION), ("run", OPTION)]

    configs.clear()
    path = tmp_path / "config.json"
    path.write_text('{"stage_b_confidence": true}\n')
    assert bench.main(run + ["--config-file", str(path), "--check"]) == 1
    assert configs == [("check", OPTION)]

    configs.clear()
    assert bench.main(run) == 0
    assert configs == [("reuse", None), ("run", None)]

    # A config that is no object, or a file that is not there, is refused
    # before anything runs; both at once is no request.
    configs.clear()
    capsys.readouterr()
    assert bench.main(run + ["--config", "[1]"]) == 2
    assert "bench: --config must be a JSON object" in capsys.readouterr().err
    assert bench.main(run + ["--config-file", str(tmp_path / "none.json")]) == 2
    assert "bench: --config-file cannot be read" in capsys.readouterr().err
    assert configs == []
    with pytest.raises(SystemExit):
        bench.main(run + ["--config", "{}", "--config-file", str(path)])


def test_a_bad_config_is_refused_before_the_pr_is_read(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(post.Poster, "open", lambda self: opened.append(self))
    home = str(tmp_path / "home")
    argv = ["--home", home, "run", "topic", "--post", "7", "--config", "nope"]
    assert bench.main(argv) == 2
    assert opened == []


def test_status_says_a_card_ran_with_a_config(rig, capsys):
    assert rig.run("topic", config=OPTION) == 0
    assert rig.run("topic", fresh=True) == 0
    capsys.readouterr()
    bench.Bench(home=rig.home, instance=rig.instance, connect=rig.connect).status()
    lines = [
        line for line in capsys.readouterr().out.splitlines() if "topic at" in line
    ]
    assert "run with a run config" in lines[0]
    assert "run with a run config" not in lines[1]
