"""The identification level a run states of each committed row (step 5.8).

Each formula sub-level from the facts that earn it, each rival test, what is
not read as an exclusion, the isotopologue and hand rows, and the mapping's
version pinned.
"""

from __future__ import annotations

import pytest

from mascope_backend.api.new.peak_assignments.cross_channel import (
    REASON_AMBIGUOUS_ADDUCT,
    REASON_AMBIGUOUS_NITROGEN,
    SAME_ION_SETTLED,
    SETTLED_BY_SECOND_CHANNEL,
    SETTLED_BY_TARGET_LIBRARY,
)
from mascope_backend.api.new.peak_assignments.engine import (
    GRID_RIVALS,
    MEASURED_SHORTLIST,
    RIVALS_PAST_CAP,
)
from mascope_backend.api.new.peak_assignments.identification_level import (
    ENGINE_LEVELS,
    HELD_BY_PLAUSIBILITY,
    HELD_BY_PRIOR,
    IDENTIFICATION_LEVEL,
    LEVEL_COLUMN,
    LEVEL_RULES_VERSION,
    LEVELS,
    OPEN,
    TEST_ISOTOPES,
    TEST_MASS,
    TEST_SECOND_CHANNEL,
    LevelContext,
    _heavy_elements,
    apply_levels,
    engine_stated,
    hand_level,
    inherited_level,
    level_fields,
    level_lines,
    level_of,
    members_level,
    reagent_unit,
    shortlist,
)
from mascope_backend.api.new.peak_assignments.mass_gate import (
    TRACKING_TRACKS,
    TRACKING_UNTRACKED,
    SpectrumLines,
)
from mascope_backend.api.new.peak_assignments.rival_set import RIVAL_SET
from mascope_backend.api.new.peak_assignments.tiering import (
    REASON_ABSENT_LINE,
    REASON_NO_CLOSE_RIVAL,
    REASON_UNRESOLVED_PEAK,
    SEPARATED_THROUGH,
    LineGate,
)
from mascope_tools.composition import profiles as presets
from mascope_tools.composition.heuristic_filter import (
    PLAUSIBILITY_FLOOR,
    formula_plausibility,
)
from mascope_tools.composition.utils import formula_identity


#: The channel every row here is read through unless it says otherwise.
PROTON = "[M+H]+"
MECHANISMS = {"im-h": PROTON, "im-nh4": "[M+NH4]+", "im-15n": "[M+[15N]O3]-"}


def m0(
    row_id: str = "pa-1",
    formula: str = "C6H12O6",
    *,
    source: str = "database",
    ion: str | None = "C6H13O6+",
    fit: float = 0.9,
    alternatives: list | None = None,
    provenance: dict | None = None,
    mechanism: str = "im-h",
    peak: str = "p-1",
    intensity: float = 1000.0,
) -> dict:
    """One committed monoisotopic row, with the provenance the level reads."""
    blob = {"cross_channel": {"channels": [PROTON]}, "plausibility": 1.0}
    if source == "database":
        # The formula search measured the peak and found no rival its
        # evidence could not separate.
        blob[GRID_RIVALS] = {"fit_score": fit, "added": 0, "rivals": []}
    blob.update(provenance or {})
    return {
        "peak_assignment_id": row_id,
        "assigned_formula": formula,
        "ion_formula": ion,
        "role": "M0",
        "tier": "assigned",
        "source": source,
        "fit_score": fit,
        "ionization_mechanism_id": mechanism,
        "sample_peak_id": peak,
        "sample_peak_intensity": intensity,
        "mz_error_ppm": 0.0,
        "owner_peak_assignment_id": None,
        "alternatives": alternatives,
        "provenance": blob,
    }


def rival(formula: str, fit: float | None, ion: str | None = None, **kwargs) -> dict:
    """One entry of a row's alternatives."""
    entry = {"assigned_formula": formula, "ion_formula": ion, **kwargs}
    if fit is not None:
        entry["fit_score"] = fit
    return entry


def iso_line(owner: str, label: str, *, tracking: str | None = TRACKING_TRACKS) -> dict:
    """A committed isotopologue row of ``owner``, as the mass gate read it."""
    return {
        "peak_assignment_id": f"{owner}-{label}",
        "assigned_formula": "C6H12O6",
        "role": "iso_child",
        "owner_peak_assignment_id": owner,
        "isotope_label": label,
        "provenance": {"mass_gate": {"tracking": tracking}} if tracking else {},
    }


def ctx(**kwargs) -> LevelContext:
    return LevelContext(
        neutral_channels=kwargs.pop("neutral_channels", {}),
        notation_by_id=kwargs.pop("notation_by_id", MECHANISMS),
        **kwargs,
    )


def measured(*rows: dict) -> list[dict]:
    """The rows as a run leaves them: each with its rival set
    (``rival_set.RIVAL_SET``), the rivals the run measured on its peak at its
    fit's model. Here those a test declares: the stored alternatives that
    carry a fit, the grid's named rivals and the 4.15 shortlist's placed
    formulas, each at its stated fit. A row that states its set keeps it."""
    for row in rows:
        provenance = row.setdefault("provenance", {})
        if row.get("role") != "M0" or RIVAL_SET in provenance:
            continue
        rivals = [
            {
                "formula": entry["assigned_formula"],
                "ion_formula": entry.get("ion_formula"),
                "fit_score": entry["fit_score"],
                "mz_error_ppm": entry.get("mz_error_ppm"),
                "monoisotopic": entry.get("isotope_label") in (None, "", "M0"),
            }
            for entry in row.get("alternatives") or []
            if isinstance(entry, dict)
            and entry.get("fit_score") is not None
            and not entry.get("same_ion")
        ]
        grid = provenance.get(GRID_RIVALS) or {}
        rivals += [
            {**entry, "monoisotopic": True}
            for entry in grid.get("rivals") or []
            if entry.get("fit_score") is not None
        ]
        rivals += [
            {**entry, "monoisotopic": True}
            for entry in provenance.get(MEASURED_SHORTLIST) or []
            if entry.get("placed") is not False
        ]
        provenance[RIVAL_SET] = {
            "fit_score": row.get("fit_score"),
            "rivals": rivals,
            "rivals_unlisted": 0,
        }
    return list(rows)


def level(row: dict, own=(), **kwargs) -> dict:
    """The row's level, with ``own`` its committed isotopologue rows."""
    measured(row)
    return level_of(row, list(own), ctx(**kwargs))


def tests_of(record: dict) -> dict[str, str]:
    return {entry["formula"]: entry["test"] for entry in record["rivals"]}


class TestTheScale:
    def test_the_rules_version_is_pinned(self):
        # A fact, a rule or the order of the rules that changes bumps this, and
        # a test that pins it is where the bump is noticed. The run records it.
        assert LEVEL_RULES_VERSION == 2
        assert apply_levels(measured(*[m0()]))["level_rules_version"] == 2
        assert "level_mapping_version" not in level(m0())

    def test_the_vocabulary_is_the_shared_scale(self):
        assert LEVELS == (
            "1",
            "2a",
            "2b",
            "3a",
            "3b",
            "3c",
            "3d",
            "4a",
            "4b",
            "5a",
            "5b",
        )

    def test_no_engine_states_level_one(self):
        """Level 1 is a person's claim in review (step 5.8f): the levels an
        engine may state leave it out, and a record that carried it - an
        owner's level an isotopologue copies - is refused, not written."""
        assert ENGINE_LEVELS == LEVELS[1:]
        assert engine_stated({"level": "4a"}) == {"level": "4a"}
        with pytest.raises(ValueError, match="no engine path states it"):
            engine_stated({"level": "1"})
        with pytest.raises(ValueError, match="no engine path states it"):
            inherited_level({"level": "1", "evidence": "", "lift": None}, "pa-1")

    def test_a_rule_that_stated_level_one_would_fail_the_pass(self, monkeypatch):
        """Through the pass and the row reading: a rule change that reached
        level 1 is refused where the record is written, not stored."""
        from mascope_backend.api.new.peak_assignments import identification_level

        monkeypatch.setattr(
            identification_level,
            "_level",
            lambda *args, **kwargs: {"level": "1", "evidence": "", "lift": None},
        )
        with pytest.raises(ValueError, match="no engine path states it"):
            apply_levels([m0()])
        with pytest.raises(ValueError, match="no engine path states it"):
            level(m0())


class TestFiveB:
    def test_a_line_the_spectrum_lacks_refutes_the_reading(self):
        row = m0(
            provenance={
                "tier_reasons": [
                    {"rule": REASON_ABSENT_LINE, "detail": "its 81Br line is absent"}
                ]
            }
        )

        record = level(row)

        assert record["level"] == "5b"
        assert record["evidence"] == (
            "refuted by its isotopes: its 81Br line is absent"
        )
        assert "another reading" in record["lift"]
        assert record["facts"][0]["fact"] == REASON_ABSENT_LINE

    def test_an_unresolved_peak_is_untestable(self):
        row = m0(
            provenance={
                "tier_reasons": [
                    {"rule": REASON_UNRESOLVED_PEAK, "detail": "not separated"}
                ]
            }
        )

        record = level(row)

        assert record["level"] == "5b"
        assert record["evidence"] == "untestable: not separated"
        assert "separated peak" in record["lift"]

    def test_a_formula_under_the_plausibility_floor_is_implausible(self):
        assert formula_plausibility("C2H10") == 0.0
        row = m0(formula="C2H10", ion="C2H11+")
        row["provenance"].pop("plausibility")

        record = level(row)

        assert record["level"] == "5b"
        assert record["evidence"].startswith("implausible: C2H10")

    def test_the_recorded_plausibility_is_read(self):
        row = m0(provenance={"plausibility": 0.0})

        assert level(row)["level"] == "5b"

    def test_a_formula_at_the_floor_is_possible(self):
        # The floor is where a possible but unusual formula is floored
        # (``formula_plausibility``); only a graph no molecule has is under it.
        row = m0(provenance={"plausibility": PLAUSIBILITY_FLOOR})

        assert level(row)["level"] == "4a"
        assert "implausible" not in level(row)["evidence"]

    def test_a_refuted_row_still_lists_its_rivals(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.89)],
            provenance={
                "tier_reasons": [{"rule": REASON_ABSENT_LINE, "detail": "absent"}]
            },
        )

        assert tests_of(level(row)) == {"C7H16O5": OPEN}

    def test_a_reading_that_earns_5b_and_5a_shows_5b_and_names_its_rivals(self):
        # The order (the confidence note's point 4): 5b before 5a, the other
        # letter named in the evidence line.
        row = m0(
            alternatives=[rival("C7H16O5", 0.89)],
            provenance={
                "tier_reasons": [{"rule": REASON_ABSENT_LINE, "detail": "absent"}]
            },
        )

        record = level(row)

        assert record["level"] == "5b"
        assert record["evidence"] == (
            "refuted by its isotopes: absent; it earns 5a too: one rival left "
            "open: C7H16O5"
        )

    def test_a_refuted_row_whose_rivals_were_not_measured_says_so(self):
        row = m0(
            source="untargeted",
            provenance={
                "tier_reasons": [{"rule": REASON_UNRESOLVED_PEAK, "detail": "x"}]
            },
        )

        assert level(row)["evidence"] == (
            "untestable: x; it earns 5a too: rivals not measured"
        )


class TestFiveA:
    def test_an_untargeted_row_without_a_measured_shortlist(self):
        row = m0(source="untargeted", alternatives=[rival("C7H16O5", None)])

        record = level(row)

        assert record["level"] == "5a"
        assert record["evidence"] == "rivals not measured"
        assert record["lift"] == "measure the shortlist"
        assert record["rivals"] == []

    def test_a_list_hit_the_formula_search_never_measured(self):
        # Competed against the formulas a list named and no others.
        row = m0(alternatives=[rival("C7H16O5", 0.2)])
        row["provenance"].pop(GRID_RIVALS)

        record = level(row)

        assert record["level"] == "5a"
        assert record["evidence"] == "rivals not measured"
        assert record["lift"] == "measure the shortlist"

    def test_a_rival_in_the_tie_stays_open_and_is_listed(self):
        row = m0(alternatives=[rival("C7H16O5", 0.85, "C7H17O5+")])

        record = level(row)

        assert record["level"] == "5a"
        assert record["evidence"] == "one rival left open: C7H16O5"
        assert "exclude C7H16O5" in record["lift"]
        [entry] = record["rivals"]
        assert entry["test"] == OPEN
        assert entry["fit"] == 0.85
        assert "within the tie margin" in entry["detail"]

    def test_the_evidence_names_three_open_rivals_and_counts_the_rest(self):
        row = m0(
            alternatives=[
                rival("C7H16O5", 0.89, "a+"),
                rival("C8H8O4", 0.88, "b+"),
                rival("C9H12O3", 0.87, "c+"),
                rival("C10H16O2", 0.86, "d+"),
            ]
        )

        assert level(row)["evidence"] == (
            "4 rivals left open: C7H16O5, C8H8O4, C9H12O3 and 1 more"
        )

    def test_a_rival_held_back_only_by_plausibility_is_not_out(self):
        # Equal fits: the rival's evidence is under the winner's only through
        # its plausibility, which weighs and does not exclude.
        assert formula_plausibility("CH2O5") == pytest.approx(0.1)
        row = m0(alternatives=[rival("CH2O5", 0.9, "CH3O5+")])

        record = level(row)

        assert record["level"] == "5a"
        [entry] = record["rivals"]
        assert entry["test"] == OPEN
        assert entry["held_by"] == [HELD_BY_PLAUSIBILITY]

    def test_a_rival_held_by_a_prior_is_still_listed(self):
        # The ambient context holds a sulfur formula at candidate unless
        # something corroborates it; that weighs a reading and excludes nothing.
        assert presets.AMBIENT_AIR.priors_met("C5H10O2S")
        row = m0(alternatives=[rival("C5H10O2S", 0.89, "C5H11O2S+")])

        record = level(row, context=presets.AMBIENT_AIR)

        assert record["level"] == "5a"
        [entry] = record["rivals"]
        assert entry["test"] == OPEN
        assert entry["held_by"] == [HELD_BY_PRIOR]
        assert "excludes nothing" in entry["detail"]

    def test_no_close_rival_is_not_read_as_an_exclusion(self):
        # The density rule separated the winner on the peak's evidence; the
        # level asks of each rival which test put it out, and none did.
        row = m0(
            alternatives=[rival("C7H16O5", 0.85)],
            provenance={
                "candidate_density": 1,
                "tier_reasons": [{"rule": REASON_NO_CLOSE_RIVAL, "detail": "x"}],
            },
        )

        assert level(row)["level"] == "5a"

    def test_a_rival_past_the_stored_cap_is_not_assumed_out(self):
        # An untargeted winner's formula-only entries past the cap were never
        # measured, the 4.15 pass measuring only the stored ones.
        row = m0(
            source="untargeted",
            alternatives=[rival("C7H16O5", 0.2)],
            provenance={RIVALS_PAST_CAP: 2, MEASURED_SHORTLIST: []},
        )

        record = level(row)

        assert record["level"] == "5a"
        assert record["rivals_unlisted"] == 2
        assert "two rivals past the stored shortlist, not tested" in record["evidence"]
        assert "one rival out: one by mass" in record["evidence"]
        assert "measure the rivals past the stored shortlist" in record["lift"]

    def test_a_formula_only_rival_the_pass_did_not_measure_is_open(self):
        row = m0(
            source="untargeted",
            alternatives=[rival("C7H16O5", None)],
            provenance={MEASURED_SHORTLIST: []},
        )

        record = level(row)

        assert record["level"] == "5a"
        assert record["rivals"][0]["detail"] == "its fit on this peak was not measured"
        assert record["lift"] == "measure the shortlist"

    def test_the_grid_rivals_a_list_hit_carries_are_rivals(self):
        row = m0(
            fit=0.8,
            provenance={
                GRID_RIVALS: {
                    "fit_score": 0.8,
                    "added": 3,
                    "rivals": [
                        {
                            "formula": "C8H8O4",
                            "ion_formula": "C8H9O4+",
                            "fit_score": 0.79,
                            "mz_error_ppm": 0.3,
                        }
                    ],
                }
            },
        )

        record = level(row)

        assert record["level"] == "5a"
        assert tests_of(record) == {"C8H8O4": OPEN}
        # Read off the row's rival set, where the run measured the grid's
        # rivals beside the list's own.
        assert record["rivals"][0]["record"] == RIVAL_SET

    def test_a_list_rows_rivals_past_the_sets_named_ones_are_unlisted(self):
        # Its stored alternatives past the cap are in the set, which names
        # all it measured up to its own count; those past it are unlisted.
        row = m0(alternatives=[rival("C7H16O5", 0.2)], provenance={RIVALS_PAST_CAP: 2})
        measured(row)
        row["provenance"][RIVAL_SET]["rivals_unlisted"] = 3

        record = level(row)

        assert record["level"] == "5a"
        assert record["rivals_unlisted"] == 3

    def test_a_fit_of_zero_is_measured(self):
        # The same ion twice: an alternatives entry with no fit, and the
        # grid's rival measured at 0.0. The measured one stands for it.
        row = m0(
            alternatives=[rival("C7H16O5", None, "C7H17O5+")],
            provenance={
                GRID_RIVALS: {
                    "fit_score": 0.9,
                    "added": 1,
                    "rivals": [
                        {
                            "formula": "C7H16O5",
                            "ion_formula": "C7H17O5+",
                            "fit_score": 0.0,
                        }
                    ],
                }
            },
        )

        record = level(row)

        assert tests_of(record) == {"C7H16O5": TEST_MASS}
        assert record["rivals"][0]["fit"] == 0.0
        assert record["rivals"][0]["record"] == RIVAL_SET
        assert record["level"] == "4a"

    def test_a_fit_of_zero_lists_before_an_unmeasured_rival(self):
        # Neither is out: the set recorded no fit of the winner's to read the
        # margin against, so both stay open, the measured one first.
        row = m0(
            alternatives=[rival("C5H12O5", None, "C5H13O5+")],
            provenance={
                GRID_RIVALS: {
                    "fit_score": None,
                    "added": 1,
                    "rivals": [
                        {
                            "formula": "C9H10O3",
                            "ion_formula": "C9H11O3+",
                            "fit_score": 0.0,
                        }
                    ],
                }
            },
        )

        measured(row)
        row["provenance"][RIVAL_SET]["fit_score"] = None
        record = level(row)

        assert [entry["formula"] for entry in record["rivals"]] == [
            "C9H10O3",
            "C5H12O5",
        ]
        assert record["evidence"].startswith("two rivals left open: C9H10O3, C5H12O5")

    def test_a_dim_reading_on_an_orbitrap_asks_for_more_signal(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={"predicted_envelope": {"snr": 2.0, "lines": []}},
        )

        assert level(row)["lift"].startswith("more signal")

    def test_a_tof_row_asks_for_a_twin_or_a_co_varying_channel(self):
        row = m0(alternatives=[rival("C7H16O5", 0.88)])

        assert "co-varies" in level(row, tof=True)["lift"]


class TestTheRivalTests:
    def test_a_fit_under_the_tie_margin_is_out_by_mass(self):
        row = m0(alternatives=[rival("C7H16O5", 0.5, "C7H17O5+")])

        record = level(row)

        assert tests_of(record) == {"C7H16O5": TEST_MASS}
        assert record["level"] == "4a"
        assert record["evidence"].startswith("one rival out: one by mass")

    def test_the_tie_margin_is_relative_to_the_winners_fit(self):
        # 0.9 less ten per cent is 0.81: a rival at 0.80 is out, one at 0.82
        # is in the tie.
        out = m0(alternatives=[rival("C7H16O5", 0.80)])
        tied = m0(alternatives=[rival("C7H16O5", 0.82)])

        assert tests_of(level(out)) == {"C7H16O5": TEST_MASS}
        assert tests_of(level(tied)) == {"C7H16O5": OPEN}

    def test_a_rival_no_channel_places_on_the_peak_is_out_by_mass(self):
        row = m0(
            source="untargeted",
            alternatives=[rival("C7H16O5", None)],
            provenance={MEASURED_SHORTLIST: [{"formula": "C7H16O5", "placed": False}]},
        )

        record = level(row)

        assert tests_of(record) == {"C7H16O5": TEST_MASS}
        assert record["rivals"][0]["record"] == RIVAL_SET
        assert record["level"] == "4a"

    def test_the_measured_shortlist_supplies_the_fit_of_a_formula_only_entry(self):
        measured = {
            "formula": "C7H16O5",
            "ion_formula": "C7H17O5+",
            "fit_score": 0.4,
            "mz_error_ppm": 0.2,
        }
        row = m0(
            source="untargeted",
            alternatives=[rival("C7H16O5", None)],
            provenance={MEASURED_SHORTLIST: [measured]},
        )

        record = level(row)

        assert tests_of(record) == {"C7H16O5": TEST_MASS}
        assert record["rivals"][0]["fit"] == 0.4

    def test_a_tracking_line_the_rival_cannot_make_puts_it_out(self):
        row = m0(
            formula="C5H10O2S",
            ion="C5H11O2S+",
            alternatives=[rival("C8H8O3", 0.88, "C8H9O3+")],
        )

        record = level(row, [iso_line("pa-1", "34S")])

        assert tests_of(record) == {"C8H8O3": TEST_ISOTOPES}
        assert "34S line" in record["rivals"][0]["detail"]
        assert "sulfur" in record["rivals"][0]["detail"]

    def test_a_rival_with_no_ion_named_is_read_by_its_neutral(self):
        row = m0(
            formula="C5H10O2S",
            ion="C5H11O2S+",
            alternatives=[rival("C8H8O3", 0.88)],
        )

        assert tests_of(level(row, [iso_line("pa-1", "34S")])) == {
            "C8H8O3": TEST_ISOTOPES
        }

    @pytest.mark.parametrize(
        "label, elements",
        [
            ("34S", ["S"]),
            ("13C+37Cl", ["C", "Cl"]),
            ("13C2", ["C"]),
            ("81Br", ["Br"]),
            ("2H", ["H"]),
            # A line merged at the file's resolving power names no isotope,
            # and an element's lightest isotope makes no line of its own.
            ("M+2", []),
            ("32S", []),
            (None, []),
        ],
    )
    def test_a_lines_label_names_its_heavy_isotopes(self, label, elements):
        assert _heavy_elements(label) == elements

    def test_a_line_that_does_not_track_its_owner_excludes_nothing(self):
        row = m0(
            formula="C5H10O2S",
            ion="C5H11O2S+",
            alternatives=[rival("C8H8O3", 0.88, "C8H9O3+")],
        )

        record = level(row, [iso_line("pa-1", "34S", tracking=TRACKING_UNTRACKED)])

        assert tests_of(record) == {"C8H8O3": OPEN}

    def test_a_line_of_an_element_the_rival_carries_excludes_nothing(self):
        row = m0(alternatives=[rival("C8H8O3", 0.88, "C8H9O3+")])

        assert tests_of(level(row, [iso_line("pa-1", "13C")])) == {"C8H8O3": OPEN}

    def test_a_rivals_own_line_absent_puts_it_out(self):
        # A bromide reading of this peak predicts its 81Br line at 97% of its
        # own; the spectrum holds the peak's 13C line and no 81Br line.
        m0_mz = 258.98227
        lines = SpectrumLines(
            ["p-1", "p-13c", "p-high"],
            [m0_mz, 259.98563, 400.0],
            [1000.0, 65.0, 30.0],
            [100.0, 6.5, 3.0],
        )
        gate = LineGate(
            mz_tolerance_ppm=5.0,
            abundance_floor=0.001,
            detect_snr=3.0,
            detect_from="default",
            rel_detect_no_snr=0.05,
            tof=False,
        )
        row = m0(
            formula="C9H8O8",
            ion="C9H7O8-",
            alternatives=[rival("C6H12O6", 0.88, "C6H12O6Br-", mz_error_ppm=0.0)],
        )

        record = level(row, lines=lines, gate=gate)

        assert tests_of(record) == {"C6H12O6": TEST_ISOTOPES}
        assert "81Br" in record["rivals"][0]["detail"]
        # Without the spectrum nothing asks the rival's lines.
        assert tests_of(level(row)) == {"C6H12O6": OPEN}

    def test_a_second_channel_puts_out_a_rival_the_run_never_commits(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}},
        )

        record = level(row)

        assert tests_of(record) == {"C7H16O5": TEST_SECOND_CHANNEL}
        # The row's own channel is no second one.
        assert record["rivals"][0]["detail"] == (
            "C6H12O6 is also committed through [M+NH4]+, and C7H16O5 through no "
            "channel of the run"
        )
        assert record["level"] == "4a"

    def test_a_second_channel_does_not_put_out_a_rival_the_run_commits(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}},
        )
        committed = {formula_identity("C7H16O5"): {PROTON: "assigned"}}

        assert tests_of(level(row, neutral_channels=committed)) == {"C7H16O5": OPEN}

    def test_a_partner_on_an_unseparated_peak_is_no_second_channel(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={
                "cross_channel": {
                    "channels": [PROTON, "[M+NH4]+"],
                    SEPARATED_THROUGH: [],
                }
            },
        )

        assert tests_of(level(row)) == {"C7H16O5": OPEN}

    def test_on_a_tof_a_second_channel_excludes_nothing(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}},
        )

        assert tests_of(level(row, tof=True)) == {"C7H16O5": OPEN}

    def test_a_reading_of_another_ions_line_is_not_asked_by_isotopes(self):
        # The peak read as another compound's 13C line: the winner's own lines
        # say nothing of that.
        row = m0(
            formula="C5H10O2S",
            ion="C5H11O2S+",
            alternatives=[rival("C8H8O3", 0.88, "C8H9O3+", isotope_label="13C")],
        )

        assert tests_of(level(row, [iso_line("pa-1", "34S")])) == {"C8H8O3": OPEN}


class TestWhatIsNoRival:
    def test_a_same_ion_reading_is_the_split_not_a_rival(self):
        # Flagged, even where the entry names no ion to compare.
        row = m0(alternatives=[rival("C5H8O5", 0.9, None, same_ion=True)])

        rivals, unlisted = shortlist(*measured(row))

        assert rivals == [] and unlisted == 0
        assert level(row)["evidence"].startswith("no rival on the measured shortlist")

    def test_an_entry_that_reads_the_winners_own_ion_is_no_rival(self):
        row = m0(alternatives=[rival("C5H8O5", 0.9, "C6H13O6+")])

        assert shortlist(*measured(row))[0] == []

    def test_a_shortlist_formula_placed_as_the_winners_own_ion_is_tested(self):
        # C6H10O5 through [M+H3O]+ makes the winner's own ion, C6H13O6+. The
        # 4.15 pass placed it there, and the set names it as no rival: it is
        # the winner's measurement read another way. Its formula-only entry
        # among the alternatives is that formula, tested, not an open rival.
        row = m0(
            source="untargeted",
            alternatives=[rival("C6H10O5", None)],
            provenance={
                MEASURED_SHORTLIST: [
                    {
                        "formula": "C6H10O5",
                        "ion_formula": "C6H13O6+",
                        "fit_score": 0.9,
                        "mz_error_ppm": 0.0,
                    }
                ],
                RIVAL_SET: {"fit_score": 0.9, "rivals": [], "rivals_unlisted": 0},
            },
        )

        rivals, unlisted = shortlist(row)
        record = level(row)

        assert rivals == [] and unlisted == 0
        assert record["level"] != "5a"
        assert record["evidence"].startswith("no rival on the measured shortlist")

    def test_an_entry_of_the_rows_own_formula_is_no_rival(self):
        # Spelled another way, through another adduct: the same neutral.
        row = m0(alternatives=[rival("H12C6O6", 0.9, "C6H16NO6+")])

        assert shortlist(*measured(row))[0] == []

    def test_one_ion_read_twice_is_one_rival_at_its_better_fit(self):
        row = m0(
            alternatives=[
                rival("C7H16O5", 0.4, "C7H17O5+"),
                rival("C6H15NO4", 0.85, "C7H17O5+"),
            ]
        )

        [only] = shortlist(*measured(row))[0]
        assert only.fit == 0.85


class TestFourBAndFourA:
    def test_an_n_bearing_nitrate_ion_with_its_split_open_is_4b(self):
        # A nitrate cluster of C5H8O3 is the deprotonated nitrate ester
        # C5H9NO6: one ion, two molecules, and nothing settled which.
        row = m0(
            formula="C5H8O3",
            ion="C5H8NO6-",
            mechanism="im-no3",
            provenance={
                "cross_channel": {
                    "channels": ["[M+NO3]-"],
                    REASON_AMBIGUOUS_NITROGEN: {
                        "alternative": "C5H9NO6",
                        "via": "[M-H]-",
                    },
                }
            },
        )

        record = level(row)

        assert record["level"] == "4b"
        assert record["evidence"] == (
            "no rival on the measured shortlist; the split is open: the same ion "
            "reads as C5H9NO6 through [M-H]-, which puts one more nitrogen on the "
            "analyte, and no second channel of this run settles the count"
        )
        assert record["lift"].startswith("settle the split")

    def test_an_ambiguous_adduct_is_4b_too(self):
        row = m0(
            provenance={
                "cross_channel": {
                    "channels": [PROTON],
                    REASON_AMBIGUOUS_ADDUCT: {"alternative": "C6H14O7", "via": "x"},
                }
            }
        )

        assert level(row)["level"] == "4b"

    def test_a_labelled_ion_is_4a(self):
        row = m0(
            formula="C5H8O3",
            ion="C5H8[15N]O6-",
            mechanism="im-15n",
            provenance={"cross_channel": {"channels": ["[M+[15N]O3]-"]}},
        )

        record = level(row)

        assert record["level"] == "4a"
        assert "labelled reagent" in record["evidence"]
        assert {
            "fact": "labelled_reagent",
            "record": "ionization_mechanism",
        }.items() <= (record["facts"][-1].items())

    def test_a_labelled_channel_pins_the_split_whatever_the_ion_is_written(self):
        row = m0(
            formula="C5H8O3",
            ion="C5H8NO6-",
            mechanism="im-15n",
            provenance={"cross_channel": {"channels": ["[M+[15N]O3]-"]}},
        )

        assert "labelled reagent of [M+[15N]O3]-" in level(row)["evidence"]

    def test_an_ion_carrying_the_label_is_4a_whatever_its_channel_is_called(self):
        row = m0(
            formula="C5H8O3",
            ion="C5H8[15N]O6-",
            mechanism="im-unknown",
            provenance={"cross_channel": {"channels": ["x"]}},
        )

        assert "labelled reagent" in level(row)["evidence"]

    def test_a_settled_split_is_4a(self):
        row = m0(
            provenance={
                "cross_channel": {
                    "channels": [PROTON],
                    SAME_ION_SETTLED: {
                        "alternative": "C6H9O5",
                        "via": "[M+NH4]+",
                        "by": SETTLED_BY_TARGET_LIBRARY,
                    },
                }
            }
        )

        record = level(row)

        assert record["level"] == "4a"
        assert record["evidence"].startswith(
            "no rival on the measured shortlist; the split is pinned by the target "
            "library: the same ion also reads as C6H9O5 through [M+NH4]+"
        )
        assert record["facts"][-1]["fact"] == SAME_ION_SETTLED

    def test_a_split_a_second_channel_settled_names_it(self):
        row = m0(
            provenance={
                "cross_channel": {
                    "channels": [PROTON, "[M+NH4]+"],
                    SAME_ION_SETTLED: {
                        "alternative": "C6H9O5",
                        "via": "[M+NH4]+",
                        "by": SETTLED_BY_SECOND_CHANNEL,
                        "through": ["[M+NH4]+"],
                    },
                }
            }
        )

        assert level(row)["facts"][-1]["detail"] == SETTLED_BY_SECOND_CHANNEL

    def test_a_second_channel_pins_the_split(self):
        row = m0(provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}})

        assert "pinned by a second channel" in level(row)["evidence"]

    def test_a_second_channel_pins_no_split_on_a_tof(self):
        # The ion could hold the ammonium of [M+NH4]+: open by its units,
        # unless a second channel pins it, which on a TOF it does not.
        row = m0(
            formula="C6H15NO6",
            ion="C6H16NO6+",
            provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}},
        )

        assert level(row)["level"] == "4a"
        record = level(row, tof=True)
        assert record["level"] == "4b"
        assert "second channel committing" in record["lift"]
        assert "pinned" not in record["evidence"]

    def test_a_partner_on_an_unseparated_peak_pins_no_split(self):
        row = m0(
            formula="C6H15NO6",
            ion="C6H16NO6+",
            provenance={
                "cross_channel": {
                    "channels": [PROTON, "[M+NH4]+", "[M+[15N]O3]-"],
                    SEPARATED_THROUGH: [],
                }
            },
        )

        assert level(row)["level"] == "4b"
        row["provenance"]["cross_channel"][SEPARATED_THROUGH] = ["[M+[15N]O3]-"]
        record = level(row)
        assert record["level"] == "4a"
        # Only the separated channel is named.
        assert record["evidence"].endswith(
            "the split is pinned by a second channel: the same neutral is also "
            "committed through [M+[15N]O3]-"
        )
        assert record["facts"][-1]["detail"] == "[M+[15N]O3]-"

    def test_one_split_under_the_runs_channels_is_4a(self):
        record = level(m0())

        assert record["level"] == "4a"
        assert record["evidence"] == (
            "no rival on the measured shortlist; the split is pinned: one "
            "plausible decomposition, the ion holding no reagent unit of the "
            "run's channels"
        )
        assert record["lift"] == (
            "a context list naming a compound of this formula, or MS/MS"
        )

    def test_an_n_free_nitrate_ion_is_4a(self):
        # Deprotonated under a nitrate run: no N to hold the cluster's NO3.
        row = m0(formula="C5H8O4", ion="C5H7O4-", mechanism="im-dep")
        nitrate = {"im-dep": "[M-H]-", "im-no3": "[M+NO3]-"}

        record = level(row, notation_by_id=nitrate)

        assert record["level"] == "4a"
        assert "one plausible decomposition" in record["evidence"]

    def test_an_n_bearing_nitrate_ion_reads_4b_by_its_units(self):
        # No same-ion record, but the ion holds NO3: it may be a nitrate
        # cluster of another molecule, and nothing settled which.
        row = m0(formula="C5H9NO6", ion="C5H8NO6-", mechanism="im-dep")
        nitrate = {"im-dep": "[M-H]-", "im-no3": "[M+NO3]-"}

        record = level(row, notation_by_id=nitrate)

        assert record["level"] == "4b"
        assert "the NO3 of [M+NO3]-" in record["evidence"]
        assert record["lift"].startswith("settle the split")

    def test_a_cluster_ion_holds_its_own_unit_where_another_channel_reads_it(self):
        row = m0(formula="C5H8O3", ion="C5H8NO6-", mechanism="im-no3")

        both = level(row, notation_by_id={"im-dep": "[M-H]-", "im-no3": "[M+NO3]-"})
        alone = level(row, notation_by_id={"im-no3": "[M+NO3]-"})

        assert both["level"] == "4b"
        assert "the NO3 of [M+NO3]-" in both["evidence"]
        # With no other channel to read it through, the cluster is the one split.
        assert alone["level"] == "4a"

    def test_fewer_than_two_n_under_uronium_is_one_decomposition(self):
        uronium = {"im-h": "[M+H]+", "im-ur": "[M+CH4N2O+H]+"}
        one_n = m0(formula="C5H11NO2", ion="C5H12NO2+")
        two_n = m0(formula="C5H12N2O2", ion="C5H13N2O2+")

        assert level(one_n, notation_by_id=uronium)["level"] == "4a"
        assert level(two_n, notation_by_id=uronium)["level"] == "4b"

    def test_an_ion_nothing_can_read_leaves_the_split_open(self):
        row = m0(ion="not a formula")

        assert level(row)["level"] == "4b"

    @pytest.mark.parametrize(
        "notation, unit",
        [
            ("[M+NO3]-", "NO3"),
            ("[M+Br]-", "Br"),
            ("[M+CH4N2O+H]+", "CH4N2O"),
            ("[M+NH4]+", "NH4"),
            ("[M+H]+", None),
            ("[M-H]-", None),
            ("[M+2H]2+", None),
            ("[M]+.", None),
            ("not a mechanism", None),
            (None, None),
        ],
    )
    def test_a_channels_reagent_unit(self, notation, unit):
        assert reagent_unit(notation) == unit

    def test_a_row_the_cross_channel_pass_never_read_is_4b(self):
        row = m0()
        row["provenance"].pop("cross_channel")

        assert level(row)["level"] == "4b"

    def test_an_open_rival_keeps_a_settled_split_at_5a(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={
                "cross_channel": {
                    "channels": [PROTON],
                    SAME_ION_SETTLED: {"by": SETTLED_BY_SECOND_CHANNEL},
                }
            },
        )

        assert level(row)["level"] == "5a"


class TestTheRun:
    def test_every_committed_row_carries_a_level_and_its_column(self):
        owner = m0("pa-1", alternatives=[rival("C7H16O5", 0.5)])
        child = iso_line("pa-1", "13C")
        reagent = {"peak_assignment_id": "pa-r", "role": "reagent", "provenance": {}}

        summary = apply_levels(
            measured(*[owner, child, reagent]), notation_by_id=MECHANISMS
        )

        assert owner[LEVEL_COLUMN] == "4a"
        assert owner["provenance"][IDENTIFICATION_LEVEL]["level"] == "4a"
        # An isotopologue shows its owner's level, and says so.
        assert child[LEVEL_COLUMN] == "4a"
        assert child["provenance"][IDENTIFICATION_LEVEL] == {
            "level": "4a",
            "evidence": owner["provenance"][IDENTIFICATION_LEVEL]["evidence"],
            "lift": owner["provenance"][IDENTIFICATION_LEVEL]["lift"],
            "inherited_from": "pa-1",
        }
        # A row that commits no formula states none.
        assert LEVEL_COLUMN not in reagent
        assert IDENTIFICATION_LEVEL not in reagent["provenance"]
        assert summary == {
            "level_rules_version": LEVEL_RULES_VERSION,
            "levels": {"4a": 1},
            "isotopologue_levels": {"4a": 1},
            "rivals_not_measured": 0,
        }

    def test_an_isotopologue_with_no_owner_on_the_run_reads_5a(self):
        child = iso_line("pa-gone", "13C")

        apply_levels(measured(*[child]))

        assert child[LEVEL_COLUMN] == "5a"
        assert child["provenance"][IDENTIFICATION_LEVEL]["inherited_from"] == "pa-gone"

    def test_the_run_counts_the_rows_with_no_measured_shortlist(self):
        refuted = m0(
            "pa-3",
            source="untargeted",
            peak="p-3",
            provenance={
                "tier_reasons": [{"rule": REASON_UNRESOLVED_PEAK, "detail": "x"}]
            },
        )
        rows = [m0("pa-1", source="untargeted"), m0("pa-2", peak="p-2"), refuted]

        summary = apply_levels(measured(*rows), notation_by_id=MECHANISMS)

        assert summary["levels"] == {"4a": 1, "5a": 1, "5b": 1}
        # The refuted row shows 5b and has no measured shortlist either.
        assert summary["rivals_not_measured"] == 2

    def test_the_run_reads_the_rivals_lines_at_its_own_gate(self):
        # The gate comes off the run's tiering record, as a hand row's does.
        m0_mz = 258.98227
        lines = SpectrumLines(
            ["p-1", "p-13c", "p-high"],
            [m0_mz, 259.98563, 400.0],
            [1000.0, 65.0, 30.0],
            [100.0, 6.5, 3.0],
        )
        tiering = {
            "absent_line": {
                "spectrum": True,
                "mz_tolerance_ppm": 5.0,
                "abundance_floor": 0.001,
                "detect_snr": 3.0,
                "detect_from": "default",
                "rel_detect_no_snr": 0.05,
                "tof_m2": False,
            }
        }
        row = m0(
            formula="C9H8O8",
            ion="C9H7O8-",
            alternatives=[rival("C6H12O6", 0.88, "C6H12O6Br-")],
        )

        apply_levels(measured(*[row]), lines=lines, tiering=tiering)

        assert row[LEVEL_COLUMN] == "4a"

    def test_the_run_reads_its_channels_for_the_second_channel_test(self):
        # The rival's neutral committed through a channel of the run, on
        # another peak: the second channel cuts both ways.
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}},
        )
        partner = m0("pa-2", mechanism="im-nh4", peak="p-2", ion="C6H16NO6+")
        partner["provenance"]["cross_channel"] = {"channels": [PROTON, "[M+NH4]+"]}
        other = m0("pa-3", formula="C7H16O5", ion="C7H17O5+", peak="p-3")

        apply_levels(measured(*[row, partner]), notation_by_id=MECHANISMS)
        alone = row[LEVEL_COLUMN]
        apply_levels(measured(*[row, partner, other]), notation_by_id=MECHANISMS)

        assert alone == "4a"
        assert row[LEVEL_COLUMN] == "5a"

    def test_on_a_tof_run_a_second_channel_excludes_nothing(self):
        row = m0(
            alternatives=[rival("C7H16O5", 0.88)],
            provenance={"cross_channel": {"channels": [PROTON, "[M+NH4]+"]}},
        )

        apply_levels(measured(*[row]), notation_by_id=MECHANISMS, instrument_type="tof")

        assert row[LEVEL_COLUMN] == "5a"

    def test_the_run_names_open_rivals_held_by_its_context(self):
        row = m0(alternatives=[rival("C5H10O2S", 0.89)])

        apply_levels(measured(*[row]), context=presets.AMBIENT_AIR)

        [entry] = row["provenance"][IDENTIFICATION_LEVEL]["rivals"]
        assert entry["held_by"] == [HELD_BY_PRIOR]


class TestAHandRow:
    def test_a_hand_row_has_no_measured_shortlist(self):
        record = hand_level(
            {"role": "M0", "source": "manual", "assigned_formula": "C6H12O6"}
        )

        assert record["level"] == "5a"
        assert record["evidence"] == "rivals not measured"

    def test_a_hand_row_a_line_refutes_is_5b(self):
        record = hand_level(
            {
                "role": "M0",
                "source": "manual",
                "assigned_formula": "C6H12O6",
                "provenance": {
                    "tier_reasons": [{"rule": REASON_ABSENT_LINE, "detail": "x"}]
                },
            }
        )

        assert record["level"] == "5b"

    def test_a_hand_isotopologue_shows_no_owners_level(self):
        record = hand_level(
            {"role": "iso_child", "source": "manual", "assigned_formula": "C6H12O6"}
        )

        assert record == inherited_level(None, "")
        assert record["level"] == "5a"


class TestTheSurfacesRead:
    def test_the_members_level_is_the_most_stated(self):
        assert members_level({"4a": 3, "5a": 2}) == "4a"

    def test_a_tie_reads_as_the_weaker_level(self):
        assert members_level({"4a": 2, "5a": 2}) == "5a"
        assert members_level({"5b": 1, "4a": 1}) == "5b"

    def test_no_member_level_is_none(self):
        assert members_level(None) is None
        assert members_level({"4a": 0, "x": 3}) is None

    def test_the_lines_of_a_row_with_a_level(self):
        provenance = {
            IDENTIFICATION_LEVEL: {"level": "4a", "evidence": "e", "lift": "l"}
        }

        assert level_lines(provenance) == {"level_evidence": "e", "level_lift": "l"}
        assert level_fields("4a", provenance) == {
            "level": "4a",
            "level_evidence": "e",
            "level_lift": "l",
        }

    def test_a_row_without_a_level_reads_none(self):
        none = {"level": None, "level_evidence": None, "level_lift": None}

        assert level_lines(None) == {"level_evidence": None, "level_lift": None}
        assert level_lines(
            {IDENTIFICATION_LEVEL: {"level": "9z", "evidence": "e", "lift": "l"}}
        ) == {"level_evidence": None, "level_lift": None}
        assert level_fields(None, {IDENTIFICATION_LEVEL: {"level": "4a"}}) == none
        assert level_fields("9z", None) == none
