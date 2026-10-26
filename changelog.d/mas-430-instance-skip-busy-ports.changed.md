- **`mascope dev run --instance` skips a slot whose ports are taken.** A new
  instance no longer takes a slot when another process already listens on its
  API or frontend port, such as a bare `mascope dev run` or another service on
  8090. It takes the next slot that is free in the registry and on the host,
  and logs which slot it skipped. A worktree that already has an instance
  keeps its slot.
