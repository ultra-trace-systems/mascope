- **A peak assignment run no longer stalls every other request of its
  worker.** The run's CPU-heavy steps (the targeted matcher's match, Stage
  A's fit, the envelope fit, the seeded re-score, the election, the judging
  rounds and the identification level) run in a worker thread instead of on
  the server's event loop. On a demo uronium sample the loop was held for
  9.5 s of a 27 s run, 6.7 s of it at once in the tiering pass; it is now
  held for about 1 s in all, no stall over 0.2 s. Pages and sign-ins stay
  responsive while runs are in flight. The targeted Match tab's matching
  runs off the loop the same way.
