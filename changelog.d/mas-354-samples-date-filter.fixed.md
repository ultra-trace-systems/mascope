- **`GET /api/samples` filters by acquisition time again.** Given both
  `datetime_min` and `datetime_max`, the route answered 500: the filter
  called a SQLite function Postgres does not have. Each bound now works on
  its own as well, where one alone used to be ignored. Both are inclusive,
  and a bound written without an offset is read as UTC, as `datetime_utc`
  is.
