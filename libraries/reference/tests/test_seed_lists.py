"""The reference lists Mascope ships, and seeding them.

Every shipped list has to pass the format's checks - a licence the gate knows,
a citable reference, neutral formulas, radicals only where a list allows them.
The Stage A radical filter is step 2.5b's, so until it lands what keeps
radicals out of assignment is this: a default seed may put exactly the radicals
somebody chose within Stage A's reach, and that is pinned below by name.
"""

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select

from mascope_reference.known import (
    DEFAULT_ELEMENTS,
    DEFAULT_MAX_CARBON,
    DEFAULT_MAX_MASS,
    _within_bound,
)
from mascope_reference.normalize import canonical_formula, monoisotopic_mass
from mascope_reference.peaklist import admitted_species, is_odd_electron, list_problems
from mascope_reference.schema import reference_compound, reference_source
from mascope_reference.seed import catalogue, lists_directory, seed, select_lists
from mascope_reference.sources import available_sources, get_adapter


SHIPPED = catalogue()
BY_ID = {peak_list.id: peak_list for peak_list in SHIPPED}
#: Every licence tag the registry's sources carry - the gate's vocabulary.
LICENCE_TAGS = {get_adapter(name).license for name in available_sources()}
EXAMPLE_CSV = (
    Path(__file__).resolve().parents[1] / "examples" / "atmospheric_organics.csv"
)


def test_the_lists_ship_inside_the_package():
    assert SHIPPED, f"no lists in {lists_directory()}"


@pytest.mark.parametrize("peak_list", SHIPPED, ids=lambda peak_list: peak_list.id)
def test_every_shipped_list_passes_the_checks(peak_list):
    assert list_problems(peak_list, licenses=LICENCE_TAGS) == []


def test_list_ids_are_unique():
    assert len(BY_ID) == len(SHIPPED)


def _in_stage_a_window(formula: str) -> bool:
    canonical = canonical_formula(formula)
    return canonical is not None and _within_bound(
        canonical,
        monoisotopic_mass(canonical),
        elements=DEFAULT_ELEMENTS,
        max_carbon=DEFAULT_MAX_CARBON,
        max_mass=DEFAULT_MAX_MASS,
    )


def test_a_default_seed_puts_only_the_chosen_radicals_within_stage_a():
    # Stage A matches every active formula inside its window, radicals included,
    # and a curated row is exempt from the tiering's odd-electron rule. Until the
    # window's own radical filter lands, the lists decide: the monoterpene RO2
    # radicals are opt-in, and the one radical a default seed brings into reach
    # is the hydroperoxyl radical, a primary analyte of bromide CIMS.
    in_reach = {
        species.formula
        for peak_list in select_lists(SHIPPED)
        for species in admitted_species(peak_list)
        if is_odd_electron(species.formula) and _in_stage_a_window(species.formula)
    }
    assert in_reach == {"HO2"}


def test_the_radical_list_is_opt_in():
    ro2 = BY_ID["monoterpene-ro2-kang2021"]
    assert ro2.allow_radicals and not ro2.load_by_default
    assert all(is_odd_electron(species.formula) for species in ro2.species)
    assert ro2 not in select_lists(SHIPPED)
    assert ro2 in select_lists(SHIPPED, include_optional=True)


def test_the_hom_list_is_its_closed_shell_half():
    hom = BY_ID["monoterpene-hom-kang2021"]
    assert not hom.allow_radicals
    assert not any(is_odd_electron(species.formula) for species in hom.species)
    # Together the two halves are the thesis' 830 formulas, each in one of them.
    halves = {s.formula for s in hom.species} | {
        s.formula for s in BY_ID["monoterpene-ro2-kang2021"].species
    }
    assert (
        len(halves)
        == len(hom.species) + len(BY_ID["monoterpene-ro2-kang2021"].species)
        == 830
    )


def test_the_example_list_and_its_csv_agree():
    # The CSV stays as the worked example of the custom adapter; the list is
    # what the seed loads. They are one list, so they may not drift apart.
    with EXAMPLE_CSV.open(encoding="utf-8") as handle:
        rows = {(row["name"], row["formula"]) for row in csv.DictReader(handle)}
    listed = {
        (species.name, species.formula)
        for species in BY_ID["atmospheric-organics"].species
    }
    assert listed == rows


# --- Seeding ------------------------------------------------------------------


def _active(conn) -> dict[str, tuple[str, int]]:
    rows = conn.execute(
        select(
            reference_source.c.name,
            reference_source.c.version,
            reference_source.c.record_count,
        ).where(reference_source.c.is_active.is_(True))
    ).all()
    return {row.name: (row.version, row.record_count) for row in rows}


def _compounds(conn) -> int:
    return conn.execute(select(func.count()).select_from(reference_compound)).scalar()


def test_seeding_loads_each_default_list_once(sync_engine):
    defaults = select_lists(SHIPPED)
    outcomes = seed(sync_engine)
    assert [o.list_id for o in outcomes] == [pl.id for pl in defaults]
    assert all(o.loaded for o in outcomes)
    with sync_engine.connect() as conn:
        active = _active(conn)
        count = _compounds(conn)
    assert active == {
        pl.id: (pl.data_version, sum(1 for _ in admitted_species(pl)))
        for pl in defaults
    }

    # A second seed finds every version already active and loads nothing.
    again = seed(sync_engine)
    assert not any(o.loaded for o in again)
    with sync_engine.connect() as conn:
        assert _compounds(conn) == count


def test_an_opt_in_list_loads_when_named(sync_engine):
    (outcome,) = seed(sync_engine, names=["monoterpene-ro2-kang2021"])
    assert outcome.loaded
    assert outcome.ingested == len(BY_ID["monoterpene-ro2-kang2021"].species)


def test_an_unknown_name_loads_nothing(sync_engine):
    with pytest.raises(KeyError, match="no-such-list"):
        seed(sync_engine, names=["no-such-list"])
    with sync_engine.connect() as conn:
        assert _compounds(conn) == 0


def _list_file(directory: Path, version: str) -> None:
    directory.mkdir(exist_ok=True)
    data = {
        "schema_version": 2,
        "id": "test-list",
        "label": "A test list",
        "data_version": version,
        "license": "CC-BY-4.0",
        "references": [{"citation": "A paper.", "doi": "10.1234/t.1"}],
        "species": [{"formula": "C10H16O7"}, {"formula": "C10H15O8"}],
    }
    (directory / "test-list.json").write_text(json.dumps(data), encoding="utf-8")


def test_a_new_version_replaces_the_old_one(sync_engine, tmp_path):
    lists = tmp_path / "lists"
    _list_file(lists, "v1")
    (first,) = seed(sync_engine, directory=lists)
    # The radical is held back: the list does not allow radicals.
    assert (first.ingested, first.held_back) == (1, 1)

    _list_file(lists, "v2")
    (second,) = seed(sync_engine, directory=lists, prune=True)
    assert second.loaded and second.version == "v2"
    with sync_engine.connect() as conn:
        versions = conn.execute(
            select(reference_source.c.version, reference_source.c.is_active)
        ).all()
    # The replaced load is pruned; the new one is the only one left, active.
    assert [(v.version, v.is_active) for v in versions] == [("v2", True)]
