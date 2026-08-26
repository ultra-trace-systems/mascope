"""
The reference-licence gate: what Stage A is allowed to match, and saying so.

The reference mirror carries a per-record licence from ingest through to
results precisely so a deployment can decline to match against sources whose
terms it has not accepted. Until now nothing wired that to a setting - the
production call site passed nothing, so every active source was matched
(issue #1727).

Three properties are pinned here, in descending order of how much damage
getting them wrong would do:

1. **Unset means no gating.** A deployment that configures nothing must behave
   exactly as it did before this existed. Narrowing the gate shrinks what
   assignment can find with nothing in the UI to say why a peak went
   unidentified, so it can only ever be an explicit choice.
2. **Narrowing invalidates the cached expansion.** The reference isotope frame
   is cached across runs; a run under a narrower gate must not be served the
   wider frame an earlier run left behind.
3. **The run records what it was allowed to match**, because otherwise nothing
   does - and a result gated to half the mirror is not comparable with one that
   matched everything.

No database: the session, the fingerprint and the IsoSpec expansion are faked,
the same way ``test_reference_isotopes.py`` fakes them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, PropertyMock, patch

import pandas as pd
import pytest

import mascope_backend.api.new.peak_assignments.service as service
from mascope_backend.api.new.peak_assignments.config import PeakAssignmentConfig
from mascope_backend.api.new.peak_assignments.service import (
    REFERENCE_LICENSES_KEY,
    _fetch_reference_known_isotopes,
    _stored_run_config,
    reference_license_gate,
)
from mascope_reference import KnownComposition, KnownIdentity


_MOD = "mascope_backend.api.new.peak_assignments.service"


def _mechanism(mech_id="m-deprot", notation="-H-", polarity="-"):
    return SimpleNamespace(
        ionization_mechanism_id=mech_id,
        ionization_mechanism=notation,
        ionization_mechanism_polarity=polarity,
    )


def _orbi_sample():
    return SimpleNamespace(sample_item_name="Sample One", filename="orbi-sample.raw")


def _session_ctx():
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=AsyncMock())
    ctx.__aexit__ = AsyncMock(return_value=False)
    return ctx


def _known():
    return [
        KnownComposition(
            formula="C10H16O3",
            monoisotopic_mass=184.1099,
            identities=[
                KnownIdentity(
                    name="Pinonic acid",
                    source="pubchem",
                    license="public-domain",
                    inchikey=None,
                    source_native_id="1",
                    xrefs={},
                )
            ],
        )
    ]


class TestGateResolution:
    """`reference_license_gate` reads the deployment's backend config."""

    def test_unset_is_the_default(self):
        """The single most important property of the whole change: the shipped
        config gates nothing, so an upgrade changes no deployment's results."""
        assert service.runtime.full_config.backend.reference_licenses is None
        assert reference_license_gate() is None

    def test_reads_the_configured_allowlist(self, monkeypatch):
        monkeypatch.setattr(
            service.runtime.full_config.backend,
            "reference_licenses",
            ["CC0", "public-domain"],
        )
        assert reference_license_gate() == ["CC0", "public-domain"]

    def test_a_config_without_the_field_is_ungated(self):
        """Defensive: `runtime.config` is whichever module the process is, and
        an older env toml validates into a model that never saw the field."""
        with patch.object(
            type(service.runtime),
            "config",
            new_callable=PropertyMock,
            return_value=SimpleNamespace(),
        ):
            assert reference_license_gate() is None


class TestGateAtTheCallSite:
    """What reaches `iter_known_compositions`, and what the cache keys on."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self):
        service._reference_isotope_cache.clear()
        yield
        service._reference_isotope_cache.clear()
        patch.stopall()

    def _patches(self, gates):
        """Fake the DB edges; `gates` is the gate returned on each call.

        The reference state is held constant across calls on purpose - the
        cache tests below must isolate the gate as the only thing that moved.
        """
        patch(f"{_MOD}.async_session", side_effect=lambda: _session_ctx()).start()
        patch(
            f"{_MOD}.known_state_fingerprint",
            new_callable=AsyncMock,
            return_value=(("src-1", "t1"),),
        ).start()
        patch(f"{_MOD}.reference_license_gate", side_effect=gates).start()
        known = patch(
            f"{_MOD}.iter_known_compositions",
            new_callable=AsyncMock,
            return_value=_known(),
        ).start()
        build = patch(
            f"{_MOD}._build_reference_isotopes_df",
            return_value=pd.DataFrame({"mz": [183.1026], "relative_abundance": [1.0]}),
        ).start()
        return known, build

    @pytest.mark.asyncio
    async def test_no_gate_passes_no_licences(self):
        """`licenses=None` is what keeps every record, so an unconfigured
        deployment must reach the query with exactly that."""
        known, _ = self._patches([None])

        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])

        assert known.await_args.kwargs["licenses"] is None

    @pytest.mark.asyncio
    async def test_a_gate_is_passed_as_a_licence_set(self):
        known, _ = self._patches([["CC0", "public-domain"]])

        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])

        assert known.await_args.kwargs["licenses"] == {"CC0", "public-domain"}

    @pytest.mark.asyncio
    async def test_narrowing_the_gate_rebuilds_rather_than_reusing(self):
        """Same reference state, narrower gate: serving the cached frame would
        hand the run compounds the deployment just said it may not match."""
        known, build = self._patches([None, ["public-domain"]])

        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])
        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])

        assert build.call_count == 2
        assert known.await_count == 2

    @pytest.mark.asyncio
    async def test_an_unchanged_gate_still_hits_the_cache(self):
        """The gate belongs in the key, but it must not defeat the key."""
        known, build = self._patches([["public-domain"]] * 2)

        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])
        await _fetch_reference_known_isotopes(_orbi_sample(), 0.0, [_mechanism()])

        build.assert_called_once()
        known.assert_awaited_once()


class TestRunRecordsTheGate:
    """A result should say what it was allowed to match."""

    def test_ungated_runs_record_null_rather_than_nothing(self):
        """Present-and-null distinguishes "everything was allowed" from a run
        written before this was recorded at all."""
        with patch(f"{_MOD}.reference_license_gate", return_value=None):
            stored = _stored_run_config(PeakAssignmentConfig())
        assert REFERENCE_LICENSES_KEY in stored
        assert stored[REFERENCE_LICENSES_KEY] is None

    def test_gated_runs_record_the_effective_set(self):
        with patch(
            f"{_MOD}.reference_license_gate", return_value=["CC0", "public-domain"]
        ):
            stored = _stored_run_config(PeakAssignmentConfig())
        assert stored[REFERENCE_LICENSES_KEY] == ["CC0", "public-domain"]

    def test_the_requested_config_is_carried_through_untouched(self):
        config = PeakAssignmentConfig(max_untargeted_peaks=42, run_untargeted=False)
        with patch(f"{_MOD}.reference_license_gate", return_value=None):
            stored = _stored_run_config(config)
        assert stored["max_untargeted_peaks"] == 42
        assert stored["run_untargeted"] is False
        assert stored["identified_threshold"] == config.identified_threshold

    def test_a_client_cannot_set_the_gate_through_the_run_config(self):
        """`reference_licenses` is deliberately not a field on the request
        model: a caller that could widen it could match against the very
        sources the deployment declined."""
        assert REFERENCE_LICENSES_KEY not in PeakAssignmentConfig.model_fields
        smuggled = PeakAssignmentConfig(**{REFERENCE_LICENSES_KEY: ["anything"]})
        with patch(f"{_MOD}.reference_license_gate", return_value=["CC0"]):
            stored = _stored_run_config(smuggled)
        assert stored[REFERENCE_LICENSES_KEY] == ["CC0"]
