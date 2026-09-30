Each assignment lands in a confidence tier read off its evidence &mdash; how
well the formula fits the peak, weighted by how chemically plausible it is
&mdash; so a formula that matches the mass beautifully but describes an unlikely
molecule does not reach the top tier on the match alone.

- **assigned** &mdash; strong evidence: the formula is the call for the peak. It
  is a *composition*, not a confirmed compound: isomers share it, and telling
  them apart needs MS/MS or a reference standard. Something besides the one
  peak has seen it too: a line of its isotope pattern, a second ionization
  channel, or a list it was matched from.
- **candidate** &mdash; a plausible formula with weaker support.
- **below assignability** (*below*) &mdash; a formula was found, but the
  evidence is too weak to trust.
- **unassigned** &mdash; no composition explained the peak.

The tiering is **provisional**, and the tier column and the inspector's tier row
say so: its rules are still being developed, and a peak's tier can change between
engine versions, so read a tier as the engine's current reading of the evidence
rather than a settled verdict. The mark goes when the rules are settled.

A tier chip names the tier alone; hovering it gives the evidence, which says how
well the formula explains the peak, not the chance it is right. A *batch peak*'s
tier is a vote across the samples it appears in, so its chip names no evidence.

A run imported from another engine can also carry that engine's own tier, in an
**engine tier** column beside Mascope's. Where the two differ, the peak is worth
a look: the other engine weighs things Mascope's tier does not. A dash there is
usual: an engine tiers only the peaks it committed a formula to. Only Mascope's
tier is used when peaks are rolled up across a batch.

Beside its tier, a peak can carry a role: **reagent** (an ion the ionization
source made: of its reagent, the air it ionizes or its calibrant, or by
breaking an analyte), **artifact** (a ringing side lobe of a very intense
neighbouring peak) or **isotopologue** (a line of another assignment's isotope
pattern). Reagent and artifact peaks are not the sample's compounds, so each has
a chip of its own in place of a tier, is counted apart from the tiers above the
ledger and sorts after them. A reagent row names its ion, written with its
charge, where an assignment names its formula, and its isotope lines fold under
it as a compound's isotopologues do. A tier count counts a compound once, with its
isotopologues folded in; a role count counts peaks, so a reagent ion's isotope
lines count with it.
