- **Match ratings filtered by date read a bound without an offset as UTC.**
  `GET /api/match_ratings` read a `datetime_min` or `datetime_max` given
  without an offset in the server's local time zone, so the same request
  listed other ratings on a server not running in UTC. Such a bound is now
  UTC, as it already was on `GET /api/samples` and `GET /api/sample/files`.
