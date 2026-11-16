- **A bench run of a branch with a migration of its own no longer leaves the
  assignment bench refusing develop.** `bench.py run` dumps the bench database
  before it migrates for a ref whose head is not on develop, and restores the
  dump once the ref's backend has stopped. A run stopped before the restore
  leaves a record, and the next bench command that migrates restores it
  first (#3171).
