- **A campaign store's sync no longer rewrites its manifest after every
  batch and every day.** `mascope_viz`'s `sync` writes `store.json` at most
  every two seconds while it fetches and rolls up, and at once for a failed
  batch, for whatever stops the sync, and at the end. On a local store a
  year of daily batches wrote it 731 times, for 5 to 6 of its 33 seconds;
  it now writes it about 14 times, for a tenth of a second. A sync stopped
  between two writes fetches the batches since then again, and a batch
  whose tables it had rewritten is fetched again as `unsaved`, so no rollup
  is left older than its tables. The store's format is unchanged.
