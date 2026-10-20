"""Compound matching resource for the Mascope SDK."""

from typing import Any

from ._base import BaseResource, Needs


class MatchingResource(BaseResource):
    """Resource for compound matching operations.

    Provides methods to match peaks in samples against target compounds
    based on m/z values, isotope patterns, and other criteria.

    Example::

        from mascope_sdk import MascopeClient

        mascope = MascopeClient()

        # Match a single compound in a sample
        matches = mascope.matching.match_compound(
            sample_id="sample-123",
            formula="C6H12O6",
            name="Glucose",
        )

        # Match multiple compounds
        matches = mascope.matching.match_compounds(
            sample_id="sample-123",
            formulas=["C6H12O6", "C12H22O11"],
        )

        # Run a batch's matching, then save what it found
        preview = mascope.matching.aggregate_batch("batch-456")
        mascope.matching.save_batch("batch-456")
    """

    def match_compound(
        self,
        sample_id: str,
        formula: str,
        name: str = "Unknown Compound",
        match_params: dict[str, Any] | None = None,
    ) -> dict | None:
        """Match a single compound in a sample.

        Searches for peaks in the sample that match the target compound formula,
        considering ionization patterns and isotope distributions.

        :param sample_id: The ID of the sample to search in.
        :type sample_id: str
        :param formula: Chemical formula of the target compound (e.g., "C6H12O6").
        :type formula: str
        :param name: Name of the compound for reference. Defaults to "Unknown Compound".
        :type name: str, optional
        :param match_params: Optional matching parameters to filter results:
            - ``mz_tolerance``: m/z tolerance in ppm
            - ``isotope_ratio_tolerance``: Tolerance for isotope ratio matching
            - ``peak_min_intensity``: Minimum peak intensity threshold
            - ``probable_match_threshold``: Threshold for probable matches
            - ``possible_match_threshold``: Threshold for possible matches
        :type match_params: dict[str, Any], optional
        :return: A dictionary containing match data with compound, ions, and isotopes.
                 Returns None if no matches are found.
        :rtype: dict | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises ValidationError: If parameters are invalid.
        :raises MascopeAPIError: If the API request fails.

        Example::

            matches = mascope.matching.match_compound(
                sample_id="sample-123",
                formula="C6H12O6",
                name="Glucose",
                match_params={
                    "mz_tolerance": 50,
                    "isotope_ratio_tolerance": 0.2,
                },
            )

            if matches:
                for ion in matches.get('ions', []):
                    print(f"Ion: {ion['formula']} at m/z {ion['mz']}")
        """
        body: dict[str, Any] = {
            "target_compound": {
                "target_compound_formula": formula,
                "target_compound_name": name,
            }
        }
        if match_params is not None:
            body["match_params"] = match_params

        return self._post(f"match/aggregate/sample/{sample_id}/compound", data=body)

    def match_compounds(
        self,
        sample_id: str,
        formulas: list[str],
        match_params: dict[str, Any] | None = None,
        ionization_mechanism_ids: list[str] | None = None,
    ) -> list[dict] | None:
        """Match multiple compounds in a sample.

        Searches for peaks in the sample that match any of the target compound
        formulas, considering ionization patterns and isotope distributions.

        :param sample_id: The ID of the sample to search in.
        :type sample_id: str
        :param formulas: List of chemical formulas to match
                         (e.g., ["C6H12O6", "C12H22O11"]).
        :type formulas: list[str]
        :param match_params: Optional matching parameters (see :meth:`match_compound`).
        :type match_params: dict[str, Any], optional
        :param ionization_mechanism_ids: Optional list of mechanism IDs to use.
        :type ionization_mechanism_ids: list[str], optional
        :return: A list of match-data dictionaries, one per compound.
                 Returns None if no matches are found.
        :rtype: list[dict] | None
        :raises AuthenticationError: If authentication fails.
        :raises NotFoundError: If the sample is not found.
        :raises ValidationError: If parameters are invalid.
        :raises MascopeAPIError: If the API request fails.

        Example::

            matches = mascope.matching.match_compounds(
                sample_id="sample-123",
                formulas=["C6H12O6", "C12H22O11", "C3H6O3"],
            )
        """
        body: dict[str, Any] = {
            "target_compound_formulas": formulas,
        }
        if match_params is not None:
            body["match_params"] = match_params
        if ionization_mechanism_ids is not None:
            body["ion_mechanism_ids"] = ionization_mechanism_ids

        return self._post(f"match/aggregate/sample/{sample_id}/compounds", data=body)

    def aggregate_sample(
        self,
        sample_id: str,
        target_ion_id: str | None = None,
        match_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the matching of a sample against its batch's collections.

        Nothing is saved: this shows what :meth:`save_sample` would save.

        :param sample_id: The sample.
        :type sample_id: str
        :param target_ion_id: Only this target ion.
        :type target_ion_id: str, optional
        :param match_params: Filter parameters, as for :meth:`match_compound`.
        :type match_params: dict[str, Any], optional
        :return: ``match_ions``, ``match_compounds``, ``match_collections`` and
            ``match_samples``, each a list of match records.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the guest role on the sample.
        """
        return self._write(
            "POST",
            f"match/aggregate/sample/{sample_id}",
            Needs("run matching on", "sample", sample_id, "guest on the sample"),
            data=_aggregate_body(target_ion_id, match_params),
        )

    def aggregate_batch(
        self,
        batch_id: str,
        target_ion_id: str | None = None,
        match_params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Run the matching of a batch's samples against its collections.

        Nothing is saved: this shows what :meth:`save_batch` would save.

        :param batch_id: The batch.
        :type batch_id: str
        :param target_ion_id: Only this target ion.
        :type target_ion_id: str, optional
        :param match_params: Filter parameters, as for :meth:`match_compound`.
        :type match_params: dict[str, Any], optional
        :return: ``match_ions``, ``match_compounds``, ``match_collections`` and
            ``match_samples``, each a list of match records.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the guest role on the batch.
        """
        return self._write(
            "POST",
            f"match/aggregate/batch/{batch_id}",
            Needs("run matching on", "batch", batch_id, "guest on the batch"),
            data=_aggregate_body(target_ion_id, match_params),
        )

    def save_sample(
        self,
        sample_id: str,
        target_ion_id: str | None = None,
        match_params: dict[str, Any] | None = None,
        *,
        match_ions: bool | None = None,
        match_compounds: bool | None = None,
        match_collections: bool | None = None,
        match_samples: bool | None = None,
    ) -> dict[str, Any]:
        """Run the matching of a sample and save the matches it finds.

        :param sample_id: The sample.
        :type sample_id: str
        :param target_ion_id: Only this target ion.
        :type target_ion_id: str, optional
        :param match_params: Filter parameters, as for :meth:`match_compound`.
        :type match_params: dict[str, Any], optional
        :param match_ions: False to leave out the ion matches; the server
            saves every level unless told otherwise.
        :type match_ions: bool, optional
        :param match_compounds: False to leave out the compound matches.
        :type match_compounds: bool, optional
        :param match_collections: False to leave out the collection matches.
        :type match_collections: bool, optional
        :param match_samples: False to leave out the sample matches.
        :type match_samples: bool, optional
        :return: The server's ``status`` and ``message``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role on the sample.
        """
        return self._write(
            "POST",
            f"match/aggregate/sample/{sample_id}/save",
            Needs("save the matches of", "sample", sample_id, "editor on the sample"),
            data=_aggregate_body(
                target_ion_id,
                match_params,
                match_ions=match_ions,
                match_compounds=match_compounds,
                match_collections=match_collections,
                match_samples=match_samples,
            ),
        )

    def save_batch(
        self,
        batch_id: str,
        target_ion_id: str | None = None,
        match_params: dict[str, Any] | None = None,
        *,
        match_ions: bool | None = None,
        match_compounds: bool | None = None,
        match_collections: bool | None = None,
        match_samples: bool | None = None,
    ) -> dict[str, Any]:
        """Run the matching of a batch and save the matches it finds.

        :param batch_id: The batch.
        :type batch_id: str
        :param target_ion_id: Only this target ion.
        :type target_ion_id: str, optional
        :param match_params: Filter parameters, as for :meth:`match_compound`.
        :type match_params: dict[str, Any], optional
        :param match_ions: As for :meth:`save_sample`, and the other levels
            likewise: ``match_compounds``, ``match_collections``,
            ``match_samples``.
        :type match_ions: bool, optional
        :return: The server's ``status`` and ``message``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role on the batch.
        """
        return self._write(
            "POST",
            f"match/aggregate/batch/{batch_id}/save",
            Needs("save the matches of", "batch", batch_id, "editor on the batch"),
            data=_aggregate_body(
                target_ion_id,
                match_params,
                match_ions=match_ions,
                match_compounds=match_compounds,
                match_collections=match_collections,
                match_samples=match_samples,
            ),
        )

    def resave_batch(
        self,
        batch_id: str,
        target_ion_id: str | None = None,
        match_params: dict[str, Any] | None = None,
        *,
        match_ions: bool | None = None,
        match_compounds: bool | None = None,
        match_collections: bool | None = None,
        match_samples: bool | None = None,
    ) -> dict[str, Any]:
        """Replace a batch's saved matches with a fresh run's.

        The matches saved before are deleted first. This cannot be undone.

        :param batch_id: The batch.
        :type batch_id: str
        :param target_ion_id: Only this target ion.
        :type target_ion_id: str, optional
        :param match_params: Filter parameters, as for :meth:`match_compound`.
        :type match_params: dict[str, Any], optional
        :param match_ions: As for :meth:`save_sample`, and the other levels
            likewise: ``match_compounds``, ``match_collections``,
            ``match_samples``.
        :type match_ions: bool, optional
        :return: The server's ``status`` and ``message``.
        :rtype: dict[str, Any]
        :raises PermissionDeniedError: Without the editor role on the batch.
        """
        return self._write(
            "POST",
            f"match/aggregate/batch/{batch_id}/resave",
            Needs("rebuild the matches of", "batch", batch_id, "editor on the batch"),
            data=_aggregate_body(
                target_ion_id,
                match_params,
                match_ions=match_ions,
                match_compounds=match_compounds,
                match_collections=match_collections,
                match_samples=match_samples,
            ),
        )


def _aggregate_body(
    target_ion_id: str | None,
    match_params: dict[str, Any] | None,
    **levels: bool | None,
) -> dict[str, Any]:
    """The body of an aggregate run or save: only what the caller gave."""
    body: dict[str, Any] = {
        level: value for level, value in levels.items() if value is not None
    }
    if target_ion_id is not None:
        body["target_ion_id"] = target_ion_id
    if match_params is not None:
        body["match_params"] = match_params
    return body
