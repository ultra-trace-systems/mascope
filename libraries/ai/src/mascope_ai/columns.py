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
meaning of the load it came from, and has none when its inputs disagree.

The texts are short because a handle carries them all inside a tool
result's byte budget (:data:`mascope_ai.mcp.MAX_RESULT_BYTES`), beside the
handle's head and schema. The user docs say the same at length, on the
page "Tables and columns" (``docs/user/concepts/tables.md``); a change
here is a change there.
"""

from .manifest import CLIENT_COMPUTED, COMPUTED


#: The intensity unit: that of the sample's averaged spectrum, which is an
#: average over scans, never over time. Heights, time-series points and
#: spectrum points share it; the spectrum route's ``counts/s`` is a label.
_UNIT = "counts (Orbitrap) or ions/s (TOF)"

#: The op recorded for a derived column whose inputs give it different
#: meanings: it then has none, rather than one input's.
MIXED = "mixed"

#: The op a peak list loaded with ``average=False`` is looked up under: its
#: height and area are sums over the scans, not means.
LOAD_PEAKS_SUMMED = "load_peaks_summed"

#: Ops under which a column has no meaning: a mixed one, and a derived
#: Frame's own op, which says nothing of where a column came from. A
#: derived Frame saved before columns recorded their source has no
#: ``inherited``, and its columns fall here rather than on the peak list's
#: texts.
_UNRECORDED = (MIXED, "derive")

#: What a column means wherever it appears, by name.
COMMON: dict[str, str] = {
    "sample_batch_name": "the batch's name: users' text",
    "sample_item_id": "the sample's ID; group by it, names repeat across batches",
    "sample_item_name": "the sample's name: users' text",
    "datetime_utc": (
        "the sample file's acquisition start, UTC; the sample's window begins "
        "t0 seconds later"
    ),
    "peak_id": (
        "Mascope's ID of the peak in its sample; repeated, one row per line, when "
        "the peak matched several"
    ),
    "mz": "the peak's measured centroid m/z, calibrated",
    "height": (
        "the peak's apex intensity in the averaged spectrum, the mean over the "
        f"sample's scans; {_UNIT}"
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
    "n_scans": (
        "Orbitrap: how many of the scans averaged into the peak hold it; a dim "
        "line misses some. NaN: TOF or no count kept"
    ),
    "n_scans_total": (
        "Orbitrap: how many scans the peak is averaged over, its polarity's or "
        "stream's; NaN: TOF or no count kept"
    ),
    "segment": (
        "the scan stream of a stitched sample the peak came from; None when "
        "not stitched"
    ),
    "is_satellite": (
        "whether the peak is a side-lobe artifact; False throughout a peak "
        "list, which leaves satellites out"
    ),
    "match_score_isotope": (
        "Mascope's 0 to 1 score of this peak as the matched isotope line; NaN "
        "when unmatched"
    ),
    "match_score_ion": CLIENT_COMPUTED["match_score_ion"],
    "match_score_compound": CLIENT_COMPUTED["match_score_compound"],
    "relative_abundance": (
        "the line's share of its ion's theoretical isotope pattern; an ion's "
        "lines sum to about 1"
    ),
    "target_isotope_id": "the matched isotope line's ID",
    "target_isotope_formula": "the matched isotope line, one m/z of the ion's pattern",
    "target_ion_id": "the matched ion's ID",
    "target_ion_formula": (
        "the matched ion, its mechanism applied; its rows are its isotope lines: "
        "a height by ion mixes them"
    ),
    "target_compound_id": "the matched compound's ID; group by it, formulas repeat",
    "target_compound_name": "the matched compound's name; NaN when unmatched",
    "target_compound_formula": (
        "the matched compound's neutral formula; NaN when unmatched; rows are its lines"
    ),
    "ionization_mechanism": "the matched ion's ionization mechanism, by name",
    "ionization_mechanism_id": (
        "the matched ion's ionization mechanism's ID; a peak list has the name only"
    ),
    "target_collection_ids": "the target collections the matched compound is in",
    "target_collection_names": "their names, aligned with the IDs; group by the IDs",
    "time": (
        "seconds on the sample file's clock, as describe's t_range_s; a "
        "sample's first point is at its window's start, not 0"
    ),
    "stage": "the stage's 0-based index in the stages asked for",
    "stage_name": "the stage's name, as given; None when none was",
    "t_min": (
        "the stage's start, seconds on the sample file's clock (t_range_s); a "
        "start before t0 is moved to t0"
    ),
    "t_max": "the stage's end, seconds on the sample file's clock (t_range_s)",
}

#: The match tree's own columns: one row per isotope line Mascope looked
#: for, every score and category Mascope's (:func:`mascope_ai.oracle.tree_rows`).
#: A line with no peak holds placeholders, named where they would mislead.
_TREE: dict[str, str] = {
    "candidate": "the formula as it was asked about",
    "target_isotope_id": "the ID of the line Mascope looked for, matched or not",
    "target_isotope_formula": (
        "the line Mascope looked for, matched or not; match_category_isotope says"
    ),
    "target_ion_id": "the ID of the ion Mascope looked for, matched or not",
    "target_ion_formula": (
        "the ion Mascope looked for, matched or not; match_category_ion says"
    ),
    "target_compound_id": "the candidate's compound ID in this answer, matched or not",
    "ionization_mechanism": "the mechanism of the ion Mascope looked for, by name",
    "ionization_mechanism_id": "the mechanism of the ion Mascope looked for, its ID",
    "origin": "where the candidate came from: given, mass_query or model",
    "mz": "the line's theoretical m/z; the measured one is sample_peak_mz",
    "match_score_compound": "Mascope's compound score: its best ion's",
    "match_score_ion": "Mascope's ion score: its lines' scores weighted by abundance",
    "match_score_isotope": (
        "Mascope's 0 to 1 score of the line on its peak; 0 when none or when the "
        "nearest failed the tolerances"
    ),
    "match_category_compound": (
        "Mascope's category for the compound: 2 probable, 1 possible, 0 unmatched"
    ),
    "match_category_ion": "Mascope's category for the ion: 2, 1 or 0",
    "match_category_isotope": "Mascope's category for the line: 2, 1 or 0",
    "match_isotope_id": "the ID of this match record",
    "intensity_compound": (
        "Mascope's sum of the compound's lines' peak intensities, heights "
        "(Orbitrap) or areas (TOF); m/z-gated lines count 0"
    ),
    "intensity_ion": (
        "sum of the ion's lines' intensities, heights (Orbitrap) or areas (TOF), "
        "m/z-gated lines 0; the app's batch point"
    ),
    "sample_peak_id": (
        "the nearest peak's ID, kept even when it failed the tolerances; empty "
        "when the line has no peak"
    ),
    "sample_peak_mz": (
        "the nearest peak's measured m/z; for a line with no peak, the "
        "theoretical mz again, a placeholder"
    ),
    "sample_peak_intensity": (
        "that peak's height (Orbitrap) or area (TOF); 0 when no peak or outside "
        "the m/z tolerance; a ratio miss keeps it, score 0"
    ),
    "sample_peak_intensity_relative": (
        "ratio to the main line's peak, before the m/z gate, may exceed 1; 0 if "
        "either line has no peak or no shared scan"
    ),
    "target_compound_name": (
        "always empty here: the tree scores formulas, not named compounds; "
        "match_category_compound says whether it matched"
    ),
    "target_compound_formula": (
        "the candidate's neutral formula, on every row; match_category_compound "
        "says whether it matched"
    ),
    "sample_peak_tof": "that peak's time-of-flight coordinate, TOF only; -1 when no peak",
    "match_mz_error": (
        "the peak's m/z against the line's theoretical m/z, in ppm; 0 when "
        "the line has no peak, a placeholder"
    ),
    "match_abundance_error": (
        "the peak's relative intensity against the line's theoretical "
        "abundance; 1 when no peak"
    ),
    "resolution": (
        "which set of target lines the line is from: HIGH, an Orbitrap's "
        "resolved lines; LOW, a TOF's merged ones"
    ),
    "signal_to_noise": "that peak's height over its noise estimate",
    "is_satellite": "whether that peak is a side-lobe artifact of a stronger peak",
    "n_scans": (
        "Orbitrap: how many of the scans averaged into that peak hold it; NaN "
        "when the line has no peak or no count"
    ),
    "n_scans_total": (
        "Orbitrap: how many scans that peak is averaged over; NaN when the line "
        "has no peak or no count"
    ),
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
        "peak_id": (
            "Mascope's ID of the peak; the same on every point of its series, one "
            "series per matched line"
        ),
        "datetime_utc": "the point's absolute time: the file's start plus time, UTC",
        "height": (
            f"the peak's height in one scan, at time; {_UNIT}. load_peaks' "
            "height is the sample's mean"
        ),
        "mz": "the peak's m/z, the same on every point of its series",
    },
    "load_peaks_summed": {
        "height": (
            "the peak's apex intensity, summed over the sample's scans "
            f"(average=False); {_UNIT}"
        ),
        "area": (
            "the peak's intensity integrated over its m/z extent, summed over the "
            "scans like height"
        ),
    },
    "load_spectrum": {
        "mz": "the profile point's m/z",
        "intensity": (
            f"the averaged profile at this m/z; {_UNIT}. Its apex is near the "
            "fitted height, not equal"
        ),
    },
    "load_peaks_by_stage": {
        "peak_id": (
            "Mascope's ID of the peak; one row per stage, repeated when the peak "
            "matched several lines"
        ),
        "height": f"the peak's apex intensity over the stage's scans only; {_UNIT}",
        "area": "the peak's area over the stage's scans only",
        "signal_to_noise": (
            "the sample's whole-window value, not the stage's: height over noise"
        ),
        "sparsity": (
            "the sample's whole-window value, not the stage's: the share of "
            "scans the peak is absent from"
        ),
        "n_scans": (
            "not served on a stage for now: its signal_to_noise is the whole "
            "sample's, which its own scan counts cannot pair with"
        ),
        "n_scans_total": "not served on a stage for now, as n_scans is not",
    },
    "match_compounds": _TREE,
}


def meaning(op: str | None, column: str) -> str | None:
    """What *column* means in the table *op* made; None when not known, and
    None under :data:`MIXED` and under ``derive`` itself (:data:`_UNRECORDED`):
    a derived column means something only through the load it came from."""
    if op in _UNRECORDED:
        return None
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
    it meant in the load named there, and nothing when that is
    :data:`MIXED`. A column nobody knows is left out.
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
