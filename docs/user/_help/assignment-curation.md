When the engine's winner is not the right answer, you can assign a peak
yourself: **Use** on a close alternative in the peak inspector, or the
**hand button** on a result of **Find other formulas for this peak**, the
composition search. The close
alternatives are the other candidates that fit the peak but lost: a scored
runner-up shows its fit, an entry from the formula search's shortlist its
plausibility until it is measured. A shortlist entry is measured through every
channel the run searched, the secondary channels it opened included, as the
search proposed it. One that names no adduct cannot be used, as
an assignment without its adduct could not be verified; search the peak with
*Find other formulas for this peak* instead and assign the hit, which names its adduct.

The peak's row is edited in place and marked as assigned by hand, and the
assignment it replaced becomes its first close alternative, so *Use* on it, or
*Undo* on the card's note of the change, puts it back. The tier is recalculated from the new formula's own evidence under
the run's thresholds, and the spectrum is asked for the isotope lines the formula
predicts, as the run asked its own rows: a missing one holds the assignment at
*candidate* at most (*expected line absent*). *Use* on a batch peak asks a pin
the same in each sample, at what that sample's assignment run asked. A sample with no
such run, one only folded into the batch ledger, is asked at what a run of it would
ask, as a reading the batch carries into it is. A peak you assign as one
of a compound's isotopologues is not asked, since the lines are read from the
compound's main one. It is stored as that line, its ion named with the heavy
isotope (`[81Br]...`). When the run holds the main line of that formula and
adduct, the peak is linked to it: it folds under that row in **Assignments**,
shows its level, and keeps the tier its own evidence gives it. The message
names the main line's m/z. When the run holds no such main line, or more than
one, the peak is a row of its own, its line (`M+4`) beside the formula, counted
under its tier above the ledger, and the message says which case it was. A
main line you assign later takes the run's isotope lines of its formula and
adduct that you assigned by hand and that belong to no main line. A rebuild of
the batch ledger reads such a peak as a line of its batch peak's compound, not
as the compound itself at the line's m/z. A main line you assign also claims
the isotope lines the spectrum shows for it, on peaks the run left unassigned:
each becomes a line under it, as a run writes one, at no higher a tier and
with its level. A line is claimed only as a run would take it: its peak within
the run's mass window of the line, at a height the line's predicted share
allows. A line on a peak another row holds is not taken: another formula, a
reagent, or a peak you or an earlier change left unassigned. The message lists
the lines it claimed and the ones it left, with what each peak reads now. The
replaced compound's isotopologues are unassigned with it and come back with
it, the lines a main line claimed included, except any assigned by hand
since. An isotope line you assigned by hand is not unassigned with it: it keeps
its formula, stands on its own, and is linked again when the compound comes
back.

After the click, **Assignments** shows the row it wrote. The run is read again,
and the row can land under another one: an isotope line folds under its
compound's main line, and a main line can gain lines. So the ledger opens that
family, selects the row and scrolls to it. A family you had open stays open.
An isotope line folded under its main line carries the hand mark when it was
assigned by hand; hover it to see whether you assigned that line or your
assignment of its main line claimed it. The ledger does not change your
filters: when a tier chip, the search, the **to verify** chip, the lens or a
choice under **Show** hides the row, a note under the filter bar names it. The
toast carries the server's whole message, which says what the change did.

A hand assignment lasts until the sample is next assigned, which recomputes the
ledger, so record a verification to keep the judgement. Assigning is not
verifying: nothing is confirmed on your behalf, and a verdict the row carried
stays with the formula it judged.
