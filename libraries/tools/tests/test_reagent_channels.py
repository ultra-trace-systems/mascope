"""The fingerprint that decides whether a secondary adduct channel is searched.

The rule this pins is the one the measurement produced: on the uronium gate set
the ammonium cluster is in every spectrum and the ammonium channel is real,
while no sodium cluster is there at all and the reference engine's 317 sodium
readings are noise. So a channel is switched on by its carrier's own cluster
ions being in *this* spectrum, above an intensity floor - never by the profile
listing it and never by the deployment declaring the mechanism.
"""

import numpy as np
import pytest

from mascope_tools.composition import reagents as R


def _spectrum(*peaks, base=1.0e6):
    """A spectrum with a base peak at m/z 100 and the given (m/z, relative)."""
    mz = [100.0] + [p[0] for p in peaks]
    intensity = [base] + [p[1] * base for p in peaks]
    return np.array(mz), np.array(intensity)


UREA_AMMONIUM = 138.0986  # [(CH4N2O)2+NH4]+
UREA_SODIUM = 83.0216  # [(CH4N2O)+Na]+
CARBONATE = 59.9853  # [CO3]-
DIBROMIDE = 157.8372  # [Br2]-
FLUORANTHENE_CATION = 202.0777  # [C16H10]+
FLUORANTHENE_13C = 203.0811  # [13C]C15H10+, 17% of the beam
FORMATE = 44.9982  # [HCOO]-
FORMATE_DIMER = 91.0037  # [HCOO+HCOOH]-
FORMATE_NITRIC = 107.9938  # [HCOO+HNO3]-
WATER = 18.0106
HYDRONIUM = 19.0178  # [H3O]+
BICARBONATE = 60.9931  # [HCO3]-


class TestTheProbes:
    def test_a_profile_without_secondary_channels_has_none(self):
        assert R.secondary_channels("none") == ()
        assert R.secondary_channels("ESI_NEG") == ()

    def test_every_probe_masses(self):
        for channels in R.SECONDARY_CHANNELS.values():
            for channel in channels:
                for probe in channel.probes:
                    assert probe.mz > 0

    def test_the_urea_ammonium_probes_include_the_monomer(self):
        # [urea+NH4]+ is the same ion as [NH3+(urea)H]+, ambient ammonia read
        # through its urea adduct - so a library that CLAIMS peaks must leave it
        # alone, and step 1.4's will. A probe claims nothing; it only asks
        # whether ammonium is clustering in the source, and on the sparse gate
        # set the monomer is the only cluster in the spectrum at all.
        labels = [
            probe.label
            for channel in R.secondary_channels("UR")
            for probe in channel.probes
        ]
        assert labels == [
            "[(CH4N2O)+NH4]+",
            "[(CH4N2O)2+NH4]+",
            "[(CH4N2O)3+NH4]+",
        ]

    def test_a_cluster_several_ppm_out_still_counts(self):
        # Measured on the dense uronium set: the reagent's own ladder sits +4.7
        # to +27.5 ppm out, because that acquisition's low-mass end is
        # calibrated against the analytes rather than against ions this bright.
        # A probe window as tight as the composition search would find none.
        probe = R.secondary_channels("UR")[0].probes[1]
        mz, intensity = _spectrum((probe.mz * (1 + 7e-6), 0.01))
        evidence = R.detect_channels(R.secondary_channels("UR"), mz, intensity)
        assert R.present_notations(evidence) == ["[M+NH4]+"]
        assert evidence[0].probe == probe.label
        assert evidence[0].mz_error_ppm == pytest.approx(7.0, abs=0.1)

    def test_the_charge_transfer_channels_need_a_partner_and_carbonate_does_not(self):
        for name in ("EASYIC_POS", "EASYIC_NEG"):
            for channel in R.secondary_channels(name):
                assert channel.needs_partner, channel.notation
                assert not channel.declared_stays_secondary, channel.notation
        for name in ("NO3", "NO3_15N", "BR", "IODIDE"):
            formate = [
                c for c in R.secondary_channels(name) if c.notation == "[M+HCOO]-"
            ][0]
            assert formate.needs_partner, name
            assert formate.declared_stays_secondary, name
        carbonate = [
            c for c in R.secondary_channels("NO3") if c.notation == "[M+CO3]-"
        ][0]
        assert not carbonate.needs_partner and carbonate.declared_stays_secondary

    def test_the_charge_transfer_probes_land_on_the_fluoranthene_beam(self):
        by_label = {
            probe.label: probe
            for channel in R.secondary_channels("EASYIC_POS")
            for probe in channel.probes
        }
        assert by_label["[C16H10]+"].mz == pytest.approx(FLUORANTHENE_CATION, abs=5e-4)
        assert by_label["[H3O]+"].mz == pytest.approx(HYDRONIUM, abs=5e-4)
        assert "[C16H10+H]+" not in by_label
        negative = {
            probe.label: probe
            for channel in R.secondary_channels("EASYIC_NEG")
            for probe in channel.probes
        }
        assert negative["[HCO3]-"].mz == pytest.approx(BICARBONATE, abs=5e-4)

    def test_the_formate_probes_follow_the_reagent_and_its_label(self):
        # The acid cluster is built from the profile's own reagent, so the
        # labelled profile probes it 0.997 Da above the unlabelled one - where
        # the chamber dataset shows it, read by the engine (lacking the
        # channel) as formic acid through the nitrate adduct: the same ion.
        light = {
            probe.label: probe
            for channel in R.secondary_channels("NO3")
            if channel.notation == "[M+HCOO]-"
            for probe in channel.probes
        }
        heavy = {
            probe.label: probe
            for channel in R.secondary_channels("NO3_15N")
            if channel.notation == "[M+HCOO]-"
            for probe in channel.probes
        }
        assert light["[HCOO]-"].mz == pytest.approx(FORMATE, abs=5e-4)
        assert light["[HCOO+HCOOH]-"].mz == pytest.approx(FORMATE_DIMER, abs=5e-4)
        assert light["[HCOO+HNO3]-"].mz == pytest.approx(FORMATE_NITRIC, abs=5e-4)
        assert light["[HCOO+HNO3+H2O]-"].mz == pytest.approx(
            FORMATE_NITRIC + WATER, abs=5e-4
        )
        assert heavy["[HCOO+H^NO3]-"].mz - light["[HCOO+HNO3]-"].mz == pytest.approx(
            0.997, abs=1e-3
        )
        assert heavy["[HCOO+H^NO3+H2O]-"].mz - light[
            "[HCOO+HNO3+H2O]-"
        ].mz == pytest.approx(0.997, abs=1e-3)
        # A halide profile keeps the evidence it can show: the bare ions only.
        bromide = [
            probe.label
            for channel in R.secondary_channels("BR")
            if channel.notation == "[M+HCOO]-"
            for probe in channel.probes
        ]
        assert bromide == ["[HCOO]-", "[HCOO+HCOOH]-"]

    def test_a_channel_with_no_probes_can_never_switch_on(self):
        channel = R.SecondaryChannel(notation="[M+Xx]+", label="Unprovable")
        mz, intensity = _spectrum((150.0, 1.0))
        evidence = R.detect_channels([channel], mz, intensity, ppm=5.0)
        assert evidence[0].present is False


class TestDetection:
    def test_the_ammonium_channel_switches_on_where_its_cluster_is(self):
        mz, intensity = _spectrum((UREA_AMMONIUM, 0.01))
        evidence = R.detect_channels(R.secondary_channels("UR"), mz, intensity, ppm=3.0)
        assert R.present_notations(evidence) == ["[M+NH4]+"]
        assert evidence[0].probe == "[(CH4N2O)2+NH4]+"

    def test_a_trace_below_the_floor_does_not_switch_a_channel_on(self):
        # The sodium counter-example, to scale: the one sodium trace on the
        # gate set sits at 0.008% of the base peak.
        mz, intensity = _spectrum((UREA_AMMONIUM, 8e-5))
        evidence = R.detect_channels(R.secondary_channels("UR"), mz, intensity, ppm=3.0)
        assert R.present_notations(evidence) == []

    def test_a_peak_outside_the_window_does_not_switch_a_channel_on(self):
        mz, intensity = _spectrum((UREA_AMMONIUM * (1 + 20e-6), 0.01))
        evidence = R.detect_channels(R.secondary_channels("UR"), mz, intensity, ppm=3.0)
        assert R.present_notations(evidence) == []

    def test_an_empty_spectrum_switches_nothing_on(self):
        evidence = R.detect_channels(
            R.secondary_channels("UR"), np.array([]), np.array([]), ppm=3.0
        )
        assert R.present_notations(evidence) == []

    def test_channels_are_decided_independently(self):
        mz, intensity = _spectrum((CARBONATE, 0.01))
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert R.present_notations(evidence) == ["[M+CO3]-"]
        assert [item.notation for item in evidence] == [
            "[M+CO3]-",
            "[M+Br2]-",
            "[M+HCOO]-",
        ]

    def test_the_brightest_qualifying_probe_is_the_one_recorded(self):
        mz, intensity = _spectrum((CARBONATE, 0.01), (60.9931, 0.2))
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert evidence[0].probe == "[HCO3]-"
        assert evidence[0].relative_intensity == pytest.approx(0.2)

    def test_the_dibromide_rung_switches_its_cluster_channel_on(self):
        mz, intensity = _spectrum((DIBROMIDE, 0.05))
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert R.present_notations(evidence) == ["[M+Br2]-"]

    def test_the_fluoranthene_ion_switches_hydride_abstraction_on(self):
        # The reagent cation is the hydride acceptor: where the beam is, the
        # abstraction channel is, and so is methyl loss, which is read off the
        # same beam. Proton transfer needs its own evidence. A window from 15
        # shows hydronium's absence, so only the beam's own channels come on.
        mz = np.array([15.0, FLUORANTHENE_CATION, 300.0])
        intensity = np.array([1.0e6, 3.0e5, 1.0e4])
        evidence = R.detect_channels(
            R.secondary_channels("EASYIC_POS"), mz, intensity, ppm=5.0
        )
        assert R.present_notations(evidence) == ["[M-H]+", "[M-CH3]+"]
        assert evidence[0].probe == "[C16H10]+"

    def test_methyl_loss_is_read_off_the_beam_as_hydride_abstraction_is(self):
        # Gated the way hydride abstraction is: the same fingerprint, on where
        # the window cannot show it, the mode's own where declared, and a
        # reading through it standing only on a partner.
        channels = {c.notation: c for c in R.secondary_channels("EASYIC_POS")}
        methyl, hydride = channels["[M-CH3]+"], channels["[M-H]+"]
        assert methyl.probes == hydride.probes
        assert methyl.when_unobservable == hydride.when_unobservable
        assert methyl.needs_partner and not methyl.declared_stays_secondary

    def test_the_beams_own_13c_line_does_not_switch_proton_transfer_on(self):
        # Protonated fluoranthene sits 22 ppm above the beam's 13C line, inside
        # the probe window, so a spectrum reading two ppm high would have
        # switched the channel on with no protonated reagent in it. The probe
        # is not there to be fooled: proton transfer is read off hydronium.
        high = 1 + 2.1e-6
        mz = np.array(
            [15.0, FLUORANTHENE_CATION * high, FLUORANTHENE_13C * high, 300.0]
        )
        intensity = np.array([1.0e4, 1.0e6, 1.7e5, 1.0e4])
        evidence = R.detect_channels(
            R.secondary_channels("EASYIC_POS"), mz, intensity, ppm=20.0
        )
        assert R.present_notations(evidence) == ["[M-H]+", "[M-CH3]+"]
        proton = [item for item in evidence if item.notation == "[M+H]+"][0]
        assert proton.status == R.STATUS_NOT_FOUND

    def test_a_narrow_window_leaves_the_charge_transfer_channels_on(self):
        # The chamber batches this profile was measured on were acquired at
        # m/z 42 to 160, below every probe. The source ran all the same, so
        # the window's silence is a fact about the window - the nitrate
        # carbonate ruling, applied to a source whose reagent sits at 202.
        mz, intensity = np.array([42.0, 160.0]), np.array([1.0e6, 1.0e4])
        evidence = R.detect_channels(
            R.secondary_channels("EASYIC_POS"), mz, intensity, ppm=5.0
        )
        assert R.present_notations(evidence) == ["[M-H]+", "[M+H]+", "[M-CH3]+"]
        assert {item.status for item in evidence} == {R.STATUS_UNOBSERVABLE}

    def test_a_dry_source_still_shows_its_hydronium(self):
        # A window starting below the first hydrate: the dry source shows
        # hydronium and its first hydrate and nothing larger, so those two are
        # the probes and a larger hydrate's absence is not asked about.
        mz, intensity = (
            np.array([30.0, 37.0284, 160.0]),
            np.array([1.0e6, 2.0e3, 1.0e4]),
        )
        evidence = R.detect_channels(
            R.secondary_channels("EASYIC_POS"), mz, intensity, ppm=5.0
        )
        proton = [item for item in evidence if item.notation == "[M+H]+"][0]
        assert proton.present is True
        assert proton.probe == "[H3O+H2O]+"

    def test_a_wide_window_without_the_beam_switches_them_off(self):
        # Every probe inside the acquisition and none matched: the source is
        # not running charge transfer, whatever the mode is called.
        mz, intensity = np.array([15.0, 450.0]), np.array([1.0e6, 1.0e4])
        evidence = R.detect_channels(
            R.secondary_channels("EASYIC_POS"), mz, intensity, ppm=5.0
        )
        assert R.present_notations(evidence) == []

    def test_formate_switches_its_channel_on_where_the_window_shows_it(self):
        mz, intensity = _spectrum((FORMATE, 0.2), (300.0, 0.01))
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert R.present_notations(evidence) == ["[M+HCOO]-"]
        assert evidence[-1].probe == "[HCOO]-"

    def test_a_nitrate_window_starting_above_formate_leaves_the_channel_on(self):
        # The batch that carries the C11 pseudo-acids is acquired from m/z 130,
        # above every formate carrier the source makes; the sister batch
        # acquired from 42 shows the carrier at up to 40% of the base peak. So
        # the window's silence is a fact about the window, as it is for
        # carbonate on the same source.
        # Built by hand: the helper's base peak at m/z 100 would put the
        # reagent-acid probes inside the window.
        mz, intensity = np.array([150.0, 600.0]), np.array([1.0e6, 1.0e4])
        evidence = R.detect_channels(
            R.secondary_channels("NO3_15N"), mz, intensity, ppm=5.0
        )
        assert R.present_notations(evidence) == ["[M+CO3]-", "[M+HCOO]-"]
        formate = [item for item in evidence if item.notation == "[M+HCOO]-"][0]
        assert formate.status == R.STATUS_UNOBSERVABLE

    def test_a_nitrate_window_that_reaches_the_hydrate_answers_for_itself(self):
        # The hydrate of the reagent-acid cluster is the highest formate
        # carrier the source makes: a window starting between the cluster and
        # the hydrate can show it, so its silence is evidence here.
        mz, intensity = np.array([115.0, 150.0, 600.0]), np.array([1.0e5, 1.0e6, 1.0e4])
        evidence = R.detect_channels(
            R.secondary_channels("NO3"), mz, intensity, ppm=5.0
        )
        formate = [item for item in evidence if item.notation == "[M+HCOO]-"][0]
        assert formate.status == R.STATUS_NOT_FOUND
        assert "[M+HCOO]-" not in R.present_notations(evidence)
        # ...and where the hydrate is there, the channel is on.
        mz, intensity = (
            np.array([115.0, FORMATE_NITRIC + WATER, 150.0, 600.0]),
            np.array([1.0e5, 2.0e4, 1.0e6, 1.0e4]),
        )
        evidence = R.detect_channels(
            R.secondary_channels("NO3"), mz, intensity, ppm=5.0
        )
        formate = [item for item in evidence if item.notation == "[M+HCOO]-"][0]
        assert formate.status == R.STATUS_FOUND
        assert formate.probe == "[HCOO+HNO3+H2O]-"

    def test_a_halide_window_starting_above_formate_leaves_the_channel_off(self):
        # Silence is not evidence, but the halide profiles claim only what
        # they can show.
        mz, intensity = np.array([150.0, 600.0]), np.array([1.0e6, 1.0e4])
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert "[M+HCOO]-" not in R.present_notations(evidence)

    def test_a_wide_nitrate_window_without_formate_switches_it_off(self):
        mz, intensity = _spectrum((42.0, 0.01), (600.0, 0.01))
        evidence = R.detect_channels(
            R.secondary_channels("NO3"), mz, intensity, ppm=5.0
        )
        formate = [item for item in evidence if item.notation == "[M+HCOO]-"][0]
        assert formate.present is False
        assert formate.status == R.STATUS_NOT_FOUND

    def test_the_source_acids_switch_deprotonation_on(self):
        mz, intensity = _spectrum((BICARBONATE, 0.02), (300.0, 0.01))
        evidence = R.detect_channels(
            R.secondary_channels("EASYIC_NEG"), mz, intensity, ppm=5.0
        )
        assert R.present_notations(evidence) == ["[M-H]-"]
        assert evidence[0].probe == "[HCO3]-"


class TestTheRecord:
    def test_an_absent_channel_is_recorded_as_considered(self):
        # A run has to say a channel was looked for and not found, or nothing
        # distinguishes that from a channel nobody thought of.
        # Wide enough that every bromide probe could have been seen, so a
        # verdict of "not found" is about the source rather than the window.
        mz, intensity = _spectrum((50.0, 0.01), (400.0, 0.01))
        records = R.evidence_records(
            R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        )
        assert records == [
            {"channel": "[M+CO3]-", "present": False, "status": "not_found"},
            {"channel": "[M+Br2]-", "present": False, "status": "not_found"},
            {"channel": "[M+HCOO]-", "present": False, "status": "not_found"},
        ]

    def test_a_present_channel_records_what_it_was_found_on(self):
        mz, intensity = _spectrum((UREA_SODIUM, 0.02))
        record = R.evidence_records(
            R.detect_channels(
                [
                    R.SecondaryChannel(
                        notation="[M+Na]+",
                        label="Sodium",
                        probes=(R.ProbeIon("CH4N2ONa", 1, "[(CH4N2O)+Na]+"),),
                    )
                ],
                mz,
                intensity,
                ppm=5.0,
            )
        )[0]
        assert record["channel"] == "[M+Na]+"
        assert record["present"] is True
        assert record["probe"] == "[(CH4N2O)+Na]+"
        assert record["relative_intensity"] == pytest.approx(0.02, rel=1e-2)

    def test_the_record_is_json_serializable(self):
        import json

        mz, intensity = _spectrum((UREA_AMMONIUM, 0.01))
        records = R.evidence_records(
            R.detect_channels(R.secondary_channels("UR"), mz, intensity, ppm=3.0)
        )
        assert json.loads(json.dumps(records)) == records


class TestTheReagentAcidClusters:
    """Carbonate's evidence has to be reachable from a window that starts above
    it, and a labelled reagent's label has to follow into the cluster."""

    def _probe_labels(self, profile):
        return [
            probe.label
            for channel in R.secondary_channels(profile)
            for probe in channel.probes
        ]

    def test_the_clusters_are_built_from_the_profiles_reagent(self):
        assert "[CO3+HNO3]-" in self._probe_labels("NO3")

    def test_bromide_keeps_the_bare_ions_only(self):
        # The acid cluster is measured for nitrate and not for bromide, where
        # the candidate line is an order of magnitude weaker and reading it as
        # a carrier cost the gate set 24 same-formula agreements.
        assert "[CO3+HBr]-" not in self._probe_labels("BR")
        assert "[CO3]-" in self._probe_labels("BR")

    def test_the_label_follows_into_the_cluster(self):
        # One table, not two: the labelled profile's clusters come out 0.997 Da
        # above the unlabelled ones because they are built from '^NO3'.
        def cluster(profile, label):
            for channel in R.secondary_channels(profile):
                for probe in channel.probes:
                    if probe.label == label:
                        return probe.mz
            raise AssertionError(f"{label} not among {profile}'s probes")

        light = cluster("NO3", "[CO3+HNO3]-")
        heavy = cluster("NO3_15N", "[CO3+H^NO3]-")
        assert heavy - light == pytest.approx(0.99703, abs=1e-4)
        assert heavy == pytest.approx(123.978, abs=1e-3)

    def test_they_reach_a_window_the_bare_ion_cannot(self):
        # Measured on a broad-window run of the nitrate chemistry: [CO3]- sits
        # at 0.5-1.0% of base and the labelled acid clusters at 0.1-0.44%. An
        # acquisition starting at 120 sees only the clusters.
        mz, intensity = _spectrum((123.978, 0.003), base=1.0e6)
        mz, intensity = mz[1:], intensity[1:]  # drop the m/z 100 base peak
        mz = np.append(mz, [400.0])
        intensity = np.append(intensity, [1.0e6])
        evidence = R.detect_channels(R.secondary_channels("NO3_15N"), mz, intensity)
        assert evidence[0].present is True
        assert evidence[0].status == R.STATUS_FOUND
        assert evidence[0].probe == "[CO3+H^NO3]-"


class TestUnobservableIsNotAbsent:
    """A channel no probe of which is inside the acquisition was never asked,
    and its silence is a fact about the window, not about the source."""

    def _narrow_nitrate(self):
        # The gate's narrow nitrate acquisition: from m/z 131, above every
        # carbonate carrier the source makes.
        return np.array([131.0, 200.0, 300.0]), np.array([1.0e6, 1.0e4, 1.0e3])

    def test_it_is_recorded_as_its_own_status(self):
        mz, intensity = self._narrow_nitrate()
        record = R.evidence_records(
            R.detect_channels(R.secondary_channels("NO3_15N"), mz, intensity)
        )[0]
        assert record["status"] == R.STATUS_UNOBSERVABLE
        assert "probe" not in record

    def test_a_profile_may_default_it_on(self):
        # Nitrate does: the source makes no carbonate carrier above m/z 126, so
        # a window starting higher can never show the channel.
        mz, intensity = self._narrow_nitrate()
        evidence = R.detect_channels(R.secondary_channels("NO3_15N"), mz, intensity)
        assert evidence[0].present is True

    def test_the_default_is_off(self):
        # Silence is not evidence unless a profile has a reason to say so.
        channel = R.SecondaryChannel(
            notation="[M+Xx]-",
            label="Unmeasured",
            probes=(R.ProbeIon("CO3", -1, "[CO3]-"),),
        )
        mz, intensity = self._narrow_nitrate()
        evidence = R.detect_channels([channel], mz, intensity)
        assert evidence[0].present is False
        assert evidence[0].status == R.STATUS_UNOBSERVABLE

    def test_an_observable_channel_that_is_absent_stays_absent(self):
        # The policy applies to the window, never to the source: a broad
        # acquisition with no carbonate in it switches nothing on.
        mz = np.array([50.0, 200.0, 400.0])
        intensity = np.array([1.0e6, 1.0e4, 1.0e3])
        evidence = R.detect_channels(R.secondary_channels("NO3_15N"), mz, intensity)
        assert evidence[0].present is False
        assert evidence[0].status == R.STATUS_NOT_FOUND

    def test_a_channel_with_no_probes_is_not_called_unobservable(self):
        channel = R.SecondaryChannel(notation="[M+Xx]+", label="Unprovable")
        mz, intensity = self._narrow_nitrate()
        evidence = R.detect_channels([channel], mz, intensity)
        assert evidence[0].present is False
        assert evidence[0].status == R.STATUS_NOT_FOUND
