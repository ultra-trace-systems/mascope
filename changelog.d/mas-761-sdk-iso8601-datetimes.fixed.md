- **SDK listings parse a datetime column also when some of its times have no
  fraction of a second.** The server writes a whole-second time without its
  fraction, and a column that mixed both forms stayed text, so comparing it
  with a `Timestamp` failed. The listings now read these columns as ISO 8601.
