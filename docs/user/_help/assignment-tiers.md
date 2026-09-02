Each assignment lands in a confidence tier from its evidence &mdash; how well it
fits the peak, weighted by how chemically plausible the formula is. A
composition that matches the mass beautifully but describes an unlikely molecule
does not reach the top tier on the strength of the match alone. The percentage
on the tier chip is that combined evidence; the fit score on its own is shown
beside the assignment. A *batch peak*'s chip carries no percentage: its tier is
a vote across the samples the peak appears in, not a threshold on one number.

- **assigned** &mdash; strong, corroborated evidence: this formula is the call for
  the peak, and you can build on it. It is a *composition*, not a confirmed
  compound &mdash; isomers share it, and telling them apart needs MS/MS or a
  reference standard.
- **candidate** &mdash; a plausible assignment with weaker support.
- **below assignability** (shown as *below*) &mdash; a formula was found, but
  the evidence is too weak to trust.
- **unassigned** &mdash; no composition explained the peak.

A run computed outside Mascope and imported can carry a second tier: the one the
engine that produced it reached on its own terms. Mascope's tier is always its
own reading of the evidence, so where the two differ the chip is marked with a
scales icon and the hover text names the other verdict. A different engine can
weigh things this one does not &mdash; how crowded the mass is, whether the
isotope pattern was corroborated &mdash; so a disagreement is a peak worth
looking at rather than an error. Only Mascope's own tier is used when peaks are
rolled up across a batch.

Orthogonal to its tier, a peak can carry a role: **reagent** and **artifact**
peaks stem from the ionization chemistry or the instrument rather than the
sample's compounds, and **isotopologue** peaks belong to another
assignment's isotope pattern, counted with their main peak.
