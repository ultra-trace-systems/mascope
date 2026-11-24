"""The CHECKs an identification claim keeps, on both verification tables.

A claim is a confirmed verdict that names a structure
(``docs/dev/identification_claims.md`` section 5.2): a level 1 to 3 names a
structure but for a class claim (3b), the letter beside a level is the scale's
for its number, only a confirmation names a structure, the name is an
InChIKey's shape and a level is 1 to 5. A batch-peak claim's window has both
ends in order and scopes only a level 1. The request bodies refuse the same
rows first; these hold for a writer that goes round them. Each CHECK is
exercised alone on each table, so dropping any one of them turns its case red.
"""

from datetime import datetime, timezone

import pytest
import pytest_asyncio
from sqlalchemy.exc import IntegrityError
from test_utils import gen_test_id

from mascope_backend.db import (
    AssignmentVerification,
    BatchPeakVerification,
    SampleFile,
    SampleItem,
)


GLUCOSE = "WQZGKKKJIJFFOK-GASJEMHNSA-N"


@pytest_asyncio.fixture
async def db_test_sample_item(session, db_test_sample_batch):
    sample_file = SampleFile(
        sample_file_id=gen_test_id(),
        filename=f"claim-test-{gen_test_id()}.zarr",
        instrument="orbi-test",
        datetime=datetime(2026, 7, 4, 12, 0, 0),
        datetime_utc=datetime(2026, 7, 4, 12, 0, 0, tzinfo=timezone.utc),
        length=60.0,
        range=[50.0, 500.0],
        polarity="+",
    )
    session.add(sample_file)
    await session.flush()
    sample_item = SampleItem(
        sample_item_id=gen_test_id(),
        sample_batch_id=db_test_sample_batch.sample_batch_id,
        sample_file_id=sample_file.sample_file_id,
        sample_item_name="Claim Test Sample",
        sample_item_type="sample",
        sample_item_utc_created=datetime.now(timezone.utc),
    )
    session.add(sample_item)
    await session.flush()
    yield sample_item


@pytest.fixture(params=["sample", "batch"])
def verdict_row(request, db_test_sample_item, db_test_sample_batch):
    """A verdict row of either table, from the fields a case sets."""

    def make(**fields):
        if request.param == "sample":
            return AssignmentVerification(
                assignment_verification_id=gen_test_id(32),
                sample_item_id=db_test_sample_item.sample_item_id,
                sample_peak_id=gen_test_id(),
                assigned_formula="C6H12O6",
                **fields,
            )
        return BatchPeakVerification(
            batch_peak_verification_id=gen_test_id(32),
            sample_batch_id=db_test_sample_batch.sample_batch_id,
            batch_peak_id=gen_test_id(),
            assigned_formula="C6H12O6",
            **fields,
        )

    return make


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "fields",
    [
        {"verdict": "confirmed", "evidence_level": "reference_standard", "level": 1},
        {"verdict": "confirmed", "evidence_level": "pattern", "level": 4},
        {"verdict": "confirmed", "evidence_level": "visual"},
        {"verdict": "rejected"},
        {
            "verdict": "confirmed",
            "evidence_level": "msms",
            "level": 3,
            "level_letter": "b",
            "claimed_name": "a carboxylic acid",
        },
        {
            "verdict": "confirmed",
            "evidence_level": "pattern",
            "level": 4,
            "level_letter": "a",
        },
        {"verdict": "confirmed", "evidence_level": "msms", "level": 2},
    ],
    ids=[
        "level 1 claim",
        "pattern, no structure",
        "visual",
        "rejection",
        "a class claim names no structure",
        "4a",
        "a version-1 level 2, no letter",
    ],
)
async def test_a_verdict_within_the_rules_is_stored(session, verdict_row, fields):
    if fields.get("level") in (1, 2):
        fields = {**fields, "claimed_inchikey": GLUCOSE, "claimed_name": "D-glucose"}
    session.add(verdict_row(**fields))
    await session.flush()


@pytest.mark.asyncio
async def test_a_confirmation_recorded_before_claims_keeps_its_evidence(
    session, verdict_row
):
    """A reference-standard confirmation naming no structure, as every one
    recorded before the columns was: the database keeps it at level 4. Only
    the request layer refuses a new one."""
    session.add(
        verdict_row(verdict="confirmed", evidence_level="reference_standard", level=4)
    )
    await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "check"),
    [
        (
            {"verdict": "confirmed", "evidence_level": "msms", "level": 2},
            "structure_level_names_structure",
        ),
        ({"verdict": "unsure", "claimed_inchikey": GLUCOSE}, "claim_is_confirmed"),
        (
            {
                "verdict": "confirmed",
                "evidence_level": "pattern",
                "claimed_inchikey": "D-glucose",
            },
            "claimed_inchikey_shape",
        ),
        (
            {"verdict": "confirmed", "evidence_level": "pattern", "level": 6},
            "level_valid",
        ),
        (
            {
                "verdict": "confirmed",
                "evidence_level": "msms",
                "level": 2,
                "level_letter": "a",
            },
            "structure_level_names_structure",
        ),
        (
            {
                "verdict": "confirmed",
                "evidence_level": "orthogonal",
                "level": 3,
                "level_letter": "c",
                "claimed_name": "pinonic acid",
            },
            "structure_level_names_structure",
        ),
        (
            {
                "verdict": "confirmed",
                "evidence_level": "msms",
                "level": 2,
                "level_letter": "c",
                "claimed_inchikey": GLUCOSE,
            },
            "level_letter_valid",
        ),
        (
            {
                "verdict": "confirmed",
                "evidence_level": "reference_standard",
                "level": 1,
                "level_letter": "a",
                "claimed_inchikey": GLUCOSE,
            },
            "level_letter_valid",
        ),
        (
            {
                "verdict": "confirmed",
                "evidence_level": "pattern",
                "level": 4,
                "level_letter": "c",
            },
            "level_letter_valid",
        ),
    ],
    ids=[
        "a structure level names no structure",
        "an unsure verdict names a structure",
        "an InChIKey of the wrong shape",
        "a level past 5",
        "a 2a names no structure",
        "a 3c naming a candidate names no structure",
        "a letter level 2 has not",
        "a letter at level 1",
        "a letter level 4 has not",
    ],
)
async def test_a_claim_breaking_a_rule_is_refused(session, verdict_row, fields, check):
    session.add(verdict_row(**fields))
    with pytest.raises(IntegrityError, match=check):
        await session.flush()


# --- A batch-peak claim's window -------------------------------------------------------

_T0 = datetime(2026, 11, 1, 8, tzinfo=timezone.utc)
_T1 = datetime(2026, 11, 8, 8, tzinfo=timezone.utc)


def _batch_claim(db_test_sample_batch, **fields):
    return BatchPeakVerification(
        batch_peak_verification_id=gen_test_id(32),
        sample_batch_id=db_test_sample_batch.sample_batch_id,
        batch_peak_id=gen_test_id(),
        assigned_formula="C6H12O6",
        verdict="confirmed",
        evidence_level="reference_standard",
        claimed_inchikey=GLUCOSE,
        **fields,
    )


@pytest.mark.asyncio
async def test_a_level_one_batch_claim_keeps_its_window(session, db_test_sample_batch):
    session.add(
        _batch_claim(
            db_test_sample_batch, level=1, window_start_utc=_T0, window_end_utc=_T1
        )
    )
    # A version-1 level 1 had no window, and keeps none.
    session.add(_batch_claim(db_test_sample_batch, level=1))
    await session.flush()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("fields", "check"),
    [
        ({"level": 1, "window_start_utc": _T0}, "window_complete"),
        ({"level": 1, "window_end_utc": _T1}, "window_complete"),
        (
            {"level": 1, "window_start_utc": _T1, "window_end_utc": _T0},
            "window_ordered",
        ),
        (
            {
                "level": 2,
                "level_letter": "b",
                "window_start_utc": _T0,
                "window_end_utc": _T1,
            },
            "window_scopes_level_one",
        ),
    ],
    ids=["no end", "no start", "out of order", "on a 2b"],
)
async def test_a_window_breaking_a_rule_is_refused(
    session, db_test_sample_batch, fields, check
):
    session.add(_batch_claim(db_test_sample_batch, **fields))
    with pytest.raises(IntegrityError, match=check):
        await session.flush()
