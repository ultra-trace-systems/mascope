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
  acquisition. The web app now sends the browser's zone with each upload,
  read as the upload starts, and the upload dialog says which zone it will
  report. The converter ranks it below an agent's zone and above its own
  guess, and records such a file with its `acquisition_timezone` set and
  `utc_offset_source` `browser`, a new value: the browser is wherever the
  person uploading sits rather than at the instrument, so a file uploaded
  from another zone than the instrument's is still read in the browser's,
  and the value says so. Only a geographic zone is sent; a browser that
  reports a placeholder such as `UTC`, as fingerprinting-resistant,
  headless and kiosk browsers do, sends none, and the server falls back as
  before. Files already stored are not changed.
