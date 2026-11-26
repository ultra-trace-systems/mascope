- **A batch assignment pass that fails while the database refuses
  connections still ends.** When Postgres ran out of connections, a pass's
  failure or cancel could not be recorded either, so its batch run stayed
  running until the backend restarted, and **Assign batch** refused every
  later pass on that batch. A batch run's end is now retried for about a
  minute, as a sample's run's is, and the error that stopped the pass is
  the one reported even when that write fails.
