"""
The oracle: the mass query's candidates, and Mascope's match tree condensed
to one verdict per candidate inside Python - the tree a Frame, never a
result.

Hermetic: the match route and the mass query answer with what a demo stack
answered (conftest.py).
"""

import json
from pathlib import Path

import pandas as pd
import pytest

from mascope_ai import (
    Candidate,
    Candidates,
    Frame,
    InvalidRequest,
    MatchResult,
    NotFound,
    Ref,
)
from mascope_ai.oracle import NOT_IN_ANSWER, REPR_VERDICTS, condense, tree_rows


#: The recorded sample's peaks the recorded tree finds CH2O2's [M+Br]- ion
#: at, the most abundant isotopologue first.
CH2O2_PEAKS = (
    "mnFT7aUMdKotTvjEjCVp",
    "mHiZS5TrPlqq3Y1z02Is",
    "w6G8z893EDxL7fGS1UKX",
    "b32cXmd4UqA2iICsVaJ6",
    "ut3VpxyU3YwqkhqrGXbD",
)
BROMIDE = "4Qcgr7rPeNL"  # [M+Br]-
DEPROTONATED = "5ni6bQ49IbX"  # [M-H]-


def _match_bodies(api):
    return [
        call[3]
        for call in api.requests_to(r"match/aggregate/sample/\w+/compounds", "POST")
    ]


class TestQueryByMz:
    def test_candidates_come_closest_first(self, kit):
        found = kit.query_by_mz(179.0561, ["[M-H]-"], tolerance_ppm=10)

        assert isinstance(found, Candidates)
        assert all(isinstance(c, Candidate) for c in found)
        errors = [abs(c.error_ppm) for c in found]
        assert errors == sorted(errors)
        assert found[0].formula == "C5H6N7O"
        assert {c.origin for c in found} == {"mass_query"}
        assert found[1].formula == "C6H12O6"
        assert found[1].mechanism == "[M-H]-"
        assert found[1].mechanism_id == DEPROTONATED
        assert found[1].query_mz == pytest.approx(179.0561)

    def test_limit_keeps_the_closest_and_total_counts_them_all(self, kit):
        found = kit.query_by_mz(179.0561, ["[M-H]-"], limit=2)

        assert [c.formula for c in found] == ["C5H6N7O", "C6H12O6"]
        assert found.total == 5
        assert "the closest 2 shown" in repr(found)

    def test_the_query_reaches_the_server(self, kit, api):
        kit.query_by_mz(
            179.0561,
            ["[M-H]-", BROMIDE],
            tolerance_ppm=7.5,
            formula_ranges="C0-10 H0-20 O0-6",
        )

        ((_, _, _, body),) = api.requests_to("cheminfo/mz/query", "POST")
        assert body["mz"] == 179.0561
        assert body["mz_precision"] == 7.5
        # By name or by ID, the server gets IDs
        assert body["ionization_mechanism_ids"] == [DEPROTONATED, BROMIDE]
        assert body["formula_ranges"] == "C0-10 H0-20 O0-6"

    def test_the_sdks_formula_ranges_unless_given(self, kit, api):
        kit.query_by_mz(179.0561, "[M-H]-")

        ((_, _, _, body),) = api.requests_to("cheminfo/mz/query", "POST")
        assert body["formula_ranges"] == "C0-100 H0-100 O0-100 N0-100"
        assert body["mz_precision"] == 5.0

    def test_a_mechanism_mascope_lacks(self, kit, api):
        with pytest.raises(NotFound) as caught:
            kit.query_by_mz(179.0561, ["[M+Xx]-"])

        assert "[M-H]-" in caught.value.message  # what there is
        assert not api.requests_to("cheminfo/mz/query", "POST")

    @pytest.mark.parametrize(
        "args",
        [
            {"mz": -1, "mechanisms": ["[M-H]-"]},
            {"mz": "179", "mechanisms": ["[M-H]-"]},
            {"mz": 179.0, "mechanisms": ["[M-H]-"], "tolerance_ppm": 0},
            {"mz": 179.0, "mechanisms": ["[M-H]-"], "limit": 0},
            {"mz": 179.0, "mechanisms": []},
        ],
    )
    def test_bad_arguments(self, kit, api, args):
        with pytest.raises(InvalidRequest):
            kit.query_by_mz(**args)
        assert not api.requests_to("cheminfo/mz/query", "POST")


class TestMatchCompounds:
    def test_a_verdict_is_mascopes_score_and_the_peaks_it_found(self, kit):
        result = kit.match_compounds("s21", ["CH2O2"])

        assert isinstance(result, MatchResult)
        (verdict,) = result.verdicts
        assert verdict.formula == "CH2O2"
        assert verdict.status == "probable"
        assert verdict.score == pytest.approx(0.9607899817360022)
        assert verdict.best_ion.formula == "CH2BrO2-"
        assert verdict.best_ion.mechanism == "[M+Br]-"
        assert verdict.best_ion.mechanism_id == BROMIDE
        assert verdict.peak_ids == CH2O2_PEAKS
        # The other ion is in the tree, scored and unmatched
        assert [ion.formula for ion in verdict.ions] == ["CH2BrO2-", "CHO2-"]
        assert verdict.ions[1].status == "unmatched"
        assert verdict.ions[1].peak_ids == ()

    def test_the_verdict_agrees_with_the_samples_stored_match(self, kit):
        verdict = kit.match_compounds("s21", ["CH2O2"]).verdicts[0]
        peaks = kit.load_peaks(samples="s21").df

        stored = peaks[peaks["target_compound_formula"] == "CH2O2"]
        # The fake server names each sample's copy of a recorded peak
        # "<sample>-<copy>-<recorded peak id>"
        recorded_ids = stored["peak_id"].str.rsplit("-", n=1).str[-1]
        assert set(recorded_ids) == set(verdict.peak_ids)
        assert set(stored["target_ion_formula"]) == {verdict.best_ion.formula}
        assert stored["match_score_compound"].iloc[0] == pytest.approx(verdict.score)

    def test_peaks_of_a_zero_score_are_not_attributed(self, kit):
        # C6H12O6's isotopologues sit nearest to some peaks, with score 0:
        # Mascope found none of them
        result = kit.match_compounds("s21", ["C6H12O6"])
        tree = result.tree.df

        assert (tree["sample_peak_id"].astype(str) != "").any()
        (verdict,) = result.verdicts
        assert verdict.status == "unmatched"
        assert verdict.score == 0
        assert verdict.peak_ids == ()
        assert all(ion.peak_ids == () for ion in verdict.ions)

    def test_verdicts_come_best_first(self, kit):
        result = kit.match_compounds("s21", ["C6H12O6", "HNO3", "CH2O2"])

        assert [v.formula for v in result.verdicts] == ["HNO3", "CH2O2", "C6H12O6"]
        assert [v.status for v in result.verdicts] == [
            "probable",
            "probable",
            "unmatched",
        ]

    def test_each_verdict_says_where_its_candidate_came_from(self, kit):
        hit = kit.query_by_mz(179.0561, ["[M-H]-"])[1]  # C6H12O6

        result = kit.match_compounds("s21", [hit, "HNO3"], suggested=["CH2O2", "HNO3"])

        origins = {v.formula: v.origin for v in result.verdicts}
        # A suggestion the caller gave too is the caller's
        assert origins == {"C6H12O6": "mass_query", "HNO3": "given", "CH2O2": "model"}
        tree = result.tree.df
        assert dict(zip(tree["candidate"], tree["origin"])) == origins

    def test_a_formula_is_sent_once(self, kit, api):
        kit.match_compounds("s21", ["CH2O2", " CH2O2 "], suggested=["CH2O2"])

        ((body),) = _match_bodies(api)
        assert body["target_compound_formulas"] == ["CH2O2"]

    def test_a_formula_mascope_refuses_is_unscored_and_the_rest_scored(self, kit, api):
        result = kit.match_compounds("s21", ["CH2O2"], suggested=["Qq2", "HNO3"])

        assert {v.formula for v in result.verdicts} == {"CH2O2", "HNO3"}
        (unscored,) = result.unscored
        assert unscored.formula == "Qq2"
        assert unscored.origin == "model"
        assert unscored.reason.startswith("Mascope refused it: Formula 'Qq2'")
        first, second = _match_bodies(api)
        assert first["target_compound_formulas"] == ["CH2O2", "Qq2", "HNO3"]
        assert second["target_compound_formulas"] == ["CH2O2", "HNO3"]
        assert "'Qq2': Mascope refused it" in result.tree.warnings[0]

    def test_a_formula_mascope_returns_nothing_for_is_unscored(self, kit):
        result = kit.match_compounds("s21", ["CH2O2", "C9H9N"])

        (unscored,) = result.unscored
        assert (unscored.formula, unscored.origin) == ("C9H9N", "given")
        assert unscored.reason == NOT_IN_ANSWER
        assert result.verdict("C9H9N") is None
        assert result.verdict("CH2O2").status == "probable"

    def test_every_formula_refused(self, kit, api):
        result = kit.match_compounds("s21", ["Qq1"], suggested=["Qq2"])

        assert result.verdicts == ()
        assert [u.formula for u in result.unscored] == ["Qq1", "Qq2"]
        assert result.tree.n_rows == 0
        assert len(_match_bodies(api)) == 2

    def test_a_refusal_naming_no_formula_is_raised(self, kit, api):
        api.fail("POST", r"match/aggregate/sample/\w+/compounds", 422)

        with pytest.raises(InvalidRequest):
            kit.match_compounds("s21", ["CH2O2"])

    def test_params_and_mechanisms_reach_the_server(self, kit, api):
        params = {
            "mz_tolerance": 5,
            "isotope_ratio_tolerance": 0.3,
            "peak_min_intensity": 0.0,
            "isotope_abundance_threshold": 0.01,
            "possible_match_threshold": 0.5,
            "probable_match_threshold": 0.8,
        }

        result = kit.match_compounds(
            "s21", ["CH2O2"], params, mechanisms=["[M+Br]-", DEPROTONATED]
        )

        ((body),) = _match_bodies(api)
        assert body["match_params"] == params
        assert body["ion_mechanism_ids"] == [BROMIDE, DEPROTONATED]
        assert result.params == params

    def test_the_apps_defaults_unless_told_otherwise(self, kit, api):
        kit.match_compounds("s21", ["CH2O2"])

        ((body),) = _match_bodies(api)
        assert "match_params" not in body
        assert "ion_mechanism_ids" not in body

    def test_a_sample_by_name(self, kit, api):
        result = kit.match_compounds("Sample 2", ["CH2O2"])

        assert result.sample.id == "s22"
        assert _match_bodies(api)

    @pytest.mark.parametrize(
        "formulas, kwargs",
        [([], {}), (["CH2O2"], {"params": "fast"}), ([3], {}), ([" "], {})],
    )
    def test_bad_candidates(self, kit, api, formulas, kwargs):
        with pytest.raises(InvalidRequest):
            kit.match_compounds("s21", formulas, **kwargs)
        assert not _match_bodies(api)


class TestTheTree:
    def test_it_is_a_frame_of_one_row_per_isotopologue(self, kit):
        result = kit.match_compounds("s21", ["CH2O2", "HNO3"])
        tree = result.tree

        assert isinstance(tree, Frame)
        recorded = [
            compound
            for compound in _recorded_tree()
            if compound["target_compound_formula"] in ("CH2O2", "HNO3")
        ]
        assert tree.n_rows == sum(
            len(ion["children"]) for c in recorded for ion in c["children"]
        )
        assert tree.source_refs == (result.sample,)
        assert tree.query["op"] == "match_compounds"
        assert tree.client_columns == ()

    def test_its_levels_keep_their_own_scores(self, kit):
        df = kit.match_compounds("s21", ["CH2O2"]).tree.df

        for column in (
            "match_score_compound",
            "match_score_ion",
            "match_score_isotope",
            "match_category_compound",
            "match_category_ion",
            "match_category_isotope",
            "intensity_compound",
            "intensity_ion",
            "ionization_mechanism",
        ):
            assert column in df
        assert "match_score" not in df and "filter_params" not in df
        bromide = df[df["target_ion_formula"] == "CH2BrO2-"]
        assert set(bromide["ionization_mechanism"]) == {"[M+Br]-"}

    def test_it_is_kept_and_loaded_again_from_its_query(self, kit, api):
        tree = kit.match_compounds("s21", ["CH2O2"]).tree
        rows = tree.df

        kit.session.evict(tree.id)
        again = kit.session.frame(tree.id).df

        pd.testing.assert_frame_equal(again, rows, check_dtype=False)
        assert len(_match_bodies(api)) == 2

    def test_the_result_never_prints_the_tree(self, kit):
        result = kit.match_compounds("s21", ["CH2O2", "HNO3", "C6H12O6"])

        text = repr(result)
        assert "probable" in text and "CH2BrO2-" in text
        assert result.tree.id in text
        assert "match_isotope_id" not in text
        isotopes = set(result.tree.df["target_isotope_id"])
        assert not any(isotope in text for isotope in isotopes)
        json.dumps(result.to_dict())  # plain JSON throughout

    def test_the_repr_is_bounded_however_many_candidates(self):
        tree = pd.DataFrame(
            [
                {
                    "candidate": f"C{i}H{i}",
                    "origin": "given",
                    "match_score_compound": 0.5,
                    "match_category_compound": 1,
                    "target_ion_id": f"i{i}",
                    "target_ion_formula": f"C{i}H{i}-",
                    "match_score_ion": 0.5,
                    "match_category_ion": 1,
                    "target_isotope_id": f"t{i}",
                    "match_score_isotope": 0.5,
                    "sample_peak_id": f"p{i}",
                }
                for i in range(200)
            ]
        )
        verdicts, _ = condense(tree, [(f"C{i}H{i}", "given") for i in range(200)])
        result = MatchResult(
            Ref("s1", "sample", "Sample 1", parent="b1"),
            verdicts,
            (),
            Frame.from_df(tree, source_refs=[], query={"op": "match_compounds"}),
        )

        text = repr(result)
        assert len(text.splitlines()) == REPR_VERDICTS + 3
        assert f"and {200 - REPR_VERDICTS} more" in text


class TestCondense:
    """The condensing on its own: what it attributes, and what it lacks."""

    def _tree(self) -> pd.DataFrame:
        compounds = [
            {
                "target_compound_formula": "C2H4O2",
                "match_score": 0.9,
                "match_category": 2,
                "sample_peak_intensity_sum": 10.0,
                "children": [
                    {
                        "target_ion_id": "ia",
                        "target_ion_formula": "C2H3O2-",
                        "ionization_mechanism_id": "m1",
                        "match_score": 0.9,
                        "match_category": 2,
                        "children": [
                            {
                                "target_isotope_id": "a1",
                                "relative_abundance": 0.1,
                                "match_score": 0.8,
                                "sample_peak_id": "p2",
                            },
                            {
                                "target_isotope_id": "a2",
                                "relative_abundance": 0.9,
                                "match_score": 0.95,
                                "sample_peak_id": "p1",
                            },
                            {
                                "target_isotope_id": "a3",
                                "relative_abundance": 0.01,
                                "match_score": 0.0,
                                "sample_peak_id": "p9",
                            },
                        ],
                    },
                    {
                        # Found at a peak, but too poorly to match
                        "target_ion_id": "ib",
                        "target_ion_formula": "C2H4BrO2-",
                        "ionization_mechanism_id": "m2",
                        "match_score": 0.1,
                        "match_category": 0,
                        "children": [
                            {
                                "target_isotope_id": "b1",
                                "relative_abundance": 0.5,
                                "match_score": 0.1,
                                "sample_peak_id": "p3",
                            },
                            {
                                "target_isotope_id": "b2",
                                "relative_abundance": 0.4,
                                "match_score": 0.2,
                                "sample_peak_id": "",
                            },
                        ],
                    },
                ],
            }
        ]
        return tree_rows(compounds, {"C2H4O2": ("C2H4O2", "model")}, {"m1": "[M-H]-"})

    def test_only_the_peaks_of_matched_ions_are_attributed(self):
        (verdict,), missing = condense(self._tree(), [("C2H4O2", "model")])

        assert missing == ()
        assert verdict.origin == "model"
        assert verdict.intensity == 10.0
        best, other = verdict.ions
        assert best.peak_ids == ("p1", "p2")  # most abundant first, score > 0
        assert other.status == "unmatched"
        assert other.peak_ids == ("p3",)  # a peak id is needed
        assert verdict.peak_ids == ("p1", "p2")

    def test_an_ions_counts_and_mechanisms(self):
        (verdict,), _ = condense(self._tree(), [("C2H4O2", "model")])

        best, other = verdict.ions
        assert (best.n_isotopes, other.n_isotopes) == (3, 2)
        assert best.mechanism == "[M-H]-"
        assert other.mechanism is None  # a mechanism Mascope did not list

    def test_it_says_which_candidates_the_tree_lacks(self):
        verdicts, missing = condense(
            self._tree(), [("C2H4O2", "model"), ("C3 H6", "given")]
        )

        assert [v.formula for v in verdicts] == ["C2H4O2"]
        assert missing == ("C3 H6",)


def _recorded_tree():
    # Read here, not imported: another library's conftest can be the one a
    # run of every library's tests imports as "conftest"
    recorded = json.loads((Path(__file__).parent / "recorded_api.json").read_text())
    return recorded["match_compounds"]["data"]
