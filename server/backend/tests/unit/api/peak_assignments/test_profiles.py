"""Resolving a run onto an assignment profile.

The presets and the grid arithmetic are covered in ``libraries/tools``; what
these pin is the seam: which preset a run gets, what the run config can override,
what the run records about it, and - the property the whole step is judged
against - that the identity profile leaves a run exactly as it was before
profiles existed.
"""

import pytest
from pydantic import ValidationError

from mascope_backend.api.new.peak_assignments.config import (
    PeakAssignmentConfig,
    PeakAssignmentPresets,
)
from mascope_backend.api.new.peak_assignments.profiles import (
    SOURCE_CONFIG,
    SOURCE_PROFILE,
    SampleChemistry,
    preview_resolutions,
    resolve_profile,
)
from mascope_backend.api.new.peak_assignments.schemas import ProfilePreviewQueryParams
from mascope_tools.composition import profiles as presets


UREA = ["+H+", "+(CH4N2O)H+"]
BROMIDE = ["+Br-", "-H+"]


class TestResolution:
    def test_auto_reads_the_profile_off_the_mechanisms(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        )
        assert resolved.profile is presets.UR
        assert resolved.context is presets.URONIUM

    def test_auto_takes_the_profiles_default_context(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        )
        assert resolved.profile is presets.BR
        assert resolved.context is presets.AMBIENT_AIR

    def test_a_named_profile_overrides_the_fingerprint(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="NO3"),
            BROMIDE,
            instrument_type="orbi",
            polarity="-",
        )
        assert resolved.profile is presets.NO3

    def test_a_named_context_overrides_the_profiles_default(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(context="chamber"),
            BROMIDE,
            instrument_type="orbi",
            polarity="-",
        )
        assert resolved.profile is presets.BR
        assert resolved.context is presets.CHAMBER

    def test_an_alias_resolves(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="bromide", context="ambient"),
            [],
            instrument_type="orbi",
        )
        assert resolved.profile is presets.BR
        assert resolved.context is presets.AMBIENT_AIR

    def test_a_sample_with_nothing_diagnostic_falls_back_to_polarity(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), ["+H+"], instrument_type="orbi", polarity="+"
        )
        assert resolved.profile is presets.ESI_POS


class TestOverrides:
    def test_the_grid_comes_from_the_profile_when_the_run_names_none(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        )
        assert resolved.element_ranges == presets.resolve_element_ranges(
            presets.UR, presets.URONIUM
        )
        assert resolved.element_ranges_source == SOURCE_PROFILE

    def test_a_run_can_still_pin_its_own_grid(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(formula_ranges="C0-20 H0-40"),
            UREA,
            instrument_type="orbi",
            polarity="+",
        )
        assert resolved.element_ranges == "C0-20 H0-40"
        assert resolved.element_ranges_source == SOURCE_CONFIG

    def test_the_window_follows_the_instrument_class(self):
        orbi = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        )
        tof = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="tof", polarity="-"
        )
        assert orbi.mz_precision_ppm == 3.0
        assert tof.mz_precision_ppm == 10.0
        assert orbi.mz_precision_source == SOURCE_PROFILE

    def test_a_run_can_still_pin_its_own_window(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(mz_precision_ppm=7.5),
            BROMIDE,
            instrument_type="tof",
            polarity="-",
        )
        assert resolved.mz_precision_ppm == 7.5
        assert resolved.mz_precision_source == SOURCE_CONFIG


class TestTheIdentityProfile:
    def test_it_reproduces_the_pre_profile_search(self):
        # The plan's regression guard, stated where a run can reach it: naming
        # `none` must leave the grid, the window and the filter untouched.
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"),
            BROMIDE,
            instrument_type="orbi",
            polarity="-",
        )
        search = resolved.search_config(["+Br-"])
        assert search.element_count_ranges == "C0-100 H0-100 O0-100 N0-100"
        assert search.mass_range_ppm == 10.0
        assert resolved.heuristics_config().context_ratio_windows == {}

    def test_it_survives_a_diagnostic_mechanism_panel(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(profile="none"),
            UREA,
            instrument_type="orbi",
            polarity="+",
        )
        assert resolved.profile is presets.NO_PROFILE
        assert resolved.context is presets.NO_CONTEXT


class TestSearchConfiguration:
    def test_the_context_windows_reach_the_filter(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        )
        heuristics = resolved.heuristics_config()
        assert heuristics.use_senior is True
        assert heuristics.context_ratio_windows == presets.AMBIENT_AIR.ratio_windows()

    def test_the_grid_reaches_the_finder_in_explicit_isotope_form(self):
        resolved = resolve_profile(
            PeakAssignmentConfig(formula_ranges="C0-20 H0-40 ^N0-1"),
            [],
            instrument_type="orbi",
        )
        # What a person wrote stays on the run; what the finder parses is the
        # explicit form.
        assert resolved.element_ranges == "C0-20 H0-40 ^N0-1"
        assert resolved.search_config([]).element_count_ranges == (
            "C0-20 H0-40 [15N]0-1"
        )

    def test_the_notations_are_joined_for_the_finder(self):
        resolved = resolve_profile(PeakAssignmentConfig(), [], instrument_type="orbi")
        assert resolved.search_config(["+H+", "+Na+"]).ionizations == "+H+,+Na+"


class TestSnapshot:
    def test_it_records_the_answer_and_the_question(self):
        snapshot = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        ).snapshot()
        assert snapshot["profile"] == "UR"
        assert snapshot["requested_profile"] == "auto"
        assert snapshot["context"] == "uronium"
        assert snapshot["requested_context"] == "auto"

    def test_it_records_what_was_actually_searched(self):
        snapshot = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="tof", polarity="-"
        ).snapshot()
        assert snapshot["element_ranges"] == presets.resolve_element_ranges(
            presets.BR, presets.AMBIENT_AIR
        )
        assert snapshot["mz_precision_ppm"] == 10.0
        assert snapshot["ratio_windows"]["O/C"] == [0.0, 1.5]

    def test_it_records_the_ceiling_stage_a_matched_the_reference_under(self):
        shipped = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="tof", polarity="-"
        ).snapshot()
        assert shipped["known_window"] == presets.KNOWN_WINDOW_CEILING.to_json()
        identity = resolve_profile(
            PeakAssignmentConfig(profile="none"), BROMIDE, instrument_type="tof"
        ).snapshot()
        assert identity["context"] == "none"
        assert identity["known_window"] is None

    def test_it_is_json_serializable(self):
        import json

        snapshot = resolve_profile(
            PeakAssignmentConfig(), UREA, instrument_type="orbi", polarity="+"
        ).snapshot()
        assert json.loads(json.dumps(snapshot)) == snapshot


class TestConfigValidation:
    def test_an_unknown_profile_is_refused_by_the_model(self):
        # Refused on the request that carried it, not by a run that has already
        # been created and reported to the client.
        with pytest.raises(ValidationError):
            PeakAssignmentConfig(profile="krypton-cims")

    def test_an_unknown_context_is_refused_by_the_model(self):
        with pytest.raises(ValidationError):
            PeakAssignmentConfig(context="mars")

    def test_the_defaults_are_auto(self):
        config = PeakAssignmentConfig()
        assert config.profile == "auto"
        assert config.context == "auto"
        assert config.formula_ranges is None
        assert config.mz_precision_ppm is None

    def test_an_empty_name_reads_as_auto(self):
        assert PeakAssignmentConfig(profile="", context=" ").profile == "auto"

    def test_the_species_cap_still_applies_to_an_explicit_grid(self):
        with pytest.raises(ValidationError):
            PeakAssignmentConfig(
                formula_ranges=" ".join(f"C{i}-{i + 1}" for i in range(13))
            )


class TestPreview:
    """What a launcher is told before a run: one answer per distinct resolution."""

    def test_samples_that_resolve_alike_are_counted_together(self):
        previews = preview_resolutions(
            PeakAssignmentConfig(),
            [
                SampleChemistry(tuple(UREA), "+", samples=3),
                # Another mode carrying the same fingerprint is the same answer.
                SampleChemistry(("+(CH4N2O)H+",), "+", samples=2),
            ],
        )
        assert [(p["profile"], p["context"], p["samples"]) for p in previews] == [
            ("UR", "uronium", 5)
        ]

    def test_the_most_samples_come_first_and_ties_do_not_follow_the_input(self):
        chemistries = [
            SampleChemistry((), "-", samples=1),
            SampleChemistry(tuple(BROMIDE), "-", samples=4),
            SampleChemistry(tuple(UREA), "+", samples=1),
        ]
        forward = preview_resolutions(PeakAssignmentConfig(), chemistries)
        backward = preview_resolutions(PeakAssignmentConfig(), chemistries[::-1])
        assert [p["profile"] for p in forward] == ["BR", "ESI_NEG", "UR"]
        assert backward == forward

    def test_it_carries_the_names_a_run_would_record(self):
        (preview,) = preview_resolutions(
            PeakAssignmentConfig(),
            [SampleChemistry(tuple(BROMIDE), "-", instrument_type="orbi")],
        )
        snapshot = resolve_profile(
            PeakAssignmentConfig(), BROMIDE, instrument_type="orbi", polarity="-"
        ).snapshot()
        for key in (
            "profile",
            "profile_label",
            "requested_profile",
            "context",
            "context_label",
            "requested_context",
            "element_ranges",
            "mz_precision_ppm",
        ):
            assert preview[key] == snapshot[key], key

    def test_the_instrument_class_sets_the_window_and_parts_the_answer(self):
        previews = preview_resolutions(
            PeakAssignmentConfig(),
            [
                SampleChemistry(tuple(UREA), "+", samples=2, instrument_type="orbi"),
                SampleChemistry(tuple(UREA), "+", samples=1, instrument_type="tof"),
            ],
        )
        assert [
            (p["profile"], p["mz_precision_ppm"], p["samples"]) for p in previews
        ] == [
            ("UR", presets.INSTRUMENT_MZ_PRECISION_PPM["orbi"], 2),
            ("UR", presets.INSTRUMENT_MZ_PRECISION_PPM["tof"], 1),
        ]

    def test_a_class_it_cannot_read_gets_the_unknown_class_window(self):
        (preview,) = preview_resolutions(
            PeakAssignmentConfig(), [SampleChemistry(tuple(UREA), "+")]
        )
        assert preview["mz_precision_ppm"] == presets.DEFAULT_MZ_PRECISION_PPM

    def test_a_window_the_config_names_is_every_class_window(self):
        (preview,) = preview_resolutions(
            PeakAssignmentConfig(mz_precision_ppm=5.0),
            [
                SampleChemistry(tuple(UREA), "+", samples=2, instrument_type="orbi"),
                SampleChemistry(tuple(UREA), "+", samples=1, instrument_type="tof"),
            ],
        )
        assert (preview["mz_precision_ppm"], preview["samples"]) == (5.0, 3)

    def test_a_named_profile_is_one_answer_per_polarity(self):
        # The polarity is kept apart, since a launcher warns where a profile's
        # own polarity is not its samples'.
        previews = preview_resolutions(
            PeakAssignmentConfig(profile="BR"),
            [
                SampleChemistry(tuple(UREA), "+", samples=2),
                SampleChemistry(tuple(BROMIDE), "-", samples=2),
            ],
        )
        assert sorted((p["polarity"], p["profile_polarity"]) for p in previews) == [
            ("+", "-"),
            ("-", "-"),
        ]
        assert {p["profile"] for p in previews} == {"BR"}

    def test_no_samples_is_no_answer(self):
        assert preview_resolutions(PeakAssignmentConfig(), []) == []


class TestPresets:
    """The names a launcher offers are the names the run config accepts."""

    def test_every_library_preset_is_served(self):
        served = PeakAssignmentPresets()
        assert {p.name for p in served.profiles} == set(presets.REAGENT_PROFILES)
        assert {c.name for c in served.contexts} == set(presets.CHEMISTRY_CONTEXTS)

    def test_every_served_name_is_one_the_config_accepts(self):
        served = PeakAssignmentPresets()
        for profile in served.profiles:
            assert PeakAssignmentConfig(profile=profile.name).profile == profile.name
        for context in served.contexts:
            assert PeakAssignmentConfig(context=context.name).context == context.name

    def test_a_profile_says_its_polarity_and_the_context_auto_takes(self):
        served = {p.name: p for p in PeakAssignmentPresets().profiles}
        assert served["BR"].polarity == "-"
        assert served["BR"].default_context == "ambient-air"
        assert served["UR"].default_context == "uronium"
        for profile in served.values():
            assert profile.default_context in presets.CHEMISTRY_CONTEXTS

    def test_the_identity_entries_come_last(self):
        served = PeakAssignmentPresets()
        assert served.profiles[-1].name == presets.IDENTITY_PROFILE_NAME
        assert served.contexts[-1].name == presets.NO_CONTEXT.name

    def test_a_context_carries_its_description(self):
        served = {c.name: c for c in PeakAssignmentPresets().contexts}
        assert served["ambient-air"].description == presets.AMBIENT_AIR.description
        assert served["ambient-air"].polarity is None


class TestPreviewQuery:
    def test_it_refuses_what_the_run_config_refuses(self):
        with pytest.raises(ValidationError):
            ProfilePreviewQueryParams(profile="krypton-cims")
        with pytest.raises(ValidationError):
            ProfilePreviewQueryParams(context="mars")

    def test_blank_and_auto_read_as_auto(self):
        query = ProfilePreviewQueryParams(profile=" ", context="AUTO")
        assert (query.profile, query.context) == ("auto", "auto")

    def test_an_alias_is_accepted(self):
        assert ProfilePreviewQueryParams(profile="bromide").profile == "bromide"
