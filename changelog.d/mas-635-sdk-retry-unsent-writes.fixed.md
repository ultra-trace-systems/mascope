- **The SDK retries a write the server did not act on.** A create, a save, a
  delete or a run import answered 429 or 425, or whose connection could not be
  made, is sent again after the backoff instead of raising at once, so a
  script or `mascope_ai`'s `apply` making many writes backs off from the rate
  limit rather than stopping part-way. A read timeout, a connection lost after
  the send, a 408 and a 5xx still raise after one send. Every retry now waits
  at least the answer's `Retry-After`, and one over a minute is raised at once.
