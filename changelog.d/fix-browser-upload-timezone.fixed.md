- **A file uploaded from the web app has its acquisition time read in the
  browser's timezone.** A raw file records its acquisition time as the
  instrument PC's wall clock, with no offset, and the server can only place
  it in UTC if the upload says which zone that clock was in. A File Agent
  reports its machine's zone with every upload; the web app reported none,
  and the converter then read the wall clock in its own zone, which is UTC
  in the production image. Every file uploaded from a browser at a site
  east of UTC was therefore stored hours late - two hours in central
  European summer time - with `utc_offset_source` `guess` and no
  `acquisition_timezone`, and its ingest appeared to precede its
  acquisition. The web app now sends the browser's IANA zone as the
  `timezone` metadata of each upload, as the File Agent does, and such a
  file is stored with its `acquisition_timezone` set and
  `utc_offset_source` `agent`. A browser in another zone than the
  instrument still reads the clock in its own, as an agent on such a
  machine does. Files already stored are not changed.
