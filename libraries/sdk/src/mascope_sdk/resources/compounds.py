"""Target compounds resource for the Mascope SDK."""

from collections.abc import Sequence
from typing import Any

from loguru import logger

from ._base import BaseResource, Needs, _log_api_message


#: The compound list is shared by every workspace, so its writes need a
#: global role rather than a workspace's.
_GLOBAL_EDITOR = "the global editor role"

#: Rows per request of :meth:`CompoundsResource.find_structures`: the most the
#: server gives on one page.
STRUCTURES_PAGE_LIMIT = 100


class CompoundsResource(BaseResource):
    """Resource for target compound writes, and the structure lookup.

    Target compounds are shared by every workspace; collections refer to
    them. Each write here needs the global editor role;
    :meth:`find_structures` only reads, with any role.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        added = mascope.compounds.add(
            [{"target_compound_formula": "C6H12O6", "target_compound_name": "Glucose"}]
        )
        compound_id = added["created_compounds"][0]["target_compound_id"]
        mascope.compounds.update(
            [{"target_compound_id": compound_id, "cas_number": "50-99-7"}]
        )
        mascope.compounds.remove(compound_id)
    """

    def find_structures(
        self,
        formula: str,
        *,
        cas_number: str | None = None,
        name: str | None = None,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        """The reference compounds that could give a compound of *formula* its
        structure.

        The rows of the server's active reference lists whose Hill formula is
        *formula*'s and that carry an InChIKey, an InChI or a SMILES: one row
        per compound per list, each with the list's name and the row's
        licence. The rows recording *cas_number* come first, then those named
        *name*, then a stable order. A row is a candidate, not an
        identification: which of them a compound is, is the caller's call.

        :param formula: The formula, in any notation.
        :type formula: str
        :param cas_number: The compound's CAS number, to put its rows first;
            compared as digits.
        :type cas_number: str, optional
        :param name: The compound's name, to put the rows of that name next;
            compared case aside.
        :type name: str, optional
        :param limit: At most this many rows, the first ones; None reads them
            all.
        :type limit: int, optional
        :return: One dict per row: ``name``, ``inchikey``, ``inchi``,
            ``smiles``, ``cas_number`` (from the row's cross-references, or
            None), ``source`` (the list), ``source_native_id``, ``license``,
            ``cas_match`` and ``name_match``. Empty when no active list has a
            structure for the formula.
        :rtype: list[dict[str, Any]]
        :raises ValueError: If ``limit`` is below 1.
        :raises AuthenticationError: If authentication fails, or the server
            predates the lookup and takes ``structures`` for a compound ID.
        :raises MascopeAPIError: If the API request fails.

        Example::

            found = mascope.compounds.find_structures(
                "C6H12O6", cas_number="50-99-7"
            )
            pick = found[0]
            mascope.compounds.update(
                [
                    {
                        "target_compound_id": compound_id,
                        "cas_number": pick["cas_number"],
                        "inchikey": pick["inchikey"],
                        "inchi": pick["inchi"],
                        "smiles": pick["smiles"],
                    }
                ]
            )
        """
        if limit is not None and limit < 1:
            raise ValueError(f"limit must be at least 1, not {limit}")
        page_size = min(limit or STRUCTURES_PAGE_LIMIT, STRUCTURES_PAGE_LIMIT)
        params: dict[str, Any] = {"formula": formula, "limit": page_size}
        if cas_number is not None:
            params["cas_number"] = cas_number
        if name is not None:
            params["name"] = name

        rows: list[dict[str, Any]] = []
        page = 0
        while True:
            envelope = self._get_envelope(
                "target/compounds/structures", {**params, "page": page}
            )
            _log_api_message(envelope)
            batch = envelope.get("data") or []
            rows.extend(batch)
            total = int(envelope.get("results") or 0)
            if (
                not batch
                or len(rows) >= total
                or (limit is not None and len(rows) >= limit)
            ):
                break
            page += 1

        if not rows:
            logger.info("No active reference list has a structure for {}", formula)
        return rows if limit is None else rows[:limit]

    def add(self, compounds: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Add compounds.

        :param compounds: Each a dict with ``target_compound_formula`` and,
            optionally, ``target_compound_name``, ``cas_number`` and the
            structure identity: ``inchikey``, ``inchi``, ``smiles``. An InChI
            must describe the formula, or the call is refused.
        :type compounds: Sequence[dict[str, Any]]
        :return: ``{"created_compounds": [...], "existing_compounds": [...]}``:
            a compound that already exists is not created again but listed
            under ``existing_compounds``; it takes the identity sent only when
            it has none.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the global editor role.
        :raises ValidationError: If a formula is not one.
        """
        formulas = ", ".join(
            str(compound.get("target_compound_formula")) for compound in compounds
        )
        return self._write(
            "POST",
            "target/compounds",
            Needs("add", "target compounds", formulas, _GLOBAL_EDITOR),
            data=list(compounds),
        )

    def update(self, compounds: Sequence[dict[str, Any]]) -> Any:
        """Update compounds.

        A formula change makes the server delete the compound and create it
        again under a new ID, with the name, the CAS number and the structure
        identity the item gives: send them with a new formula, or the name
        comes back empty and the rest unset. The answer names the new ID.

        :param compounds: Each a dict with its ``target_compound_id`` and the
            fields to change: ``target_compound_name``,
            ``target_compound_formula``, ``cas_number``, ``inchikey``,
            ``inchi``, ``smiles``. An InChI must describe the formula the
            compound will have - the stored one when the item sends none - or
            the call is refused.
        :type compounds: Sequence[dict[str, Any]]
        :return: The updated compounds, as the route reports them.
        :rtype: Any
        :raises PermissionDeniedError: Without the global editor role.
        """
        compound_ids = ", ".join(
            str(compound.get("target_compound_id")) for compound in compounds
        )
        return self._write(
            "PATCH",
            "target/compounds",
            Needs("update", "target compounds", compound_ids, _GLOBAL_EDITOR),
            data=list(compounds),
        )

    def remove(self, compound_id: str) -> Any:
        """Delete a compound, taking it out of every collection with it.

        This cannot be undone.

        :param compound_id: The compound.
        :type compound_id: str
        :return: What the route answers with.
        :rtype: Any
        :raises PermissionDeniedError: Without the global editor role.
        """
        return self._write(
            "DELETE",
            f"target/compounds/{compound_id}",
            Needs("remove", "target compound", compound_id, _GLOBAL_EDITOR),
        )
