"""A re-processed file is bound under the current versions of its modes.

Its samples may hold versions an edit has since replaced. The file is rebuilt
under the setups as they stand, and keeps how each polarity was bound: the
provenance its samples recorded follows the setup to its current version.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from mascope_backend.api.controllers.sample.files.process import service


_SVC = "mascope_backend.api.controllers.sample.files.process.service"


def _mode(mode_id: str, polarity: str):
    return SimpleNamespace(
        ionization_mode_id=mode_id,
        ionization_mode_name=mode_id,
        ionization_mode_token=None,
        ionization_mode_polarity=polarity,
    )


@pytest.mark.asyncio
async def test_kept_modes_are_the_current_versions_with_their_samples_rung():
    sample_file = SimpleNamespace(
        sample_file_id="sf-kept", filename="ORBI_kept.raw", polarity="-+"
    )
    held = {"neg-v1": "token provenance", "pos-v1": "explicit provenance"}
    current = {"neg-v1": _mode("neg-v3", "-"), "pos-v1": _mode("pos-v1", "+")}

    with (
        patch(
            f"{_SVC}._acquisition_item_provenance",
            new_callable=AsyncMock,
            return_value=held,
        ),
        patch(
            f"{_SVC}.current_ionization_modes",
            new_callable=AsyncMock,
            return_value=current,
        ) as resolve,
    ):
        kept = await service._kept_modes(sample_file)

    resolve.assert_awaited_once_with(["neg-v1", "pos-v1"])
    assert kept.mode_ids == ["neg-v3", "pos-v1"]
    assert kept.provenance == {
        "neg-v3": "token provenance",
        "pos-v1": "explicit provenance",
    }


@pytest.mark.asyncio
async def test_a_setup_with_no_current_version_keeps_nothing():
    sample_file = SimpleNamespace(
        sample_file_id="sf-gone", filename="ORBI_gone.raw", polarity="-"
    )
    with (
        patch(
            f"{_SVC}._acquisition_item_provenance",
            new_callable=AsyncMock,
            return_value={"gone": "token provenance"},
        ),
        patch(
            f"{_SVC}.current_ionization_modes",
            new_callable=AsyncMock,
            side_effect=ValueError("No ionization mode has the id gone"),
        ),
    ):
        assert await service._kept_modes(sample_file) is None
