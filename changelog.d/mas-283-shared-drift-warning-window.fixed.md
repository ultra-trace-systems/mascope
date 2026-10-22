- **The m/z drift warning reaches error monitoring once a day per instrument.**
  Each backend worker kept its own once-a-day window, so one drifting
  instrument still warned about ten times a day, and again after every
  restart. The window is now shared through Redis. If Redis cannot answer,
  each worker falls back to its own window.
