- **Decoy runs bound how many rows the assignment engine assigns falsely.** An
  assign request's config takes a `decoy` block naming one arm: `mz_shift_ppm`
  scales every peak's m/z (9 either way on an Orbitrap), `mz_shift_da` moves
  every peak (0.35 on a TOF), and `swap_adducts` searches each channel of the
  sample's mode through an adduct its chemistry does not make. The run is the
  engine run again on the sample's latest completed run, its control, at the
  control's settings and scored at its calibration and envelope tolerance, so
  what still reaches *assigned* is what the engine assigns where the sample's
  chemistry is wrong. A decoy that cannot bound its control (there is none, a
  setting differs, a ppm shift is inside the window the control searched in,
  or no wrong adduct is left) is refused with a 422 whose code is
  `decoy_refused`. A decoy run is marked on its row (`decoy`, a new column) and
  is never read as the sample's result: the runs listing and the SDK's
  `list_runs` leave it out unless asked (`include_decoys`); the ledger read
  that names no run, the batch fold and its consensus, the batch's sample
  status, the seeded re-scores, the envelope tolerance read outside a run and
  the curve a sibling pools read the control; its rows take no verdict; the
  confidence calibration never reads one; and each arm keeps a retention quota
  of its own, so a sample's decoys never evict the run they bound.
  `tooling/assignment_compare/compare_runs.py --decoy` reports, per arm, the
  share of the control's assigned rows and of their intensity the arm still
  holds, below and above m/z 350, and how many of the adduct arm's rows are
  new ions (`docs/user/how-it-works/peak-assignment.md`, *Decoy runs*).
