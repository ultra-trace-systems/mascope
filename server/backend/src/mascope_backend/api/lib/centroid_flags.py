"""
The ``exclude`` query parameter of the per-scan centroid routes.

``GET /api/samples/centroids`` and ``GET /api/sample/files/{id}/metadata``
serve every stored centroid of every scan, each with its flags: the lock-mass
peak a scan was corrected with (``reference``), weak side-lobe peaks
(``exception``), merged peaks (``merged``) and the lobes of a split weak peak
(``fragmented``). ``exclude`` names the flags whose centroids a caller wants
left out; without it nothing is.
"""

from fastapi import HTTPException, Query

from mascope_thermo.backend import CENTROID_FLAGS, centroid_flag_mask


def centroid_exclusion(
    exclude: list[str] = Query(
        [],
        description=(
            "Leave out the centroids carrying any of these flags: "
            f"{', '.join(f'`{name}`' for name in CENTROID_FLAGS)}. Comma "
            "separated (`exclude=reference,exception`) or repeated. Nothing "
            "is left out by default."
        ),
    ),
) -> tuple[str, ...]:
    """The flag names an ``exclude`` query parameter gives, checked.

    :param exclude: The parameter's values, each one name or several
        separated by commas.
    :raises HTTPException: 422 for a name that is not a flag's.
    :return: The names, lower-cased, in the order given.
    """
    names = tuple(
        name.strip().lower()
        for value in exclude
        for name in value.split(",")
        if name.strip()
    )
    try:
        centroid_flag_mask(names)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    return names
