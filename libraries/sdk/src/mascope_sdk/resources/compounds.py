"""Target compounds resource for the Mascope SDK."""

from collections.abc import Sequence
from typing import Any

from ._base import BaseResource, Needs


#: The compound list is shared by every workspace, so its writes need a
#: global role rather than a workspace's.
_GLOBAL_EDITOR = "the global editor role"


class CompoundsResource(BaseResource):
    """Resource for target compound writes.

    Target compounds are shared by every workspace; collections refer to
    them. Each write here needs the global editor role.

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

    def add(self, compounds: Sequence[dict[str, Any]]) -> dict[str, Any]:
        """Add compounds.

        :param compounds: Each a dict with ``target_compound_formula`` and,
            optionally, ``target_compound_name`` and ``cas_number``.
        :type compounds: Sequence[dict[str, Any]]
        :return: ``{"created_compounds": [...], "existing_compounds": [...]}``:
            a compound that already exists is not created again but listed
            under ``existing_compounds``.
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
        again under a new ID, with the name and the CAS number the item
        gives: send both with a new formula, or the name comes back empty
        and the CAS number unset. The answer names the new ID.

        :param compounds: Each a dict with its ``target_compound_id`` and the
            fields to change: ``target_compound_name``,
            ``target_compound_formula``, ``cas_number``.
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
