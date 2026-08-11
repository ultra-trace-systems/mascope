"""Source-specific ETL adapters.

Each adapter turns one source's dump into raw :class:`ReferenceRecord`
instances. See :mod:`mascope_reference.sources` for the name -> adapter
registry the CLI drives.
"""

from mascope_reference.adapters.base import Adapter
from mascope_reference.adapters.chebi import ChebiAdapter
from mascope_reference.adapters.coconut import CoconutAdapter
from mascope_reference.adapters.comptox import CompToxAdapter
from mascope_reference.adapters.custom import CustomAdapter
from mascope_reference.adapters.hmdb import HmdbAdapter
from mascope_reference.adapters.lipidmaps import LipidMapsAdapter
from mascope_reference.adapters.norman import NormanAdapter
from mascope_reference.adapters.pubchem import PubChemAdapter


__all__ = [
    "Adapter",
    "ChebiAdapter",
    "CompToxAdapter",
    "CoconutAdapter",
    "CustomAdapter",
    "HmdbAdapter",
    "LipidMapsAdapter",
    "NormanAdapter",
    "PubChemAdapter",
]
