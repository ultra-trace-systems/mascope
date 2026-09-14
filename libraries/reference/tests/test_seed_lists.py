"""The reference lists Mascope ships, and seeding them.

Every shipped list has to pass the format's checks - a licence the gate knows,
a citable reference, neutral formulas, radicals only where a list allows them.
Stage A matches a radical only from a source whose row allows radicals, and a
list's row allows them only where the list does, so the radicals a default seed
lets through are pinned below by name. So is the ceiling every shipped context
sets: every formula a default seed loads falls inside it.
"""

import csv
import json
from pathlib import Path

import pytest
from sqlalchemy import func, select, update

from mascope_reference.adapters.peaklist import list_scope
from mascope_reference.normalize import canonical_formula, monoisotopic_mass
from mascope_reference.peaklist import admitted_species, is_odd_electron, list_problems
from mascope_reference.schema import reference_compound, reference_source
from mascope_reference.scope import MIRROR_WINDOW, UNBOUNDED, SourceScope
from mascope_reference.seed import catalogue, lists_directory, seed, select_lists
from mascope_reference.sources import available_sources, get_adapter
from mascope_tools.composition.profiles import KNOWN_WINDOW_CEILING


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


def test_a_default_seed_lets_through_only_the_radicals_its_lists_allow():
    # A curated row is exempt from the tiering's odd-electron rule, so the radical
    # filter is what decides: the monoterpene RO2 radicals are opt-in, and a
    # default seed matches the hydroperoxyl radical, a primary analyte of bromide
    # CIMS, and iodine dioxide, which its list names as a radical.
    matched = {
        species.formula
        for peak_list in select_lists(SHIPPED)
        if list_scope(peak_list).allow_radicals
        for species in admitted_species(peak_list)
        if is_odd_electron(species.formula)
    }
    assert matched == {"HO2", "IO2"}


@pytest.mark.parametrize("peak_list", SHIPPED, ids=lambda peak_list: peak_list.id)
def test_every_formula_a_shipped_list_holds_is_inside_the_context_ceiling(peak_list):
    # A list loads unbounded, so the ceiling is its only bound in a shipped
    # context: a formula outside it would be carried by the list and never matched.
    outside = [
        species.formula
        for species in peak_list.species
        if not KNOWN_WINDOW_CEILING.admits(
            canonical_formula(species.formula),
            monoisotopic_mass(canonical_formula(species.formula)),
        )
    ]
    # The nylon 6,6 cyclic tetramer is over both caps (C48, 905 Da); the list
    # keeps it for the identity context, which sets no ceiling.
    expected = ["C48H88N8O8"] if peak_list.id == "contaminants-keller2008" else []
    assert outside == expected


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


# --- The scope a seeded list's row records -------------------------------------------


def _scopes(conn, *, active_only: bool = True) -> dict[str, SourceScope]:
    query = select(
        reference_source.c.name,
        reference_source.c.version,
        reference_source.c.known_window,
        reference_source.c.allow_radicals,
        reference_source.c.polarity,
    )
    if active_only:
        query = query.where(reference_source.c.is_active.is_(True))
    return {
        (
            row.name if active_only else f"{row.name}@{row.version}"
        ): SourceScope.from_row(row)
        for row in conn.execute(query)
    }


def test_seeding_writes_each_lists_scope_on_its_row(sync_engine):
    seed(sync_engine)
    with sync_engine.connect() as conn:
        scopes = _scopes(conn)
    assert scopes == {pl.id: list_scope(pl) for pl in select_lists(SHIPPED)}
    # Every shipped list is its own bound, and says the rest in its header.
    assert all(scope.known_window == UNBOUNDED for scope in scopes.values())
    assert scopes["atmospheric-inorganics"].allow_radicals
    assert not scopes["monoterpene-hom-kang2021"].allow_radicals
    assert scopes["cyclic-siloxanes"].polarity == "positive"
    assert scopes["perfluorocarboxylic-acids"].polarity == "negative"
    # Keller's contaminants are seen in both polarities, which a row records as none.
    assert scopes["contaminants-keller2008"].polarity is None
    # So are the monoterpene HOMs: the thesis measured them in negative mode, and
    # ammonium and urea CIMS detect them as adducts in positive mode.
    assert scopes["monoterpene-hom-kang2021"].polarity is None
    # Their RO2 radicals stay negative, and so do the acids and iodine species.
    assert BY_ID["monoterpene-ro2-kang2021"].polarity == "negative"


def test_an_active_lists_row_is_brought_up_to_date_without_a_reload(sync_engine):
    seed(sync_engine)
    # What a deployment's rows hold after the migration: the mirror window, no
    # radicals and both polarities, whatever the list says.
    backfill = SourceScope(MIRROR_WINDOW)
    with sync_engine.begin() as conn:
        conn.execute(update(reference_source).values(**backfill.row_values()))
        compounds = _compounds(conn)

    outcomes = seed(sync_engine)

    assert not any(o.loaded for o in outcomes)
    assert all(o.refreshed for o in outcomes)
    with sync_engine.connect() as conn:
        assert _scopes(conn) == {pl.id: list_scope(pl) for pl in select_lists(SHIPPED)}
        assert _compounds(conn) == compounds

    # Once the rows say what the lists say, a seed has nothing to refresh.
    assert not any(o.refreshed for o in seed(sync_engine))


def test_the_refresh_leaves_an_earlier_inactive_load_alone(sync_engine, tmp_path):
    lists = tmp_path / "lists"
    _list_file(lists, "v1")
    seed(sync_engine, directory=lists)
    _list_file(lists, "v2")
    seed(sync_engine, directory=lists)
    stale = SourceScope(MIRROR_WINDOW)
    with sync_engine.begin() as conn:
        conn.execute(update(reference_source).values(**stale.row_values()))

    (outcome,) = seed(sync_engine, directory=lists)

    assert outcome.refreshed and not outcome.loaded
    with sync_engine.connect() as conn:
        scopes = _scopes(conn, active_only=False)
    # The active load reads as the list; the replaced one is not what Stage A
    # reads, and keeps what it had until someone activates it.
    assert scopes == {"test-list@v2": SourceScope(UNBOUNDED), "test-list@v1": stale}
