"""The CHECKs an identification claim keeps, on both verification tables.

A claim is a confirmed verdict that names a structure
(``docs/dev/identification_claims.md`` section 5.2): a level 1 to 3 names a
structure, only a confirmation names one, the name is an InChIKey's shape and a
level is 1 to 5. The request bodies refuse the same rows first; these hold for
a writer that goes round them. Each CHECK is exercised alone on each table, so
dropping any one of them turns its case red.
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
    ],
    ids=["level 1 claim", "pattern, no structure", "visual", "rejection"],
)
async def test_a_verdict_within_the_rules_is_stored(session, verdict_row, fields):
    if fields.get("level") == 1:
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
    ],
    ids=[
        "a structure level names no structure",
        "an unsure verdict names a structure",
        "an InChIKey of the wrong shape",
        "a level past 5",
    ],
)
async def test_a_claim_breaking_a_rule_is_refused(session, verdict_row, fields, check):
    session.add(verdict_row(**fields))
    with pytest.raises(IntegrityError, match=check):
        await session.flush()
