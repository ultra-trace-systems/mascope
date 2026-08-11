"""HMDB adapter.

Reads the HMDB metabolites XML bulk dump (``hmdb_metabolites.xml``). Human
metabolites. Free with attribution - commercial terms should be verified before
any commercial use, which is exactly why the per-record license tag is carried.

The dump is a single multi-gigabyte XML document, so it is parsed with
``iterparse`` and each finished ``<metabolite>`` is dropped from the root as it
is consumed, which is what actually keeps memory bounded.
"""

from collections.abc import Iterator
from pathlib import Path
from xml.etree.ElementTree import Element, iterparse

from mascope_reference.adapters._io import _open_binary
from mascope_reference.record import ReferenceRecord


def _localname(tag: str) -> str:
    """Strip any XML namespace prefix from a tag, e.g. '{ns}name' -> 'name'."""
    return tag.rsplit("}", 1)[-1]


def _child_text(element: Element, name: str) -> str | None:
    """First direct child with the given local name, its text stripped."""
    for child in element:
        if _localname(child.tag) == name:
            text = (child.text or "").strip()
            return text or None
    return None


class HmdbAdapter:
    """Adapter for HMDB metabolites XML dumps."""

    name = "hmdb"
    license = "hmdb-attribution"

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        with _open_binary(path) as handle:
            context = iterparse(handle, events=("start", "end"))
            # The root is needed to actually free finished elements: clear() empties
            # a subtree but the root keeps a reference to every child it has seen,
            # so on a document with hundreds of thousands of metabolites the element
            # list grows for the whole parse regardless. Dropping each finished
            # child from the root is what bounds memory.
            root = None
            for event, element in context:
                if root is None and event == "start":
                    root = element
                    continue
                if event != "end" or _localname(element.tag) != "metabolite":
                    continue
                accession = _child_text(element, "accession")
                formula = _child_text(element, "chemical_formula")
                if accession and formula:
                    yield ReferenceRecord(
                        formula=formula,
                        inchikey=_child_text(element, "inchikey"),
                        name=_child_text(element, "name"),
                        smiles=_child_text(element, "smiles"),
                        inchi=_child_text(element, "inchi"),
                        source=self.name,
                        source_native_id=accession,
                        xrefs={"hmdb_id": accession},
                        license=self.license,
                    )
                element.clear()
                if root is not None:
                    root.clear()
