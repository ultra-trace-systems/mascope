- **Assign a sample again before searching its batch, where its run is from
  1.16.1 or earlier and measured nothing.** Such a run recorded the centre
  and width it scored at apart from the calibration its mass gate capped at.
  The batch's untargeted search reads it at the first alone, so where its
  known compounds matched too few library ions to measure the sample (a TOF
  sample with a thin library is the usual case), the search caps none of
  its rows off calibration and gives them no mass z. Run *Assign sample* on
  such a sample once after the upgrade; *Assign batch* alone does not
  replace the run. `docs/maintaining.md`, "Samples assigned before the one
  measurement model", has the query that lists them.
