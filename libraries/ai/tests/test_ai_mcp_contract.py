"""
The MCP server against a live demo stack: every tool once, on the published
demo dataset, each answering within a few kilobytes.

Opt-in, with the switch and settings of the toolkit's contract tests
(``test_ai_contract.py``): a running demo stack and
``MASCOPE_SDK_CONTRACT=1``. The inputs come from the demo data itself, so the
tests survive bundle updates.
"""

import json
import os

import pandas as pd
import pytest

from mascope_ai import Session, Toolkit
from mascope_ai.mcp import MAX_RESULT_BYTES, Server


BASE_URL = os.environ.get("MASCOPE_SDK_TEST_URL", "http://127.0.0.1:8080")
TOKEN = os.environ.get("MASCOPE_SDK_TEST_TOKEN", "mascope_demo_sdk_token")

pytestmark = pytest.mark.skipif(
    os.environ.get("MASCOPE_SDK_CONTRACT") != "1",
    reason=(
        "contract tests need a running demo stack; start one "
        "(docker compose -f docker-compose.demo.yaml up -d) and set "
        "MASCOPE_SDK_CONTRACT=1"
    ),
)


@pytest.fixture(scope="module")
def server(tmp_path_factory) -> Server:
    from test_ai_contract import _workspace_with_data

    from mascope_sdk import MascopeClient

    session = Session(root=tmp_path_factory.mktemp("sessions"))

    def make(session: Session) -> Toolkit:
        client = MascopeClient(
            url=BASE_URL,
            access_token=TOKEN,
            workspace=_workspace_with_data(),
            service_name="mascope_sdk",
        )
        return Toolkit(client, session=session)

    return Server(session, make_toolkit=make)


def ok(server: Server, tool: str, **arguments) -> dict:
    result = server.call(tool, arguments)
    text = result["content"][0]["text"]
    assert len(text.encode()) <= MAX_RESULT_BYTES
    out = json.loads(text)
    assert not result["isError"], out
    return out


@pytest.fixture(scope="module")
def sample(server) -> dict:
    """A demo sample with matched peaks, and what the tools say of it."""
    for batch in ok(server, "find", query="", kind="batch", limit=50)["refs"]:
        if not ok(server, "describe", kind="batch", name=batch["id"])["n_samples"]:
            continue
        ref = ok(server, "find", query="", kind="sample", batch=batch["id"], limit=1)[
            "refs"
        ][0]
        peaks = ok(server, "load_peaks", samples=[ref["id"]])
        rows = eval(peaks["rows_in_python"], {"Session": Session})
        matched = rows.dropna(subset=["target_compound_formula"])
        if matched.empty:
            continue
        top = matched.sort_values("height").iloc[-1]
        return {
            "ref": ref,
            "batch": batch,
            "peaks": peaks,
            "rows": rows,
            "formula": top["target_compound_formula"],
            "ion": top["target_ion_formula"],
            "mz": float(top["mz"]),
            "describe": ok(server, "describe", kind="sample", name=ref["id"]),
        }
    pytest.fail("the demo stack has no sample with a matched peak")


def test_find_and_describe_every_kind(server):
    found = ok(server, "find", query="")
    kinds = {ref["kind"]: ref for ref in found["refs"]}
    assert {"workspace", "dataset", "batch", "collection"} <= set(kinds)
    for kind, ref in kinds.items():
        assert (
            ok(server, "describe", kind=kind, name=ref["id"])["ref"]["id"] == ref["id"]
        )


def test_a_peak_load_is_a_handle_whose_rows_open_in_python(sample):
    peaks = sample["peaks"]
    assert peaks["complete"] is True
    assert peaks["rows"] == len(sample["rows"]) > 0
    assert isinstance(sample["rows"], pd.DataFrame)
    assert len(peaks["head"]) == 3


def test_a_whole_batch_s_peaks_stay_a_handle(server, sample):
    out = ok(server, "load_peaks", batches=[sample["batch"]["id"]])
    assert out["rows"] > sample["peaks"]["rows"]


def test_the_other_loaders(server, sample):
    sample_id = sample["ref"]["id"]
    series = ok(
        server, "load_timeseries", compound=[sample["formula"]], samples=[sample_id]
    )
    assert series["rows"] > 0
    spectrum = ok(
        server,
        "load_spectrum",
        sample=sample_id,
        mz_range=[sample["mz"] - 0.05, sample["mz"] + 0.05],
    )
    assert spectrum["rows"] > 0
    start, end = sample["describe"]["t_range_s"]
    stage = ok(server, "load_peaks_by_stage", sample=sample_id, stages=[[start, end]])
    assert stage["rows"] > 0


def test_the_oracle(server, sample):
    mechanisms = sample["describe"]["ionization_mechanisms"]
    proposed = ok(server, "query_by_mz", mz=sample["mz"], mechanisms=mechanisms)
    assert proposed["candidates"]
    formulas = [c["formula"] for c in proposed["candidates"][:5]]
    out = ok(
        server,
        "match_compounds",
        sample=sample["ref"]["id"],
        formulas=formulas,
        suggested=[sample["formula"]],
    )
    assert out["verdicts"]
    origins = {v["formula"]: v["origin"] for v in out["verdicts"]}
    assert set(origins.values()) <= {"mass_query", "model"}
    assert origins.get(formulas[0]) in (None, "mass_query")


def test_answer_and_session(server, sample):
    out = ok(server, "answer", text="a contract test's answer", code="the tools")
    assert out["recorded"] > 0
    session = ok(server, "session")
    assert sample["peaks"]["frame"] in [f["frame"] for f in session["frames"]]
