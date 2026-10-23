- **`GET /api/sample/files` reads a time bound without an offset as UTC.**
  `datetime_min` and `datetime_max` written with no offset were read in the
  server's local time zone, so on a server not running in UTC the same
  request listed other files. They are now UTC, as `datetime_utc` is, and
  as `GET /api/samples` already reads them.
