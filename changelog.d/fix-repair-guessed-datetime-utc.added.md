- **Acquisition times that were guessed as UTC can be repaired.** A file
  uploaded without a reported timezone has its wall clock read in the
  converter's own zone, UTC in the production image, so at a site in another
  zone such a file was stored with a zero offset: its `datetime_utc` is the
  instrument PC's wall clock labelled UTC, hours off, with
  `utc_offset_source` `guess` and no `acquisition_timezone`. Any upload that
  reports no zone does this - the web app before it reported the browser's
  zone, SDK scripts, File Agents older than zone reporting - so the rows keep
  arriving until a site's clients all report one. `mascope prod db script run
  fix_guessed_datetime_utc` repairs the Orbitrap rows stored that way,
  reading each one's wall clock in the zone the instrument PC's clock is set
  to (`MASCOPE_FIX_GUESSED_UTC_ZONE`), optionally for one instrument or one
  window of registration time, and writing what the converter would have
  stored had the upload reported that zone; the instrument function fitted
  from the file moves with it. A dry run is the default and reports what
  would change, with evidence about the zone named: how many rows end after
  their own registration as stored and after the repair.
  `MASCOPE_FIX_GUESSED_UTC_APPLY=1` writes, and every apply leaves a ledger
  of the rows it changed, old and new value, under the environment's
  `repairs/` folder. Guessed TOF rows are not touched, since their
  `datetime_utc` is already right; nor are rows that carry a reported zone,
  or guessed rows stored with another offset, which are counted and left
  alone. The two source columns of a repaired row are not changed. See
  `docs/maintaining.md`.
