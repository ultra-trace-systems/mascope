"""A known species decided once for a batch, on the evidence of all its files.

A known species is an ion the run knows before it reads the sample: an ion of
the source's own library (its reagent's ladder, the air's ions, an Orbitrap's
calibrant beam, a solvent ladder's rungs; ``reagents``) or a compound a
reference list tags as background (the cyclic siloxanes first). Each file's
run decides on its own whether such a species is there: the reagent pre-pass
claims a library ion where that file's spectrum puts it on its corrected mass
above the pass's floor, or where the ladder below it holds, and the stages
read a listed compound where that file's lines commit it. A species present
across a batch is then claimed in some files and read as an analyte, or left
unread, in the others, and the reference engine's log shows a per-file lock
that fired in one file of ten.

The batch decides it once. This module is the pure half of the seventh rider
of step 4.2 of the assignment plan (``docs/dev/assignment_quality_plan.md``,
step 4.2h; the reference engine's 0.10 line): the evidence a batch pools for
one species, and the verdict it reads off it.

The evidence
------------

Per species, over the files of its partition (one ionization mode and one
instrument type, the files whose peaks fold together), :class:`PooledEvidence`
counts the files its peak is present in and, of those, the files whose own
test held (the file claimed it, or read the listed compound at candidate or
better), the files the batch may claim it in, and the files a hold keeps as
they are. The caller sorts the files; the reading here does not see them.

The verdict
-----------

:func:`decide_known_species`:

- **Unpooled** where the species' peak is present in fewer than
  :data:`MIN_PRESENT_FILES` files, or in fewer than :data:`MIN_PRESENT_SHARE`
  of the partition's files: a species seen in one file, or in a few, is that
  file's own reading, and each file keeps it.
- **Absent** where it is present in enough files and no file's own test held:
  the mass alone, at the batch's tolerance, is not evidence that the peak is
  the species.
- **Present** otherwise: the species is the batch's, and the batch claims it
  in every file its peak is present in, but for the files a hold keeps.

The co-variation of the species' trace with its core is read beside the
verdict (``coherence``, step 4.2's co-variation reading) and recorded; it
decides nothing while the co-variation reading is display-only (decision 35).

The thresholds are this step's own, set from the rider's two cases (a species
present in most files is claimed in all of them, one present in a single file
is not pooled) and not yet measured on a bench; the bench's card reads them
(milestone 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


#: The share of a partition's files a species' peak must be present in for the
#: batch to decide it: half, the share a trace must be present in to count as
#: persistent in the co-variation reading's common mode
#: (``coherence.PERSISTENT_SHARE``).
MIN_PRESENT_SHARE = 0.5

#: The fewest files a species' peak must be present in for the batch to decide
#: it. A species in one file has no batch to pool: that file's own reading is
#: the only evidence there is.
MIN_PRESENT_FILES = 2

#: The fewest files whose own test must have held for the batch to decide a
#: species present: at least one file read the peak as the species on its own
#: evidence, at its own precision.
MIN_TEST_FILES = 1

VERDICT_PRESENT = "present"
VERDICT_ABSENT = "absent"
VERDICT_UNPOOLED = "unpooled"

#: Every verdict, in the order a reader meets them.
VERDICTS = (VERDICT_PRESENT, VERDICT_ABSENT, VERDICT_UNPOOLED)

#: Why a species is unpooled: present in fewer than :data:`MIN_PRESENT_FILES`.
REASON_ONE_FILE = "one_file"
#: Why a species is unpooled: present in under :data:`MIN_PRESENT_SHARE`.
REASON_FEW_FILES = "few_files"
#: Why a species is absent: no file's own test held.
REASON_TEST_NEVER_HELD = "test_never_held"

#: A species of the source's own library.
KIND_LIBRARY = "library"
#: A compound a reference list tags as background.
KIND_LISTED = "listed"


@dataclass(frozen=True)
class PooledEvidence:
    """What a batch knows of one species, file by file.

    :ivar files: The partition's files.
    :ivar present: The files the species' peak is present in.
    :ivar test_held: Of those, the files whose own test held.
    :ivar claimable: Of those, the files the batch may claim it in: the peak
        unread, or read as something else no hold protects.
    :ivar kept: Of those, the files a hold keeps as they are.
    """

    files: int
    present: int
    test_held: int
    claimable: int = 0
    kept: int = 0

    @property
    def present_share(self) -> float:
        """The share of the partition's files the peak is present in."""
        return self.present / self.files if self.files else 0.0

    def to_json(self) -> dict:
        return {
            "files": self.files,
            "present": self.present,
            "present_share": round(self.present_share, 4),
            "test_held": self.test_held,
            "claimable": self.claimable,
            "kept": self.kept,
        }


@dataclass(frozen=True)
class SpeciesVerdict:
    """What a batch decided of one species.

    :ivar verdict: :data:`VERDICT_PRESENT`, :data:`VERDICT_ABSENT` or
        :data:`VERDICT_UNPOOLED`.
    :ivar reason: Why it is not present, None where it is.
    :ivar evidence: What it was decided on.
    """

    verdict: str
    evidence: PooledEvidence
    reason: Optional[str] = None

    @property
    def present(self) -> bool:
        return self.verdict == VERDICT_PRESENT

    def to_json(self) -> dict:
        return {
            "verdict": self.verdict,
            "reason": self.reason,
            **self.evidence.to_json(),
        }


def decide_known_species(
    evidence: PooledEvidence,
    *,
    min_present_files: int = MIN_PRESENT_FILES,
    min_present_share: float = MIN_PRESENT_SHARE,
    min_test_files: int = MIN_TEST_FILES,
) -> SpeciesVerdict:
    """Decide one species once for its batch.

    :param evidence: The species' pooled evidence.
    :param min_present_files: :data:`MIN_PRESENT_FILES`.
    :param min_present_share: :data:`MIN_PRESENT_SHARE`.
    :param min_test_files: :data:`MIN_TEST_FILES`.
    :return: The verdict.
    """
    if evidence.present < min_present_files:
        return SpeciesVerdict(VERDICT_UNPOOLED, evidence, REASON_ONE_FILE)
    if evidence.present_share < min_present_share:
        return SpeciesVerdict(VERDICT_UNPOOLED, evidence, REASON_FEW_FILES)
    if evidence.test_held < min_test_files:
        return SpeciesVerdict(VERDICT_ABSENT, evidence, REASON_TEST_NEVER_HELD)
    return SpeciesVerdict(VERDICT_PRESENT, evidence)


def thresholds() -> dict:
    """The thresholds a decision is read at, for a run's record."""
    return {
        "min_present_files": MIN_PRESENT_FILES,
        "min_present_share": MIN_PRESENT_SHARE,
        "min_test_files": MIN_TEST_FILES,
    }


__all__ = [
    "KIND_LIBRARY",
    "KIND_LISTED",
    "MIN_PRESENT_FILES",
    "MIN_PRESENT_SHARE",
    "MIN_TEST_FILES",
    "REASON_FEW_FILES",
    "REASON_ONE_FILE",
    "REASON_TEST_NEVER_HELD",
    "VERDICTS",
    "VERDICT_ABSENT",
    "VERDICT_PRESENT",
    "VERDICT_UNPOOLED",
    "PooledEvidence",
    "SpeciesVerdict",
    "decide_known_species",
    "thresholds",
]
