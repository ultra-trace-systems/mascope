- **The Raw files list reads its page off an index.** Each page load runs
  two statements, the page and a count for the paginator, and both scanned
  every sample file on the server: nothing indexed the acquisition time, the
  instrument or the processing time they filter on, and the count sorted
  every row it kept, in the list's order. So the cost grew with the
  look-back, and for all time with everything the server had acquired.
  `sample_file` gains three indexes: acquisition time in the list's
  newest-first order, the same per instrument, and processing time.
  Measured on a synthetic year of half a million files from four
  instruments, page and count: the last 7 days from 45 and 51 ms to 0.1 and
  3 ms; 30 days from 54 and 59 ms to 0.1 and 7 ms; one instrument over 30
  days from 51 and 48 ms to 0.1 and 3 ms; all time from 129 and 235 ms to
  0.1 and 80 ms; 7 days by processing time from 53 and 62 ms to 0.1 and
  8 ms. Sorted oldest first, the list is bounded by the index but still
  sorted. One migration, indexes only; it builds in under two seconds on a
  table that size.
