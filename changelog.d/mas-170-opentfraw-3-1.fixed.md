- **A multiplexed PRM file from a Q Exactive or an Exploris is read instead
  of refused.** Such a scan stores one reaction per precursor, so its record
  is longer than a single-precursor scan's, and the reader read every record
  after the first from the wrong bytes. Mascope refused the file for the
  analyzers that gave its scans. The pinned reader walks these records at
  their own sizes: a public Q Exactive HF-X PRM run that was refused now
  reads as 4,142 Orbitrap MS2 scans, each with the filter the Thermo library
  gives it. A file refused under 1.13.0 is not read again by itself: it
  needs processing again, by *Re-process* in Raw files.
