"""Source-specific ETL adapters.

Each adapter turns one source's dump into raw :class:`ReferenceRecord`
instances, or, for a spectral library (MassBank, MoNA, GNPS), into
:class:`~mascope_reference.spectrum.SpectrumRecord` instances. See :mod:`mascope_reference.sources` for the name -> adapter
registry the CLI drives.
"""

from mascope_reference.adapters.base import Adapter, SpectrumAdapter
from mascope_reference.adapters.chebi import ChebiAdapter
from mascope_reference.adapters.coconut import CoconutAdapter
from mascope_reference.adapters.comptox import CompToxAdapter
from mascope_reference.adapters.custom import CustomAdapter
from mascope_reference.adapters.gnps import GnpsAdapter
from mascope_reference.adapters.hmdb import HmdbAdapter
from mascope_reference.adapters.lipidmaps import LipidMapsAdapter
from mascope_reference.adapters.massbank import MassBankAdapter
from mascope_reference.adapters.mona import MonaAdapter
from mascope_reference.adapters.norman import NormanAdapter
from mascope_reference.adapters.peaklist import PeakListAdapter
from mascope_reference.adapters.pubchem import PubChemAdapter


__all__ = [
    "Adapter",
    "SpectrumAdapter",
    "ChebiAdapter",
    "CompToxAdapter",
    "CoconutAdapter",
    "CustomAdapter",
    "GnpsAdapter",
    "HmdbAdapter",
    "LipidMapsAdapter",
    "MassBankAdapter",
    "MonaAdapter",
    "NormanAdapter",
    "PeakListAdapter",
    "PubChemAdapter",
]
