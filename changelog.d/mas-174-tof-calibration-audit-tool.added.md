- **A read-only audit of a TOF instrument's m/z calibration.** `db script run
  audit_tof_calibration`, for one instrument over a date range
  (`TOF_AUDIT_INSTRUMENT`, `TOF_AUDIT_FROM`, `TOF_AUDIT_TO`; every file, or
  `TOF_AUDIT_PER_DAY` of each day), refits each file's stored peaks with
  the calibration node's own matching, selection and fit, under the mode's
  calibration collection and any calibrant sets named in `TOF_AUDIT_SETS`,
  and reports per ion and per mass band how far each fit reads: the error
  where the fit used the ion and where it did not, how often each calibrant
  is found and kept, the offset of strong peaks from the nearest CHO formula
  under each of the mode's mechanisms, and the members of the nitrate,
  sulfate and bromide cluster series. It checks that its refit under the
  mode's collection lands on the stored fit to 0.01 ppm on every file the
  node fitted, and says why where it does not. It writes nothing, so no
  backup is taken before it; `TOF_AUDIT_JSON=1` adds the whole result as
  JSON. It is the yardstick of the TOF calibration plan
  (`docs/dev/tof_calibration.md`).
