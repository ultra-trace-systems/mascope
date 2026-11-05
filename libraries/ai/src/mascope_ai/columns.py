"""What the columns of a loaded table mean, for whoever reads a handle.

A loader's Frame names its columns and their dtypes. A reader who knows
Mascope reads ``height`` and knows what it is; a model does not, and the
wrong one of three similar columns is a wrong answer. This module keeps one
line per column: what the number is, its unit, and what it is not. Some
columns mean different things in different tables - ``height`` is a mean
over the sample's scans in a peak list and one scan's value in a time
series, ``mz`` is a measurement in a peak list and a theoretical value in
the match tree - so a meaning is looked up by the load that made the table
first, then by the column's name. A derived table's column keeps the
meaning of the load it came from.

The texts are short because a handle carries them all inside a tool
result's byte budget (:data:`mascope_ai.mcp.MAX_RESULT_BYTES`), beside the
handle's head and schema. The user docs say the same at length, on the
page "Tables and columns" (``docs/user/concepts/tables.md``); a change
here is a change there.
"""

from .manifest import CLIENT_COMPUTED, COMPUTED


#: The intensity unit: that of the sample's averaged spectrum, which the
#: server labels counts/s. Heights, areas and spectrum points share it.
_UNIT = "counts/s"

#: What a column means wherever it appears, by name.
COMMON: dict[str, str] = {
    "sample_batch_name": "the batch's name: users' text",
    "sample_item_id": "the sample's ID; group by it, names repeat across batches",
    "sample_item_name": "the sample's name: users' text",
    "datetime_utc": "when the sample's time window starts, UTC",
    "peak_id": "Mascope's ID of the peak in its sample; one row per matched line",
    "mz": "the peak's measured centroid m/z, calibrated",
    "height": (
        "the peak's apex intensity in the averaged spectrum: the mean over the "
        f"sample's scans (sum with average=False); {_UNIT}"
    ),
    "area": (
        "the peak's intensity integrated over its m/z extent, aggregated like "
        "height; a TOF peak's intensity in matching"
    ),
    "signal_to_noise": (
        "the peak's height over its noise estimate; NaN when the file stores "
        "none, an absent estimate, not a clean peak"
    ),
    "sparsity": (
        "the share of the sample's scans the peak is absent from; also 0 while "
        "its time series is not yet computed"
    ),
    "segment": (
        "the scan stream of a stitched sample the peak came from; None when "
        "not stitched"
    ),
    "is_satellite": "whether the peak is a side-lobe artifact of a stronger peak",
    "match_score_isotope": (
        "Mascope's 0 to 1 score of this peak as the matched isotope line; NaN "
        "when unmatched"
    ),
    "match_score_ion": CLIENT_COMPUTED["match_score_ion"],
    "match_score_compound": CLIENT_COMPUTED["match_score_compound"],
    "relative_abundance": (
        "the matched line's theoretical abundance in its ion's pattern; 1 is "
        "the most abundant"
    ),
    "target_isotope_id": "the matched isotope line's ID",
    "target_isotope_formula": "the matched isotope line, one m/z of the ion's pattern",
    "target_ion_id": "the matched ion's ID",
    "target_ion_formula": "the matched ion: the compound with its mechanism applied",
    "target_compound_id": "the matched compound's ID; group by it, formulas repeat",
    "target_compound_name": "the matched compound's name; NaN when unmatched",
    "target_compound_formula": "the matched compound's neutral formula; NaN when unmatched",
    "ionization_mechanism": "the matched ion's ionization mechanism, by name",
    "ionization_mechanism_id": "the matched ion's ionization mechanism's ID",
    "target_collection_ids": "the target collections the matched compound is in",
    "target_collection_names": "their names, aligned with the IDs; group by the IDs",
    "time": "seconds from the sample's start",
    "stage": "the stage's 0-based index in the stages asked for",
    "stage_name": "the stage's name, as given; None when none was",
    "t_min": "the stage's start, seconds from the sample's start",
    "t_max": "the stage's end, seconds from the sample's start",
}

#: The match tree's own columns: one row per isotope line Mascope looked
#: for, every score and category Mascope's (:func:`mascope_ai.oracle.tree_rows`).
_TREE: dict[str, str] = {
    "candidate": "the formula as it was asked about",
    "origin": "where the candidate came from: given, mass_query or model",
    "mz": "the line's theoretical m/z; the measured one is sample_peak_mz",
    "match_score_compound": "Mascope's compound score: its best ion's",
    "match_score_ion": "Mascope's ion score: its lines' scores weighted by abundance",
    "match_score_isotope": "Mascope's 0 to 1 score of the line on the peak found",
    "match_category_compound": (
        "Mascope's category for the compound: 2 probable, 1 possible, 0 unmatched"
    ),
    "match_category_ion": "Mascope's category for the ion: 2, 1 or 0",
    "match_category_isotope": "Mascope's category for the line: 2, 1 or 0",
    "match_isotope_id": "the ID of this match record",
    "intensity_compound": (
        "Mascope's summed intensity of the peaks found at the compound's lines"
    ),
    "intensity_ion": "Mascope's summed intensity of the peaks found at the ion's lines",
    "sample_peak_id": "the sample's peak the line was found at; empty if none",
    "sample_peak_mz": "the measured m/z of the peak the line was found at",
    "sample_peak_intensity": "that peak's intensity: height on an Orbitrap, area on a TOF",
    "sample_peak_intensity_relative": (
        "that intensity relative to the ion's most abundant line's peak"
    ),
    "sample_peak_tof": "that peak's time-of-flight coordinate; TOF files only",
    "match_mz_error": "the peak's m/z against the line's theoretical m/z",
    "match_abundance_error": (
        "the peak's relative intensity against the line's theoretical abundance"
    ),
    "resolution": "that peak's resolving power, m/z over its width",
    "signal_to_noise": "that peak's height over its noise estimate",
    "is_satellite": "whether that peak is a side-lobe artifact of a stronger peak",
    "instrument": "the instrument whose match parameters scored the ion",
    "cas_number": "the compound's CAS number, if recorded; the match is on the formula",
    "inchi": "the compound's InChI, if recorded; a formula match confirms no structure",
    "inchikey": "the compound's InChIKey, if recorded",
    "smiles": "the compound's SMILES, if recorded",
}

#: What a column means in the table one load makes, by the load's op; what
#: is not here falls back to :data:`COMMON`.
BY_OP: dict[str, dict[str, str]] = {
    "load_timeseries": {
        "datetime_utc": "the point's absolute time: the sample's start plus time, UTC",
        "height": (
            f"the peak's height in one scan, at time; {_UNIT}. The sample's own "
            "value is load_peaks' height"
        ),
        "mz": "the peak's m/z, the same on every point of its series",
    },
    "load_spectrum": {
        "mz": "the profile point's m/z",
        "intensity": (
            f"the averaged profile at this m/z, {_UNIT}; a window's apex is its "
            "highest point, near the fitted height, not equal"
        ),
    },
    "load_peaks_by_stage": {
        "height": f"the peak's apex intensity over the stage's scans only; {_UNIT}",
        "area": "the peak's area over the stage's scans only",
        "signal_to_noise": (
            "the sample's whole-window value, not the stage's: height over noise"
        ),
        "sparsity": (
            "the sample's whole-window value, not the stage's: the share of "
            "scans the peak is absent from"
        ),
    },
    "match_compounds": _TREE,
}


def meaning(op: str | None, column: str) -> str | None:
    """What *column* means in the table *op* made; None when not known."""
    by_op = BY_OP.get(op or "", {})
    return by_op.get(column) or COMMON.get(column)


def meanings(
    op: str | None,
    columns: "tuple[str, ...] | list[str]",
    *,
    computed: "tuple[str, ...] | list[str]" = (),
    inherited: "dict[str, str] | None" = None,
) -> dict[str, str]:
    """What each of *columns* means, for those the toolkit knows.

    A column in *computed* - one a derived Frame's code made - says so,
    whatever its name: a column the code wrote is the code's, and its
    meaning is in the code, not here. A column in *inherited* means what
    it meant in the load named there. A column nobody knows is left out.
    """
    out: dict[str, str] = {}
    for column in columns:
        if column in computed:
            text: str | None = COMPUTED
        else:
            text = meaning((inherited or {}).get(column, op), column)
        if text is not None:
            out[column] = text
    return out
