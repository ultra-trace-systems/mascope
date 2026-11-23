A **batch peak** is a cross-sample anchor: one m/z bin that gives a species a
single stable identity across a batch's samples, so its intensity can be drawn
as one trace over the batch. Every observed peak folds into exactly one batch
peak &mdash; unassigned m/z still get a batch-level trend &mdash; and each
batch peak carries a **consensus formula** and **consensus tier**, an
evidence-weighted consensus of its member peaks' per-sample assignments, plus
the number of samples it is seen in (**Samples**) and the **max
intensity** it reaches in any of them. The rows open brightest first. Click an
m/z to open the batch peak in its brightest sample.

Above the table, the **search box** finds a formula, a listed name or an m/z
(within 5 ppm, the window an imported batch result is matched at; a number also
finds a compound by its isotope lines). **Show** holds the row kinds
(isotopologue lines, roles, stripped rows), the batch's flags (probable blends,
flat series, one badge kind at a time), the engine's readings and the verdict
filter; **...** holds **Export ledger (CSV)** and **Write listed names in
full**. Every choice is kept for this ledger, through a visit to a sample and a
reload.
