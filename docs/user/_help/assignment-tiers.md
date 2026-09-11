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

The peak inspector says why a row holds its tier, under **Why this tier**: what
capped it at *candidate* &mdash; a radical neutral, a peak whose evidence left
other formulas standing, a line a neighbouring compound's isotope pattern
already predicts &mdash; or what it kept its tier on. These rules only ever lower
a tier, never raise one. A row assigned by hand, a run imported from another
engine and a run made before Mascope recorded reasons show none.

A run computed outside Mascope and imported can carry a second tier: the one the
engine that produced it reached on its own terms. Such a run shows it in its own
**engine tier** column beside Mascope's, which is always Mascope's own reading of
the evidence. A row where the two differ is worth a look &mdash; a different
engine can weigh things this one does not, such as how crowded the mass is or
whether the isotope pattern was corroborated, so a disagreement is a peak to
examine rather than an error. A dash in that column means the engine stated no
tier for the peak, which is usual: an engine typically tiers only the peaks it
committed a formula to. Only Mascope's own tier is used when peaks are rolled up
across a batch.

Orthogonal to its tier, a peak can carry a role: **reagent** and **artifact**
peaks stem from the ionization chemistry or the instrument rather than the
sample's compounds, and **isotopologue** peaks belong to another
assignment's isotope pattern, counted with their main peak.
