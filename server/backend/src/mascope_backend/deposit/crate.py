"""
The package as an RO-Crate: ``ro-crate-metadata.json``, the standards-facing
view of what ``manifest.json`` says.

`RO-Crate <https://w3id.org/ro/crate/1.2>`_ is JSON-LD in schema.org terms,
which data repositories, harvesters and generic viewers read without knowing
Mascope. ``manifest.json`` stays the package's own record, in the demo
bundle's vocabulary, and the crate is rendered from it and from the files
written before it, so it says nothing the manifest does not:

- the root ``Dataset`` is the dataset: its name, description, licence,
  authors (a person's ORCID iD is their ``@id``), keywords, funding, related
  identifiers, global id, the time span its files were acquired in, and the
  PSI-MS terms of what measured them;
- every file of the archive is a ``File`` with its SHA-256 and size, each
  directory a ``Dataset`` holding its files, all reachable from the root
  through ``hasPart``;
- a ``CreateAction`` says which software wrote the package, and when.

A related identifier's DataCite relation becomes the schema.org property
closest to it (:data:`RELATION_PROPERTIES`), ``citation`` where none is
closer; the entity's description keeps the relation as written. Its ``@id``
is the work's IRI, a DOI's at doi.org. An IRI the crate has an entity by
already is that entity: Mascope's repository is the software, an author's
ORCID record the author, and a work given under two relations one work that
names both. RO-Crate cites by absolute URI alone, so a work cited without an
IRI is one the dataset ``mentions``.

The crate is written after ``manifest.json``, so it carries the manifest's
checksum, and before ``SHA256SUMS``, which checks the crate too. It gives
``SHA256SUMS`` its size but no checksum: that file holds the crate's own.
"""

import re
from urllib.parse import quote

from mascope_backend.deposit.documents import LICENCE_URLS, MASCOPE_DOI


#: The RO-Crate release the crate conforms to.
RO_CRATE_VERSION = "1.2"

#: The crate's file name, which RO-Crate fixes.
METADATA_FILE = "ro-crate-metadata.json"

#: Where Mascope's source is: the software entity's ``@id`` and ``url``.
MASCOPE_URL = "https://github.com/ultra-trace-systems/mascope"

#: The SPDX names of the licences the dataset dialog suggests, whose
#: identifiers are the dataset's ``license`` values.
SPDX_NAMES = {
    "CC-BY-4.0": "Creative Commons Attribution 4.0 International",
    "CC0-1.0": "Creative Commons Zero v1.0 Universal",
    "CC-BY-NC-4.0": "Creative Commons Attribution Non Commercial 4.0 International",
    "CC-BY-SA-4.0": "Creative Commons Attribution Share Alike 4.0 International",
}

#: The schema.org property for a DataCite relation, by the relation in lower
#: case. Any other relation is a ``citation``: a reference to another work.
RELATION_PROPERTIES = {
    "isderivedfrom": "isBasedOn",
    "isnewversionof": "isBasedOn",
    "isversionof": "isBasedOn",
    "ispartof": "isPartOf",
}

#: The ``encodingFormat`` of a file, by its extension. A Thermo raw file has
#: no registered media type.
_FORMATS = {
    "md": "text/markdown",
    "json": "application/json",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "h5": "application/x-hdf5",
    "raw": "application/octet-stream",
}

#: The package's directories, as the crate names and describes them.
_DIRECTORIES = {
    "raw/": (
        "Raw files",
        "Each sample file's raw file, as it was uploaded, and the acquisition "
        "record the instrument's control program wrote beside it.",
    ),
    "batches/": (
        "Batch spreadsheets",
        "Each batch's spreadsheet, as the app's spreadsheet export writes it.",
    ),
    "target_collections/": (
        "Target collections",
        "The compounds searched for, with their identity, and the ions formed "
        "from them.",
    ),
}

#: The PSI-MS ontology, as a term's ``inDefinedTermSet`` names it.
_PSI_MS = "http://purl.obolibrary.org/obo/ms.owl"

#: What a relative ``@id`` keeps as it is: what a URI path allows, and every
#: character outside ASCII, which RO-Crate 1.2 keeps unencoded.
_ID_SAFE = re.compile(r"[A-Za-z0-9\-._~!$&'()*+,;=@/]|[^\x00-\x7f]")

#: What a DOI keeps as it is in its doi.org IRI: what a URI path allows, and
#: every character outside ASCII. A ``#``, ``?`` or ``%`` is encoded: in a DOI
#: it is a character of the name, not a fragment, a query or an escape.
_DOI_SAFE = re.compile(r"[A-Za-z0-9\-._~!$&'()*+,;=:@/]|[^\x00-\x7f]")

#: What a URL keeps as it is: all but what no IRI can hold. Its ``#``, ``?``
#: and ``%`` mean what they mean in the URL as it was typed.
_URL_SAFE = re.compile(r'[^\x00-\x20<>"{}|\\^`\x7f]')


def render_crate(
    manifest: dict, written: list[tuple[str, str, int]], sums_size: int
) -> dict:
    """
    ``ro-crate-metadata.json``: the package's manifest as an RO-Crate.

    :param manifest: The package's manifest, its checksums filled in.
    :param written: Every file written to the package so far, in archive
        order, as ``(path, sha256, bytes)``: everything but the crate and
        ``SHA256SUMS``.
    :param sums_size: The size ``SHA256SUMS`` will have, the crate's line
        in it included: its checksum cannot be known before the crate is.
    :return: The crate's JSON-LD document.
    :rtype: dict
    """
    graph = _Graph()
    dataset = manifest["dataset"]
    root: dict = {
        "@id": "./",
        "@type": "Dataset",
        "name": dataset["dataset_name"],
        "description": dataset["dataset_description"] or "No description was recorded.",
        "datePublished": manifest["created"],
        "dateCreated": dataset.get("dataset_utc_created"),
        "dateModified": dataset.get("dataset_utc_modified"),
        "identifier": graph.add(
            {
                "@id": "#identifier",
                "@type": "PropertyValue",
                "name": "Mascope dataset id",
                "propertyID": "mascope-global-id"
                if dataset.get("dataset_global_id")
                else "mascope-id",
                "value": dataset.get("dataset_global_id") or dataset["dataset_id"],
            }
        ),
        "license": graph.add(_licence(dataset.get("license"))),
        "author": [
            graph.add(_person(author, n, graph))
            for n, author in enumerate(dataset.get("authors") or [], start=1)
        ],
        "keywords": list(dataset.get("keywords") or []),
        "temporalCoverage": _span(manifest["measurement"]["acquired"]),
        "measurementTechnique": _techniques(manifest, graph),
    }
    grants, funders = [], []
    for n, entry in enumerate(dataset.get("funding") or [], start=1):
        funder = graph.organization(entry["funder"])
        grants.append(
            graph.add(
                {
                    "@id": f"#grant-{n}",
                    "@type": "Grant",
                    "name": f"{entry['funder']}, {entry['award']}"
                    if entry.get("award")
                    else f"Funding from {entry['funder']}",
                    "identifier": entry.get("award"),
                    "funder": funder,
                }
            )
        )
        if funder not in funders:
            funders.append(funder)
    root["funding"] = grants
    root["funder"] = funders
    root["hasPart"] = _data_entities(manifest, written, sums_size, graph)
    root["mentions"] = [graph.add(_action(manifest, written, graph))]
    _add_related(root, dataset.get("related_identifiers") or [], graph)

    descriptor = {
        "@id": METADATA_FILE,
        "@type": "CreativeWork",
        "conformsTo": {"@id": f"https://w3id.org/ro/crate/{RO_CRATE_VERSION}"},
        "about": {"@id": "./"},
    }
    return {
        "@context": f"https://w3id.org/ro/crate/{RO_CRATE_VERSION}/context",
        "@graph": [_compact(entity) for entity in [descriptor, root, *graph.entities]],
    }


class _Graph:
    """The crate's entities other than its descriptor and root, each once."""

    def __init__(self):
        self.entities: list[dict] = []
        self._ids: set[str] = set()
        self._organizations: dict[str, dict] = {}

    def add(self, entity: dict) -> dict:
        """Add an entity, unless one by its ``@id`` is in. :return: A
        reference to it."""
        if entity["@id"] not in self._ids:
            self._ids.add(entity["@id"])
            self.entities.append(entity)
        return {"@id": entity["@id"]}

    def organization(self, name: str) -> dict:
        """An organization by its name, one entity however often it is
        named. :return: A reference to it."""
        if name not in self._organizations:
            self._organizations[name] = self.add(
                {
                    "@id": f"#organization-{len(self._organizations) + 1}",
                    "@type": "Organization",
                    "name": name,
                }
            )
        return self._organizations[name]


def _licence(licence: str | None) -> dict:
    """
    The dataset's licence as an entity. A licence the dataset dialog suggests
    is its SPDX entry; any other, ``proprietary`` or none at all, is
    described as it is recorded, and ``DATA_LICENSE`` says the same.
    """
    if licence in SPDX_NAMES:
        return {
            "@id": f"https://spdx.org/licenses/{licence}",
            "@type": "CreativeWork",
            "name": SPDX_NAMES[licence],
            "identifier": licence,
            "description": f"The SPDX licence {licence}; its terms are at "
            f"{LICENCE_URLS[licence]}.",
            "url": LICENCE_URLS[licence],
        }
    if licence is None:
        return {
            "@id": "#license",
            "@type": "CreativeWork",
            "name": "No licence recorded",
            "description": "No licence was recorded for this dataset. Without "
            "one nobody but its authors may reuse the data.",
        }
    return {
        "@id": "#license",
        "@type": "CreativeWork",
        "name": licence,
        "description": f"The dataset's licence, as recorded: {licence}.",
    }


def _person(author: dict, n: int, graph: _Graph) -> dict:
    """An author, identified by their ORCID iD where they have one."""
    orcid = author.get("orcid")
    return {
        "@id": f"https://orcid.org/{orcid}" if orcid else f"#author-{n}",
        "@type": "Person",
        "name": author["name"],
        "affiliation": graph.organization(author["affiliation"])
        if author.get("affiliation")
        else None,
    }


def _add_related(root: dict, entries: list[dict], graph: _Graph) -> None:
    """
    The related identifiers, each under the property closest to its
    relation. Added last, so that a work whose IRI is an entity of the crate
    already, Mascope's repository say, refers to that entity rather than
    replacing it.
    """
    works: dict[str, tuple[dict, list[str], str | None]] = {}
    for n, entry in enumerate(entries, start=1):
        work = _related(entry, n)
        prop = RELATION_PROPERTIES.get(entry["relation"].lower(), "citation")
        if prop == "citation" and work["@id"].startswith("#"):
            prop = "mentions"
        if work["@id"] in works:
            work, relations, kind = works[work["@id"]]
            relations.append(entry["relation"])
            work["description"] = _relation_text(relations, kind)
        else:
            works[work["@id"]] = (
                work,
                [entry["relation"]],
                entry.get("identifier_type"),
            )
        reference = graph.add(work)
        if reference not in root.setdefault(prop, []):
            root[prop].append(reference)


def _related(entry: dict, n: int) -> dict:
    """A related identifier as a work, its ``@id`` its IRI where it has one."""
    identifier = entry["identifier"].strip()
    kind = entry.get("identifier_type")
    return {
        "@id": _related_iri(identifier, kind) or f"#related-{n}",
        "@type": "CreativeWork",
        "name": identifier,
        "identifier": identifier,
        "description": _relation_text([entry["relation"]], kind),
    }


def _relation_text(relations: list[str], kind: str | None) -> str:
    """A related work's description: how it relates to the dataset."""
    return f"Related to the dataset as {' and '.join(relations)} (DataCite)" + (
        f"; a {kind}." if kind else "."
    )


def _related_iri(identifier: str, kind: str | None) -> str | None:
    """
    A related identifier's IRI: a URL as typed, a DOI at doi.org (with or
    without its ``doi:`` prefix, in any case), what an IRI cannot hold
    percent-encoded. :return: None for any other identifier.
    """
    if re.match(r"https?://", identifier, re.IGNORECASE):
        return _encode(identifier, _URL_SAFE)
    doi = re.sub(r"^doi:\s*", "", identifier, flags=re.IGNORECASE)
    if (kind or "").upper() == "DOI" or re.match(r"10\.\d+/", doi):
        return "https://doi.org/" + _encode(doi, _DOI_SAFE)
    return None


def _span(acquired: dict) -> str | None:
    """The time the files were acquired in, as an ISO 8601 interval."""
    if not acquired.get("first"):
        return None
    return f"{acquired['first']}/{acquired['last']}"


def _techniques(manifest: dict, graph: _Graph) -> list[dict]:
    """The PSI-MS terms of what measured the files: models, ion sources,
    mass analysers and detectors, each once."""
    references = []
    for key in ("instrument_model", "ion_source", "mass_analyser", "detector"):
        for entry in manifest["raw"]:
            term = entry["cv_terms"][key]
            if term is None:
                continue
            reference = graph.add(_term(term, manifest["vocabulary"]["PSI-MS"], graph))
            if reference not in references:
                references.append(reference)
    return references


def _term(term: dict, version: str, graph: _Graph) -> dict:
    """A PSI-MS term, at its OBO PURL."""
    graph.add(
        {
            "@id": _PSI_MS,
            "@type": "DefinedTermSet",
            "name": "PSI-MS controlled vocabulary",
            "version": version,
            "url": "https://github.com/HUPO-PSI/psi-ms-CV",
        }
    )
    return {
        "@id": "http://purl.obolibrary.org/obo/" + term["accession"].replace(":", "_"),
        "@type": "DefinedTerm",
        "name": term["name"],
        "termCode": term["accession"],
        "inDefinedTermSet": {"@id": _PSI_MS},
    }


def _data_entities(
    manifest: dict, written: list[tuple[str, str, int]], sums_size: int, graph: _Graph
) -> list[dict]:
    """
    Every file written, and a directory for each directory they are in.

    :return: What the root holds: its files and its directories, in the
        archive's order.
    """
    described = _descriptions(manifest)
    top: list[dict] = []
    directories: dict[str, dict] = {}
    for path, digest, size in [*written, ("SHA256SUMS", None, sums_size)]:
        name, description = described.get(path, (path.rsplit("/", 1)[-1], None))
        file = graph.add(
            {
                "@id": _path_id(path),
                "@type": "File",
                "name": name,
                "description": description,
                "encodingFormat": _FORMATS.get(path.rsplit(".", 1)[-1], "text/plain"),
                "contentSize": str(size) if size is not None else None,
                "sha256": digest,
            }
        )
        directory, _, _ = path.rpartition("/")
        if not directory:
            top.append(file)
            continue
        directory += "/"
        if directory not in directories:
            name, description = _DIRECTORIES.get(directory, (directory, None))
            directories[directory] = {
                "@id": _path_id(directory),
                "@type": "Dataset",
                "name": name,
                "description": description,
                "hasPart": [],
            }
            top.append(graph.add(directories[directory]))
        directories[directory]["hasPart"].append(file)
    return top


def _descriptions(manifest: dict) -> dict[str, tuple[str, str]]:
    """Each file's name and description in the crate, by its path."""
    described = {
        "README.md": ("README.md", "The dataset, for a person."),
        "DATA_LICENSE": ("DATA_LICENSE", "The licence of the data."),
        "manifest.json": (
            "manifest.json",
            "Everything this crate says and more, in Mascope's own vocabulary "
            f"(schema {manifest['schema']}): every record's id, the "
            "acquisition context of every file and the provenance block.",
        ),
        "SHA256SUMS": (
            "SHA256SUMS",
            "The SHA-256 of every other file, as sha256sum -c reads them.",
        ),
        manifest["instrument_functions"]["path"]: (
            "Instrument functions",
            "The peak shape and resolution each raw file was processed with.",
        ),
    }
    for entry in manifest["raw"]:
        if entry["included"]:
            when = entry.get("datetime_utc")
            where = entry.get("instrument")
            described[entry["path"]] = (
                entry["filename"],
                "Raw file of sample file "
                + entry["filename"]
                + (f", acquired {when}" if when else "")
                + (f" on {where}" if where else "")
                + ".",
            )
        if entry["acquisition"]:
            described[entry["acquisition"]["path"]] = (
                f"Acquisition record of {entry['filename']}",
                "The acquisition record the instrument's control program wrote "
                "beside the raw file (schema mascope-acquisition/1), without "
                "the computer it names.",
            )
    for batch in manifest["batches"]:
        described[batch["spreadsheet"]["path"]] = (
            f"Batch {batch['sample_batch_name']}",
            f"The spreadsheet of batch {batch['sample_batch_name']}: "
            f"{len(batch['samples'])} sample(s), the compounds and ions matched "
            "in them, and the provenance of the export.",
        )
    for collection in manifest["target_collections"]:
        described[collection["path"]] = (
            f"Target collection {collection['target_collection_name']}",
            f"Used as {', '.join(collection['used_as'])}: "
            f"{collection['target_compounds']} compound(s), "
            f"{collection['target_ions']} ion(s).",
        )
    return described


def _action(manifest: dict, written: list[tuple[str, str, int]], graph: _Graph) -> dict:
    """
    Writing the package: Mascope, and the raw-file reader where it is
    installed, as its instruments; the raw files as its object; every file
    Mascope made as its result.
    """
    produced = manifest["produced_with"]
    deployment = manifest["provenance"].get("deployment_id")
    instruments = [
        graph.add(
            {
                "@id": MASCOPE_URL,
                "@type": "SoftwareApplication",
                "name": "Mascope",
                "url": MASCOPE_URL,
                "version": produced["mascope_version"],
                "identifier": f"https://doi.org/{MASCOPE_DOI}",
                "license": graph.add(
                    {
                        "@id": "https://spdx.org/licenses/Apache-2.0",
                        "@type": "CreativeWork",
                        "name": "Apache License 2.0",
                        "identifier": "Apache-2.0",
                        "description": "The SPDX licence Apache-2.0, Mascope's "
                        "own: it covers the software, not the data.",
                    }
                ),
            }
        )
    ]
    reader = produced.get("opentfraw_version")
    if reader:
        distribution, _, version = reader.partition("==")
        instruments.append(
            graph.add(
                {
                    "@id": f"https://pypi.org/project/{distribution}/",
                    "@type": "SoftwareApplication",
                    "name": "OpenTFRaw",
                    "url": f"https://pypi.org/project/{distribution}/",
                    "version": version,
                }
            )
        )
    raw = {entry["path"] for entry in manifest["raw"] if entry["included"]}
    return {
        "@id": "#package",
        "@type": "CreateAction",
        "name": "Package written by Mascope",
        "description": (
            f"Mascope {produced['mascope_version']} wrote this package"
            + (f" on deployment {deployment}" if deployment else "")
            + f". Match score version {produced['match_score_version']}, peak "
            f"assignment engine {produced['peak_assignment_engine_version']}. "
            "The results in the spreadsheets were computed when each batch "
            "was processed, by the build that ran then."
        ),
        "endTime": manifest["created"],
        "instrument": instruments,
        "object": [{"@id": _path_id(path)} for path, _, _ in written if path in raw],
        "result": [
            {"@id": _path_id(path)}
            for path in [p for p, _, _ in written] + ["SHA256SUMS"]
            if path not in raw
        ],
    }


def _path_id(path: str) -> str:
    """A path in the package as a relative ``@id``: what a URI cannot hold,
    a space among it, percent-encoded."""
    return _encode(path, _ID_SAFE)


def _encode(text: str, safe: re.Pattern) -> str:
    """``text`` with each character ``safe`` does not match percent-encoded."""
    return "".join(
        char if safe.fullmatch(char) else quote(char, safe="") for char in text
    )


def _compact(entity: dict) -> dict:
    """An entity as RO-Crate 1.2 prefers it: no empty values, and a list of
    one written as its one value."""
    compacted = {}
    for key, value in entity.items():
        if isinstance(value, list):
            if len(value) == 1:
                value = value[0]
            elif not value:
                continue
        if value is None:
            continue
        compacted[key] = value
    return compacted
