"""
The session store: one directory per conversation, each Frame's rows as
parquet beside its summary, reloadable from the recorded query, and the
whole session disposable.

Hermetic: the reload goes through a stand-in reloader here; the toolkit's
own reload (its loaders run again) is in ``test_ai_loaders.py``.
"""

import json

import pandas as pd
import pytest

from mascope_ai import Frame, Ref, Session, StaleFrame
from mascope_ai.session import default_root


SAMPLE = Ref("s1", "sample", "Sample 1", parent="b1")


def _df() -> pd.DataFrame:
    df = pd.DataFrame(
        {
            "peak_id": ["p1", "p2", "p3"],
            "mz": [100.1, 200.2, 300.3],
            "target_collection_ids": [["c1"], None, ["c1", "c2"]],
            "when": pd.to_datetime(["2026-01-01"] * 3, utc=True),
        }
    )
    df.attrs.update(
        complete=False,
        failures=[{"sample_item_id": "s2", "error_type": "E", "error": "x"}],
    )
    return df


def _saved(session: Session, df: pd.DataFrame | None = None) -> Frame:
    df = _df() if df is None else df
    frame = Frame.from_df(
        df,
        source_refs=[SAMPLE],
        query={"op": "load_peaks", "params": {"x": 1}},
        session=session,
    )
    session.save(frame, df)
    return frame


@pytest.fixture
def session(tmp_path) -> Session:
    return Session("conversation-1", root=tmp_path)


class TestRoundTrip:
    def test_a_frame_comes_back_with_its_rows_and_summary(self, session, tmp_path):
        frame = _saved(session)

        again = Session("conversation-1", root=tmp_path).frame(frame.id)

        assert again.to_dict() == frame.to_dict()
        pd.testing.assert_frame_equal(again.df, _df(), check_dtype=False)
        assert again.complete is False
        assert again.failures[0].where == {"sample_item_id": "s2"}

    def test_the_files_are_where_the_layout_says(self, session):
        frame = _saved(session)

        folder = session.path / "frames"
        assert session.path.parent.name and session.path.name == "conversation-1"
        assert (folder / f"{frame.id}.parquet").exists()
        meta = json.loads((folder / f"{frame.id}.json").read_text())
        assert meta["query"] == {"op": "load_peaks", "params": {"x": 1}}
        assert meta["n_rows"] == 3

    def test_frames_lists_them_oldest_first(self, session):
        first, second = _saved(session), _saved(session)

        assert [f.id for f in session.frames()] == [first.id, second.id]

    def test_a_column_parquet_cannot_hold_is_kept_as_json_text(self, session):
        df = pd.DataFrame({"mixed": [{"a": 1}, "text", 3, None], "n": [1, 2, 3, 4]})

        frame = _saved(session, df)

        meta = json.loads((session.path / "frames" / f"{frame.id}.json").read_text())
        assert meta["stringified"] == ["mixed"]
        stored = pd.read_parquet(session.path / "frames" / f"{frame.id}.parquet")
        assert stored["mixed"].tolist()[:3] == ['{"a": 1}', '"text"', "3"]
        # ... and read back as the values it held
        assert session.frame(frame.id).df["mixed"].tolist() == [
            {"a": 1},
            "text",
            3,
            None,
        ]

    def test_attrs_parquet_cannot_hold_stay_out_of_the_file(self, session):
        df = _df()
        # The SDK's frames carry such attrs: a batch ledger's species table
        df.attrs["species"] = pd.DataFrame({"x": [1]})

        frame = _saved(session, df)

        assert len(session.frame(frame.id).df) == 3

    def test_an_unknown_frame(self, session):
        with pytest.raises(KeyError):
            session.frame("f_000000000000")
        with pytest.raises(ValueError, match="Not a Frame ID"):
            session.frame("../../etc/passwd")


class TestEviction:
    def test_evicted_rows_are_loaded_again_from_the_query(self, session):
        frame = _saved(session)
        asked: list[dict] = []

        def reloader(query):
            asked.append(query)
            return _df()

        session.reloader = reloader
        session.evict(frame.id)
        frame.evict()

        rows = frame.df

        assert asked == [{"op": "load_peaks", "params": {"x": 1}}]
        assert len(rows) == 3
        # ... and kept again, so the next read is the session's copy
        frame.df  # noqa: B018
        assert len(asked) == 1

    @pytest.mark.parametrize(
        "change, complete, said",
        [
            (lambda df: df.iloc[:2], False, "2 rows, not 3"),
            (lambda df: df.drop(columns="mz"), False, "other columns"),
            # The Frame was incomplete; the reload read everything
            (lambda df: df, True, "complete"),
        ],
    )
    def test_rows_loaded_again_that_differ_are_refused(
        self, session, change, complete, said
    ):
        frame = _saved(session)
        session.evict(frame.id)
        frame.evict()

        def reloader(query):
            df = change(_df())
            df.attrs = {"complete": complete}
            return df

        session.reloader = reloader

        with pytest.raises(StaleFrame, match=said) as caught:
            frame.df  # noqa: B018
        assert caught.value.to_dict()["error"] == "stale_frame"
        assert not (session.path / "frames" / f"{frame.id}.parquet").exists()

    def test_without_a_reloader_it_says_so(self, session):
        frame = _saved(session)
        session.evict(frame.id)

        with pytest.raises(RuntimeError, match="no toolkit"):
            session.frame(frame.id).df  # noqa: B018


class TestDisposal:
    def test_discard_deletes_the_session(self, session):
        _saved(session)

        session.discard()

        assert not session.path.exists()
        assert session.frames() == []

    def test_a_session_id_is_a_plain_name(self, tmp_path):
        with pytest.raises(ValueError, match="session ID"):
            Session("../elsewhere", root=tmp_path)
        assert Session(root=tmp_path).id.startswith("s-")

    def test_the_root(self, monkeypatch, tmp_path):
        monkeypatch.setenv("MASCOPE_AI_SCRATCH", str(tmp_path / "scratch"))
        assert default_root() == tmp_path / "scratch"

        monkeypatch.delenv("MASCOPE_AI_SCRATCH")
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
        assert default_root() == tmp_path / "cache" / "mascope_ai" / "sessions"
