"""The bench's engine digest and the card a head gets without a run
(``tooling/assignment_bench/run.py``: ``engine_digest``, ``BenchRun.reuse``).

The digest over real git commits; the reuse over the run's fake instance,
server and comparison (``test_assignment_bench_run.py``), and over the fake
PR of ``test_assignment_bench_post.py``. No host, no server.
"""

import io
import json
import subprocess
import threading
from pathlib import Path

import pytest
import test_assignment_bench_post as base_post
import test_assignment_bench_run as base


bench = base.bench
brun = base.brun
post = bench.bench_post

DEVELOP, BRANCH = base.DEVELOP, base.BRANCH
DOCS = "c" * 40
FRONTEND = "f" * 40
ENGINE = "e" * 40

REUSE_LINE = f"*Engine inputs unchanged since `{BRANCH[:9]}`; this is that run's card.*"


# ---------------------------------------------------------------- helpers


def _head(rig, ref, sha, of=BRANCH, extra=(), replace=()):
    """A new head whose tree is ``of``'s with ``extra`` entries added and
    ``replace`` ones swapped in, and the migrations of ``of``."""
    tree = dict(rig.instance.trees[of])
    tree.update(dict(extra))
    tree.update(dict(replace))
    rig.instance.trees[sha] = sorted(tree.items())
    rig.instance.shas[ref] = sha
    rig.instance.revisions[sha] = rig.instance.revisions[of]
    return sha


def _docs_head(rig, ref="topic", sha=DOCS, of=BRANCH):
    """A docs-only commit on top of ``of``, as the branch's new head."""
    return _head(
        rig,
        ref,
        sha,
        of=of,
        extra=[
            ("docs/user/how-it-works/assignment.md", "100644 blob d0c"),
            ("tooling/assignment_bench/README.md", "100644 blob d0d"),
        ],
    )


def _card_files(folder):
    return {
        p.name: p.read_text()
        for p in sorted(folder.iterdir())
        if p.is_file() and p.name.startswith("card")
    }


def _post(rig, gh, ref="topic", **kwargs):
    return rig.run(ref, post=post.Poster(gh=gh, number=7), **kwargs)


THRESHOLDS = base.cr.load_thresholds(
    Path(base.cr.__file__).resolve().parent / "thresholds.json"
)


def _thresholds(card):
    # A card gated as the real comparison gates one: a reuse read against
    # another baseline judges the card again by its thresholds.
    card["thresholds"] = THRESHOLDS
    card["gate"] = base.cr.judge(card, THRESHOLDS)


@pytest.fixture
def rig(tmp_path):
    return base.Rig(tmp_path)


# ------------------------------------------------------------ the digest


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "bench@example.org")
    _git(repo, "config", "user.name", "bench")
    for path in (
        "server/backend/src/mascope_backend/peak_assignments/engine.py",
        "server/backend/src/mascope_backend/alembic/versions/r1.py",
        "libraries/tools/src/mascope_tools/score.py",
        "server/frontend/src/App.vue",
        "docs/user/index.md",
        "tooling/assignment_bench/run.py",
        "tooling/assignment_compare/compare_runs.py",
        "uv.lock",
    ):
        _write(repo, path, f"{path}\n")
    return repo


def _write(repo, path, text):
    target = repo / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text)


def _commit(repo, changes=()):
    for path, text in changes:
        _write(repo, path, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "x")
    return _git(repo, "rev-parse", "HEAD")


def _tree(repo, sha):
    return brun.tree_digest(brun.git_tree(repo, sha))


def test_commits_that_differ_only_where_nothing_counts_have_one_digest(repo):
    first = _commit(repo)
    second = _commit(
        repo,
        [
            ("server/frontend/src/App.vue", "changed\n"),
            ("server/frontend/src/New.vue", "new\n"),
            ("docs/user/index.md", "changed\n"),
            ("docs/dev/new_note.md", "new\n"),
            ("README.md", "new\n"),
            ("tooling/assignment_bench/README.md", "new\n"),
            ("changelog.d/mas-1-x.added.md", "- **New.**\n"),
            (".github/workflows/tests.yaml", "on: push\n"),
            ("agents/file/src/agent.py", "x = 1\n"),
            ("server/backend/tests/test_engine.py", "def test(): pass\n"),
            ("libraries/tools/tests/test_score.py", "def test(): pass\n"),
            ("tooling/cli/src/mascope_cli/main.py", "x = 1\n"),
        ],
    )
    assert first != second
    assert _tree(repo, first) == _tree(repo, second)


@pytest.mark.parametrize(
    "path",
    [
        "libraries/tools/src/mascope_tools/score.py",
        "server/backend/src/mascope_backend/peak_assignments/engine.py",
        "tooling/assignment_bench/run.py",
        "tooling/assignment_compare/compare_runs.py",
        "uv.lock",
    ],
)
def test_a_one_line_change_where_it_counts_is_another_digest(repo, path):
    first = _commit(repo)
    second = _commit(repo, [(path, (repo / path).read_text() + "x = 1\n")])
    assert _tree(repo, first) != _tree(repo, second)


@pytest.mark.parametrize(
    "path",
    [
        # A new migration head.
        "server/backend/src/mascope_backend/alembic/versions/r2.py",
        # A folder nobody has listed yet counts.
        "plugins/new/thing.py",
    ],
)
def test_a_new_file_where_it_counts_is_another_digest(repo, path):
    first = _commit(repo)
    second = _commit(repo, [(path, "x = 1\n")])
    assert _tree(repo, first) != _tree(repo, second)


def test_the_instance_lists_a_commit_s_tree_from_its_repository(repo, tmp_path):
    sha = _commit(repo)
    instance = bench.DevInstance(bench.Home(tmp_path / "home"), repo=repo)
    assert dict(instance.tree(sha))["uv.lock"].startswith("100644 blob ")
    with pytest.raises(brun.RunError, match="cannot list the tree"):
        instance.tree("0" * 40)


@pytest.mark.parametrize(
    "path, counts",
    [
        ("server/backend/src/mascope_backend/peak_assignments/engine.py", True),
        ("libraries/tools/src/mascope_tools/x.py", True),
        ("pyproject.toml", True),
        ("tooling/assignment_bench/manifests/demo.json", True),
        ("tooling/assignment_compare/thresholds.json", True),
        ("server/frontend/package.json", False),
        ("docs/dev/assignment_bench.md", False),
        ("server/backend/NOTES.MD", False),
        ("changelog.d/x.fixed.md", False),
        (".github/workflows/tests.yaml", False),
        ("agents/file/pyproject.toml", False),
        ("server/backend/tests/conftest.py", False),
        ("tooling/cli/src/mascope_cli/main.py", False),
        ("tooling/smoke-test.sh", False),
        # A file named like the folders is no folder.
        ("server/backend/src/tests", True),
    ],
)
def test_what_counts(path, counts):
    assert brun.counts(path) is counts


def test_the_bench_s_holdings_are_in_the_digest(rig):
    state = rig.state()
    first = brun.bench_digest(state)

    def changed(edit):
        one = json.loads(json.dumps(state))
        edit(one)
        return brun.bench_digest(one)

    # What the card does not read leaves it as it is.
    assert changed(lambda s: s["cards"].append({"path": "x"})) == first
    assert changed(lambda s: s.update(baseline={"path": "x"}, updated="t")) == first
    assert changed(lambda s: s["sets"]["Db"].update(label="relabelled")) == first
    # A new manifest, pin or reference block; a republished sample; a sample
    # init rebuilt or rechecked; a set read another way.
    for edit in (
        lambda s: s.update(manifest={"name": "demo", "digest": "m2"}),
        lambda s: s["reference"].update(pin="v1.1.0"),
        lambda s: s["reference"].update(version="1.1.0+assign0.4"),
        lambda s: s["reference"].update(library="2026.11.1"),
        lambda s: s["reference"].update(block="blk2"),
        lambda s: s["reference"]["runs"]["Db"]["S1"].update(run_id="P-S1b"),
        lambda s: s["reference"].update(unrunnable={"Db": "no build"}),
        lambda s: s["sets"]["Db"]["samples"]["k1"]["items"][0].update(
            sample_item_id="S9"
        ),
        lambda s: s["sets"]["Db"]["samples"]["k1"]["problems"].append("no peaks"),
        lambda s: s["sets"]["Db"].update(profile="UR"),
        lambda s: s["sets"]["Et"].update(
            reference_window_ppm={"trust": 5.0, "search": 25.0}
        ),
        lambda s: s["sets"]["Db"].update(truth={"formulas": ["C2H4O2"]}),
        lambda s: s["sets"].pop("Et"),
    ):
        assert changed(edit) != first


def test_the_caller_s_bench_and_comparison_are_in_the_digest(tmp_path):
    root = tmp_path / "assignment_bench"
    (root / "__pycache__").mkdir(parents=True)
    (root / "run.py").write_text("x = 1\n")
    first = brun.tool_digest([root])
    (root / "__pycache__" / "run.cpython-312.pyc").write_bytes(b"\0")
    (root / "README.md").write_text("words\n")
    assert brun.tool_digest([root]) == first
    (root / "run.py").write_text("x = 2\n")
    assert brun.tool_digest([root]) != first


def test_the_libraries_the_comparison_imports_are_in_the_tool_digest():
    # The caller's own installs, as the comparison imports them: another
    # reagent library reads G10 another way.
    import mascope_sdk
    import mascope_tools

    for module in (mascope_tools, mascope_sdk):
        assert Path(module.__file__).resolve().parent in [
            root.resolve() for root in brun.TOOL
        ]
    assert brun.package_root("mascope_no_such_package") is None


def test_the_engine_digest_is_its_three_parts(rig):
    state = rig.state()
    one = brun.engine_digest(rig.instance, BRANCH, state, brun.TOOL)
    assert set(one) == {"digest", "tree", "tool", "bench"}
    assert one["tree"] == brun.tree_digest(rig.instance.trees[BRANCH])
    assert one["bench"] == brun.bench_digest(state)
    assert one["tool"] == brun.tool_digest(brun.TOOL)
    assert (
        one["digest"]
        != brun.engine_digest(rig.instance, DEVELOP, state, brun.TOOL)["digest"]
    )


# ------------------------------------------------------------- the reuse


def test_a_head_of_unchanged_engine_inputs_gets_the_card_without_a_run(rig, capsys):
    assert rig.run("topic") == 0
    first = rig.card_dir()
    _docs_head(rig)
    rig.instance.calls.clear()
    assigned = len(rig.server.assigned)
    capsys.readouterr()

    assert rig.run("topic") == 0

    # Nothing ran: no build, no database, no backend, no engine, no comparison.
    assert rig.instance.calls == []
    assert len(rig.server.assigned) == assigned and len(rig.compare.calls) == 1
    second = rig.card_dir()
    assert second != first and second.parent == first.parent
    assert _card_files(second) == _card_files(first)
    summary = json.loads((second / "run.json").read_text())
    source = json.loads((first / "run.json").read_text())
    assert summary["sha"] == DOCS and summary["engine"] == source["engine"]
    assert summary["reused"] == {
        "path": "topic/20261112-120000",
        "ref": "topic",
        "sha": BRANCH,
        "at": source["finished"],
    }
    assert summary["card"] == source["card"] and summary["sets"] == source["sets"]
    record = rig.state()["cards"][-1]
    assert record["sha"] == DOCS and record["reused"] == "topic/20261112-120000"
    assert record["digest"] == source["engine"]["digest"]
    printed = capsys.readouterr().out
    assert f"unchanged since topic at {BRANCH[:9]}" in printed
    assert "GATE stage3: PASS" in printed and f"card: {second}" in printed


def test_a_reuse_exits_as_its_source_did(rig):
    # A sample init left with a problem is left out of every run alike.
    state = rig.state()
    state["sets"]["Db"]["samples"]["k2"]["problems"] = ["no peaks"]
    bench.save_state(rig.home, state)
    rig.compare.verdict = "fail"
    assert rig.run("topic") == 1
    _docs_head(rig)
    assert rig.run("topic") == 1
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120000"


def test_a_card_that_left_out_what_another_run_might_read_is_not_reused(rig):
    rig.server.ends[("S1", None)] = ("failed", "boom")
    assert rig.run("topic") == 1
    del rig.server.ends[("S1", None)]
    _docs_head(rig)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None
    assert len(rig.compare.calls) == 2


def test_a_failed_decoy_run_keeps_its_card_from_being_reused(rig):
    rig.server.ends[("S1", "swap_adducts")] = ("failed", "boom")
    assert rig.run("topic", decoys=True) == 1
    del rig.server.ends[("S1", "swap_adducts")]
    _docs_head(rig)
    assert rig.run("topic", decoys=True) == 0
    assert rig.state()["cards"][-1]["reused"] is None


def test_a_request_with_decoys_is_not_served_by_a_card_without_them(rig):
    assert rig.run("topic") == 0
    _docs_head(rig)
    assert rig.run("topic", decoys=True) == 0
    record = rig.state()["cards"][-1]
    assert record["reused"] is None and record["decoys"] is True
    assert rig.compare.calls[-1]["decoy"] is True


def test_a_card_with_decoys_serves_a_request_without_them(rig):
    assert rig.run("topic", decoys=True) == 0
    _docs_head(rig)
    assert rig.run("topic") == 0
    record = rig.state()["cards"][-1]
    assert record["reused"] == "topic/20261112-120000" and record["decoys"] is True
    assert len(rig.compare.calls) == 1


def test_a_card_of_other_sets_is_not_reused(rig):
    assert rig.run("topic", sets=["Db"]) == 0
    _docs_head(rig)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None
    assert rig.run("topic", sets=["Et", "Db"]) == 0
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120001"


def test_a_change_where_it_counts_runs(rig):
    assert rig.run("topic") == 0
    _head(
        rig,
        "topic",
        ENGINE,
        replace=[("server/backend/src/engine.py", "100644 blob e2")],
    )
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None
    assert len(rig.compare.calls) == 2


def test_a_new_pin_runs(rig):
    assert rig.run("topic") == 0
    state = rig.state()
    state["reference"]["block"] = "blk2"
    bench.save_state(rig.home, state)
    _docs_head(rig)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None


def test_fresh_runs_regardless(rig):
    assert rig.run("topic") == 0
    _docs_head(rig)
    assert rig.run("topic", fresh=True) == 0
    assert rig.state()["cards"][-1]["reused"] is None
    assert len(rig.compare.calls) == 2
    assert ("checkout", "topic") in rig.instance.calls


def test_a_stopped_run_or_a_card_whose_folder_is_gone_is_not_reused(rig):
    assert rig.run("topic") == 0
    (rig.card_dir() / "card.json").unlink()
    _docs_head(rig)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None


def test_a_set_over_its_budget_keeps_its_card_from_being_reused(rig):
    rig.server.ends[("S2", None)] = None  # never ends
    assert rig.run("topic", budget_s=30.0) == 1
    del rig.server.ends[("S2", None)]
    _docs_head(rig)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None


def test_a_card_recorded_without_a_written_verdict_is_not_reused(rig):
    assert rig.run("topic") == 0
    state = rig.state()
    state["cards"][-1]["verdict"] = "refused"
    bench.save_state(rig.home, state)
    _docs_head(rig)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] is None


def test_a_reuse_names_the_card_that_was_run_not_another_reuse(rig):
    assert rig.run("topic") == 0
    _docs_head(rig)
    assert rig.run("topic") == 0
    _docs_head(rig, sha="a" * 40, of=DOCS)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120000"


def test_develop_s_head_with_baseline_runs_and_its_card_serves_a_frontend_branch(
    rig, capsys
):
    rig.compare.edit = _thresholds
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 0
    # Develop's head again with --baseline: it makes the baseline, so it runs.
    assert rig.run("origin/develop", baseline=True) == 0
    assert len(rig.compare.calls) == 2
    assert rig.state()["baseline"]["path"] == "origin-develop/20261112-120001"
    _head(
        rig,
        "frontend",
        FRONTEND,
        of=DEVELOP,
        extra=[("server/frontend/src/New.vue", "100644 blob f1")],
    )
    capsys.readouterr()
    assert rig.run("frontend") == 0
    assert len(rig.compare.calls) == 2
    record = rig.state()["cards"][-1]
    assert record["reused"] == "origin-develop/20261112-120001"
    assert record["sha"] == FRONTEND
    # It does not become the baseline: only a run of develop's head does.
    assert rig.state()["baseline"]["path"] == "origin-develop/20261112-120001"


def test_develop_s_head_with_post_runs_and_becomes_the_baseline(rig, capsys):
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    gh = base_post.FakeGh(head=DEVELOP)
    assert _post(rig, gh, ref="origin/develop") == 0
    first = rig.state()["baseline"]["path"]
    capsys.readouterr()
    # A --post run turns --baseline on, so develop's head runs again and
    # makes the newer baseline rather than be served the older card.
    poster = post.Poster(gh=gh, number=7)
    assert rig.runner().reuse("origin/develop", post=poster) is None
    assert "with --baseline or --post it makes the baseline" in capsys.readouterr().out
    assert _post(rig, gh, ref="origin/develop") == 0
    assert len(rig.compare.calls) == 2
    assert rig.state()["cards"][-1]["reused"] is None
    assert rig.state()["baseline"]["path"] != first


def test_a_reused_card_on_a_pr_is_posted_with_the_reuse_line(rig):
    gh = base_post.FakeGh(head=BRANCH)
    assert _post(rig, gh) == 0
    _docs_head(rig)
    gh.head = DOCS
    assert _post(rig, gh) == 0
    assert len(rig.compare.calls) == 1
    [comment] = gh.bench_comments()
    body = comment["body"]
    assert f"`topic` at `{DOCS[:9]}`, the PR's head" in body
    assert REUSE_LINE in body
    # Above the table.
    assert body.index(REUSE_LINE) < body.index("| metric |")
    assert "### Bench card: PASS" in body and "No diff: there is no baseline" in body


def test_a_reused_card_is_diffed_against_the_baseline_a_run_would_read(rig):
    rig.compare.edit = _thresholds
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    gh = base_post.FakeGh(head=BRANCH)
    # A card made with no baseline, then develop's baseline.
    assert rig.run("topic") == 0
    assert rig.run("origin/develop", baseline=True) == 0
    _docs_head(rig)
    gh.head = DOCS
    assert _post(rig, gh) == 0
    assert len(rig.compare.calls) == 2
    out = rig.card_dir()
    diff = json.loads((out / "diff.json").read_text())
    assert diff["baseline"]["path"] == "origin-develop/20261112-120001"
    assert diff["compared"] > 0
    card = json.loads((out / "card.json").read_text())
    assert card["baseline"]["compared"] == diff["compared"]
    assert "## Since the baseline" in (out / "card.md").read_text()
    summary = json.loads((out / "run.json").read_text())
    assert summary["baseline"]["path"] == "origin-develop/20261112-120001"
    body = gh.bench_comments()[0]["body"]
    assert "**Since develop's baseline**" in body and REUSE_LINE in body
    # The source's own files are as they were.
    source = rig.home.cards / "topic" / "20261112-120000"
    assert json.loads((source / "card.json").read_text())["baseline"] is None


def test_a_reused_card_keeps_the_rule_on_a_stale_baseline(rig):
    rig.compare.edit = _thresholds
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 0
    gh = base_post.FakeGh(head=BRANCH)
    assert _post(rig, gh) == 0
    _docs_head(rig)
    gh.head = DOCS
    # The PR's merge base moved past the baseline's commit.
    rig.instance.merge_bases[(DOCS, "origin/develop")] = "9" * 40
    assert _post(rig, gh) == 0
    out = rig.card_dir()
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120001"
    assert not (out / "diff.json").exists()
    assert json.loads((out / "card.json").read_text())["baseline"] is None
    assert "older than this PR's merge base" in gh.bench_comments()[0]["body"]


def test_a_reused_card_reads_the_stand_in_a_run_would_read(rig, capsys):
    rig.compare.edit = _thresholds
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 0
    gh = base_post.FakeGh(head=BRANCH)
    assert _post(rig, gh) == 0
    _docs_head(rig)
    gh.head = DOCS
    # The rolling baseline is older than the PR's merge base, but it is the
    # newest card of develop behind the head: a run reads it in its place.
    rig.instance.merge_bases[(DOCS, "origin/develop")] = "9" * 40
    rig.instance.ancestry.add((DEVELOP, DOCS))
    capsys.readouterr()
    assert _post(rig, gh) == 0
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120001"
    out = rig.card_dir()
    diff = json.loads((out / "diff.json").read_text())
    assert diff["baseline"]["path"] == "origin-develop/20261112-120000"
    assert "the rolling baseline, as no newer card of develop" in (
        capsys.readouterr().out
    )
    assert "older than this PR's merge base" not in gh.bench_comments()[0]["body"]


def _develop_misses(card):
    # A gated reading every engine here misses: G3_N5 is at most 1.
    card["columns"]["Db"]["readings"]["G3_N5"] = {"value": 3.0, "n": 10}
    _thresholds(card)


def test_a_reused_card_is_gated_against_the_baseline_a_run_would_read(rig):
    rig.compare.edit = _develop_misses
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    # Gated on its own, the card misses; so does develop's.
    assert rig.run("topic") == 1
    assert rig.run("origin/develop", baseline=True) == 1
    assert rig.state()["baseline"]["path"] == "origin-develop/20261112-120001"
    _docs_head(rig)
    gh = base_post.FakeGh(head=DOCS)
    # Against develop's baseline the miss is develop's, not this ref's.
    assert _post(rig, gh) == 0
    assert len(rig.compare.calls) == 2
    out = rig.card_dir()
    card = json.loads((out / "card.json").read_text())
    assert card["gate"]["verdict"] == "pass" and card["gate"]["absolute"] == "fail"
    assert [one["metric"] for one in card["gate"]["against"]["develops"]] == ["G3_N5"]
    summary = json.loads((out / "run.json").read_text())
    assert summary["card"]["verdict"] == "pass"
    assert "PASS for this ref" in summary["card"]["gate"]
    assert rig.state()["cards"][-1]["verdict"] == "pass"
    assert "### Bench card: PASS" in gh.bench_comments()[0]["body"]
    # A head on develop is gated on its own, the source's card as it was.
    _docs_head(rig, ref="origin/develop", sha="a" * 40, of=DEVELOP)
    rig.instance.on_develop.add("a" * 40)
    assert rig.run("origin/develop") == 1
    assert rig.state()["cards"][-1]["reused"] == "origin-develop/20261112-120001"


class GatingCompare(base.FakeCompare):
    """The fake comparison, gated as the real one is: against the baseline it
    is given, unless told to gate the card on its own (``--absolute-gate``)."""

    def __call__(self, *, baseline, absolute=False, **kwargs):
        def edit(card):
            _develop_misses(card)
            if baseline is not None and not absolute:
                was = json.loads(baseline.read_text())
                card["gate"] = base.cr.judge_against(card, was, THRESHOLDS)

        self.edit = edit
        return super().__call__(baseline=baseline, absolute=absolute, **kwargs)


def test_a_branch_s_card_reused_at_develop_s_head_is_gated_on_its_own(rig):
    rig.compare = GatingCompare()
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 1
    gh = base_post.FakeGh(head=BRANCH)
    # Against develop's baseline the branch's miss is develop's.
    assert _post(rig, gh) == 0
    assert json.loads((rig.card_dir() / "card.json").read_text())["gate"]["against"]
    # The branch merged: develop's head is its tree and a docs commit.
    merged = _docs_head(rig, ref="origin/develop", sha="a" * 40, of=BRANCH)
    rig.instance.develop_sha = lambda: merged
    rig.instance.on_develop.add(merged)
    # On develop's head, with no baseline, the miss is its own, as a run's
    # gate says. (With --baseline or --post develop's head runs.)
    assert rig.run("origin/develop") == 1
    record = rig.state()["cards"][-1]
    assert record["reused"] == "topic/20261112-120001" and record["verdict"] == "fail"
    card = json.loads((rig.card_dir() / "card.json").read_text())
    assert card["gate"]["verdict"] == "fail" and "against" not in card["gate"]
    assert card["baseline"] is None


def test_develop_s_card_gated_on_its_own_is_gated_again_for_a_branch(rig):
    rig.compare = GatingCompare()
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    # The rolling baseline: develop's card of every set, its miss its own.
    assert rig.run("origin/develop", baseline=True) == 1
    baseline = rig.state()["baseline"]["path"]
    # Develop's head again, of one set: read against that baseline, gated on
    # its own, and no baseline itself.
    assert rig.run("origin/develop", baseline=True, sets=["Db"]) == 1
    assert rig.state()["baseline"]["path"] == baseline
    assert len(rig.compare.calls) == 2
    # A branch that changed only the frontend, read against the same
    # baseline: develop's card serves it, and the miss is develop's.
    _head(
        rig,
        "frontend",
        FRONTEND,
        of=DEVELOP,
        extra=[("server/frontend/src/New.vue", "100644 blob f1")],
    )
    assert rig.run("frontend", baseline=True, sets=["Db"]) == 0
    assert len(rig.compare.calls) == 2
    record = rig.state()["cards"][-1]
    assert record["reused"] == "origin-develop/20261112-120001"
    assert record["verdict"] == "pass"
    card = json.loads((rig.card_dir() / "card.json").read_text())
    assert card["gate"]["verdict"] == "pass" and card["gate"]["absolute"] == "fail"


def test_post_posts_a_reused_card_with_its_line(rig):
    assert rig.run("topic") == 0
    _docs_head(rig)
    assert rig.run("topic") == 0
    gh = base_post.FakeGh(head=DOCS)
    assert rig.runner().post("topic", post.Poster(gh=gh, number=7)) == 0
    assert REUSE_LINE in gh.bench_comments()[0]["body"]


def test_a_reused_card_cannot_be_made_the_baseline(rig):
    assert rig.run("develop") == 0
    _docs_head(rig, ref="develop", sha=FRONTEND, of=DEVELOP)
    rig.instance.on_develop.add(FRONTEND)
    assert rig.run("develop") == 0
    assert rig.state()["cards"][-1]["reused"] == "develop/20261112-120000"
    with pytest.raises(brun.RunError, match="make that card the baseline"):
        rig.runner().baseline("develop/20261112-120001")
    assert rig.runner().baseline("develop/20261112-120000") == 0


def test_a_baseline_made_while_a_reuse_records_keeps_the_reuse_s_record(rig):
    assert rig.run("topic") == 0
    assert rig.run("develop") == 0
    _docs_head(rig, ref="docs")
    on_develop = rig.instance.is_on_develop

    def reuse_meanwhile(sha):
        # Another caller's reuse, after baseline read the state.
        assert rig.runner().reuse("docs") == 0
        return on_develop(sha)

    rig.instance.is_on_develop = reuse_meanwhile
    assert rig.runner().baseline("develop/20261112-120001") == 0

    state = rig.state()
    assert [(c["sha"], c["reused"]) for c in state["cards"]] == [
        (BRANCH, None),
        (DEVELOP, None),
        (DOCS, "topic/20261112-120000"),
    ]
    assert state["baseline"]["path"] == "develop/20261112-120001"


def test_a_baseline_waits_for_a_reuse_recording_under_state_lock(rig):
    assert rig.run("topic") == 0
    assert rig.run("develop") == 0
    runner, done = rig.runner(), []

    def baseline():
        done.append(runner.baseline("develop/20261112-120001", out=io.StringIO()))

    writer = threading.Thread(target=baseline)
    # A reuse's read-modify-write, with the baseline started inside it: the
    # baseline must wait for it, or the reuse's save drops the baseline.
    with brun.state_guard(rig.home.root):
        state = rig.state()
        writer.start()
        writer.join(0.5)
        assert writer.is_alive(), "the baseline did not wait for state.lock"
        state["cards"].append({"sha": DOCS, "reused": "topic/20261112-120000"})
        bench.save_state(rig.home, state)
    writer.join(10)

    assert done == [0]
    state = rig.state()
    assert [(c["sha"], c["reused"]) for c in state["cards"]] == [
        (BRANCH, None),
        (DEVELOP, None),
        (DOCS, "topic/20261112-120000"),
    ]
    assert state["baseline"]["path"] == "develop/20261112-120001"


def test_a_run_that_began_before_a_reuse_keeps_the_reuse_s_record(rig):
    assert rig.run("topic") == 0
    _docs_head(rig, ref="docs")
    compare = rig.compare

    def reuse_meanwhile(**kwargs):
        # Another caller's reuse, while this run holds the bench.
        assert rig.runner().reuse("docs") == 0
        return compare(**kwargs)

    rig.compare = reuse_meanwhile
    assert rig.run("topic", fresh=True) == 0
    assert [(c["sha"], c["reused"]) for c in rig.state()["cards"]] == [
        (BRANCH, None),
        (DOCS, "topic/20261112-120000"),
        (BRANCH, None),
    ]


def test_the_command_line_reuses_while_another_command_holds_the_lock(
    rig, monkeypatch, capsys
):
    assert rig.run("topic") == 0
    _docs_head(rig)
    real = brun.BenchRun

    def runner(**kwargs):
        monkeypatch.setattr(brun, "BenchRun", real)
        try:
            return rig.runner()
        finally:
            monkeypatch.setattr(brun, "BenchRun", runner)

    monkeypatch.setattr(brun, "BenchRun", runner)
    argv = ["--home", str(rig.home.root), "run", "topic", "--no-wait"]
    with rig.home.lock():
        assert bench.main(argv) == 0
        assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120000"
        # --fresh takes the lock, which another command holds.
        assert bench.main(argv + ["--fresh"]) == 2
    assert "holds the lock" in capsys.readouterr().err


def test_a_caller_asking_before_the_lock_is_told_why_it_runs(rig, capsys):
    assert rig.runner().reuse("topic") is None
    assert "no complete card of it with set(s) Db, Et; it runs" in (
        capsys.readouterr().out
    )
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.runner().reuse("origin/develop", baseline=True) is None
    assert "with --baseline or --post it makes the baseline: it runs" in (
        capsys.readouterr().out
    )


def test_a_card_that_now_holds_a_denied_name_is_not_reused(rig, capsys):
    assert rig.run("topic") == 0
    card = rig.card_dir() / "card.md"
    card.write_text(card.read_text() + "Zeppelin\n")
    # A name the instance came to hold since the card was made.
    rig.instance.names["names"].append("Zeppelin lab")
    _docs_head(rig)
    capsys.readouterr()
    assert rig.runner().reuse("topic") is None
    assert "refused by the scrub: card.md holds a name" in capsys.readouterr().out
    assert len(rig.state()["cards"]) == 1


@pytest.mark.parametrize(
    "columns", [{}, {"Db": {"rows": ["of another shape"]}}], ids=["empty", "shaped"]
)
def test_a_baseline_of_another_card_format_is_left_to_a_run(rig, capsys, columns):
    rig.compare.edit = _thresholds
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("topic") == 0
    assert rig.run("origin/develop", baseline=True) == 0
    baseline = rig.home.cards / rig.state()["baseline"]["path"] / "card.json"
    # Columns of another shape: the format is read before the gate reads them.
    baseline.write_text(json.dumps({"format": "old", "columns": columns}))
    _docs_head(rig)
    capsys.readouterr()
    assert rig.runner().reuse("topic", baseline=True) is None
    assert "cannot be read against the baseline (the baseline is not a card" in (
        capsys.readouterr().out
    )
    assert len(rig.state()["cards"]) == 2


def test_an_error_in_the_reuse_is_posted_as_the_run_s_failure(rig):
    gh = base_post.FakeGh(head=BRANCH)
    with pytest.raises(brun.RunError, match="the bench has no set Zz"):
        _post(rig, gh, sets=["Zz"])
    [comment] = gh.bench_comments()
    assert "**The run stopped: RunError.**" in comment["body"]
    assert "the bench has no set Zz" in comment["body"]


def test_a_reuse_that_cannot_read_the_database_leaves_the_ref_to_a_run(
    rig, monkeypatch, capsys
):
    assert rig.run("topic") == 0
    _docs_head(rig)
    real = rig.instance.private_names

    def restoring():
        raise RuntimeError("cannot read the database: it is being restored")

    monkeypatch.setattr(rig.instance, "private_names", restoring)
    capsys.readouterr()
    assert rig.runner().reuse("topic") is None
    out = capsys.readouterr().out
    assert "no card to reuse (RuntimeError: cannot read the database" in out
    assert out.rstrip().endswith("; it runs")
    assert len(rig.state()["cards"]) == 1
    # Behind the lock, where the database answers, the same head is reused.
    monkeypatch.setattr(rig.instance, "private_names", real)
    assert rig.run("topic") == 0
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120000"


def test_a_reused_card_whose_source_ref_names_a_held_word_is_written(rig):
    # The words of the card's own ref are its to print, here a word of a
    # name the instance holds; so are those of the ref whose card it reuses.
    rig.instance.shas["bromide-fix"] = BRANCH
    assert rig.run("bromide-fix") == 0
    _docs_head(rig)
    assert rig.run("topic") == 0
    out = rig.card_dir()
    assert json.loads((out / "run.json").read_text())["reused"]["ref"] == "bromide-fix"


def test_a_reused_card_that_cannot_be_posted_says_how_to_post_it(rig):
    class Gh(base_post.FakeGh):
        def create(self, number, body):
            raise post.PostError("gh api failed: 502")

    assert rig.run("topic") == 0
    _docs_head(rig)
    with pytest.raises(post.PostError, match="`bench.py post topic --pr 7` posts it"):
        _post(rig, Gh(head=DOCS))
    assert rig.state()["cards"][-1]["reused"] == "topic/20261112-120000"


def test_status_says_whether_a_card_was_run_or_reused(rig):
    assert rig.run("topic") == 0
    _docs_head(rig)
    assert rig.run("topic") == 0
    out = io.StringIO()
    bench.Bench(home=rig.home, instance=rig.instance, connect=rig.connect).status(out)
    digest = rig.state()["cards"][-1]["digest"][:12]
    text = out.getvalue()
    assert f"engine {digest}  run  topic/20261112-120000" in text
    assert (
        f"engine {digest}  reused topic/20261112-120000  topic/20261112-120001" in text
    )


def test_the_nightly_baseline_runs_fresh():
    service = (
        Path(__file__).resolve().parents[3]
        / "tooling"
        / "systemd"
        / "bench-baseline.service"
    ).read_text()
    assert "bench.py run origin/develop --baseline --decoys --fresh'" in service
