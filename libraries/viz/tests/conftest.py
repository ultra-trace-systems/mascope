"""
Fixtures for mascope_viz's hermetic tests.

``campaign`` is the synthetic month of :mod:`fixtures.campaign`, once per
chemistry; ``halide_campaign`` and ``positive_campaign`` name one. Each is
generated once per session: a month is two million peak-series rows, and
tests read it without changing it.
"""

import pytest
from fixtures.campaign import HALIDE_NEGATIVE, POSITIVE, Campaign, generate


@pytest.fixture(scope="session")
def halide_campaign() -> Campaign:
    return generate(HALIDE_NEGATIVE, seed=0)


@pytest.fixture(scope="session")
def positive_campaign() -> Campaign:
    return generate(POSITIVE, seed=0)


@pytest.fixture(scope="session", params=["halide_negative", "positive"])
def campaign(request) -> Campaign:
    return request.getfixturevalue(f"{request.param.split('_')[0]}_campaign")
