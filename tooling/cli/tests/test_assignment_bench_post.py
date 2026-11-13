"""The assignment bench's ``run --post`` (``tooling/assignment_bench/post.py``).

A run of the bench rig of ``test_assignment_bench_run.py`` given a pull
request on a fake GitHub: the first post, a re-run that edits the comment in
place, a failed run, a refused card, a fork, a stale baseline. Then the
comment's text from a card, ``gh`` itself through a fake ``gh`` on the PATH,
and the nightly baseline's units. No host, no server, no GitHub.
"""

import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest
import test_assignment_bench_run as base


bench = base.bench
brun = base.brun
cr = base.cr
post = bench.bench_post

DEVELOP, BRANCH = base.DEVELOP, base.BRANCH
REPO = Path(__file__).resolve().parents[3]
SYSTEMD = REPO / "tooling" / "systemd"


# ------------------------------------------------------------- the fakes


class FakeGh:
    """A PR and its comments, as the poster reaches them through ``gh``."""

    def __init__(self, cross=False, head=BRANCH):
        self.cross = cross
        self.head = head
        self.comments = []
        self.calls = []
        #: Comment ids whose edit is refused, as another identity's would be.
        self.uneditable = set()

    def pull(self, number):
        self.calls.append(("pull", number))
        return {
            "number": number,
            "url": f"https://github.example/pull/{number}",
            "headRefOid": self.head,
            "headRefName": "topic",
            "baseRefName": "develop",
            "isCrossRepository": self.cross,
        }

    def marked(self, number):
        return [
            {"id": c["id"], "url": c["url"]}
            for c in self.comments
            if c["body"].startswith(post.MARKER)
        ]

    def create(self, number, body):
        self.calls.append(("create", number))
        one = {
            "id": 100 + len(self.comments),
            "url": f"https://github.example/c/{100 + len(self.comments)}",
            "body": body,
        }
        self.comments.append(one)
        return one["url"]

    def edit(self, comment_id, body):
        self.calls.append(("edit", comment_id))
        if comment_id in self.uneditable:
            raise post.PostError("gh api failed: 403")
        one = next(c for c in self.comments if c["id"] == comment_id)
        one["body"] = body
        return one["url"]

    def bench_comments(self):
        return [c for c in self.comments if c["body"].startswith(post.MARKER)]


@pytest.fixture
def rig(tmp_path):
    return base.Rig(tmp_path)


@pytest.fixture
def gh():
    return FakeGh()


def _post(rig, gh, ref="topic", number=7, **kwargs):
    poster = post.Poster(gh=gh, number=number)
    return rig.run(ref, post=poster, **kwargs)


def _develop_baseline(rig):
    rig.instance.revisions[BRANCH] = rig.instance.revisions[DEVELOP]
    assert rig.run("origin/develop", baseline=True) == 0
    return rig.state()["baseline"]


# ------------------------------------------------------- posting a card


def test_the_first_run_posts_the_card_as_one_comment(rig, gh, capsys):
    _develop_baseline(rig)

    assert _post(rig, gh) == 0

    assert [c[0] for c in gh.calls] == ["pull", "create"]
    (comment,) = gh.comments
    body = comment["body"]
    assert body.startswith(post.MARKER + "\n### Bench card: PASS\n")
    assert f"`topic` at `{BRANCH[:9]}`, the PR's head" in body
    assert "reference v1.0.0 (1.0.0+assign0.4)" in body
    assert "Samples read: Db 2 of 2, Et 1 of 1." in body
    assert "**GATE stage3: PASS - 4 gated readings met**" in body
    assert "| metric | Db | Et | pooled |" in body
    assert "| samples read | 2 | 1 | 3 |" in body
    assert f"**Since develop's baseline** (`origin/develop` at `{DEVELOP[:9]}`" in body
    assert "0 of 3 readings moved" in body
    assert "<summary>The whole card</summary>" in body
    assert "<summary>Card Db</summary>" in body and "<summary>Card Et</summary>" in body
    assert "posted the bench comment on PR #7" in capsys.readouterr().out
    # --post diffs against the baseline, as --baseline does.
    assert rig.compare.calls[-1]["baseline"] is not None


def test_a_re_run_edits_the_comment_in_place(rig, gh, capsys):
    _develop_baseline(rig)
    assert _post(rig, gh) == 0
    first = gh.comments[0]["id"]
    gh.comments.append({"id": 50, "url": "u", "body": "a review comment"})

    rig.instance.shas["topic"] = gh.head = "c" * 40
    rig.instance.revisions["c" * 40] = rig.instance.revisions[DEVELOP]
    rig.compare.verdict = "fail"
    assert _post(rig, gh) == 1

    assert [c["id"] for c in gh.bench_comments()] == [first]
    assert ("edit", first) in gh.calls
    assert sum(1 for c in gh.calls if c[0] == "create") == 1
    body = gh.bench_comments()[0]["body"]
    assert "### Bench card: FAIL" in body and f"`{'c' * 9}`" in body
    assert "edited the bench comment on PR #7" in capsys.readouterr().out


def test_a_comment_that_cannot_be_edited_gets_a_new_one(rig, gh):
    gh.comments.append({"id": 9, "url": "u9", "body": post.MARKER + "\nold"})
    gh.uneditable.add(9)
    _post(rig, gh)
    assert ("edit", 9) in gh.calls
    assert len(gh.bench_comments()) == 2


def test_a_ref_that_is_not_the_pr_s_head_is_said_so(rig, gh):
    gh.head = "e" * 40
    _post(rig, gh)
    assert f"not the PR's head, which is `{'e' * 9}`" in gh.comments[0]["body"]


def test_a_run_that_left_samples_out_lists_them(rig, gh):
    rig.server.ends[("S1", None)] = ("failed", "x")
    assert _post(rig, gh) == 1
    body = gh.comments[0]["body"]
    assert "**Left out:**" in body and "- set Db: 1 sample(s) failed" in body
    assert "S1" not in body


# -------------------------------------------------------- the baseline


def test_no_baseline_yet_is_said_and_nothing_is_diffed(rig, gh):
    _post(rig, gh)
    assert rig.compare.calls[0]["baseline"] is None
    assert "No diff: there is no baseline of develop yet." in gh.comments[0]["body"]


def test_a_baseline_older_than_the_merge_base_is_not_diffed_against(rig, gh, capsys):
    first = _develop_baseline(rig)
    # The PR was cut from a develop newer than the baseline's commit.
    rig.instance.merge_bases[(BRANCH, "origin/develop")] = "f" * 40

    _post(rig, gh)

    assert ("merge_base", BRANCH, "origin/develop") in rig.instance.calls
    assert rig.compare.calls[-1]["baseline"] is None
    assert not (rig.card_dir() / "diff.json").exists()
    assert rig.state()["cards"][-1]["baseline"] is None
    why = (
        f"No diff: develop's baseline ({DEVELOP[:9]}, made {first['at']}) is older "
        f"than this PR's merge base {'f' * 9}"
    )
    assert why in gh.comments[0]["body"]
    assert why in capsys.readouterr().out


def test_a_baseline_at_or_after_the_merge_base_is_diffed_against(rig, gh):
    _develop_baseline(rig)
    rig.instance.merge_bases[(BRANCH, "origin/develop")] = "f" * 40
    rig.instance.ancestry.add(("f" * 40, DEVELOP))
    _post(rig, gh)
    assert rig.compare.calls[-1]["baseline"] is not None
    assert "Since develop's baseline" in gh.comments[0]["body"]


def test_a_merge_base_that_cannot_be_found_is_not_diffed_against(rig, gh):
    _develop_baseline(rig)
    rig.instance.merge_bases[(BRANCH, "origin/develop")] = None
    _post(rig, gh)
    assert rig.compare.calls[-1]["baseline"] is None
    assert "the merge base of this PR" in gh.comments[0]["body"]


def test_a_run_without_post_never_asks_for_the_merge_base(rig):
    _develop_baseline(rig)
    rig.run("topic", baseline=True)
    assert not [c for c in rig.instance.calls if c[0] == "merge_base"]


# ------------------------------------------------------- refusals


def test_a_fork_is_refused_before_anything_runs(rig, capsys):
    gh = FakeGh(cross=True)
    with pytest.raises(post.PostError, match="comes from a fork"):
        _post(rig, gh)
    assert rig.instance.calls == []
    assert gh.comments == [] and not rig.compare.calls


def test_a_planted_name_posts_the_refusal_and_never_the_name(rig, gh):
    rig.compare.plant = ("card_Db.json", " Instrument9")
    assert _post(rig, gh) == 2
    body = gh.comments[0]["body"]
    assert "### Bench card refused" in body
    assert (
        "**The card was refused by the scrub:** card_Db.json holds a name the "
        "bench instance holds" in body
    )
    assert "Nothing of it was written or posted" in body
    assert "Instrument9" not in body


@pytest.mark.parametrize(
    "plant, kind",
    [
        ("sample S3", "a sample, file or run id"),
        (" a1.raw ", "a raw file's name"),
        (" localhost ", "a host name"),
    ],
)
def test_a_refusal_names_the_kind_of_name(rig, gh, plant, kind):
    rig.compare.plant = ("card.md", plant)
    _post(rig, gh)
    body = gh.comments[0]["body"]
    assert f"card.md holds {kind}." in body
    assert plant.strip().split()[-1] not in body


def test_the_comparison_s_own_refusal_posts_its_kind(rig, gh):
    def refusing(**kwargs):
        with open(kwargs["log"], "a") as fh:
            fh.write("$ compare_runs.py --sets x\n")
            fh.write(
                "card refused, nothing written: card_Et.md holds 'W2', which the "
                "deny list names\n"
            )
        return 2

    rig.compare = refusing
    assert _post(rig, gh) == 2
    body = gh.comments[0]["body"]
    assert "card_Et.md holds a sample, file or run id" in body
    assert "W2" not in body


def test_a_comment_the_scrub_refuses_is_replaced_by_its_refusal(rig, gh, capsys):
    # The reference's version string is the server's text, on the comment only.
    state = rig.state()
    state["reference"]["version"] = "1.0.0+Instrument9"
    bench.save_state(rig.home, state)
    for rows in rig.server.listing.values():
        rows[0]["engine_version"] = "1.0.0+Instrument9"

    assert _post(rig, gh) == 0

    body = gh.comments[0]["body"]
    assert "### Bench card refused" in body
    assert "the comment holds a name the bench instance holds" in body
    assert (
        "The card was written on the agent box, but the comment was not posted" in body
    )
    assert "Instrument9" not in body
    printed = capsys.readouterr().out
    assert "the comment was refused by the scrub" in printed
    assert "Instrument9" not in printed
    assert "the scrub refused the comment" in (rig.card_dir() / "run.log").read_text()


def test_the_comment_s_own_words_refuse_nothing(rig, gh):
    _develop_baseline(rig)
    rig.instance.merge_bases[(BRANCH, "origin/develop")] = "f" * 40
    # Every word of these is one the comment prints of its own, and prints here.
    rig.instance.names["names"] += ["Merge base", "Nightly", "Bench card", "PR head"]
    assert _post(rig, gh) == 0
    body = gh.comments[0]["body"]
    assert "### Bench card: PASS" in body and "the nightly run" in body


# ------------------------------------------------------- failures


def test_a_failed_run_posts_the_error_s_tail(rig, gh):
    rig.instance.db = ["r9"]
    with pytest.raises(brun.RunError, match="alembic revision r9"):
        _post(rig, gh)
    body = gh.comments[0]["body"]
    assert "### Bench run failed" in body
    assert "**The run stopped: RunError.**" in body
    assert "the bench database is at alembic revision r9" in body
    assert f"`topic` at `{BRANCH[:9]}`" in body
    # The stack is still taken down.
    assert rig.instance.calls[-2:] == ["stop", "remove_worktree"]


def test_a_run_that_fails_before_the_checkout_is_posted_too(rig, gh):
    state = rig.state()
    state["reference"] = None
    bench.save_state(rig.home, state)
    with pytest.raises(brun.RunError):
        _post(rig, gh)
    body = gh.comments[0]["body"]
    assert "no reference at a pin" in body and "`topic` at `?`" in body


def test_an_error_that_names_a_sample_is_not_posted(rig, gh):
    def broken(**kwargs):
        raise RuntimeError("compare broke on sample S2")

    rig.compare = broken
    with pytest.raises(RuntimeError):
        _post(rig, gh)
    body = gh.comments[0]["body"]
    assert "**The run stopped: RuntimeError.**" in body
    assert (
        "The error's text was refused by the scrub: it holds a sample, file or "
        "run id." in body
    )
    assert "S2" not in body


def test_an_error_that_names_this_run_s_run_is_not_posted(rig, gh):
    # Run ids of their own, apart from the sample's: only this run knows them.
    assign = rig.server.assign

    def renamed(sample, config):
        launch = assign(sample, config)
        if launch.run_id:
            row = rig.server.listing[sample][0]
            row["peak_assignment_run_id"] = launch.run_id = (
                f"Q{len(rig.server.assigned)}"
            )
        return launch

    def broken(**kwargs):
        raise RuntimeError("the comparison hung on Q2")

    rig.server.assign = renamed
    rig.compare = broken
    with pytest.raises(RuntimeError):
        _post(rig, gh, sets=["Db"])
    body = gh.comments[0]["body"]
    assert "it holds a sample, file or run id" in body and "Q2" not in body


def test_a_refusal_the_scrub_refuses_too_posts_nothing(rig, gh, monkeypatch):
    monkeypatch.setattr(
        post, "refused_body", lambda summary, pr, where, kind: "leaks Instrument9"
    )
    state = rig.state()
    state["reference"]["version"] = "1.0.0+Instrument9"
    bench.save_state(rig.home, state)
    for rows in rig.server.listing.values():
        rows[0]["engine_version"] = "1.0.0+Instrument9"
    with pytest.raises(post.PostError, match="nothing was posted"):
        _post(rig, gh)
    assert gh.comments == []


def test_an_error_whose_deny_list_cannot_be_built_posts_no_text(rig, gh):
    def no_database():
        raise RuntimeError("the database at db.example is down")

    # The card's deny list needs the database: the run stops there, and the
    # failure's own deny list cannot be built either.
    rig.instance.private_names = no_database
    with pytest.raises(RuntimeError, match="is down"):
        _post(rig, gh)
    body = gh.comments[0]["body"]
    assert "**The run stopped: RuntimeError.**" in body
    assert "The deny list could not be built to scrub the error" in body
    assert "db.example" not in body


def test_a_failure_that_cannot_be_posted_keeps_the_run_s_error(rig, gh, capsys):
    def broken(*args):
        raise post.PostError("gh api failed")

    rig.instance.db = ["r9"]
    gh.create = broken
    with pytest.raises(brun.RunError, match="alembic revision"):
        _post(rig, gh)
    assert "the failure could not be posted: gh api failed" in capsys.readouterr().out


def test_a_comparison_that_wrote_no_card_posts_its_output_s_tail(rig, gh):
    def failing(**kwargs):
        with open(kwargs["log"], "a") as fh:
            fh.write("$ compare_runs.py --sets x\n")
            fh.write("Traceback (most recent call last):\nValueError: no frame\n")
        return 1

    rig.compare = failing
    assert _post(rig, gh) == 2
    body = gh.comments[0]["body"]
    assert "**The comparison wrote no card (exit 1).**" in body
    assert "ValueError: no frame" in body
    assert "$ compare_runs.py" not in body


def test_a_card_that_cannot_be_posted_is_a_refusal_on_the_command_line(
    tmp_path, monkeypatch, capsys
):
    class Gh(FakeGh):
        def create(self, number, body):
            raise post.PostError("gh api failed: 502")

    class Runner:
        def __init__(self, **kwargs):
            pass

        def run(self, ref, sets, decoys, baseline, post):
            post.post("x")

    monkeypatch.setattr(brun, "BenchRun", Runner)
    monkeypatch.setattr(post, "GhCli", lambda: Gh())
    home = str(tmp_path / "home")
    assert bench.main(["--home", home, "run", "topic", "--post", "7"]) == 2
    assert "bench: gh api failed: 502" in capsys.readouterr().err


# ------------------------------------------------------- the command line


def test_the_command_line_posts_and_diffs_against_the_baseline(tmp_path, monkeypatch):
    calls = []
    gh = FakeGh()

    class Runner:
        def __init__(self, **kwargs):
            pass

        def run(self, ref, sets, decoys, baseline, post):
            calls.append((ref, baseline, post.number, post.pr.head_sha))
            return 0

    monkeypatch.setattr(brun, "BenchRun", Runner)
    monkeypatch.setattr(post, "GhCli", lambda: gh)
    home = str(tmp_path / "home")
    assert bench.main(["--home", home, "run", "topic", "--post", "7"]) == 0
    assert calls == [("topic", True, 7, BRANCH)]


def test_the_command_line_refuses_a_fork_before_it_takes_the_lock(
    tmp_path, monkeypatch, capsys
):
    class Runner:
        def __init__(self, **kwargs):
            pass

        def run(self, *args, **kwargs):
            raise AssertionError("a fork never runs")

    monkeypatch.setattr(brun, "BenchRun", Runner)
    monkeypatch.setattr(post, "GhCli", lambda: FakeGh(cross=True))
    home = bench.Home(tmp_path / "home")
    home.make()
    with home.lock():
        argv = ["--home", str(home.root), "run", "topic", "--post", "7", "--no-wait"]
        assert bench.main(argv) == 2
    err = capsys.readouterr().err
    assert "comes from a fork" in err and "holds the lock" not in err


# ------------------------------------------------------- the comment's text


def _card(tmp_path, *, missed=False, moved=()):
    """A card folder as a run writes it, gated by the shipped thresholds."""
    thresholds = cr.load_thresholds(cr.DEFAULT_THRESHOLDS)
    readings = {
        "samples": {"value": 80, "n": None},
        "peaks": {"value": 67845, "n": None},
        "G6_assigned": {"value": 47 if missed else 2, "n": 12208},
    }
    card = {
        "format": cr.CARD_FORMAT,
        "engines": {"a": "mascope", "b": "peaky"},
        "columns": {"Db": {"instrument": "orbitrap", "readings": readings}},
        "gate": {
            "stage": "stage3",
            "verdict": "fail" if missed else "pass",
            "marks": {"Db": {"G6_assigned": "miss" if missed else "ok"}},
        },
        "thresholds": thresholds,
        "baseline": None,
    }
    out = tmp_path / "cards" / "topic" / "20261113-010000"
    out.mkdir(parents=True)
    (out / "card.json").write_text(json.dumps(card))
    (out / "card.md").write_text(
        "# Gate card: mascope against peaky\n\nthe whole card\n"
    )
    (out / "card_Db.md").write_text("# Card Db\n\nthe set's card\n")
    if moved is not None:
        diff = {
            "baseline": {
                "ref": "origin/develop",
                "sha": DEVELOP,
                "at": "then",
                "path": "p",
            },
            "compared": 3,
            "by_column": {"Db": 3},
            "moved": list(moved),
            "only_baseline": [],
            "only_card": [],
        }
        (out / "diff.json").write_text(json.dumps(diff))
    summary = {
        "ref": "topic",
        "sha": BRANCH,
        "reference": {"pin": "v0.9.0", "version": "0.9.0"},
        "decoys": False,
        "minutes": 93.8,
        "finished": "now",
        "sets": {"Db": {"samples": 80, "read": 80}},
        "card": {
            "verdict": card["gate"]["verdict"],
            "gate": "GATE line",
            "sets": ["Db"],
        },
    }
    return summary, out


PR = post.PullRequest(7, "url", BRANCH, "topic", "develop")


def test_the_headline_table_holds_the_samples_and_the_gated_readings(tmp_path):
    summary, out = _card(tmp_path, missed=True)
    body = post.card_body(summary, out, PR, [], cr)
    head = body.split("<details>")[0]
    assert "| metric | Db | target (stage3) |" in head
    assert "| samples read | 80 | - |" in head
    assert "| G6 of them at assigned | 47 MISS | <= 5 |" in head
    # A reading no target gates is in the whole card only.
    assert "| peaks |" not in head
    assert "### Bench card: FAIL" in head and "**GATE line**" in head


def test_a_reading_a_set_missed_is_a_headline_one_without_a_target_of_its_own(
    tmp_path,
):
    # A set held to a target of its own misses where the metric has none.
    summary, out = _card(tmp_path)
    card = json.loads((out / "card.json").read_text())
    card["gate"]["marks"]["Db"]["peaks"] = "miss"
    (out / "card.json").write_text(json.dumps(card))
    head = post.card_body(summary, out, PR, [], cr).split("<details>")[0]
    assert "| peaks | 67845 MISS | - |" in head


def test_the_moved_headline_readings_are_listed_and_the_rest_counted(tmp_path):
    moved = [
        {
            "set": "Db",
            "metric": "G6_assigned",
            "was": {"value": 11, "n": 11698},
            "now": {"value": 47, "n": 12208},
        },
        {
            "set": "Db",
            "metric": "peaks",
            "was": {"value": 1, "n": None},
            "now": {"value": 2, "n": None},
        },
    ]
    summary, out = _card(tmp_path, moved=moved)
    head = post.card_body(summary, out, PR, [], cr).split("<details>")[0]
    assert "2 of 3 readings moved." in head
    assert "| Db | G6 of them at assigned | 11 of 11698 | 47 of 12208 |" in head
    assert "| Db | peaks |" not in head

    summary, out = _card(tmp_path / "2", moved=moved[1:])
    head = post.card_body(summary, out, PR, [], cr).split("<details>")[0]
    assert "None of the headline readings moved." in head


def test_the_whole_card_and_the_set_s_cards_are_collapsed(tmp_path):
    summary, out = _card(tmp_path, moved=None)
    body = post.card_body(summary, out, PR, [], cr, no_diff="No diff: none yet.")
    assert "**No diff: none yet.**" in body
    whole = body.split("<summary>The whole card</summary>")[1]
    assert "#### Gate card: mascope against peaky" in whole
    assert "<summary>Card Db</summary>" in whole and "#### Card Db" in whole


def test_a_card_too_long_for_a_comment_drops_the_set_s_cards_then_cuts(tmp_path):
    summary, out = _card(tmp_path)
    (out / "card_Db.md").write_text("x" * 40_000)
    (out / "card.md").write_text("y" * 30_000)
    body = post.card_body(summary, out, PR, [], cr)
    assert len(body) <= post.LIMIT and "<summary>Card Db</summary>" not in body
    assert "y" * 30_000 in body

    (out / "card.md").write_text("y" * 70_000)
    body = post.card_body(summary, out, PR, [], cr)
    assert len(body) <= post.LIMIT
    assert "cut to fit a comment; the whole card is on the agent box" in body


def test_a_refusal_and_a_failure_print_the_ref_and_its_commit_alone():
    summary = {
        "ref": "topic",
        "sha": BRANCH,
        "reference": {"pin": "v1", "version": "1+leak"},
        "sets": {},
    }
    for body in (
        post.refused_body(summary, PR, "card.md", "a host name"),
        post.failed_body(summary, PR, "The run stopped: X.", "a\n```b```"),
    ):
        assert body.startswith(post.MARKER + "\n")
        assert "leak" not in body and "v1" not in body
    assert "````\na\n```b```\n````" in post.failed_body(summary, PR, "w", "a\n```b```")


@pytest.mark.parametrize(
    "token, kind",
    [
        ("S3", "a sample, file or run id"),
        ("a1.raw", "a raw file's name"),
        ("ingest", "a raw file's name"),
        ("box", "a host name"),
        ("Instrument9", "a name the bench instance holds"),
        ("elsewhere", post.UNKNOWN_KIND),
    ],
)
def test_a_token_is_named_by_its_kind(token, kind):
    deny = {
        "ids": ["S3"],
        "strict": ["a1.raw", "ingest_2025.raw"],
        "hosts": ["box.lan"],
        "names": ["Acquisitions Instrument9"],
    }
    assert post.token_kind(token, deny).startswith(kind)


def test_the_comparison_s_refusal_is_read_off_its_output():
    text = (
        "x\ncard refused, nothing written: card.md holds 'S1', which the deny list names\n"
        'card refused, nothing written: card_Db.md holds "it\'s", which the deny list names\n'
    )
    assert post.refused_token(text) == ("card_Db.md", "it's")
    assert post.refused_token("nothing\n") is None


def test_the_tail_keeps_the_last_lines_within_its_size():
    text = "\n".join(f"line {n}" for n in range(100))
    assert post.tail(text, lines=3) == "line 97\nline 98\nline 99"
    assert post.tail(text, chars=7) == "line 99"


def test_the_comparison_s_output_is_what_follows_its_command(tmp_path):
    log = tmp_path / "run.log"
    log.write_text(
        "12:00 before\n$ compare_runs.py --sets a\nfirst\n$ compare_runs.py --sets b\nsecond\n"
    )
    assert brun._compare_output(log) == "second\n"
    assert brun._compare_output(tmp_path / "none") == ""


# ------------------------------------------------------- gh itself


@pytest.fixture
def fake_gh(tmp_path):
    """A ``gh`` that records its arguments and stdin and answers from a file."""
    log = tmp_path / "gh.jsonl"
    answers = tmp_path / "answers.json"
    script = tmp_path / "gh"
    script.write_text(
        f"#!{sys.executable}\n"
        "import json, sys\n"
        f"log = open({str(log)!r}, 'a')\n"
        "log.write(json.dumps({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}) + '\\n')\n"
        f"answers = json.load(open({str(answers)!r}))\n"
        "key = ' '.join(sys.argv[1:3])\n"
        "code, out = answers.get(key, [0, ''])\n"
        "sys.stdout.write(out)\n"
        "sys.stderr.write('' if code == 0 else 'HTTP 403: refused')\n"
        "sys.exit(code)\n"
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)

    def calls():
        return [json.loads(line) for line in log.read_text().splitlines()]

    def answer(**by_key):
        answers.write_text(
            json.dumps({k.replace("_", " "): v for k, v in by_key.items()})
        )

    answer()
    return post.GhCli(cwd=tmp_path, gh=str(script)), calls, answer


def test_gh_reads_the_pr(fake_gh):
    gh, calls, answer = fake_gh
    answer(pr_view=[0, json.dumps({"number": 7, "isCrossRepository": False})])
    assert gh.pull(7)["number"] == 7
    assert calls()[0]["argv"][:3] == ["pr", "view", "7"]
    assert "isCrossRepository" in calls()[0]["argv"][-1]


def test_gh_lists_the_marked_comments_and_posts_and_edits_with_a_json_body(fake_gh):
    gh, calls, answer = fake_gh
    answer(
        **{"api --paginate": [0, '{"id": 5, "url": "u5"}\n{"id": 6, "url": "u6"}\n']}
    )
    assert gh.marked(7) == [{"id": 5, "url": "u5"}, {"id": 6, "url": "u6"}]
    argv = calls()[0]["argv"]
    assert argv[2] == "repos/{owner}/{repo}/issues/7/comments"
    assert json.dumps(post.MARKER) in argv[-1]

    answer(**{"api -X": [0, "https://c/1\n"]})
    body = post.MARKER + '\n"quotes" and `ticks`\n'
    assert gh.create(7, body) == "https://c/1"
    assert gh.edit(5, body) == "https://c/1"
    create, edit = calls()[1:]
    assert create["argv"][:4] == [
        "api",
        "-X",
        "POST",
        "repos/{owner}/{repo}/issues/7/comments",
    ]
    assert edit["argv"][:4] == [
        "api",
        "-X",
        "PATCH",
        "repos/{owner}/{repo}/issues/comments/5",
    ]
    assert json.loads(create["stdin"]) == {"body": body} == json.loads(edit["stdin"])


def test_a_gh_that_fails_is_a_post_error(fake_gh, tmp_path):
    gh, _, answer = fake_gh
    answer(**{"api -X": [1, ""]})
    with pytest.raises(post.PostError, match="HTTP 403: refused"):
        gh.edit(5, "x")
    with pytest.raises(post.PostError, match="could not be run"):
        post.GhCli(cwd=tmp_path, gh=str(tmp_path / "no-gh")).pull(7)


def test_the_dev_instance_reads_the_merge_base_and_the_history(tmp_path, monkeypatch):
    dev, seen = base._dev(
        tmp_path,
        monkeypatch,
        {
            ("merge-base", BRANCH): (0, "f" * 40 + "\n"),
            ("merge-base", "--is-ancestor"): (0, ""),
        },
    )
    assert dev.merge_base(BRANCH, "origin/develop") == "f" * 40
    assert seen[-1][3:] == ["merge-base", BRANCH, "origin/develop"]
    assert dev.contains("f" * 40, DEVELOP) is True
    assert seen[-1][3:] == ["merge-base", "--is-ancestor", "f" * 40, DEVELOP]

    dev, _ = base._dev(
        tmp_path,
        monkeypatch,
        {("merge-base", BRANCH): (1, ""), ("merge-base", "--is-ancestor"): (1, "")},
    )
    assert dev.merge_base(BRANCH, "origin/develop") is None
    assert dev.contains("f" * 40, DEVELOP) is False


# ------------------------------------------------------- the nightly units


def _unit(name):
    return (SYSTEMD / name).read_text(encoding="utf-8")


def test_the_nightly_baseline_runs_develop_s_head_from_develop_s_tool():
    service = _unit("bench-baseline.service")
    assert "Type=oneshot" in service
    assert "EnvironmentFile=%h/.config/mascope/bench-baseline.env" in service
    run = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    assert "checkout -q --force --detach origin/develop" in run
    assert "uv sync --frozen --all-groups" in run
    assert "tooling/assignment_bench/bench.py run origin/develop --baseline" in run
    # A failed gate still made the baseline; only a run that wrote no card fails.
    assert "SuccessExitStatus=1" in service
    timer = _unit("bench-baseline.timer")
    assert "OnCalendar=" in timer and "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_the_nightly_baseline_s_command_is_one_bash_can_read(tmp_path):
    """The unit's shell line, as systemd hands it to bash: ``$$`` is a ``$``."""
    run = next(
        line
        for line in _unit("bench-baseline.service").splitlines()
        if line.startswith("ExecStart=")
    )
    script = run.split("-lc '", 1)[1].rsplit("'", 1)[0].replace("$$", "$")
    assert "$BENCH_CHECKOUT" in script and "$$" not in script
    subprocess.run(["bash", "-n", "-c", script], check=True)


def test_the_bench_s_units_are_not_a_deployment_s(monkeypatch):
    from mascope_cli.cmd.prod import units

    assert not {"bench-baseline.service", "bench-baseline.timer"} & set(units.UNITS)
    assert os.path.exists(SYSTEMD / "bench-baseline.timer")
