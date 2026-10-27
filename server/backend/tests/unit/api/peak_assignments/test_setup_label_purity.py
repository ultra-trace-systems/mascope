"""A run reads labelled ions at the purity its sample's setup carries.

The purity in effect (``custom_elements.labelled_purity``) is set around a
run and around a batch search from the sample's ionization setup, so every
prediction of a labelled envelope inside them - the reference lines Stage A
builds, the reagent pre-pass, the untargeted search, the tiering - reads it.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mascope_backend.api.controllers.target.lib.compute import target_ions_compute
from mascope_backend.api.new.ionization.modes.schema import DEFAULT_LABEL_PURITY
from mascope_backend.api.new.peak_assignments import batch_untargeted, service
from mascope_backend.db import IonizationMode
from mascope_tools.composition.custom_elements import (
    CUSTOM_ELEMENTS,
    label_purity,
    labelled_purity,
)


def test_a_setup_that_names_none_reads_the_library_default():
    assert DEFAULT_LABEL_PURITY == CUSTOM_ELEMENTS["^N"].default_purity
    # The model writes the same figure, without importing the library.
    column = IonizationMode.__table__.c.label_purity
    assert column.default.arg == DEFAULT_LABEL_PURITY
    assert float(column.server_default.arg.text) == DEFAULT_LABEL_PURITY


@pytest.mark.asyncio
async def test_a_run_reads_the_purity_of_its_samples_setup():
    seen = {}

    async def assign(*args, **kwargs):
        seen["purity"] = label_purity("^N")
        return {"status": "success"}

    with (
        patch.object(service, "move_to_current_versions", AsyncMock()) as move,
        patch.object(
            service, "sample_label_purity", AsyncMock(return_value=0.9)
        ) as purity,
        patch.object(service, "_assign_sample", assign),
    ):
        await service._run_sample_assignment("si-1")

    move.assert_awaited_once_with(sample_item_ids=["si-1"])
    purity.assert_awaited_once_with("si-1")
    assert seen["purity"] == 0.9
    assert label_purity("^N") == CUSTOM_ELEMENTS["^N"].default_purity


@pytest.mark.asyncio
async def test_a_batch_search_reads_each_samples_setup():
    seen = {}

    async def search(sample_item_id, target_peak_ids, config):
        seen[sample_item_id] = label_purity("^N")
        return [], 0

    with (
        patch.object(
            batch_untargeted,
            "sample_label_purity",
            AsyncMock(side_effect=lambda item: {"a": 0.9, "b": None}[item]),
        ),
        patch.object(batch_untargeted, "_search_sample_peaks", search),
    ):
        await batch_untargeted._search_sample("a", set(), MagicMock())
        await batch_untargeted._search_sample("b", set(), MagicMock())

    assert seen == {"a": 0.9, "b": CUSTOM_ELEMENTS["^N"].default_purity}


@pytest.mark.parametrize(
    ("profile_purity", "claimed"), [(0.98, 0.9), (None, None)], ids=["labelled", "not"]
)
def test_the_reagent_pass_reads_a_labelled_reagent_at_the_setups_purity(
    profile_purity, claimed
):
    resolved = SimpleNamespace(
        profile=SimpleNamespace(name="profile", label_purity=profile_purity),
        mz_precision_ppm=2.0,
    )
    with (
        patch.object(service, "claim_reagent_peaks") as claim,
        patch.object(service, "reagent_library_for"),
        labelled_purity(0.9),
    ):
        service._claim_reagent_lines(MagicMock(), resolved, resolution=None)
    assert claim.call_args.kwargs["purity"] == claimed


def test_reference_lines_read_a_labelled_element_at_the_purity_in_effect():
    def abundances():
        element = target_ions_compute._extract_custom_elements({"^N": 1})["^N"]
        return [isotope["abundance"] for isotope in element["isotopes"]]

    assert abundances() == pytest.approx([0.02, 0.98])
    with labelled_purity(0.9):
        assert abundances() == pytest.approx([0.1, 0.9])
