"""What the columns of a loaded table mean, for whoever reads a handle.

A loader's Frame names its columns and their dtypes. A reader who knows
Mascope reads ``height`` and knows what it is; a model does not, and the
wrong one of three similar columns is a wrong answer. This module keeps one
line per column: what the number is, its unit, and what it is not. Some
columns mean different things in different tables - ``height`` is a mean
over the sample's scans in a peak list and one scan's value in a time
series - so a meaning is looked up by the load that made the table first,
then by the column's name.

The user docs say the same at length, on the page "Tables and columns"
(``docs/user/concepts/tables.md``); a change here is a change there.
"""

from .manifest import CLIENT_COMPUTED


#: The intensity unit, as the columns name it.
_UNIT = "counts on an Orbitrap, ions on a TOF"

#: What a column means wherever it appears, by name.
COMMON: dict[str, str] = {
    "sample_batch_name": "the batch's name: users' text",
    "sample_item_id": "the sample's ID; group by it, names repeat across batches",
    "sample_item_name": "the sample's name: users' text",
    "datetime_utc": "when the sample's time window starts, UTC",
    "peak_id": (
        "Mascope's ID of the detected peak in its sample; a peak matched to "
        "several lines has a row per line"
    ),
    "mz": "the peak's centroid m/z, calibrated",
    "height": (
        "the peak's apex intensity in the sample's averaged spectrum, the mean "
        f"over its scans (the sum with average=False); {_UNIT}"
    ),
    "area": (
        "the peak's intensity integrated over its m/z extent, aggregated over "
        "the scans like height"
    ),
    "signal_to_noise": (
        "the peak's height over its noise estimate; NaN where the file stores "
        "none, an absent estimate, not a clean peak"
    ),
    "sparsity": (
        "the share of the sample's scans the peak is absent from; 0 means seen "
        "in every scan"
    ),
    "segment": (
        "the scan stream of a stitched sample the peak came from; None for a "
        "sample that is not stitched"
    ),
    "is_satellite": (
        "whether the peak is a side-lobe artifact of a stronger peak nearby, "
        "not a compound"
    ),
    "match_score_isotope": (
        "Mascope's 0 to 1 score of this peak as the matched isotope line; NaN "
        "for an unmatched peak"
    ),
    "match_score_ion": CLIENT_COMPUTED["match_score_ion"],
    "match_score_compound": CLIENT_COMPUTED["match_score_compound"],
    "relative_abundance": (
        "the matched line's theoretical abundance within its ion's isotope "
        "pattern; 1 is the most abundant line"
    ),
    "target_isotope_id": "the matched isotope line's ID",
    "target_isotope_formula": (
        "the matched isotope line: one m/z of the ion's isotope pattern"
    ),
    "target_ion_id": "the matched ion's ID",
    "target_ion_formula": (
        "the matched ion: the compound with its ionization mechanism applied"
    ),
    "target_compound_id": "the matched compound's ID; group by it, formulas repeat",
    "target_compound_name": "the matched compound's name; NaN for an unmatched peak",
    "target_compound_formula": (
        "the matched compound's neutral formula; NaN for an unmatched peak"
    ),
    "ionization_mechanism": "the matched ion's ionization mechanism, by name",
    "ionization_mechanism_id": "the matched ion's ionization mechanism's ID",
    "target_collection_ids": "the target collections the matched compound is in",
    "target_collection_names": (
        "their names, aligned with the IDs; display only, group by the IDs"
    ),
    "time": "seconds from the sample's start",
    "stage": "the stage's 0-based index in the stages asked for",
    "stage_name": "the stage's name, as given; None when none was",
    "t_min": "the stage's start, seconds from the sample's start",
    "t_max": "the stage's end, seconds from the sample's start",
}

#: What a column means in the table one load makes, by the load's op; what
#: is not here falls back to :data:`COMMON`.
BY_OP: dict[str, dict[str, str]] = {
    "load_timeseries": {
        "datetime_utc": (
            "the point's absolute time: the sample's start plus time, UTC"
        ),
        "height": (
            f"the peak's height in one scan, at time; {_UNIT}. A sample's own "
            "point is its averaged peak's height from load_peaks"
        ),
        "mz": "the peak's m/z, the same on every point of its series",
    },
    "load_spectrum": {
        "mz": "the profile point's m/z",
        "intensity": (
            "the averaged profile's intensity at this m/z, counts/s; a window's "
            "apex is its highest point, near the fitted peak's height, not equal"
        ),
    },
    "load_peaks_by_stage": {
        "height": (
            f"the peak's apex intensity averaged over the stage's scans only; {_UNIT}"
        ),
        "area": "the peak's area, aggregated over the stage's scans only",
    },
    "match_compounds": {
        "candidate": "the formula as it was asked about",
        "origin": "where the candidate came from: given, mass_query or model",
        "match_score_compound": "Mascope's compound score: its best ion's",
        "match_score_ion": (
            "Mascope's ion score: its isotope lines' scores, weighted by abundance"
        ),
        "match_category_compound": (
            "Mascope's category for the compound: 2 probable, 1 possible, 0 unmatched"
        ),
        "match_category_ion": "Mascope's category for the ion: 2, 1 or 0",
        "match_category_isotope": "Mascope's category for the line: 2, 1 or 0",
        "intensity_compound": (
            "Mascope's summed intensity of the peaks found at the compound's "
            "lines, all its ions"
        ),
        "intensity_ion": (
            "Mascope's summed intensity of the peaks found at the ion's lines"
        ),
        "sample_peak_mz": "the m/z of the sample's peak the line was found at",
        "sample_peak_id": "the sample's peak the line was found at; empty if none",
        "target_isotope_mz": "the line's theoretical m/z",
        "target_isotope_mz_error_ppm": (
            "the peak's m/z against the line's theoretical one, in ppm"
        ),
    },
}

#: What a column a derived Frame's code computed means, when no better is
#: known: the code is in the Frame's query.
COMPUTED = "computed in Python by the code that made this Frame (its query's code)"


def meaning(op: str | None, column: str) -> str | None:
    """What *column* means in the table *op* made; None when not known."""
    by_op = BY_OP.get(op or "", {})
    return by_op.get(column) or COMMON.get(column)


def meanings(
    op: str | None,
    columns: "tuple[str, ...] | list[str]",
    *,
    computed: "tuple[str, ...] | list[str]" = (),
) -> dict[str, str]:
    """What each of *columns* means, for those the toolkit knows.

    A column in *computed* - one a derived Frame's code made - says so,
    whatever its name: a column the code wrote is the code's, and its
    meaning is in the code, not here. A column nobody knows is left out.
    """
    out: dict[str, str] = {}
    for column in columns:
        text = COMPUTED if column in computed else meaning(op, column)
        if text is not None:
            out[column] = text
    return out
