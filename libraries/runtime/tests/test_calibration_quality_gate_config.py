"""
Tests for `backend.calibration_quality_gate`.

The default matters most: a deployment that says nothing must keep matching
samples whose calibration misses the quality bar, so an upgrade does not
silently stop matching on a site whose calibration collections were never
checked against the bar. Keeping them out is an operator's explicit choice.
"""

import pytest
from pydantic import ValidationError

from mascope_runtime.config import BackendConfig


def _backend(**kwargs) -> BackendConfig:
    return BackendConfig(name="backend", **kwargs)


def test_warns_by_default():
    assert _backend().calibration_quality_gate == "warn"


def test_enforce_can_be_chosen():
    assert _backend(calibration_quality_gate="enforce").calibration_quality_gate == (
        "enforce"
    )


def test_an_unknown_mode_is_refused():
    # A typo must stop the backend rather than fall back to either behaviour.
    with pytest.raises(ValidationError):
        _backend(calibration_quality_gate="enforced")
