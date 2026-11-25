"""Which samples read a source: the two facts of a list's header a row records.

A reference list says where it applies (:mod:`mascope_reference.peaklist`):
``applies_to_contexts`` names its regimes, and ``always_active`` says it is
read for every sample. Its ``reference_source`` row records both, written at
load from the header, so the environment a sample is tagged with can select
the lists it is read against without the file at hand
(``docs/dev/sample_filters_and_metadata.md``, section 4.6).

A null is what the header leaves out. A list that declares no regimes keeps
``applies_to_contexts`` null, and one that does not say ``always_active``
keeps that null, as does every database mirror and hand-loaded CSV: none of
them declares anything, and a source that declares no regimes is read for
every sample. A row loaded before rows recorded them is null too, and reads
the same way, as Stage A reads every source today; ``reference seed`` writes
a shipped list's facts on its active row without reloading it.
"""

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class SourceSelection:
    """What a source row records of the samples that read it.

    :param applies_to_contexts: The regimes the source declares, in the order
        its header names them; None where it declares none.
    :param always_active: True where the header says the source is read for
        every sample; None where it does not say so.
    """

    applies_to_contexts: tuple[str, ...] | None = None
    always_active: bool | None = None

    @classmethod
    def declared(
        cls, applies_to_contexts: Sequence[str] = (), always_active: bool = False
    ) -> "SourceSelection":
        """The selection a header's two keys make, nulls for what it leaves out.

        :param applies_to_contexts: The regimes the header names, maybe none.
        :param always_active: Whether the header says ``always_active: true``.
        """
        return cls(
            applies_to_contexts=tuple(applies_to_contexts) or None,
            always_active=True if always_active else None,
        )

    def row_values(self) -> dict:
        """The two columns as a row is written with them."""
        return {
            "applies_to_contexts": (
                list(self.applies_to_contexts)
                if self.applies_to_contexts is not None
                else None
            ),
            "always_active": self.always_active,
        }

    def describe(self) -> str:
        """One line for a load's report."""
        parts = []
        if self.applies_to_contexts:
            parts.append("regimes " + ", ".join(self.applies_to_contexts))
        else:
            parts.append("no regimes: read for every sample")
        if self.always_active:
            parts.append("always active")
        return ", ".join(parts)


#: What a source that declares nothing records: both columns null.
UNDECLARED = SourceSelection()
