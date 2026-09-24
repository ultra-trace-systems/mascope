"""
Tests: learning which chemistry an acquisition method runs.

Nothing routes on a method binding yet, so what these pin is what the rows
say. The two that decide whether they will ever be safe to route on are
unanimity - a key seen with a second chemistry stops being a candidate - and
the constant method name, which must not become a binding that outranks the
filename token.
"""

import pytest
import pytest_asyncio
from sqlalchemy import delete, select

from mascope_backend.api.controllers.sample.files.process.bindings import (
    learn_method_bindings,
)
from mascope_backend.db import IonizationMode, MethodBinding
from mascope_backend.db.id import gen_id
from mascope_backend.method_keys import binding_digest, method_key, signature_class


ORBI_METHOD = r"C:\Xcalibur\methods\Nitrate_survey.meth"
ORBI_STREAM = "FTMS - p NSI Full ms [40.0000-600.0000] R=120000"


class _File:
    """The few fields of a sample file the learner reads."""

    def __init__(self, instrument, method_file=ORBI_METHOD, mz_range=None):
        self.instrument = instrument
        self.method_file = method_file
        self.filename = "a-file.raw"
        self.range = mz_range or [40.0, 600.0]


@pytest.fixture
def instrument():
    """An instrument name no other test shares.

    The binding key is a digest of the instrument, the method name and the
    signature class, all of which these tests otherwise hold constant - so
    without this every test would fold into one row and read the one before
    it as a second observation.
    """
    return f"instrument-{gen_id(8)}"


def _streams(key=ORBI_STREAM, polarity="-"):
    return [{"key": key, "signature": {"polarity": polarity, "ms_order": 1}}]


@pytest_asyncio.fixture
async def modes(async_session_factory):
    """Two modes of one chemistry and one of another, all negative.

    ``twin`` shares ``nitrate``'s mechanisms under a different name, which is
    what a deployment that has configured the same reagent twice looks like:
    the two must read as one chemistry.
    """
    made = {
        "nitrate": IonizationMode(
            ionization_mode_id=gen_id(),
            ionization_mode_name=f"Nitrate {gen_id(6)}",
            ionization_mode_polarity="-",
            ionization_mechanism_ids=["mech-no3", "mech-deprot"],
        ),
        "twin": IonizationMode(
            ionization_mode_id=gen_id(),
            ionization_mode_name=f"NO3 {gen_id(6)}",
            ionization_mode_polarity="-",
            # The same two, written the other way round: one chemistry.
            ionization_mechanism_ids=["mech-deprot", "mech-no3"],
        ),
        "bromide": IonizationMode(
            ionization_mode_id=gen_id(),
            ionization_mode_name=f"Bromide {gen_id(6)}",
            ionization_mode_polarity="-",
            ionization_mechanism_ids=["mech-br", "mech-deprot"],
        ),
    }
    async with async_session_factory() as session:
        session.add_all(list(made.values()))
        await session.commit()
    yield made
    async with async_session_factory() as session:
        ids = [mode.ionization_mode_id for mode in made.values()]
        await session.execute(
            delete(MethodBinding).where(MethodBinding.ionization_mode_id.in_(ids))
        )
        await session.execute(
            delete(IonizationMode).where(IonizationMode.ionization_mode_id.in_(ids))
        )
        await session.commit()


@pytest_asyncio.fixture
async def binding_of(async_session_factory):
    """Read back the binding a file and polarity would key on."""

    async def _read(sample_file, polarity="-", streams=None):
        digest = binding_digest(
            sample_file.instrument,
            method_key(sample_file.method_file),
            signature_class(streams, polarity, sample_file.range),
        )
        async with async_session_factory() as session:
            return (
                await session.execute(
                    select(MethodBinding).where(MethodBinding.binding_key == digest)
                )
            ).scalar_one_or_none()

    return _read


@pytest.mark.asyncio
async def test_a_routed_file_teaches_its_method(modes, binding_of, instrument):
    sample_file = _File(instrument)
    counts = await learn_method_bindings(
        sample_file, [modes["nitrate"]], source="token", streams=_streams()
    )

    assert counts["created"] == 1
    row = await binding_of(sample_file, streams=_streams())
    assert row is not None
    assert row.state == "learned"
    assert row.source == "token"
    assert row.ionization_mode_id == modes["nitrate"].ionization_mode_id
    assert row.method_key == "nitrate_survey.meth"
    assert row.signature_class == ORBI_STREAM
    assert row.n_streams == 1
    assert row.n_disagreements == 0
    assert len(row.chemistry_keys) == 1


@pytest.mark.asyncio
async def test_a_second_file_of_the_same_method_refreshes_one_row(
    modes, binding_of, instrument
):
    sample_file = _File(instrument)
    for _ in range(3):
        await learn_method_bindings(
            sample_file, [modes["nitrate"]], source="token", streams=_streams()
        )

    row = await binding_of(sample_file, streams=_streams())
    assert row.n_streams == 3
    assert row.state == "learned"
    assert row.n_disagreements == 0


@pytest.mark.asyncio
async def test_the_same_chemistry_under_another_name_is_not_a_disagreement(
    modes, binding_of, instrument
):
    sample_file = _File(instrument)
    await learn_method_bindings(
        sample_file, [modes["nitrate"]], source="token", streams=_streams()
    )
    counts = await learn_method_bindings(
        sample_file, [modes["twin"]], source="token", streams=_streams()
    )

    assert counts["refreshed"] == 1
    row = await binding_of(sample_file, streams=_streams())
    assert row.state == "learned"
    assert len(row.chemistry_keys) == 1
    # And the row does not wander between two modes that mean the same thing.
    assert row.ionization_mode_id == modes["nitrate"].ionization_mode_id


@pytest.mark.asyncio
async def test_a_second_chemistry_stops_the_key_routing(modes, binding_of, instrument):
    sample_file = _File(instrument)
    await learn_method_bindings(
        sample_file, [modes["nitrate"]], source="token", streams=_streams()
    )
    counts = await learn_method_bindings(
        sample_file, [modes["bromide"]], source="token", streams=_streams()
    )

    assert counts["ambiguous"] == 1
    row = await binding_of(sample_file, streams=_streams())
    assert row.state == "ambiguous"
    assert len(row.chemistry_keys) == 2
    assert row.n_disagreements == 1
    # Never repointed: the file that disagreed routed on a stronger rung and
    # was processed correctly, so there is nothing to correct here.
    assert row.ionization_mode_id == modes["nitrate"].ionization_mode_id


@pytest.mark.asyncio
async def test_an_ambiguous_key_does_not_come_back(modes, binding_of, instrument):
    sample_file = _File(instrument)
    for mode in ("nitrate", "bromide", "nitrate", "nitrate"):
        await learn_method_bindings(
            sample_file, [modes[mode]], source="token", streams=_streams()
        )

    row = await binding_of(sample_file, streams=_streams())
    assert row.state == "ambiguous"
    assert row.n_disagreements == 1


@pytest.mark.asyncio
async def test_a_constant_method_name_keys_on_the_signature_alone(
    modes, binding_of, instrument
):
    # Tofwerk's: the same name for every acquisition, whatever the reagent.
    # A binding under it would outrank the token the day the reagent changed.
    sample_file = _File(instrument, method_file="currentacquisition.ini")
    await learn_method_bindings(sample_file, [modes["nitrate"]], source="token")

    row = await binding_of(sample_file)
    assert row is not None
    assert row.method_key == ""
    assert row.signature_class == "- [40.0000-600.0000]"


@pytest.mark.asyncio
async def test_two_methods_of_one_instrument_key_apart(modes, binding_of, instrument):
    first = _File(instrument, method_file="nitrate.meth")
    second = _File(instrument, method_file="bromide.meth")
    await learn_method_bindings(
        first, [modes["nitrate"]], source="token", streams=_streams()
    )
    await learn_method_bindings(
        second, [modes["bromide"]], source="token", streams=_streams()
    )

    assert (await binding_of(first, streams=_streams())).state == "learned"
    assert (await binding_of(second, streams=_streams())).state == "learned"


@pytest.mark.asyncio
async def test_one_method_on_two_instruments_keys_apart(modes, binding_of, instrument):
    here = _File(f"{instrument}-A")
    there = _File(f"{instrument}-B")
    await learn_method_bindings(
        here, [modes["nitrate"]], source="token", streams=_streams()
    )
    await learn_method_bindings(
        there, [modes["bromide"]], source="token", streams=_streams()
    )

    # Two instruments running a method of the same name is not one history.
    assert (await binding_of(here, streams=_streams())).state == "learned"
    assert (await binding_of(there, streams=_streams())).state == "learned"


@pytest.mark.asyncio
async def test_a_dual_polarity_file_teaches_one_key_per_polarity(
    async_session_factory, modes, binding_of, instrument
):
    positive = IonizationMode(
        ionization_mode_id=gen_id(),
        ionization_mode_name=f"Protonation {gen_id(6)}",
        ionization_mode_polarity="+",
        ionization_mechanism_ids=["mech-h"],
    )
    async with async_session_factory() as session:
        session.add(positive)
        await session.commit()

    sample_file = _File(instrument)
    streams = _streams() + _streams(
        key="FTMS + p NSI Full ms [50.0000-750.0000]", polarity="+"
    )
    counts = await learn_method_bindings(
        sample_file, [modes["nitrate"], positive], source="token", streams=streams
    )

    assert counts["created"] == 2
    negative_row = await binding_of(sample_file, polarity="-", streams=streams)
    positive_row = await binding_of(sample_file, polarity="+", streams=streams)
    assert negative_row.signature_class == ORBI_STREAM
    assert positive_row.signature_class == "FTMS + p NSI Full ms [50.0000-750.0000]"

    async with async_session_factory() as session:
        await session.execute(
            delete(MethodBinding).where(
                MethodBinding.ionization_mode_id == positive.ionization_mode_id
            )
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == positive.ionization_mode_id
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_a_person_s_choice_teaches_the_same_key(modes, binding_of, instrument):
    sample_file = _File(instrument)
    await learn_method_bindings(
        sample_file, [modes["nitrate"]], source="explicit", streams=_streams()
    )

    row = await binding_of(sample_file, streams=_streams())
    assert row.source == "explicit"
    assert row.state == "learned"


@pytest.mark.asyncio
async def test_a_rung_below_the_binding_teaches_nothing(modes, binding_of, instrument):
    sample_file = _File(instrument)
    counts = await learn_method_bindings(
        sample_file, [modes["nitrate"]], source="detected", streams=_streams()
    )

    assert counts == {"created": 0, "refreshed": 0, "ambiguous": 0}
    assert await binding_of(sample_file, streams=_streams()) is None


@pytest.mark.asyncio
async def test_off_records_nothing(modes, binding_of, monkeypatch, instrument):
    monkeypatch.setattr(
        "mascope_backend.api.controllers.sample.files.process.bindings"
        ".method_binding_mode",
        lambda: "off",
    )
    sample_file = _File(instrument)
    counts = await learn_method_bindings(
        sample_file, [modes["nitrate"]], source="token", streams=_streams()
    )

    assert counts == {"created": 0, "refreshed": 0, "ambiguous": 0}
    assert await binding_of(sample_file, streams=_streams()) is None


@pytest.mark.asyncio
async def test_a_failure_never_costs_the_file_its_processing(
    modes, monkeypatch, instrument
):
    # Every ingest calls this. A broken recorder must not stop a file being
    # processed, so the caller is not asked to guard it.
    monkeypatch.setattr(
        "mascope_backend.api.controllers.sample.files.process.bindings.async_session",
        _boom,
    )
    counts = await learn_method_bindings(
        _File(instrument), [modes["nitrate"]], source="token", streams=_streams()
    )
    assert counts == {"created": 0, "refreshed": 0, "ambiguous": 0}


def _boom(*args, **kwargs):
    raise RuntimeError("the database is gone")


@pytest.mark.asyncio
@pytest.mark.parametrize("polarity", ["+", "-"])
async def test_a_file_with_no_census_keys_on_its_own_range(
    async_session_factory, polarity, binding_of, instrument
):
    mode = IonizationMode(
        ionization_mode_id=gen_id(),
        ionization_mode_name=f"Ambient {gen_id(6)}",
        ionization_mode_polarity=polarity,
        ionization_mechanism_ids=[polarity],
    )
    async with async_session_factory() as session:
        session.add(mode)
        await session.commit()

    sample_file = _File(instrument, method_file="a.meth", mz_range=[10.0, 500.0])
    await learn_method_bindings(sample_file, [mode], source="token")

    row = await binding_of(sample_file, polarity=polarity)
    assert row.signature_class == f"{polarity} [10.0000-500.0000]"

    async with async_session_factory() as session:
        await session.execute(
            delete(MethodBinding).where(
                MethodBinding.ionization_mode_id == mode.ionization_mode_id
            )
        )
        await session.execute(
            delete(IonizationMode).where(
                IonizationMode.ionization_mode_id == mode.ionization_mode_id
            )
        )
        await session.commit()
