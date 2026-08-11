"""The source registry: source name -> ETL adapter.

The CLI (``mascope reference sync <source>``) and any programmatic ingestion
resolve an adapter through here, so adding a source is a one-line registration
and nothing else in the pipeline changes.
"""

from mascope_reference.adapters import (
    Adapter,
    ChebiAdapter,
    CoconutAdapter,
    CompToxAdapter,
    CustomAdapter,
    HmdbAdapter,
    LipidMapsAdapter,
    NormanAdapter,
    PubChemAdapter,
)


# One instance per source, keyed by its stable ``name``.
_ADAPTERS: dict[str, Adapter] = {
    adapter.name: adapter
    for adapter in (
        PubChemAdapter(),
        CompToxAdapter(),
        ChebiAdapter(),
        HmdbAdapter(),
        LipidMapsAdapter(),
        CoconutAdapter(),
        NormanAdapter(),
        CustomAdapter(),
    )
}


def available_sources() -> list[str]:
    """Sorted list of registered source names."""
    return sorted(_ADAPTERS)


def get_adapter(source: str) -> Adapter:
    """Resolve a source name to its adapter.

    :param source: Registered source name (see :func:`available_sources`).
    :raises KeyError: If the source is not registered, with the valid names.
    :return: The adapter instance for the source.
    """
    try:
        return _ADAPTERS[source]
    except KeyError:
        raise KeyError(
            f"Unknown reference source '{source}'. "
            f"Available: {', '.join(available_sources())}"
        ) from None
