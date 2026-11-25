- **An assignment run that fails while the database refuses connections
  still ends failed.** When Postgres ran out of connections, the run's
  failure could not be recorded either, so the run stayed running until
  the backend restarted. It blocked a new assignment of its sample, and a
  client waiting on it never saw it end. The run's end is now retried for
  about a minute while the database refuses connections, and the error
  that ended the run is logged and raised even when that write fails.
