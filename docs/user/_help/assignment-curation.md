When the engine's winner is not the right answer, you can assign a peak
yourself: **use this** on a close alternative in the peak inspector, or the
**hand button** on a re-search result to put a composition you found onto the
selected peak. The peak's ledger row is edited in place and marked as assigned
by hand, and the assignment it replaced becomes the row's first close
alternative &mdash; so the same control puts it back. The **close
alternatives** are the other candidates that fit the peak but lost the
arbitration: a scored runner-up shows its fit, while entries from the
untargeted composition finder's formula-only shortlist show chemical
plausibility instead, or read as not scored. One that names no adduct cannot be
used, because a formula without the adduct it was seen under cannot be
verified; to commit that formula, re-search the peak and assign the hit, which
names the adduct it was found under.

The row's confidence is re-read for the new formula. Its tier is recalculated
from the new assignment's own evidence &mdash; how well it fits the peak,
weighted by how chemically plausible the formula is &mdash; under this run's
thresholds, so a hand assignment is tiered by the same yardstick as every other
row in the ledger.
Its calibrated **P(correct)** is dropped rather than carried over: that number
was fitted to score the engine's own arbitration, and nothing has calibrated
the formula you chose.

Isotopologue satellites follow their compound. Assigning a different formula or
adduct unassigns the satellites of the one it replaced &mdash; they were the
same compound seen through a heavy atom, so nothing is left for them to claim
&mdash; and putting the original assignment back restores them, except any
satellite that has itself been assigned by hand in the meantime, which is left
as it stands.

A hand assignment lives in the run you made it in &mdash; **re-assigning the
sample recomputes the ledger from the data and supersedes it** &mdash; so
record a verification if you want the judgment to survive. Assigning is not
verifying: picking the better reading of the evidence and vouching for it are
separate acts, so nothing is confirmed on your behalf, and a verdict the row
already carried stays with the formula it judged rather than following the row
onto a new one.
