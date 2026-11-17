- **The bench card reads both engines against a set's authors' list.** A
  bench set drawn from a published or shared study can name the neutral
  formulas its authors assigned (`"truth": {"file": P, "sha256": H}` in the
  private manifest's folder). `bench init` checks the list's hash and keeps
  it in the bench's state, never in the instance, whose engine would read it
  as known targets. The card then adds, per engine, the share of its
  assigned monoisotopic formulas the list holds and the share of the list it
  assigns (at assigned, and at assigned or candidate), comparing formulas by
  element counts. The lists are not exhaustive, so these are readings that
  no target gates (`tooling/assignment_compare/README.md`, "An authors'
  list"). `compare_runs.py --card` takes the same `truth` in its sets file.
