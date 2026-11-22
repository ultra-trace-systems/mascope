- **A sign-out that Redis cuts off partway is finished.** A logout's sign-out
  cut off by a Redis restart, after a socket's session was cleared but before
  it left its rooms, used to leave that socket hearing record-sync events
  until it reconnected: the retry could no longer find it. The worker now
  keeps the socket itself and finishes its sign-out once Redis answers. An
  eviction cut off the same way now also drops the account's place in the
  rooms its sockets had already left (#3099).

- **A lost eviction is caught up on within the hour.** The hourly renewal of
  each open web socket now reads its account, and evicts the socket if the
  account is disabled, deleted, held at the password screen or has had its
  sessions ended. An eviction lost with a worker that stopped during a Redis
  outage used to last until the socket reconnected or its token expired. A
  logout lost that way still does, as the server keeps no mark of it
  (#3091).
