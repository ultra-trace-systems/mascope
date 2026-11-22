A **batch run** is one batch-level operation that rewrote the batch ledger
&mdash; a pass of the batch stages, a rebuild of the ledger, or an import
&mdash; or the folding of samples that built the ledger in the first place. A
**pass** is one run however many stages it runs, and keeps each stage's
status and result. **Run history** names each: *Assignment* for a pass
*Assign batch* made, *Assignment from a stage (Detect series)* for one that
started past the first stage, a stage's own name (*Search untargeted*, ...)
for a pass its API route started, and *Import*, *Rebuild* or *Folded samples*
for the rest. Exactly one run is **current**: the live ledger, which every
new sample folds into and every curation and verdict edits. When a new run
starts, the ledger as the current run left it is kept as a **snapshot**, so
any earlier run can be viewed read-only from Run history, and **Back to
current** returns to the live one. The newest few runs are kept per batch;
older ones are pruned with their snapshots when a run completes.
