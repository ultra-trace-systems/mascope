- **A forced rematch keeps a batch's pending recalibration.** A rematch
  forced on a batch that waited for an m/z recalibration ended at `rematch`
  when some samples failed, and at `ready` when it succeeded, so the
  pending re-fit was lost. It now ends at `recalibrate` whatever its
  outcome, as a failed one already did: a rematch does not re-fit the m/z
  axis.
