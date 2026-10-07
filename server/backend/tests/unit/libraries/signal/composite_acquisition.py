"""
A composite acquisition as its scans, for the scripted reader.

A composite method measures one chemistry as several scan ranges, each an
experiment of its own, and no committed file holds one. This is the layout a
site settled on for it, as scans ``ScriptedAcquisition`` can be handed, with
what the stitch makes of it.

A plain module, like ``scripted_acquisition``, so that the tests can import
it by name.
"""

from scripted_acquisition import SAMPLE_FILENAME

import mascope_file.io as m_io


REAGENT = "FTMS - p NSI Full ms [40.0000-138.0000]"
LOW = "FTMS - p NSI Full ms [66.0000-124.0000]"
MID = "FTMS - p NSI Full ms [132.0000-460.0000]"
HIGH = "FTMS - p NSI Full ms [440.0000-900.0000]"
POSITIVE = "FTMS + p NSI Full ms [40.0000-600.0000]"

KEYS = [f"{text} R=120000" for text in (REAGENT, LOW, MID, HIGH)]

# The same ion as two windows read it: half a ppm apart in the reagent scan
# and the low window, a little less in the mid and the high one.
IN_LOW = 80.0 * (1 + 0.5e-6)
IN_HIGH = 450.0 * (1 + 0.4e-6)

# A composite file: five reagent scans over the whole low range at one
# microscan, then the low, the mid and the high window at ten, three, four
# and two scans of them. The reagent scan and the low window both record the
# ion at 80, the mid and the high window the one at 450; the reagent scan
# alone holds the reagent ion at 62 and its dimer at 125.
COMPOSITE = (
    [(REAGENT, 1, {62.0: 1000.0, 80.0: 10.0, 125.0: 400.0, 134.0: 30.0})] * 5
    + [(LOW, 2, {IN_LOW: 14.0, 100.0: 50.0, 121.0: 6.0})] * 3
    + [(MID, 3, {134.5: 7.0, 300.0: 40.0, 450.0: 8.0})] * 4
    + [(HIGH, 4, {IN_HIGH: 10.0, 700.0: 30.0})] * 2
)
MICROSCANS = {1: 1, 2: 10, 3: 10, 4: 10}

# The map the rule draws for it: the one its site drew by hand.
RUNS = [[40, 67, 0], [67, 122, 1], [122, 133, 0], [133, 444, 2], [444, 900, 3]]


def record_calibration(factor):
    """Record an m/z calibration beside the test sample, as an apply does last."""
    m_io.update_props(
        SAMPLE_FILENAME,
        {"mz_calibration": {"par": {"calibration_factor": factor}}},
    )
