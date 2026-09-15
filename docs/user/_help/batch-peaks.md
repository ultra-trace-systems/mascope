A **batch peak** is a cross-sample anchor: one m/z bin that gives a species a
single stable identity across a batch's samples, so its intensity can be drawn
as one trace over the batch. Every observed peak folds into exactly one batch
peak &mdash; unassigned m/z still get a batch-level trend &mdash; and each
batch peak carries a **consensus formula** and **consensus tier**, an
evidence-weighted consensus of its member peaks' per-sample assignments, plus
the number of samples it is seen in and the **highest intensity** it reaches in
any of them.
