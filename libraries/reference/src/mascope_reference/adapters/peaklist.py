"""Reference-list adapter: one self-describing JSON list per file.

Reads the list format of :mod:`mascope_reference.peaklist` - schema 2, which
Mascope ships, and schema 1, the reference engine's own. A list is one source:
its ``id`` is the natural provenance name and its ``data_version`` the version,
which is how :func:`mascope_reference.seed.seed` loads a directory of them with
no flags, while ``mascope reference sync peaklist <file>`` loads one like any
other dump.

Each species becomes one record carrying only facts of its own - its name, and
its own DOI when the list compiles several papers. What belongs to the whole
list stays in the file: Stage A copies every identity, cross-references
included, into the provenance of each row whose formula it matches, so a list's
constants on every record would be written onto every matched peak of every
sample and read by nothing.

A radical is held back unless the list says ``allow_radicals``, which a
schema 1 list cannot say.
"""

from collections.abc import Iterator
from pathlib import Path

from mascope_reference.peaklist import admitted_species, read_peak_list
from mascope_reference.record import ReferenceRecord


class PeakListAdapter:
    """Adapter for reference list files (JSON, schema 1 or 2)."""

    name = "peaklist"
    #: For a list that names no licence of its own, which only schema 1 can do.
    license = "custom"

    def __init__(self, license: str | None = None) -> None:
        """:param license: The licence of the source row an ingest records;
        :func:`mascope_reference.seed.seed` passes the list's own."""
        if license is not None:
            self.license = license

    def parse(self, path: Path) -> Iterator[ReferenceRecord]:
        peak_list = read_peak_list(path)
        license_tag = peak_list.license or self.license
        for species in admitted_species(peak_list):
            yield ReferenceRecord(
                formula=species.formula,
                name=species.name,
                source=self.name,
                # A list names each identity once - a formula repeats only as
                # isomers under different names - so formula and name together
                # identify the row within its source.
                source_native_id=(
                    species.formula
                    if species.name is None
                    else f"{species.formula} {species.name}"
                ),
                xrefs={"reference": species.reference} if species.reference else {},
                license=license_tag,
            )
