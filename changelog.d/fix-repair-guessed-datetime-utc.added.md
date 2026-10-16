- **Acquisition times that were guessed as UTC can be repaired.** A file
  uploaded without a reported timezone has its wall clock read in the
  converter's own zone, UTC in the production image, so at a site in another
  zone such a file was stored with a zero offset: its `datetime_utc` is the
  instrument PC's wall clock labelled UTC, hours off, with
  `utc_offset_source` `guess` and no `acquisition_timezone`. The web app now
  reports the browser's zone; `mascope prod db script run
  fix_guessed_datetime_utc` repairs the rows stored before that, reading each
  one's wall clock in the zone the operator names
  (`MASCOPE_FIX_GUESSED_UTC_ZONE`), optionally for one instrument or one
  window of registration time, and writing what the converter would have
  stored had the upload reported that zone. A dry run is the default and
  reports what would change; `MASCOPE_FIX_GUESSED_UTC_APPLY=1` writes. Rows
  that were guessed in a real zone - their stored offset is not zero - are
  counted and left alone, as are rows that carry a reported zone. The two
  source columns of a repaired row are not changed. See
  `docs/maintaining.md`.
