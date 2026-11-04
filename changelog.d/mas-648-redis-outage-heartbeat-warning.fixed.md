- **A Redis outage that lasts is reported to GlitchTip again.** Since
  Redis blips stopped filing errors from the web socket storage, a Redis
  that stayed down showed only in the log files. Each backend worker's
  heartbeat now files one warning once Redis has been out of reach for
  about a minute, and one more when it is back.
