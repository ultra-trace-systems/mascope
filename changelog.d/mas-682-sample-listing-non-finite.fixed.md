- **A sample no longer stores a TIC of NaN.** Three TOF samples on a
  production server stored a NaN TIC, and the sample listing, which cannot
  send one in JSON, sent it as null and logged a warning on every listing of
  their batch. The likeliest source is a TOF file that recorded no signal:
  its reader rescaled each scan to the file's total, and 0/0 is NaN. Such a
  file now has a TIC of 0, and a TIC that still reads as no finite number is
  stored as none. The database upgrade clears the NaN and infinite TICs
  stored before (`docs/maintaining.md`, "Sample TIC as the mean per scan").
