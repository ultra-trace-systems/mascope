"""The stage stubs' stand-ins reach every read of what they stand in for.

A stand-in is set in each module of a run that reads its name
(``stage_stubs._SAMPLE_READERS``). A module that starts to read one, or a
table that loses one, would leave that read on the real function: the test
would read the test store where it meant to read its canned sample.
"""

import pandas as pd
from patch_readers import readers  # the tests root, on sys.path
from stage_stubs import _SAMPLE_READERS, stub_sample_reads


#: The modules that read a name apart from a run, each through its own import,
#: as each did when the service held the name: a stand-in in the service
#: never reached them, and the stub leaves them as they were.
_APART = {
    "load_sample_peaks": {"batch_isotopologues", "curation"},
    "fetch_sample_mechanisms": {"batch_series"},
}


def test_the_sample_reads_are_stood_in_for_in_every_module_of_a_run(monkeypatch):
    real = {
        name: getattr(modules[0], name) for name, modules in _SAMPLE_READERS.items()
    }
    stub_sample_reads(monkeypatch, lambda _sample_item_id: pd.DataFrame(), [])
    for name, value in real.items():
        assert readers(name, value) == _APART.get(name, set()), name
