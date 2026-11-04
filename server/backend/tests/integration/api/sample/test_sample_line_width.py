"""
Integration tests: a sample says what width its lines have, run or not.

``GET /api/samples/{id}`` answers ``line_width``: the class the assignment
engine reads the file as, and the resolving power and full width at half
maximum detection fitted, at a reference m/z. A client comparing itself to a
sample reads them instead of measuring the raw profile, and a sample that has
no assignment run answers as well as one that has.

Run through the route and a real database, because what is pinned is the
whole way: the file's instrument functions row, its read by polarity, and the
record the route serves. No file is on disk, so the class comes from the
instrument's name and the width from the file's own functions.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy import delete, text

from mascope_backend.api.new.instrument_configs.lib import LINE_WIDTH_REFERENCE_MZ
from mascope_backend.db import (
    Dataset,
    InstrumentFunction,
    Sample,
    SampleBatch,
    SampleFile,
    SampleItem,
    Workspace,
    WorkspaceMember,
)
from mascope_backend.db.id import gen_id


_NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)

#: Per file: its instrument's name, the class its row recorded at ingest,
#: and the coefficients of its resolution function, None for a blank, whose
#: functions were never fitted. The rows of the first three record the class
#: opposite to their names, so that a pass shows the engine's rule decided.
_FILES = {
    # r_orbi: R = a / sqrt(m/z), 141 421 at m/z 200.
    "orbi": ("lworbi", "tof", [2e6]),
    # r_tof: R = m/z / (a m/z + b), 5000 at m/z 200.
    "tof": ("lwtof", "orbi", [1e-4, 0.02]),
    "blank": ("lwtof", "orbi", None),
    # A name that says neither class, and no data file to say it either:
    # only the row does.
    "unnamed": ("lwplain", "orbi", [2e6]),
}


@pytest_asyncio.fixture(scope="module", autouse=True)
async def line_width_sample_view(async_engine):
    """``sample_view``, which ``Base.metadata.create_all`` does not create."""
    async with async_engine.begin() as conn:
        await conn.execute(text(Sample.drop_view()))
        await conn.execute(text(Sample.create_view()))


@pytest_asyncio.fixture
async def samples(async_session_factory, test_users):
    """One sample per file of ``_FILES``, in a batch the editor may read.

    :return: ``{name: sample_item_id}``.
    """
    workspace, dataset, batch = gen_id(16), gen_id(16), gen_id(16)
    items, files, functions = {}, [], []
    async with async_session_factory() as session:
        session.add(
            Workspace(
                workspace_id=workspace,
                workspace_name=f"Line width {workspace}",
                workspace_status="active",
            )
        )
        await session.flush()
        session.add(
            WorkspaceMember(
                workspace_member_id=gen_id(16),
                workspace_id=workspace,
                user_id=test_users["editor"].id,
                workspace_role="editor",
            )
        )
        session.add(
            Dataset(
                dataset_id=dataset,
                workspace_id=workspace,
                dataset_name="Line width",
                dataset_type="ANALYSIS",
                dataset_utc_created=_NOW,
            )
        )
        session.add(
            SampleBatch(
                sample_batch_id=batch,
                dataset_id=dataset,
                sample_batch_name="Line width",
                sample_batch_utc_created=_NOW,
            )
        )
        for name, (instrument, recorded, coefficients) in _FILES.items():
            function_id = None
            if coefficients is not None:
                function_id = gen_id(32)
                functions.append(function_id)
                session.add(
                    InstrumentFunction(
                        instrument_function_id=function_id,
                        instrument=instrument,
                        method_file="",
                        datetime_utc=_NOW,
                        peakshape={"x": [0.0], "y": [1.0]},
                        resolution_function=coefficients,
                    )
                )
                await session.flush()
            file_id = gen_id(16)
            files.append(file_id)
            session.add(
                SampleFile(
                    sample_file_id=file_id,
                    instrument_function_id=function_id,
                    filename=f"{instrument}_2026.10.29_12h00m00s_{file_id}",
                    instrument=instrument,
                    instrument_type=recorded,
                    datetime=_NOW.replace(tzinfo=None),
                    datetime_utc=_NOW,
                    length=60.0,
                    range=[100.0, 500.0],
                    polarity="-",
                )
            )
            await session.flush()
            items[name] = gen_id(16)
            session.add(
                SampleItem(
                    sample_item_id=items[name],
                    sample_batch_id=batch,
                    sample_file_id=file_id,
                    sample_item_name=f"Line width {name}",
                    sample_item_type="SAMPLE" if coefficients else "BLANK",
                    sample_item_attributes={},
                    polarity="-",
                    tic=1000.0,
                    t0=0.0,
                    t1=60.0,
                    sample_item_utc_created=_NOW,
                )
            )
        await session.commit()
    yield items
    async with async_session_factory() as session:
        await session.execute(
            delete(Workspace).where(Workspace.workspace_id == workspace)
        )
        await session.execute(
            delete(SampleFile).where(SampleFile.sample_file_id.in_(files))
        )
        await session.execute(
            delete(InstrumentFunction).where(
                InstrumentFunction.instrument_function_id.in_(functions)
            )
        )
        await session.commit()


async def _line_width(client, sample_item_id: str) -> dict:
    response = await client.get(f"/api/samples/{sample_item_id}")
    assert response.status_code == 200, response.text
    return response.json()["data"]["line_width"]


@pytest.mark.asyncio
async def test_an_orbitrap_and_a_tof_answer_their_class_and_width(
    editor_client, samples
):
    """Neither sample has a run: the width is what detection fitted."""
    orbi = await _line_width(editor_client, samples["orbi"])
    tof = await _line_width(editor_client, samples["tof"])

    assert orbi["instrument_class"] == "orbi"
    assert orbi["reference_mz"] == LINE_WIDTH_REFERENCE_MZ == 200.0
    assert orbi["resolution"] == pytest.approx(2e6 / 200**0.5)
    assert orbi["fwhm_ppm"] == pytest.approx(1e6 / (2e6 / 200**0.5))

    assert tof["instrument_class"] == "tof"
    assert tof["reference_mz"] == 200.0
    assert tof["resolution"] == pytest.approx(5000.0)
    assert tof["fwhm_ppm"] == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_a_blank_answers_its_class_without_a_width(editor_client, samples):
    """No functions were fitted for it, so it has no width to say."""
    assert await _line_width(editor_client, samples["blank"]) == {
        "instrument_class": "tof",
        "reference_mz": 200.0,
        "resolution": None,
        "fwhm_ppm": None,
    }


@pytest.mark.asyncio
async def test_a_file_whose_name_says_no_class_answers_the_recorded_one(
    editor_client, samples
):
    """Nothing of the file says its class, so the one its row recorded does."""
    width = await _line_width(editor_client, samples["unnamed"])

    assert width["instrument_class"] == "orbi"
    assert width["resolution"] == pytest.approx(2e6 / 200**0.5)
