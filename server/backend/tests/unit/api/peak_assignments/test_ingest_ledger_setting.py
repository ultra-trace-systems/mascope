"""Unit tests for the ingest-ledger setting's reading.

``peak_assignment_ingest_ledger()`` decides whether a newly processed sample
gets a per-sample run or only a place in the batch ledger. The default is the
batch ledger; only the one other value it knows reads as anything else.
"""

from types import SimpleNamespace
from unittest.mock import patch

import pytest

from mascope_backend.api.new.peak_assignments.config import (
    INGEST_LEDGER_BATCH,
    INGEST_LEDGER_SAMPLE,
    peak_assignment_ingest_ledger,
)


_CFG = "mascope_backend.api.new.peak_assignments.config"


def _with_meta(**meta):
    return patch(f"{_CFG}.runtime", SimpleNamespace(meta=SimpleNamespace(**meta)))


def test_the_default_is_the_batch_ledger():
    """A runtime whose meta config predates the setting reads as the default."""
    with _with_meta():
        assert peak_assignment_ingest_ledger() == INGEST_LEDGER_BATCH


def test_the_runtime_default_agrees():
    from mascope_runtime.config import MetaConfig

    assert MetaConfig().peak_assignment_ingest_ledger == INGEST_LEDGER_BATCH


@pytest.mark.parametrize("value", ["sample", " Sample ", "SAMPLE"])
def test_sample_reads_as_sample_whatever_its_casing(value):
    with _with_meta(peak_assignment_ingest_ledger=value):
        assert peak_assignment_ingest_ledger() == INGEST_LEDGER_SAMPLE


@pytest.mark.parametrize("value", ["batch", "BATCH", "", "nonsense", None])
def test_anything_else_reads_as_the_default(value):
    with _with_meta(peak_assignment_ingest_ledger=value):
        assert peak_assignment_ingest_ledger() == INGEST_LEDGER_BATCH


def test_the_runtime_model_admits_only_the_two_values():
    from pydantic import ValidationError

    from mascope_runtime.config import MetaConfig

    assert (
        MetaConfig(peak_assignment_ingest_ledger="sample").peak_assignment_ingest_ledger
        == "sample"
    )
    with pytest.raises(ValidationError):
        MetaConfig(peak_assignment_ingest_ledger="both")
