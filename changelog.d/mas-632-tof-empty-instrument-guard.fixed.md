- **A TOF agent acquisition event without an instrument no longer logs an
  error.** The acquisition started, progress and finished events need an
  instrument to know whom to notify. One that names none is now dropped with a
  single warning, grouped per event in monitoring, instead of an "Unexpected
  socket error" with a traceback each time.
