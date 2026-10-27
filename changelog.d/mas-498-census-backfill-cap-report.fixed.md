- **A capped `backfill_scan_stream_census` run says when files are left.**
  With `CENSUS_LIMIT` set, a server with fewer than 2000 Orbitrap files ended
  its run without the "run this again" line, so a run that left files looked
  finished. The survey now counts the files the cap leaves for later, and its
  line says how many need a census and how many this run reads.
