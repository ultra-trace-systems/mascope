A **batch run** is one batch-level operation that rewrote the batch ledger
&mdash; *Rebuild batch ledger*, *Search untargeted* with the parameters it was
given, or an import &mdash; or the folding of samples that built the ledger in
the first place. Exactly one run is **current**: the live ledger, which every
new sample folds into and every curation and verdict edits. When a new run
starts, the ledger as the current run left it is kept as a **snapshot**, so
any earlier run can be viewed read-only from the run selector and its settings
compared with the next. The newest few runs are kept per batch; older ones are
pruned with their snapshots when a run completes.
