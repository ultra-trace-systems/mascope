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

Beside the tier, a peak carries its **identification level**: what you may
state of it, in the scale the field uses (Schymanski et al. 2014),
from 5 (a measured mass) to 1 (a confirmed structure). Mascope states 5b, 5a,
4b and 4a from what the run measured; the level never changes the tier.

The tiering is **provisional**, and the tier column and the inspector's tier row
say so: its rules are still being developed, and a peak's tier can change between
engine versions, so read a tier as the engine's current reading of the evidence
rather than a settled verdict. The mark goes when the rules are settled.

A tier chip names the tier alone, with a dot in its colour: filled for
*assigned*, half for *candidate*, a ring for *below* and a dashed ring for
*unassigned*, the one dashed mark there is, for the one outcome not yet known.
Hovering it gives the evidence, which says how well the formula explains the
peak, not the chance it is right. A *batch peak*'s
tier is a vote across the samples it appears in, so its chip names no evidence.
A batch peak the batch's untargeted search took only because its peak recurs
across the batch, not for its height, holds the formula it found at
*candidate* at most (**persistence only**, a down arrow on the chip) until
an isotope line, another channel or a series of the batch sees it too. A
**flat series** badge is not a tier: it flags a batch peak whose trace holds a
constant ratio to the batch's reagent ions or total ion current, which says
where the ion likely comes from, not whether its formula is right.

A down arrow on a chip says a rule holds the tier down: hover it for the rule
and what it did, and open the peak for why.

A run imported from another engine can also carry that engine's own tier and
level. The ledger's **Show** menu then offers **Engine tier and level**, which
draws the other engine's tier as a second, outlined chip beside Mascope's in
the same cell, and its level the same way in the **Level** cell, and makes the
tier chips count and filter by its tier, with **not stated** for the formulas
it gave none. Where the two differ, the peak is worth a look: the other
engine weighs things Mascope's tier does not. An engine tiers only the peaks it
committed a formula to, so many rows show one chip. Only Mascope's tier is used
when peaks are rolled up across a batch. A batch import that carried the
engine's tiers or levels adds the same switch, and **Engine disagrees**, to the
*Batch peaks* ledger.

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

**The tier bar.** Above a ledger, a thin bar draws each tier's share of the
rows, in the tiers' colours, over the tier chips and their counts. A chip
filters: turn on several to see either, none to see every row. A tier with no
rows has no chip. The **reagent** and **artifact** chips sit apart after the
tiers and are not in the bar, since a role is no confidence; **Show › Roles**
hides those rows and their chips. The chips you turn on are kept for each
ledger, through a switch between the sample and the batch and a reload.

**The badges across the batch.** Each of the batch's readings of a peak is a
small square badge. The **Batch peaks** ledger draws them in its **Across the
batch** column, at most three to a row: the ones that hold the reading back
first, then the others, and **+n** for the rest. Click **+n** to see every badge
of the row with its reading in words; a badge there does what it does in the
row. Each batch line
in the inspector opens with one too. Click the ratio-stability badge for the
pairs behind its grade. A badge's glyph says which reading it is, and no two
readings share one. Its colour says what the reading does to confidence, in the
colours the tiers use:

| Colour | What it does |
|---|---|
| green | supports the reading, as the *assigned* chip is green |
| amber | holds it back: one of the reasons the peak is not more confident |
| teal | drifted: the ratio moves while the channels co-vary, and the assignment stands |
| grey | moves nothing: it says something of the peak, not of its formula |
| an outline | a person changed this |

| Glyph | Reading | Colour |
|---|---|---|
| <span class="ph ph-stairs" aria-hidden="true"></span> stairs | in a homologous series of the batch | green |
| <span class="ph ph-wave-sine" aria-hidden="true"></span> wave | its ionization channels co-vary | green, grey where they do not or cannot be read |
| <span class="ph ph-chart-bar" aria-hidden="true"></span> three bars | its isotope lines, stamped or stripped to it | green once one holds, grey otherwise |
| <span class="ph ph-pulse" aria-hidden="true"></span> pulse | ratio stability, its pairs one click away | green when stable, teal when it drifts, amber when unstable |
| <span class="ph ph-check" aria-hidden="true"></span> check | the batch facts | green, amber where a fact refutes the reading |
| <span class="ph ph-repeat" aria-hidden="true"></span> repeat | searched on persistence alone | amber, grey where something else has seen the reading |
| <span class="ph ph-intersect" aria-hidden="true"></span> two circles | a probable blend of two compounds | amber |
| <span class="ph ph-equals" aria-hidden="true"></span> equals | a flat series | grey |
| <span class="ph ph-drop" aria-hidden="true"></span> drop | a reagent decided: a water-ladder rung or a known species | grey |
| <span class="ph ph-hand-pointing" aria-hidden="true"></span> hand | changed by hand | an outline |

A verdict shows nothing until someone records one. A batch-level verdict that
reaches a sample is the same icon with a small **#**, the batch's mark, at its
corner.
