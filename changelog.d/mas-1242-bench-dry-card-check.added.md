- **The assignment bench can say whether a head already has a card without
  making one.** `bench.py run <ref> --check`, with the request's `--sets`,
  `--decoys`, `--batch`, `--baseline` or `--post`, prints the head's engine
  digest and its three parts and the complete card a run would be served:
  its folder, ref, commit, when it was made and its verdict. It writes,
  records and posts nothing, and takes no lock, so it answers in seconds
  while the bench's queue is long. It exits 0 when there is such a card, 1
  when the head would run and 2 when it cannot tell
  (`tooling/assignment_bench/README.md`, "Asking without a run").
