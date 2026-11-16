- **A thin Orbitrap file's pooled envelope curve no longer depends on which
  files of its batch ran before it.** A file with too few 13C lines for a
  curve of its own (under 20) pooled them with the lines its batch siblings'
  latest runs had recorded, so a sibling not run yet added nothing: two runs of
  one batch on the same data gave the first files other fits and tiers, and
  files run at the same time saw none of each other's lines. The pool is now
  the six files of its batch nearest it in acquisition time that share its
  acquisition settings, have a peak list and that the engine would run, each
  read afresh by its own Stage A first pass under the reading run's config and
  match parameters, never from a run of its own. A thin file folded into the
  batch ledger at ingest pools the same way. The run's record
  (`config.envelope_tolerance.pool`) names the files pooled, the lines each
  gave and the time span they cover. A thin file's run takes about a minute
  longer for the six reads; thin files run at the same time share the reads
  they have in common (`docs/user/how-it-works/peak-assignment.md`).
