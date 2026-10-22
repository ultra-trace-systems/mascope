- **The m/z drift warning reaches error monitoring once a day per instrument
  again, not once per backend worker.** Each worker kept its own once-a-day
  window, and production runs one worker per two CPU cores, so one drifting
  instrument still raised the warning dozens of times a day. The window is now
  shared through Redis. If Redis does not answer within a second, each worker
  falls back to its own window, and the calibration is never held up.
