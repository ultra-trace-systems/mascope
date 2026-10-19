- **The Raw files list reads its page off an index, newest or oldest
  first.** Each page load runs two statements, the page and a count for the
  paginator, and both scanned every sample file on the server: nothing
  indexed the acquisition time, the instrument or the processing time they
  filter on, and the count sorted every row it kept, in the list's order. So
  the cost grew with the look-back, and for all time with everything the
  server had acquired. `sample_file` gains three indexes - acquisition time,
  the same per instrument, and processing time - and the list is ordered so
  that one index serves it in either direction. The count now has no order.
  Files that tie on the sorted column follow it in the sort's direction, so
  on a descending sort they come back in descending id order where they used
  to come back ascending. Measured on a synthetic year of half a million
  files from four instruments, page and count: the last 7 days from 46 and
  42 ms to 0.1 and 3 ms; 30 days from 52 and 58 ms to 0.1 and 6 ms, and the
  same oldest first; one instrument over 30 days from 50 and 49 ms to 0.1
  and 3 ms; all time from 124 and 240 ms to 0.1 and 22 ms; 7 days by
  processing time from 50 and 52 ms to 0.1 and 3 ms. One migration, indexes
  only; it builds in under two seconds on a table that size.
