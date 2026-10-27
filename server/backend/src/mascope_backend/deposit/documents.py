"""
The package's two documents for a person: ``README.md`` and ``DATA_LICENSE``.

Both are rendered from the manifest and from nothing else, so they cannot say
anything ``manifest.json`` does not. The README answers, in this order, what
the dataset is, what was measured, how it was processed, with which software,
and what each file is; the licence file says what a reader may do with the
data, and that the software is licensed apart from it.
"""

from collections import Counter


#: Mascope's concept DOI (``CITATION.cff``), which resolves to its latest
#: release: what a deposit cites for the software.
MASCOPE_DOI = "10.5281/zenodo.21037634"

#: Where the licences the dataset dialog suggests are written out.
LICENCE_URLS = {
    "CC-BY-4.0": "https://creativecommons.org/licenses/by/4.0/",
    "CC0-1.0": "https://creativecommons.org/publicdomain/zero/1.0/",
    "CC-BY-NC-4.0": "https://creativecommons.org/licenses/by-nc/4.0/",
    "CC-BY-SA-4.0": "https://creativecommons.org/licenses/by-sa/4.0/",
}

#: How many raw files the README names where it lists them; the manifest
#: lists every one.
_LISTED = 20


def render_licence(manifest: dict) -> str:
    """
    ``DATA_LICENSE``: the dataset's licence, or that it has none.

    :param manifest: The package's manifest.
    :return: The file's text.
    :rtype: str
    """
    dataset = manifest["dataset"]
    licence = dataset.get("license")
    lines = [dataset["dataset_name"], ""]
    if licence is None:
        lines += [
            "No licence was recorded for this dataset.",
            "",
            "Without a licence nobody but its authors may reuse the data. Set "
            "one on the dataset in Mascope and build the package again before "
            "depositing it.",
        ]
    elif licence == "proprietary":
        lines += [
            "Licence: proprietary.",
            "",
            "The authors keep every right to the data. Ask them before reusing it.",
        ]
    else:
        lines += [f"Licence: {licence}"]
        if licence in LICENCE_URLS:
            lines += [LICENCE_URLS[licence]]
    lines += [
        "",
        "This licence covers the data in this package: the raw files, the "
        "spreadsheets and the documents that describe them. Mascope, the "
        "software that wrote the package, is licensed apart from it, under "
        "Apache-2.0.",
    ]
    return "\n".join(lines) + "\n"


def render_readme(manifest: dict) -> str:
    """
    ``README.md``: the dataset, for a person.

    :param manifest: The package's manifest.
    :return: The file's Markdown.
    :rtype: str
    """
    dataset = manifest["dataset"]
    lines = [f"# {_inline(dataset['dataset_name'])}", ""]
    lines += [dataset["dataset_description"] or "_No description was recorded._", ""]
    lines += _record_table(dataset)
    lines += _measured(manifest)
    lines += _processed(manifest)
    lines += _software(manifest)
    lines += _files(manifest)
    lines += _not_included(manifest)
    return "\n".join(lines).rstrip("\n") + "\n"


def _record_table(dataset: dict) -> list[str]:
    """The metadata record as a two-column table, every row always there."""

    def authors() -> str | None:
        rendered = []
        for author in dataset.get("authors") or []:
            text = _inline(author["name"])
            if author.get("orcid"):
                orcid = _inline(author["orcid"])
                text += f" (ORCID [{orcid}](https://orcid.org/{orcid}))"
            if author.get("affiliation"):
                text += f", {_inline(author['affiliation'])}"
            rendered.append(text)
        return "; ".join(rendered) or None

    def related() -> str | None:
        rendered = []
        for entry in dataset.get("related_identifiers") or []:
            text = f"{_inline(entry['relation'])}: {_inline(entry['identifier'])}"
            if entry.get("identifier_type"):
                text += f" ({_inline(entry['identifier_type'])})"
            rendered.append(text)
        return "; ".join(rendered) or None

    def funding() -> str | None:
        rendered = []
        for entry in dataset.get("funding") or []:
            text = _inline(entry["funder"])
            if entry.get("award"):
                text += f", {_inline(entry['award'])}"
            rendered.append(text)
        return "; ".join(rendered) or None

    keywords = ", ".join(_inline(word) for word in dataset.get("keywords") or [])
    rows = [
        ("Licence", _inline(dataset.get("license")) or "Not recorded"),
        ("Authors", authors() or "Not recorded"),
        ("Keywords", keywords or "None"),
        ("Related identifiers", related() or "None"),
        ("Funding", funding() or "None"),
        (
            "Dataset id",
            f"`{dataset.get('dataset_global_id') or dataset['dataset_id']}`",
        ),
    ]
    table = ["| | |", "|---|---|"]
    table += [f"| {label} | {value} |" for label, value in rows]
    return table + [""]


def _measured(manifest: dict) -> list[str]:
    raw = manifest["raw"]
    measurement = manifest["measurement"]
    lines = ["## What was measured", ""]
    if not raw:
        return lines + ["The dataset holds no samples.", ""]
    acquired = measurement["acquired"]
    lines.append(
        f"- {_count(len(raw), 'sample file')} from "
        f"{_list(measurement['instruments'])}"
        + (
            f", acquired from {acquired['first']} to {acquired['last']} (UTC)."
            if acquired["first"]
            else "."
        )
    )
    for label, key in (
        ("Instrument models", "instrument_model"),
        ("Ion sources", "ion_source"),
        ("Mass analysers", "mass_analyser"),
        ("Detectors", "detector"),
    ):
        terms = _terms(entry["cv_terms"][key] for entry in raw)
        if terms:
            lines.append(f"- {label}: {terms}.")
    polarities = _terms(term for entry in raw for term in entry["cv_terms"]["polarity"])
    if polarities:
        lines.append(f"- Polarities: {polarities}.")
    lines.append(
        "- Terms are from the PSI-MS controlled vocabulary, release "
        f"{manifest['vocabulary']['PSI-MS']}."
    )
    return lines + [""]


def _processed(manifest: dict) -> list[str]:
    lines = ["## How it was processed", ""]
    collections = {
        entry["target_collection_id"]: entry for entry in manifest["target_collections"]
    }
    for mode in manifest["ionization_modes"]:
        mechanisms = [
            mechanism["ionization_mechanism"] or mechanism["ionization_mechanism_id"]
            for mechanism in mode["ionization_mechanisms"]
        ]
        text = (
            f"- Ionization mode **{_inline(mode['ionization_mode_name'])}**, "
            f"polarity {mode['ionization_mode_polarity']}"
        )
        if mechanisms:
            text += f", ions formed as {_list(mechanisms)}"
        calibration = collections.get(mode["calibration_collection_id"])
        if calibration:
            text += (
                ", m/z calibrated against "
                f"**{_inline(calibration['target_collection_name'])}**"
            )
        lines.append(text + ".")
    for batch in manifest["batches"]:
        names = [
            collections[collection_id]["target_collection_name"]
            for collection_id in batch["target_collection_ids"]
            if collection_id in collections
        ]
        lines.append(
            f"- Batch **{_inline(batch['sample_batch_name'])}**: "
            f"{_count(len(batch['samples']), 'sample')}"
            + (f", matched against {_list(names, bold=True)}" if names else "")
            + f". Results: `{batch['spreadsheet']['path']}`."
        )
    for collection in manifest["target_collections"]:
        lines.append(
            f"- Target collection **{_inline(collection['target_collection_name'])}**"
            f" ({', '.join(collection['used_as'])}): "
            f"{_count(collection['target_compounds'], 'compound')}, "
            f"{_count(collection['target_ions'], 'ion')}. "
            f"`{collection['path']}`."
        )
    if len(lines) == 2:
        lines.append("Nothing was processed: the dataset holds no batches.")
    return lines + [""]


def _software(manifest: dict) -> list[str]:
    produced = manifest["produced_with"]
    provenance = manifest["provenance"]
    lines = [
        "## Software",
        "",
        f"- Packaged on {manifest['created']} by Mascope "
        f"{produced['mascope_version']}"
        + (
            f", deployment `{provenance['deployment_id']}`."
            if provenance.get("deployment_id")
            else "."
        ),
        f"- Raw file reader: {produced.get('opentfraw_version') or 'unknown'}. "
        f"Match score version {produced['match_score_version']}, peak "
        f"assignment engine {produced['peak_assignment_engine_version']}.",
        "- These are the versions of the build that wrote the package. The "
        "results in the spreadsheets were computed when each batch was "
        "processed, by the build that ran then.",
        f"- Cite Mascope as <https://doi.org/{MASCOPE_DOI}>.",
        "",
    ]
    return lines


def _files(manifest: dict) -> list[str]:
    rows = [
        ("`README.md`", "This file."),
        ("`DATA_LICENSE`", "The licence of the data."),
        (
            "`manifest.json`",
            "Everything this file says and more, for a program, with the "
            "SHA-256 and size of every file it names. Its schema is "
            f"`{manifest['schema']}`.",
        ),
        (
            "`SHA256SUMS`",
            "The SHA-256 of every other file. In the package's directory, "
            "`sha256sum -c SHA256SUMS` checks them all.",
        ),
    ]
    if any(entry["included"] for entry in manifest["raw"]):
        rows.append(
            (
                "`raw/`",
                "Each sample file's raw file, as it was uploaded. Its header is "
                "unchanged and may name the instrument and the computer that "
                "acquired it.",
            )
        )
    if any(entry["acquisition"] for entry in manifest["raw"]):
        rows.append(
            (
                "`raw/*.mascope.json`",
                "The acquisition record the instrument's control program wrote "
                "beside a raw file (schema `mascope-acquisition/1`), without "
                "the computer it names (`machine`).",
            )
        )
    if manifest["batches"]:
        rows.append(
            (
                "`batches/`",
                "Each batch's spreadsheet: its samples, their acquisition "
                "context, the compounds and ions matched in them, and the "
                "provenance of the export.",
            )
        )
    if manifest["target_collections"]:
        rows.append(
            (
                "`target_collections/`",
                "The compounds searched for, with their CAS number, InChIKey, "
                "InChI and SMILES where recorded, and the ions formed from them.",
            )
        )
    rows.append(
        (
            "`instrument_functions.json`",
            "The peak shape and resolution each raw file was processed with.",
        )
    )
    table = ["## Files", "", "| Path | What it holds |", "|---|---|"]
    table += [f"| {path} | {text} |" for path, text in rows]
    return table + [""]


def _not_included(manifest: dict) -> list[str]:
    missing = [entry["filename"] for entry in manifest["raw"] if not entry["included"]]
    if not missing:
        return []
    lines = [
        "## Not included",
        "",
        f"Mascope keeps no raw file of {_count(len(missing), 'sample file')}, "
        "only the data converted from it, so the package describes "
        f"{'it' if len(missing) == 1 else 'them'} without the file:",
        "",
    ]
    lines += [f"- `{name}`" for name in missing[:_LISTED]]
    if len(missing) > _LISTED:
        lines.append(f"- and {len(missing) - _LISTED} more, listed in `manifest.json`.")
    return lines + [""]


def _terms(terms) -> str:
    """Distinct PSI-MS terms as ``name (accession)``, the commonest first."""
    counted = Counter(f"{term['name']} ({term['accession']})" for term in terms if term)
    return ", ".join(text for text, _ in sorted(counted.items(), key=_by_count))


def _by_count(item: tuple[str, int]) -> tuple[int, str]:
    return -item[1], item[0]


def _count(n: int, noun: str) -> str:
    return f"{n} {noun}" if n == 1 else f"{n} {noun}s"


def _list(values: list[str], bold: bool = False) -> str:
    if not values:
        return "no instrument named"
    rendered = [f"**{_inline(value)}**" if bold else _inline(value) for value in values]
    return ", ".join(rendered)


def _inline(text: str | None) -> str:
    """User text on one line, with the characters Markdown would act on
    escaped."""
    text = " ".join(str(text or "").split())
    for char in "\\`*_[]<>|":
        text = text.replace(char, f"\\{char}")
    return text
