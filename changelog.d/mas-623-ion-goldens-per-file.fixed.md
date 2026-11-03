- **`mascope demo snapshot --update` checks the ion goldens file by file.**
  A refresh is refused when a file still in the raw set exports no golden
  ions this time, even while other files still do; before, only a run with no
  ions at all was refused, so one file's ions could vanish without a word. A
  file that left the raw set drops its ion goldens with a warning instead of
  refusing the refresh, as its MS2 goldens already did (#2814).
