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
        assert R.present_notations(evidence) == ["+NH4+"]
        assert evidence[0].probe == probe.label
        assert evidence[0].mz_error_ppm == pytest.approx(7.0, abs=0.1)

    def test_a_channel_with_no_probes_can_never_switch_on(self):
        channel = R.SecondaryChannel(notation="+Xx+", label="Unprovable")
        mz, intensity = _spectrum((150.0, 1.0))
        evidence = R.detect_channels([channel], mz, intensity, ppm=5.0)
        assert evidence[0].present is False


class TestDetection:
    def test_the_ammonium_channel_switches_on_where_its_cluster_is(self):
        mz, intensity = _spectrum((UREA_AMMONIUM, 0.01))
        evidence = R.detect_channels(R.secondary_channels("UR"), mz, intensity, ppm=3.0)
        assert R.present_notations(evidence) == ["+NH4+"]
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
        assert R.present_notations(evidence) == ["+CO3-"]
        assert [item.notation for item in evidence] == ["+CO3-", "+Br2-"]

    def test_the_brightest_qualifying_probe_is_the_one_recorded(self):
        mz, intensity = _spectrum((CARBONATE, 0.01), (60.9931, 0.2))
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert evidence[0].probe == "[HCO3]-"
        assert evidence[0].relative_intensity == pytest.approx(0.2)

    def test_the_dibromide_rung_switches_its_cluster_channel_on(self):
        mz, intensity = _spectrum((DIBROMIDE, 0.05))
        evidence = R.detect_channels(R.secondary_channels("BR"), mz, intensity, ppm=5.0)
        assert R.present_notations(evidence) == ["+Br2-"]


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
            {"channel": "+CO3-", "present": False, "status": "not_found"},
            {"channel": "+Br2-", "present": False, "status": "not_found"},
        ]

    def test_a_present_channel_records_what_it_was_found_on(self):
        mz, intensity = _spectrum((UREA_SODIUM, 0.02))
        record = R.evidence_records(
            R.detect_channels(
                [
                    R.SecondaryChannel(
                        notation="+Na+",
                        label="Sodium",
                        probes=(R.ProbeIon("CH4N2ONa", 1, "[(CH4N2O)+Na]+"),),
                    )
                ],
                mz,
                intensity,
                ppm=5.0,
            )
        )[0]
        assert record["channel"] == "+Na+"
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
            notation="+Xx-",
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
        channel = R.SecondaryChannel(notation="+Xx+", label="Unprovable")
        mz, intensity = self._narrow_nitrate()
        evidence = R.detect_channels([channel], mz, intensity)
        assert evidence[0].present is False
        assert evidence[0].status == R.STATUS_NOT_FOUND
